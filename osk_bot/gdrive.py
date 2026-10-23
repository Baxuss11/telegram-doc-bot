"""Связь с Google Диском и Таблицей через веб-приложение Apps Script.

Apps Script работает от имени владельца папки, поэтому файлы занимают его
место на Диске — у сервисных аккаунтов Google своего места нет.
"""
import base64
import logging
import re

import httpx

from . import config

logger = logging.getLogger(__name__)


class DriveError(Exception):
    pass


# Слова, которые не отличают одну компанию/ЖК от другой. Та же логика — в Code.gs
# (normName_): «ЖК Елизавета», «елизавета» и «Елизавета» попадают в одну папку.
_NOISE = re.compile(r"\b(жилой комплекс|жк|ж к|мжк|строительная компания|компания|ск|осоо|оао|"
                    r"ооо|зао|ао|ип|llc|ltd)\b")


def norm_name(name: str) -> str:
    s = name.lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-яөүң]+", " ", s)
    s = _NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def enabled() -> bool:
    return bool(config.APPS_SCRIPT_URL and config.APPS_SCRIPT_SECRET)


async def _call(action: str, **payload) -> dict:
    if not enabled():
        raise DriveError("Google Диск не настроен (APPS_SCRIPT_URL / APPS_SCRIPT_SECRET)")
    body = {"secret": config.APPS_SCRIPT_SECRET, "action": action, **payload}
    try:
        # Apps Script отвечает редиректом 302 на googleusercontent.com — идём по нему.
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            resp = await client.post(config.APPS_SCRIPT_URL, json=body)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise DriveError(f"Нет ответа от Apps Script: {e}") from e
    if not data.get("ok"):
        raise DriveError(data.get("error") or "неизвестная ошибка Apps Script")
    return data


async def upload(path: list[str], name: str, mime: str, content: bytes) -> dict:
    """Кладёт файл в папку ROOT/path[0]/path[1]/... (папки создаются при необходимости).

    Возвращает {fileId, fileUrl, folderId, folderUrl}.
    """
    return await _call(
        "upload", path=path, name=name, mimeType=mime,
        data=base64.b64encode(content).decode("ascii"),
    )


async def folder(path: list[str]) -> dict:
    """Находит или создаёт папку. Возвращает {folderId, folderUrl}."""
    return await _call("folder", path=path)


async def children(path: list[str]) -> list[str]:
    """Названия папок внутри ROOT/path (ничего не создаёт). Пусто, если папки нет."""
    return (await _call("children", path=path)).get("names", [])


async def trash(file_id: str) -> None:
    await _call("trash", fileId=file_id)


async def append_rows(sheet: str, headers: list[str], rows: list[list]) -> dict:
    """Дописывает строки в лист таблицы рейтинга. Возвращает {spreadsheetUrl}."""
    return await _call("appendRows", sheet=sheet, headers=headers, rows=rows)


async def ping() -> dict:
    return await _call("ping")
