"""Определяет, набрано ли слово в неправильной раскладке (RU <-> EN).

Модуль не зависит от клавиатурных библиотек, поэтому его легко тестировать.
"""
import math
import os
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# Клавиши в одном и том же порядке на раскладках QWERTY и ЙЦУКЕН
_EN_KEYS = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./" '~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>?' '@#$^&'
_RU_KEYS = "ёйцукенгшщзхъфывапролджэячсмитьбю." 'ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,' '"№;:?'

EN_TO_RU = dict(zip(_EN_KEYS, _RU_KEYS))
RU_TO_EN = dict(zip(_RU_KEYS, _EN_KEYS))

RU, EN = 'ru', 'en'
ALPHABETS = {
    RU: set('абвгдеёжзийклмнопрстуфхцчшщъыьэюя'),
    EN: set('abcdefghijklmnopqrstuvwxyz'),
}
# Символы, которые могут встречаться внутри слова (don't, кто-то)
_INNER = {RU: set('-'), EN: set("-'")}

# Насколько вероятнее (в среднем на букву, в натуральных логарифмах)
# должен быть перевод, чтобы заменить слово без подтверждения словарём
NGRAM_THRESHOLD = 1.1
MIN_NGRAM_LENGTH = 4
# Если слово есть в обоих словарях («рук» / «her»), меняем, только когда перевод
# встречается во столько раз чаще
AMBIGUOUS_RATIO = 30
# Двухбуквенные слова меняем, только если перевод — очень частое слово (однобуквенные — никогда)
SHORT_WORD_MAX_RANK = 3000
# Короткое слово не из словаря, похожее на свой язык, не трогаем ('лун' -> 'key')
OOV_MIN_GAIN = -0.5

# Знаки, обычные по краям английского слова: ("quote"), end. Остальные — скорее буквы
_NATURAL_PREFIX = set('("\'')
_NATURAL_SUFFIX = set('.,;:!?)"\'')


def set_key_tables(en_to_ru: dict, ru_to_en: dict) -> None:
    """Заменяет таблицы перевода (на Mac раскладки отличаются от Windows)."""
    if not all(ch in en_to_ru for ch in 'qwertyuiopasdfghjklzxcvbnm'):
        return  # что-то не так с раскладками — остаёмся на стандартных таблицах
    EN_TO_RU.clear()
    EN_TO_RU.update(en_to_ru)
    RU_TO_EN.clear()
    RU_TO_EN.update(ru_to_en)


def convert(text: str, source: str) -> str:
    """Перепечатывает текст так, будто те же клавиши нажаты в другой раскладке."""
    table = EN_TO_RU if source == EN else RU_TO_EN
    return ''.join(table.get(ch, ch) for ch in text)


def detect_layout(text: str):
    """Возвращает раскладку, в которой набран текст, или None, если непонятно."""
    has_ru = any(ch.lower() in ALPHABETS[RU] for ch in text)
    has_en = any(ch.lower() in ALPHABETS[EN] for ch in text)
    if has_ru == has_en:
        return None
    return RU if has_ru else EN


def split_punctuation(word: str, lang: str):
    """Отделяет знаки препинания по краям слова: '(привет!)' -> '(', 'привет', '!)'."""
    letters = ALPHABETS[lang]
    start, end = 0, len(word)
    while start < end and word[start].lower() not in letters:
        start += 1
    while end > start and word[end - 1].lower() not in letters:
        end -= 1
    return word[:start], word[start:end], word[end:]


class LanguageModel:
    """Словарь частых слов + триграммная модель букв для одного языка."""

    def __init__(self, lang: str, words):
        self.lang = lang
        self.ranks = {}
        self._trigrams = defaultdict(float)
        self._bigrams = defaultdict(float)
        alphabet_size = len(ALPHABETS[lang]) + len(_INNER[lang]) + 1

        for rank, word in enumerate(words, start=1):
            self.ranks.setdefault(word, rank)
            # Частым словам — больший вес, но без перекоса в сторону «the» и «и»
            weight = 1.0 / math.log(rank + 2)
            padded = f'^^{word}$'
            for i in range(len(padded) - 2):
                self._trigrams[padded[i:i + 3]] += weight
                self._bigrams[padded[i:i + 2]] += weight

        self._smoothing = 0.01
        self._vocabulary = alphabet_size

    @classmethod
    def load(cls, lang: str) -> 'LanguageModel':
        path = os.path.join(DATA_DIR, f'{lang}.txt')
        with open(path, encoding='utf-8') as f:
            words = [line.strip() for line in f if line.strip()]
        return cls(lang, words)

    def rank(self, word: str):
        """Место слова в частотном словаре (1 — самое частое) или None."""
        return self.ranks.get(word.lower())

    def score(self, word: str) -> float:
        """Средний логарифм вероятности буквы. Чем выше, тем «роднее» слово языку."""
        allowed = ALPHABETS[self.lang] | _INNER[self.lang]
        padded = f'^^{word.lower()}$'
        total = 0.0
        for i in range(len(padded) - 2):
            if padded[i + 2] != '$' and padded[i + 2] not in allowed:
                total += -15.0  # чужой символ внутри слова
                continue
            tri = self._trigrams.get(padded[i:i + 3], 0.0)
            bi = self._bigrams.get(padded[i:i + 2], 0.0)
            total += math.log((tri + self._smoothing) / (bi + self._smoothing * self._vocabulary))
        return total / (len(padded) - 2)


EXCEPTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'exceptions.txt')


def load_exceptions(path: str = EXCEPTIONS_PATH) -> set:
    """Слова, которые никогда не нужно переводить (по одному в строке, # — комментарий)."""
    if not os.path.exists(path):
        return set()
    with open(path, encoding='utf-8') as f:
        return {line.strip().lower() for line in f if line.strip() and not line.startswith('#')}


class Detector:
    def __init__(self, models=None, exceptions=None):
        if models is None:
            models = {RU: LanguageModel.load(RU), EN: LanguageModel.load(EN)}
        self.models = models
        self.exceptions = load_exceptions() if exceptions is None else set(exceptions)

    def fix(self, word: str):
        """Возвращает исправленное слово, если оно набрано не в той раскладке, иначе None."""
        source = detect_layout(word)
        if source is None or any(ch.isdigit() for ch in word):
            return None
        target = RU if source == EN else EN
        typed_model, target_model = self.models[source], self.models[target]

        prefix, core, suffix = split_punctuation(word, source)
        if not _is_plain_case(core) or core.lower() in self.exceptions:
            return None
        typed_rank = typed_model.rank(core)

        # Целиком: ',eltv' -> 'будем' (запятая на этой клавише — буква «б»).
        # Но 'ещё' -> 'to`' не годится: буква «ё» не может стать знаком препинания.
        converted = convert(word, source)
        candidates = []
        # ';t' — не английское 't' с точкой с запятой впереди, а «же»
        unusual_edges = bool(set(prefix) - _NATURAL_PREFIX) or bool(set(suffix) - _NATURAL_SUFFIX)
        conv_prefix, conv_core, conv_suffix = split_punctuation(converted, target)
        if _only_letters(converted, target) and len(conv_prefix) <= len(prefix) \
                and len(conv_suffix) <= len(suffix):
            # 'j,' — не английское 'j' с запятой, а «об»
            ignore_typed = unusual_edges or (len(core) < 2 and len(conv_core) >= 2)
            candidates.append((converted, conv_core, None if ignore_typed else typed_rank))
        # Обычные знаки по краям оставляем как есть: 'ghbdtn,' -> 'привет,'
        if (prefix or suffix) and not unusual_edges:
            converted_core = convert(core, source)
            candidates.append((prefix + converted_core + suffix, converted_core, typed_rank))

        for result, result_core, rank in candidates:
            target_rank = target_model.rank(result_core)
            if target_rank is None or not _dictionary_prefers(len(result_core), rank, target_rank):
                continue
            # 'лун' нет в словаре, но это явно русское слово — не превращаем его в 'key'
            if rank is None and core and len(result_core) <= 4 and \
                    target_model.score(result_core) - typed_model.score(core) < OOV_MIN_GAIN:
                continue
            return result

        if typed_rank is not None or not _only_letters(converted, target):
            return None
        converted_core = split_punctuation(converted, target)[1]
        if len(converted_core) < MIN_NGRAM_LENGTH:
            return None
        gain = target_model.score(converted_core) - typed_model.score(core)
        if gain > NGRAM_THRESHOLD:
            return converted
        return None

    def fix_previous(self, word: str, source: str):
        """Однобуквенное слово перед исправленным: 'z ljvf' -> 'я дома'.

        Само по себе 'z' трогать опасно, но если следующее слово оказалось
        набрано не в той раскладке, то и 'z' почти наверняка тоже.
        """
        if detect_layout(word) != source:
            return None
        target = RU if source == EN else EN
        prefix, core, suffix = split_punctuation(word, source)
        if not 1 <= len(core) <= 2 or not _is_plain_case(core):
            return None
        converted = convert(core, source)
        target_rank = self.models[target].rank(converted)
        if target_rank is None:
            return None
        typed_rank = self.models[source].rank(core)
        if typed_rank is None or target_rank < typed_rank:
            return prefix + converted + suffix
        return None


def _dictionary_prefers(length: int, typed_rank, target_rank) -> bool:
    if length < 2:
        return False
    if length == 2 and target_rank > SHORT_WORD_MAX_RANK:
        return False
    if typed_rank is None:
        return True
    return target_rank * AMBIGUOUS_RATIO < typed_rank


def _only_letters(word: str, lang: str) -> bool:
    _, core, _ = split_punctuation(word, lang)
    allowed = ALPHABETS[lang] | _INNER[lang]
    return bool(core) and all(ch.lower() in allowed for ch in core)


def _is_plain_case(word: str) -> bool:
    """«слово», «Слово» или «СЛОВО» — но не «сЛОВО» (это скорее Caps Lock или код)."""
    letters = [ch for ch in word if ch.isalpha()]
    if not letters:
        return False
    rest = letters[1:]
    return all(ch.islower() for ch in rest) or all(ch.isupper() for ch in letters)
