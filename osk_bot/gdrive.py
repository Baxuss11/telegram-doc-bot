"""Связь с Google Диском и Таблицей через веб-приложение Apps Script.

Apps Script работает от имени владельца папки, поэтому файлы занимают его
место на Диске — у сервисных аккаунтов Google своего места нет.
"""
import base64
import logging

import httpx

from . import config

logger = logging.getLogger(__name__)


class DriveError(Exception):
    pass


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


async def trash(file_id: str) -> None:
    await _call("trash", fileId=file_id)


async def append_rows(sheet: str, headers: list[str], rows: list[list]) -> dict:
    """Дописывает строки в лист таблицы рейтинга. Возвращает {spreadsheetUrl}."""
    return await _call("appendRows", sheet=sheet, headers=headers, rows=rows)


async def ping() -> dict:
    return await _call("ping")
