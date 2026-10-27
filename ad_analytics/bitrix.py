"""Клиент Bitrix24 REST (входящий вебхук): чтение лидов.

Вебхук должен иметь только право crm. Читаем нужные поля, включая UTM и поле
качества лида, и проходим всю постраничную выдачу (Bitrix отдаёт по 50).
"""

import requests


class BitrixError(Exception):
    """Ошибка REST Bitrix24 (неверный вебхук, нет прав, неизвестное поле)."""


def fetch_leads(webhook, since, select_fields, timeout=60):
    """Возвращает список лидов, созданных начиная с даты since ('YYYY-MM-DD').

    select_fields — список полей, которые нужно вернуть (ID, DATE_CREATE,
    поле качества, поля-ключи связывания и т.д.).
    """
    method = webhook + "crm.lead.list"
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
