"""Проверка, что на фото именно тот документ, который просит шаг.

Сначала бесплатно читаем текст на фото (Tesseract OCR) и ищем ключевые слова
документа (buyer.KEYWORDS). Если документу нужно несколько страниц и есть образцы —
фото сверяет ИИ (Claude) с образцами: какая это страница и не повтор ли она.
ИИ подключается и тогда, когда OCR ничего не нашёл.
"""
import asyncio
import base64
import io
import json
import logging
import re
import shutil
from dataclasses import dataclass

import anthropic
from PIL import Image, ImageOps

from . import buyer, config

logger = logging.getLogger(__name__)

MAX_SIDE = 1600       # больше не нужно ни OCR, ни ИИ, а запрос легче
OCR_TIMEOUT = 60
OCR_HINT_CHARS = 1500

_client: anthropic.AsyncAnthropic | None = None


@dataclass
class Verdict:
    ok: bool
    page: int = 0       # номер страницы образца (0 — не определяли)
    method: str = ""    # ocr / ai / none — чем подтверждено
    reason: str = ""    # пояснение для покупателя, если не принято


def ocr_enabled() -> bool:
    return shutil.which("tesseract") is not None


def ai_enabled() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"-\s*\n\s*", "", text)  # перенос слова по слогам
    return re.sub(r"\s+", " ", text)


def keyword_hits(doc_key: str, text: str) -> list[str]:
    norm = normalize(text)
    return [k for k in buyer.KEYWORDS.get(doc_key, ()) if k in norm]


def prepare_jpeg(content: bytes) -> bytes:
    """Поворачивает по EXIF, уменьшает до MAX_SIDE и пережимает в JPEG."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


async def ocr_text(jpeg: bytes) -> str | None:
    """Текст с фото или None, если OCR недоступен или упал."""
    if not ocr_enabled():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "tesseract", "stdin", "stdout", "-l", "rus+eng", "--psm", "3",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(jpeg), OCR_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as ex:
        logger.warning("OCR не сработал: %s", ex)
        return None
    return out.decode("utf-8", "ignore")


SYSTEM = (
    "Ты проверяешь фотографии документов застройщика в Кыргызстане (Бишкек), которые "
    "покупатель квартиры присылает в Telegram-бот по шагам. На каждом шаге нужен "
    "определённый документ. Твоя задача — честно определить, действительно ли на фото "
    "этот документ, чтобы нельзя было набрать баллы случайными листами, селфи или "
    "другими документами. Документы бывают на русском и кыргызском, сняты телефоном, "
    "могут быть под углом, с бликами, частично замазаны — это нормально. "
    "Если даны образцы страниц, определи, какой странице образца соответствует фото: "
    "совпадать должен тип страницы (титульный лист, последний лист с печатью, приложение "
    "и т.п.), а не конкретные данные. Отвечай только JSON по схеме; reason — одно "
    "короткое предложение по-русски для покупателя."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {"type": "boolean"},
        "page": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["matches", "page", "reason"],
    "additionalProperties": False,
}


def _image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpeg).decode("ascii")}}


async def ai_check(doc: buyer.BuyerDoc, content: bytes, is_pdf: bool,
                   refs: list[bytes], ocr: str | None) -> Verdict:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    blocks: list[dict] = [{"type": "text", "text": f"Документ этого шага: «{doc.title}».\n"
                                                   f"Что покупатель должен снять: {doc.instruction}"}]
    for i, ref in enumerate(refs, 1):
        blocks += [{"type": "text", "text": f"Образец, страница {i}:"}, _image_block(ref)]
    blocks.append({"type": "text", "text": "Фото от покупателя:"})
    if is_pdf:
        blocks.append({"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(content).decode("ascii")}})
    else:
        blocks.append(_image_block(content))
    if ocr and ocr.strip():
        blocks.append({"type": "text", "text": "Текст с фото, распознанный OCR (может быть с "
                                               "ошибками):\n" + ocr.strip()[:OCR_HINT_CHARS]})
    blocks.append({"type": "text", "text": (
        "matches — true, только если на фото действительно этот документ (его страница). "
        + (f"page — номер страницы образца (1–{len(refs)}), которой соответствует фото, "
           "или 0, если ни одной." if refs else "page — всегда 0.")
    )})
    response = await _client.beta.messages.create(
        model=config.AI_MODEL,
        max_tokens=2048,
        system=SYSTEM,
        messages=[{"role": "user", "content": blocks}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise anthropic.AnthropicError("модель отказалась проверять фото")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    page = data["page"] if refs and 1 <= data["page"] <= len(refs) else 0
    return Verdict(bool(data["matches"]), page, "ai", data.get("reason", ""))


async def check(doc: buyer.BuyerDoc, content: bytes, mime: str, refs: list[bytes],
                allow_ai: bool = True) -> Verdict:
    """Подтверждает, что файл — документ шага. refs — фото-образцы страниц (может быть пусто)."""
    use_ai = allow_ai and ai_enabled()
    is_pdf = mime == "application/pdf"
    ocr = None
    if not is_pdf:
        content = await asyncio.to_thread(prepare_jpeg, content)
        ocr = await ocr_text(content)
    hits = keyword_hits(doc.key, ocr) if ocr else []
    # Страницы различает только ИИ: при нескольких образцах OCR лишь подсказка.
    if hits and (len(refs) < 2 or not use_ai):
        return Verdict(True, 0, "ocr")
    if use_ai:
        try:
            return await ai_check(doc, content, is_pdf, refs, ocr)
        except (anthropic.AnthropicError, ValueError, KeyError) as ex:
            logger.warning("ИИ не проверил фото (%s): %s", doc.key, ex)
            if hits:
                return Verdict(True, 0, "ocr")
            if ocr is None:
                return Verdict(True, 0, "none")
            return Verdict(False, reason="Не получилось проверить фото — пришлите его ещё раз.")
    if ocr is None:
        return Verdict(True, 0, "none")  # проверить нечем — принимаем, в таблице будет видно
    return Verdict(False, reason=f"Не удалось узнать на фото «{doc.title}». Снимите документ "
                                 "целиком, ровно и при хорошем свете, чтобы читался текст.")
