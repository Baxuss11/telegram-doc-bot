"""Клиент Bitrix24 REST (входящий вебхук): чтение заявок (сделок или лидов).

Вебхук должен иметь только право crm. Читаем нужные поля, включая UTM и поле
качества, и проходим всю постраничную выдачу (Bitrix отдаёт по 50).
"""

import requests


class BitrixError(Exception):
    """Ошибка REST Bitrix24 (неверный вебхук, нет прав, неизвестное поле)."""


def fetch_items(webhook, entity, since, select_fields, timeout=60):
    """Возвращает список записей CRM (entity='deal' или 'lead') от даты since.

    since — дата 'YYYY-MM-DD'. select_fields — поля, которые нужно вернуть
    (ID, DATE_CREATE, поле качества, поля-ключи связывания и т.д.).
    """
    method = webhook + f"crm.{entity}.list"
    # Убираем дубли, сохраняя порядок.
    select = list(dict.fromkeys(select_fields))

    leads = []
    start = 0
    while True:
        payload = {
            "order": {"DATE_CREATE": "ASC"},
            "filter": {">=DATE_CREATE": since},
            "select": select,
            "start": start,
        }
        resp = requests.post(method, json=payload, timeout=timeout)
        try:
            data = resp.json()
        except ValueError:
            raise BitrixError(f"Bitrix вернул не-JSON (HTTP {resp.status_code})")

        if isinstance(data, dict) and data.get("error"):
            raise BitrixError(data.get("error_description") or data.get("error"))

        leads.extend(data.get("result", []))

        # Bitrix отдаёт смещение следующей страницы в поле next.
        nxt = data.get("next")
        if nxt is None:
            break
        start = nxt

    return leads
