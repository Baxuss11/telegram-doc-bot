"""Собирает частотные словари data/ru.txt и data/en.txt.

Нужен только разработчику: сам переключатель читает готовые .txt файлы.
Запуск:  pip install wordfreq && python build_dictionaries.py
"""
import os
import re

from wordfreq import top_n_list

WORDS_PER_LANGUAGE = 40000
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

PATTERNS = {
    'ru': re.compile(r"^[а-яё]+(-[а-яё]+)*$"),
    'en': re.compile(r"^[a-z]+('[a-z]+)?(-[a-z]+)*$"),
}


def build(lang: str) -> None:
    pattern = PATTERNS[lang]
    words = []
    seen = set()
    # Берём с запасом: часть слов отсеется (цифры, латиница в ru и т.п.)
    for word in top_n_list(lang, WORDS_PER_LANGUAGE * 2):
        word = word.lower()
        if pattern.match(word) and word not in seen:
            seen.add(word)
            words.append(word)
        if len(words) == WORDS_PER_LANGUAGE:
            break

    path = os.path.join(DATA_DIR, f'{lang}.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(words) + '\n')
    print(f'{path}: {len(words)} слов')


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    for lang in PATTERNS:
        build(lang)
