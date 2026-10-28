"""Сведение расхода Meta с заявками Bitrix24 по единому ключу — имени объявления.

Ключ связывания — нормализованное имя объявления. В Meta это ad_name, в Bitrix —
utm_content (по ТЗ в объявлениях ставится utm_content={{ad.name}}) либо указанное
UF-поле. Здесь же считаем цену заявки и цену квалифицированного лида.
"""

import re
from collections import defaultdict


def normalize_key(value) -> str:
    """Приводим имя объявления к единому виду: нижний регистр, схлопнутые пробелы."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def lead_key(lead: dict, key_fields) -> str:
    """Берём первый непустой ключ из полей-кандидатов (по порядку)."""
    for field in key_fields:
        if lead.get(field):
            return normalize_key(lead[field])
    return ""


def is_qualified(lead: dict, field: str, values) -> bool:
    raw = lead.get(field)
    if raw is None:
        return False
    return str(raw).strip().upper() in values


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _i(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


NO_TAG = "(без метки)"


def build_report(insights, leads, key_fields, qualified_field, qualified_values):
    """Строит сводный отчёт по креативам.

    Возвращает dict с полями:
      rows            — список креативов (расход, клики, заявки, квал, CPL, CPQL);
      totals          — суммарные показатели;
      wasted          — креативы с расходом, но без заявок («только тратят деньги»);
      unmatched_leads — число заявок без ключа (нечем связать с креативом).
    """
    # --- агрегируем расход Meta по ключу ---
    meta_agg = {}
    for row in insights:
        key = normalize_key(row.get("ad_name"))
        agg = meta_agg.setdefault(
            key,
            {
                "ad_name": row.get("ad_name") or "",
                "campaign": row.get("campaign_name") or "",
                "adset": row.get("adset_name") or "",
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
            },
        )
        agg["spend"] += _f(row.get("spend"))
        agg["clicks"] += _i(row.get("clicks"))
        agg["impressions"] += _i(row.get("impressions"))

    # --- агрегируем заявки Bitrix по ключу ---
    leads_by_key = defaultdict(int)
    qual_by_key = defaultdict(int)
    unmatched_leads = 0
    for lead in leads:
        key = lead_key(lead, key_fields)
        if not key:
            unmatched_leads += 1
            key = NO_TAG
        leads_by_key[key] += 1
        if is_qualified(lead, qualified_field, qualified_values):
            qual_by_key[key] += 1

    # --- объединяем по всем ключам из обоих источников ---
    rows = []
    for key in set(meta_agg) | set(leads_by_key):
        info = meta_agg.get(key, {})
        spend = round(info.get("spend", 0.0), 2)
        leads_n = leads_by_key.get(key, 0)
        qual_n = qual_by_key.get(key, 0)
        rows.append(
            {
                "key": key or NO_TAG,
                "ad_name": info.get("ad_name") or (key or NO_TAG),
                "campaign": info.get("campaign", ""),
                "spend": spend,
                "clicks": info.get("clicks", 0),
                "leads": leads_n,
                "qualified": qual_n,
                "cost_per_lead": round(spend / leads_n, 2) if leads_n else None,
                "cost_per_qualified": round(spend / qual_n, 2) if qual_n else None,
                # matched — есть и расход, и заявки: экономика по креативу достоверна.
                "matched": spend > 0 and leads_n > 0,
            }
        )

    rows.sort(key=lambda r: r["spend"], reverse=True)

    totals = {
        "spend": round(sum(r["spend"] for r in rows), 2),
        "leads": sum(r["leads"] for r in rows),
        "qualified": sum(r["qualified"] for r in rows),
    }
    totals["cost_per_lead"] = (
        round(totals["spend"] / totals["leads"], 2) if totals["leads"] else None
    )
    totals["cost_per_qualified"] = (
        round(totals["spend"] / totals["qualified"], 2) if totals["qualified"] else None
    )

    # Креативы, которые тратят деньги без единой заявки.
    wasted = [r for r in rows if r["spend"] > 0 and r["leads"] == 0]

    return {
        "rows": rows,
        "totals": totals,
        "wasted": wasted,
        "unmatched_leads": unmatched_leads,
    }


def format_text(report: dict, since: str, until: str) -> str:
    """Человекочитаемый отчёт для терминала / Telegram."""
    lines = []
    lines.append(f"Период: {since} … {until}")
    t = report["totals"]
    cpl = "—" if t["cost_per_lead"] is None else f"{t['cost_per_lead']}"
    cpql = "—" if t["cost_per_qualified"] is None else f"{t['cost_per_qualified']}"
    lines.append(
        f"Итого: расход {t['spend']} · заявок {t['leads']} · квал {t['qualified']} "
        f"· цена заявки {cpl} · цена квал {cpql}"
    )
    lines.append("")

    header = f"{'Креатив':<32}{'Расход':>12}{'Клики':>8}{'Заявки':>8}{'Квал':>6}{'CPL':>10}{'CPQL':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in report["rows"]:
        name = (r["ad_name"] or r["key"])[:31]
        cpl = "—" if r["cost_per_lead"] is None else f"{r['cost_per_lead']}"
        cpql = "—" if r["cost_per_qualified"] is None else f"{r['cost_per_qualified']}"
        lines.append(
            f"{name:<32}{r['spend']:>12}{r['clicks']:>8}{r['leads']:>8}"
            f"{r['qualified']:>6}{cpl:>10}{cpql:>10}"
        )

    if report["wasted"]:
        lines.append("")
        lines.append("⚠ Тратят деньги без заявок:")
        for r in report["wasted"]:
            lines.append(f"  · {r['ad_name']}: расход {r['spend']}, заявок 0")

    if report["unmatched_leads"]:
        lines.append("")
        lines.append(
            f"⚠ Заявок без ключа связывания (нечем привязать к креативу): "
            f"{report['unmatched_leads']}. Проверьте UTM/поле имени объявления на лидах."
        )

    return "\n".join(lines)
