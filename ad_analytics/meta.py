"""Клиент Marketing API (Meta): расход по каждому объявлению.

Нужен только scope ads_read. Записи в кабинет не делаем.
"""

import json

import requests

GRAPH = "https://graph.facebook.com"

# Поля на уровне объявления, которых достаточно для экономики по креативу.
INSIGHT_FIELDS = "campaign_name,adset_name,ad_name,ad_id,spend,impressions,clicks"


class MetaError(Exception):
    """Ошибка Graph API (в т.ч. неверный токен или нет доступа к кабинету)."""


def fetch_ad_insights(token, api_version, account_id, since, until, timeout=60):
    """Возвращает список строк статистики по каждому объявлению за период.

    account_id — с префиксом act_. since/until — даты 'YYYY-MM-DD'.
    Проходит все страницы пагинации.
    """
    url = f"{GRAPH}/{api_version}/{account_id}/insights"
    params = {
        "level": "ad",
        "fields": INSIGHT_FIELDS,
        "time_range": json.dumps({"since": since, "until": until}),
        "limit": 500,
        "access_token": token,
    }

    rows = []
    while url:
        resp = requests.get(url, params=params, timeout=timeout)
        # Ссылка paging.next уже содержит все параметры, включая токен.
        params = None
        try:
            data = resp.json()
        except ValueError:
            raise MetaError(f"Meta вернул не-JSON (HTTP {resp.status_code})")

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise MetaError(err.get("message") or str(err))

        rows.extend(data.get("data", []))
        url = (data.get("paging") or {}).get("next")

    return rows
