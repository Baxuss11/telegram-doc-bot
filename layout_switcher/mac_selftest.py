"""Проверка переключателя на Mac: права, раскладки, переключение.

Запуск:  python3 mac_selftest.py
"""
import sys

import detector
from detector import EN, RU
from macos import InputSources, check_permissions, key_tables

ok = True


def report(passed: bool, text: str):
    global ok
    ok &= passed
    print(('✅ ' if passed else '❌ ') + text)


def main():
    sources = InputSources()
    if '--enable-russian' in sys.argv:  # для автотестов на чистом Mac
        sources.enable('com.apple.keylayout.Russian')
    print('Включённые раскладки:', ', '.join(sources.enabled_ids()))

    for lang, name in ((RU, 'русская'), (EN, 'английская')):
        report(sources.available(lang), f'{name} раскладка включена')

    if sources.available(RU) and sources.available(EN):
        en_to_ru, ru_to_en = key_tables(sources)
        word = ''.join(en_to_ru.get(ch, '?') for ch in 'ghbdtn')
        report(word == 'привет', f'клавиши ghbdtn в русской раскладке дают «{word}»')
        letters = 'qwertyuiop[]asdfghjkl;\'zxcvbnm,.'
        differences = [f'{ch}→{en_to_ru.get(ch)} (в Windows {detector.EN_TO_RU[ch]})'
                       for ch in letters if en_to_ru.get(ch) != detector.EN_TO_RU[ch]]
        print('   Отличия от Windows-раскладки:', ', '.join(differences) or 'нет')

        for lang in (RU, EN, RU):
            sources.select(lang)
            current = sources.current_id()
            expected = sources.RUSSIAN if lang == RU else sources.ENGLISH
            report(current in expected, f'переключение на {lang}: сейчас {current}')

    has_rights = check_permissions(prompt=False)
    text = 'есть права «Универсальный доступ» и «Мониторинг ввода» у приложения, из которого запущен тест'
    if '--enable-russian' in sys.argv:
        print(('✅ ' if has_rights else 'ℹ️  нет: ') + text)  # на CI прав нет, это нормально
    else:
        report(has_rights, text)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
