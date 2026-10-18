"""Чек-лист проверки и правила начисления баллов.

Чтобы поменять баллы, добавить пункт или новый раздел (например, юридическую
часть) — правьте только этот файл. Ключи (key) не меняйте у уже существующих
пунктов: по ним в базе хранятся ответы прошлых проверок.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Criterion:
    key: str
    title: str
    points: int


@dataclass(frozen=True)
class Item:
    key: str
    title: str
    group: str  # раздел: «Техническая часть», позже — «Юридическая часть»
    hint: str
    criteria: tuple[Criterion, ...] = field(default_factory=tuple)

    @property
    def max_points(self) -> int:
        return sum(c.points for c in self.criteria)


def _journal(prefix: str) -> tuple[Criterion, ...]:
    """Общие критерии для журналов (производства работ, техники безопасности)."""
    return (
        Criterion(f"{prefix}_fill", "Заполнение, ведение", 1),
        Criterion(f"{prefix}_lace", "Прошнурован", 1),
        Criterion(f"{prefix}_pages", "Листы пронумерованы", 1),
        Criterion(f"{prefix}_stamp", "Печать", 1),
        Criterion(f"{prefix}_sign", "Подпись", 1),
    )


def _presence_norms(prefix: str) -> tuple[Criterion, ...]:
    return (
        Criterion(f"{prefix}_has", "Наличие", 1),
        Criterion(f"{prefix}_norm", "Соответствие нормам и требованиям", 1),
    )


SIGNERS = "гендиректор, ПТО, технадзор"

TECH = "Техническая часть"

ITEMS: tuple[Item, ...] = (
    Item("ppr", "Проект производства работ (ППР)", TECH,
         "Есть ли ППР и достаточен ли он. Сфотографируйте титульный лист и лист согласований.",
         (Criterion("ppr_has", "Наличие, достаточный", 2),)),
    Item("jpr", "Журнал производства работ", TECH,
         "Фото: обложка, последняя заполненная страница, шнуровка с печатью.",
         _journal("jpr")),
    Item("jan", "Журнал авторского надзора", TECH,
         "Пока без баллов — только фиксация наличия.",
         (Criterion("jan_has", "Наличие", 0),)),
    Item("tb", "Журнал по технике безопасности", TECH,
         "Фото: обложка, последняя заполненная страница, шнуровка с печатью.",
         _journal("tb")),
    Item("lab_soil", "Лабораторные испытания — грунты", TECH,
         "Протоколы испытаний грунтов.",
         _presence_norms("lab_soil")),
    Item("lab_rebar", "Лабораторные испытания — арматура", TECH,
         "Протоколы испытаний арматуры.",
         _presence_norms("lab_rebar")),
    Item("lab_concrete", "Лабораторные испытания — бетон", TECH,
         "Протоколы испытаний бетона.",
         _presence_norms("lab_concrete")),
    Item("hidden", "Акты скрытых работ", TECH,
         "Общее фото актов и пример заполненного акта.",
         _presence_norms("hidden")),
    Item("geo", "Геодезия, исполнительная документация", TECH,
         "Исполнительные схемы геодезической съёмки.",
         (Criterion("geo_has", "Наличие", 1),)),
    Item("cert", "Сертификаты и качество материалов", TECH,
         "Сертификаты / паспорта качества на арматуру и бетон.",
         (Criterion("cert_rebar", "Арматура", 1),
          Criterion("cert_concrete", "Бетон", 1))),
    Item("stage_acc", "Акт поэтапной приёмки", TECH,
         "Есть документ — 2 балла, нет — 0.",
         (Criterion("stage_acc_has", "Наличие", 2),)),
    Item("genplan", "Генплан", TECH,
         "Висит ли на стене, утверждён ли БГА (Бишкекглавархитектура).",
         (Criterion("genplan_has", "Наличие", 1),
          Criterion("genplan_bga", "Утверждён БГА", 1),
          Criterion("genplan_sign", "Роспись и печать", 1))),
    Item("sgp", "Стройгенплан", TECH,
         "Висит ли на стене, есть ли утверждение и печать ГАИ, подписи.",
         (Criterion("sgp_has", "Наличие", 1),
          Criterion("sgp_gai", "Утверждение и печать ГАИ", 1),
          Criterion("sgp_sign", f"Утверждён: {SIGNERS}", 2))),
    Item("schedule", "График производства работ", TECH,
         "Укрупнённый — по месяцам/годам, детальный — по дням. Могут быть оба.",
         (Criterion("schedule_has", "Наличие", 1),
          Criterion("schedule_big", "Укрупнённый", 1),
          Criterion("schedule_detail", "Детальный (по дням)", 2),
          Criterion("schedule_sign", f"Утверждён: {SIGNERS}", 2))),
)

ITEMS_BY_KEY = {item.key: item for item in ITEMS}

MAX_POINTS = sum(item.max_points for item in ITEMS)

# Оценка по сумме баллов: (минимум баллов, название). Проверяется сверху вниз.
GRADES = (
    (30, "Хорошо"),
    (20, "Удовлетворительно"),
    (0, "Неудовлетворительно"),
)


def item_score(item: Item, checked: set[str]) -> int:
    return sum(c.points for c in item.criteria if c.key in checked)


def total_score(checked: set[str]) -> int:
    return sum(item_score(item, checked) for item in ITEMS)


def grade_for(score: int) -> str:
    for threshold, name in GRADES:
        if score >= threshold:
            return name
    return GRADES[-1][1]
