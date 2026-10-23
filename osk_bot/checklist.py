"""Чек-листы проверки и правила начисления баллов.

Бот ведёт два независимых раздела:
- «tech»  — техническая часть: по пунктам галочки (складываются), итог из 38 баллов;
- «legal» — юридическая часть: на пункте выбирается ОДИН вариант, итог в процентах.

Чтобы поменять баллы, тексты или добавить пункт — правьте только этот файл.
Ключи (key) у уже существующих пунктов и вариантов НЕ меняйте: по ним в базе
хранятся ответы прошлых проверок.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Criterion:
    """Независимый критерий-галочка (в пункте можно отметить несколько)."""
    key: str
    title: str
    points: int


@dataclass(frozen=True)
class Option:
    """Взаимоисключающий вариант (в пункте выбирается только один)."""
    key: str
    title: str
    points: int


@dataclass(frozen=True)
class Item:
    key: str
    title: str
    group: str
    hint: str
    criteria: tuple[Criterion, ...] = field(default_factory=tuple)  # режим «галочки»
    options: tuple[Option, ...] = field(default_factory=tuple)      # режим «один вариант»

    @property
    def single(self) -> bool:
        return bool(self.options)

    @property
    def choices(self) -> tuple:
        return self.options if self.single else self.criteria

    @property
    def max_points(self) -> int:
        if self.single:
            return max((o.points for o in self.options), default=0)
        return sum(c.points for c in self.criteria)


@dataclass(frozen=True)
class Module:
    key: str          # tech / legal
    title: str
    emoji: str
    scoring: str      # points (из N баллов) | percent (из 100%)
    items: tuple[Item, ...]
    grades: tuple[tuple[int, str], ...]

    @property
    def items_by_key(self) -> dict[str, Item]:
        return {it.key: it for it in self.items}

    @property
    def max_points(self) -> int:
        return sum(it.max_points for it in self.items)

    def grade(self, score: int) -> str:
        value = self.percent(score) if self.scoring == "percent" else score
        for threshold, name in self.grades:
            if value >= threshold:
                return name
        return self.grades[-1][1]

    def percent(self, score: int) -> int:
        return round(100 * score / self.max_points) if self.max_points else 0


# =========================== Техническая часть ===========================

def _journal(prefix: str) -> tuple[Criterion, ...]:
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

TECH_ITEMS: tuple[Item, ...] = (
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
         "Протоколы испытаний грунтов.", _presence_norms("lab_soil")),
    Item("lab_rebar", "Лабораторные испытания — арматура", TECH,
         "Протоколы испытаний арматуры.", _presence_norms("lab_rebar")),
    Item("lab_concrete", "Лабораторные испытания — бетон", TECH,
         "Протоколы испытаний бетона.", _presence_norms("lab_concrete")),
    Item("hidden", "Акты скрытых работ", TECH,
         "Общее фото актов и пример заполненного акта.", _presence_norms("hidden")),
    Item("geo", "Геодезия, исполнительная документация", TECH,
         "Исполнительные схемы геодезической съёмки.",
         (Criterion("geo_has", "Наличие", 1),)),
    Item("cert", "Сертификаты и качество материалов", TECH,
         "Сертификаты / паспорта качества на арматуру и бетон.",
         (Criterion("cert_rebar", "Арматура", 1), Criterion("cert_concrete", "Бетон", 1))),
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

# =========================== Юридическая часть ===========================
# Источник: таблица LEGAL, лист «После августа 2025». На каждом пункте — один
# вариант. «Отсутствует» = не выбрано ни одного варианта (0 баллов).

LAND = "Земля и разрешения"
DOCS = "Договор и застройщик"
EXTRA = "Репутация и условия"
GOV = "Запросы в госструктуры"


def _opt(prefix: str, *pairs: tuple[str, str, int]) -> tuple[Option, ...]:
    return tuple(Option(f"{prefix}_{k}", t, p) for k, t, p in pairs)


LEGAL_ITEMS: tuple[Item, ...] = (
    Item("l_land", "Статус земельного участка", LAND,
         "Фото документа на землю. Собственность — 10, зелёная книга (аренда) — 3, синяя — 0.",
         options=_opt("l_land", ("own", "В собственности строй­компании", 10),
                      ("green", "Зелёная книга (аренда)", 3),
                      ("blue", "Синяя книга", 0))),
    Item("l_use", "Назначение земельного участка", LAND,
         "Соответствует ли объект строительства назначению участка (по брошюре/рекламе).",
         options=_opt("l_use", ("ok", "Соответствует объекту строительства", 10))),
    Item("l_registry", "Реестр строящихся объектов", LAND,
         "Внесён ли объект в реестр.",
         options=_opt("l_registry", ("own", "В собственности строй­компании", 10),
                      ("green", "Зелёная книга", 3), ("blue", "Синяя книга", 0))),
    Item("l_gz", "ГЗ (градостроительное заключение)", LAND,
         "Наличие ГЗ.", options=_opt("l_gz", ("has", "Наличие", 10))),
    Item("l_tu", "Технические условия (ТУ)", LAND,
         "Наличие ТУ по каждой сети (по 1 баллу).",
         (Criterion("l_tu_el", "Электричество", 1), Criterion("l_tu_water", "Водоснабжение", 1),
          Criterion("l_tu_sewer", "Канализация", 1), Criterion("l_tu_gas", "Газоснабжение", 1),
          Criterion("l_tu_heat", "Отопление", 1))),
    Item("l_comm", "Центральные коммуникации", LAND,
         "Подключение к центральным сетям (по 1 баллу).",
         (Criterion("l_comm_el", "Электричество", 1), Criterion("l_comm_water", "Водоснабжение", 1),
          Criterion("l_comm_sewer", "Канализация", 1), Criterion("l_comm_gas", "Газоснабжение", 1),
          Criterion("l_comm_heat", "Отопление", 1))),
    Item("l_goszakl", "Госзаключение / разрешение на строительство", LAND,
         "Запрос в Бишкекглавархитектуру. 2 этап — 10, 1 этап — 5, существенные замечания — 0.",
         options=_opt("l_goszakl", ("stage2", "2 этап", 10), ("stage1", "1 этап", 5))),
    Item("l_license", "Лицензия", LAND,
         "Запрос в Госстрой. Своя и соответствует уровню — 10, подрядчика / не соответствует — 0.",
         options=_opt("l_license", ("own", "Своя, соответствует уровню", 10),
                      ("contractor", "Подрядчика / не соответствует", 0))),
    Item("l_ddu_type", "Тип договора", DOCS,
         "Выберите, по какому договору продают.",
         options=_opt("l_ddu_type", ("ddu", "Договор долевого участия (ДДУ)", 5),
                      ("hybrid", "Гибридный ПДКП + перезаключение ДДУ", 3),
                      ("pkp", "Предв. договор купли-продажи", 0))),
    Item("l_ddu", "Условия договора", DOCS,
         "Отметьте, что есть в договоре.",
         (Criterion("l_ddu_deadline", "Срок сдачи прописан в договоре", 5),
          Criterion("l_ddu_pso", "Сроки ПСО", 1),
          Criterion("l_ddu_liability", "Ответственность застройщика (штрафы/пеня)", 1),
          Criterion("l_ddu_signer", "Подписывает гендиректор (не по доверенности)", 2),
          Criterion("l_ddu_powers", "Проверены полномочия подписанта", 1),
          Criterion("l_ddu_chess", "Шахматка этажа и квартиры", 1),
          Criterion("l_ddu_works", "Полный перечень работ застройщика", 1))),
    Item("l_extract", "Выписка", DOCS, "Взяли выписку.",
         (Criterion("l_extract_taken", "Выписка взята", 1),)),
    Item("l_history", "История прав на недвижимость (аресты)", DOCS,
         "Нет арестов — 10, были, но сейчас сняты — 5, есть арест — 0.",
         options=_opt("l_history", ("clean", "Арестов нет", 10),
                      ("cleared", "Были, сейчас сняты", 5), ("arrest", "Есть арест", 0))),
    Item("l_consent_changes", "Изменения в договор по запросу дольщика", DOCS,
         "Готовы ли вносить изменения.",
         options=_opt("l_consent_changes", ("add", "Вносят изменения", 10),
                      ("partial", "Частично", 5))),
    Item("l_consent_notary", "Регистрация и нотариальное заверение", DOCS,
         "Предупреждают ли о нотариальном заверении.",
         options=_opt("l_consent_notary", ("warned", "Предупреждают", 10))),
    Item("l_consent_currency", "Валюта оплаты", DOCS,
         "В чём фиксируется цена.",
         options=_opt("l_consent_currency", ("som", "Фиксированная в сомах", 10),
                      ("usd", "В долларах", 5))),
    Item("l_deadlines", "Соблюдение сроков сдачи (прошлые объекты)", EXTRA,
         "По завершённым объектам застройщика.",
         options=_opt("l_deadlines", ("ontime", "Вовремя (±3 месяца)", 10),
                      ("late3", "Задержка более 3 месяцев", 5))),
    Item("l_ads", "Соответствие рекламы реальности", EXTRA,
         "Рекламные материалы совпадают с реальным положением дел.",
         options=_opt("l_ads", ("ok", "Соответствует", 10))),
    Item("l_site", "Свой сайт с финансовой отчётностью", EXTRA,
         "Наличие сайта с фин. отчётностью.",
         options=_opt("l_site", ("yes", "Есть", 10))),
    Item("l_agree", "Договорённости по отделке (осведомлён)", EXTRA,
         "Что оговорено по договору.",
         (Criterion("l_agree_doors", "Двери", 1), Criterion("l_agree_facade", "Облицовка", 1),
          Criterion("l_agree_windows", "Окна", 1), Criterion("l_agree_lift", "Фирма лифта", 1),
          Criterion("l_agree_flex", "Гибкие варианты приобретения", 1))),
    Item("l_contract", "Договор на руках", EXTRA, "Взяли копию договора с собой.",
         (Criterion("l_contract_taken", "Договор взят", 1),)),
    Item("l_gov", "Запросы в госструктуры", GOV,
         "Отдельный блок: запросы по разрешениям и сетям (по 1 баллу, БГА — 5).",
         (Criterion("l_gov_check", "Проверка на наличие разрешения", 1),
          Criterion("l_gov_gosreg", "Госрегистр (ID на землю)", 1),
          Criterion("l_gov_vodokanal", "Водоканал", 1),
          Criterion("l_gov_severelektro", "Северэлектро", 1),
          Criterion("l_gov_gazprom", "Газпром", 1),
          Criterion("l_gov_teploset", "Теплосеть", 1),
          Criterion("l_gov_goszakl", "Госзаключение", 1),
          Criterion("l_gov_bga", "Бишкекглавархитектура", 5))),
)

# =============================== Модули ===============================

TECH_MODULE = Module(
    "tech", "Техническая часть", "🔧", "points", TECH_ITEMS,
    grades=((30, "Хорошо"), (20, "Удовлетворительно"), (0, "Неудовлетворительно")),
)
LEGAL_MODULE = Module(
    "legal", "Юридическая часть", "⚖️", "percent", LEGAL_ITEMS,
    grades=((75, "Хорошо"), (50, "Удовлетворительно"), (0, "Неудовлетворительно")),
)

MODULES = {m.key: m for m in (TECH_MODULE, LEGAL_MODULE)}
DEFAULT_KIND = "tech"


def module_for(kind: str) -> Module:
    return MODULES.get(kind, TECH_MODULE)


def item_score(item: Item, checked: set[str]) -> int:
    if item.single:
        return sum(o.points for o in item.options if o.key in checked)
    return sum(c.points for c in item.criteria if c.key in checked)


def module_total(module: Module, checked: set[str]) -> int:
    return sum(item_score(it, checked) for it in module.items)
