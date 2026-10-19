"""PDF-отчёт по проверке: итог, баллы по пунктам и фото-доказательства."""
import io
import os
from dataclasses import dataclass, field

from fpdf import FPDF
from PIL import Image, ImageOps

from . import config
from .checklist import ITEMS, MAX_POINTS, item_score

MAX_IMAGE_SIDE = 1400
JPEG_QUALITY = 70


@dataclass
class ReportData:
    inspection_id: int
    company: str
    zhk: str
    inspector: str
    started: str
    finished: str
    score: int
    grade: str
    checked: set[str]
    states: dict  # item_key -> {visited, absent, comment}
    # item_key -> список (байты, тип, имя файла), тип: photo / image / pdf
    files: dict[str, list[tuple[bytes, str, str]]] = field(default_factory=dict)
    folder_url: str | None = None


class _PDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("DejaVu", size=8)
        self.set_text_color(120)
        self.cell(0, 6, f"Стр. {self.page_no()}", align="C")
        self.set_text_color(0)


def _prepare_image(raw: bytes) -> tuple[io.BytesIO, int, int]:
    """Фото → сжатый JPEG. Возвращает (поток, ширина, высота).

    JPEG-поток кладётся в PDF как есть (DCTDecode), поэтому страницы с фото
    весят в разы меньше, чем при хранении без сжатия, — иначе Telegram
    отклоняет отчёт как слишком большой файл.
    """
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # фото, отправленные файлом, не будут повёрнуты
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    buf.seek(0)
    return buf, img.width, img.height


def build(data: ReportData) -> bytes:
    pdf = _PDF(format="A4")
    pdf.add_font("DejaVu", "", os.path.join(config.FONTS_DIR, "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", os.path.join(config.FONTS_DIR, "DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_page()
    width = pdf.epw

    pdf.set_font("DejaVu", "B", 16)
    pdf.multi_cell(width, 8, "Технический рейтинг ОСК — акт проверки", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("DejaVu", size=11)
    for label, value in (
        ("Проверка №", str(data.inspection_id)),
        ("Строительная компания", data.company),
        ("ЖК", data.zhk),
        ("Проверяющий", data.inspector),
        ("Начало", data.started),
        ("Завершение", data.finished),
    ):
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(60, 6.5, label)
        pdf.set_font("DejaVu", size=11)
        pdf.multi_cell(width - 60, 6.5, value, new_x="LMARGIN", new_y="NEXT")
    if data.folder_url:
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(60, 6.5, "Фото на Диске")
        pdf.set_font("DejaVu", size=11)
        pdf.set_text_color(30, 80, 200)
        pdf.cell(width - 60, 6.5, "открыть папку", link=data.folder_url, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0)

    pdf.ln(4)
    pdf.set_font("DejaVu", "B", 14)
    percent = round(100 * data.score / MAX_POINTS) if MAX_POINTS else 0
    pdf.multi_cell(width, 8, f"Итог: {data.score} из {MAX_POINTS} баллов ({percent}%) — {data.grade}",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Таблица баллов по пунктам
    pdf.set_font("DejaVu", size=9.5)
    with pdf.table(col_widths=(8, 52, 14, 26), text_align=("CENTER", "LEFT", "CENTER", "LEFT"),
                   line_height=5.5) as table:
        header = table.row()
        for title in ("№", "Пункт / критерий", "Баллы", "Фото / комментарий"):
            header.cell(title)
        for n, item in enumerate(ITEMS, 1):
            state = data.states.get(item.key) or {}
            files = data.files.get(item.key, [])
            lines = [item.title]
            for c in item.criteria:
                mark = "✔" if c.key in data.checked else "✘"
                lines.append(f"{mark} {c.title} ({c.points})")
            notes = []
            if not state.get("visited"):
                notes.append("не проверен")
            elif state.get("absent"):
                notes.append("документа нет")
            notes.append(f"фото: {len(files)}" if files else "без фото")
            if state.get("comment"):
                notes.append(state["comment"])
            row = table.row()
            row.cell(str(n))
            row.cell("\n".join(lines))
            row.cell(f"{item_score(item, data.checked)} / {item.max_points}")
            row.cell("\n".join(notes))

    # Фото по пунктам
    for n, item in enumerate(ITEMS, 1):
        files = data.files.get(item.key, [])
        if not files:
            continue
        pdf.add_page()
        pdf.set_font("DejaVu", "B", 13)
        pdf.multi_cell(width, 7, f"{n}. {item.title}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font("DejaVu", size=10)
        for raw, kind, name in files:
            if kind == "pdf":
                pdf.multi_cell(width, 6, f"PDF-файл «{name}» — сохранён в папке проверки на Диске.",
                               new_x="LMARGIN", new_y="NEXT")
                continue
            try:
                stream, iw, ih = _prepare_image(raw)
            except Exception:
                pdf.multi_cell(width, 6, f"Не удалось открыть изображение «{name}».",
                               new_x="LMARGIN", new_y="NEXT")
                continue
            # Вписываем фото в ширину страницы и не выше 46% высоты —
            # по два фото на страницу.
            max_h = pdf.eph * 0.46
            w = width
            h = w * ih / iw
            if h > max_h:
                h = max_h
                w = h * iw / ih
            if pdf.get_y() + h > pdf.page_break_trigger:
                pdf.add_page()
            pdf.image(stream, x=pdf.l_margin + (width - w) / 2, w=w, h=h)
            pdf.ln(4)

    return bytes(pdf.output())

