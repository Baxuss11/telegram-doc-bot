"""Единая точка сборки отчёта — используется и CLI, и Telegram-ботом.

Загружает конфиг из окружения, тянет расход Meta и заявки Bitrix24 и возвращает
готовый отчёт. Сетевые вызовы синхронные (requests), поэтому в async-коде бота
вызывать через asyncio.to_thread.
"""

import datetime

from .bitrix import fetch_items
from .config import Config
from .meta import fetch_ad_insights
from .report import build_report


def _default_dates(days: int):
    until = datetime.date.today()
    since = until - datetime.timedelta(days=days)
    return since.isoformat(), until.isoformat()


def generate_report(since: str | None = None, until: str | None = None, days: int = 30):
    """Собирает и возвращает (report_dict, since, until).

    Если since/until не заданы — берётся период последних `days` дней.
    Бросает ConfigError / MetaError / BitrixError при проблемах — их ловит
    вызывающая сторона и показывает понятный текст.
    """
    cfg = Config()

    if not (since and until):
        since, until = _default_dates(days)

    insights = []
    for account_id in cfg.ad_account_ids:
        insights += fetch_ad_insights(
            cfg.meta_token, cfg.api_version, account_id, since, until
        )

    select = ["ID", "TITLE", "DATE_CREATE", cfg.qualified_field] + cfg.lead_key_fields
    items = fetch_items(cfg.bitrix_webhook, cfg.entity, since, select)

    report = build_report(
        insights, items, cfg.lead_key_fields, cfg.qualified_field, cfg.qualified_values
    )
    return report, since, until
