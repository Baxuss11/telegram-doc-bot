"""Конфигурация из переменных окружения.

Все ключи берём из окружения, чтобы бессрочный токен и вебхук жили в секретах
сервиса, а не в переписке. Ни одно значение здесь не логируется.
"""

import os


class ConfigError(Exception):
    """Поднимается, когда обязательная переменная окружения не задана."""


def _require(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ConfigError(f"Не задана переменная окружения {name}")
    return value


def _normalize_account(raw: str) -> str:
    """Marketing API требует префикс act_. Принимаем ID и с ним, и без."""
    acc = raw.strip()
    if not acc:
        return ""
    return acc if acc.startswith("act_") else f"act_{acc}"


def _split(raw: str) -> list:
    return [x.strip() for x in raw.split(",") if x.strip()]


class Config:
    """Собранная конфигурация интеграции.

    Обязательные переменные:
      META_ACCESS_TOKEN     — токен Meta с правами ads_read.
      META_AD_ACCOUNT_IDS   — ID кабинетов через запятую (с act_ или без).
      BITRIX_WEBHOOK_URL    — входящий вебхук, напр. https://portal.bitrix24.ru/rest/1/xxxx/

    Необязательные (с разумными значениями по умолчанию):
      META_API_VERSION      — версия Graph API (по умолчанию v21.0).
      BITRIX_QUALIFIED_FIELD — поле лида, где хранится качество (по умолчанию STATUS_ID).
      BITRIX_QUALIFIED_VALUES — значения «квалифицирован» через запятую (по умолчанию CONVERTED).
      BITRIX_AD_KEY_FIELDS  — поля-кандидаты для ключа связывания с объявлением
                              (по умолчанию UTM_CONTENT; можно добавить своё UF-поле).
    """

    def __init__(self, env: dict | None = None):
        if env is not None:
            # Для тестов: временно подменяем окружение.
            old = dict(os.environ)
            os.environ.update(env)
            try:
                self._load()
            finally:
                os.environ.clear()
                os.environ.update(old)
        else:
            self._load()

    def _load(self) -> None:
        self.meta_token = _require("META_ACCESS_TOKEN")
        self.ad_account_ids = [
            _normalize_account(a) for a in _split(_require("META_AD_ACCOUNT_IDS"))
        ]
        self.api_version = (os.environ.get("META_API_VERSION") or "v21.0").strip()

        self.bitrix_webhook = _require("BITRIX_WEBHOOK_URL").rstrip("/") + "/"

        # Сущность CRM: deal (Сделки) или lead (Лиды). В ОСК работают через Сделки.
        self.entity = (os.environ.get("BITRIX_ENTITY") or "deal").strip().lower()
        if self.entity not in ("deal", "lead"):
            raise ConfigError("BITRIX_ENTITY должно быть deal или lead")

        # По умолчанию «квалифицирован» = выигранная сделка (STAGE_SEMANTIC_ID = S),
        # что не зависит от конкретной воронки. Для лидов — стадия CONVERTED.
        if self.entity == "deal":
            default_field, default_values = "STAGE_SEMANTIC_ID", "S"
        else:
            default_field, default_values = "STATUS_ID", "CONVERTED"

        self.qualified_field = (
            os.environ.get("BITRIX_QUALIFIED_FIELD") or default_field
        ).strip()
        self.qualified_values = {
            v.upper()
            for v in _split(os.environ.get("BITRIX_QUALIFIED_VALUES") or default_values)
        }
        self.lead_key_fields = _split(
            os.environ.get("BITRIX_AD_KEY_FIELDS") or "UTM_CONTENT"
        )
