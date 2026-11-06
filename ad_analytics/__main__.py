"""CLI: собрать и вывести сводный отчёт «расход vs заявки по креативам».

Примеры:
  python -m ad_analytics --days 30
  python -m ad_analytics --since 2026-10-01 --until 2026-10-27 --format json

Все секреты — из окружения / файла .env (см. ad_analytics/README.md и .env.example).
"""

import argparse
import json
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .bitrix import BitrixError
from .config import ConfigError
from .meta import MetaError
from .report import format_text
from .service import generate_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ad_analytics",
        description="Сводит расход Meta с заявками Bitrix24 по каждому креативу.",
    )
    parser.add_argument("--days", type=int, default=30, help="период, дней назад от сегодня (по умолчанию 30)")
    parser.add_argument("--since", help="начало периода YYYY-MM-DD (переопределяет --days)")
    parser.add_argument("--until", help="конец периода YYYY-MM-DD")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    try:
        report, since, until = generate_report(args.since, args.until, args.days)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    except MetaError as exc:
        print(f"Ошибка Meta: {exc}", file=sys.stderr)
        return 3
    except BitrixError as exc:
        print(f"Ошибка Bitrix24: {exc}", file=sys.stderr)
        return 4

    if args.format == "json":
        print(json.dumps({"since": since, "until": until, **report}, ensure_ascii=False, indent=2))
    else:
        print(format_text(report, since, until))

    return 0


if __name__ == "__main__":
    sys.exit(main())
