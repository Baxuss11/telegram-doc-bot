"""Живой тест переключателя на macOS (для CI): печатает в TextEdit как настоящая клавиатура.

Запуск из папки layout_switcher:  python ../.github/scripts/mac_e2e.py
"""
import os
import subprocess
import sys
import tempfile
import time

import Quartz

sys.path.insert(0, os.getcwd())
from macos import InputSources  # noqa: E402

KEYS = {'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7, 'c': 8, 'v': 9, 'b': 11,
        'e': 14, 't': 17, 'o': 31, 'l': 37, 'j': 38, 'n': 45, ' ': 49}
SHIFT, RIGHT_ARROW = 56, 124
SOURCE = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)


def refresh():
    """Даём процессу узнать о смене раскладки (macOS сообщает о ней через цикл событий)."""
    Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.3, False)


def key(code, flags=0):
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(SOURCE, code, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.02)


def shift_tap():
    for flags in (Quartz.kCGEventFlagMaskShift, 0):
        event = Quartz.CGEventCreateKeyboardEvent(SOURCE, SHIFT, bool(flags))
        Quartz.CGEventSetType(event, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.05)


def type_keys(text):
    refresh()
    for char in text:
        key(KEYS[char])
        time.sleep(0.15)
    time.sleep(0.5)


def editor_text():
    key(KEYS['a'], Quartz.kCGEventFlagMaskCommand)
    key(KEYS['c'], Quartz.kCGEventFlagMaskCommand)
    key(RIGHT_ARROW)  # снять выделение, курсор в конец
    time.sleep(0.5)
    return subprocess.run(['pbpaste'], capture_output=True, text=True).stdout


def main():
    for setting in ('NSAutomaticSpellingCorrectionEnabled', 'NSAutomaticCapitalizationEnabled',
                    'NSAutomaticTextCompletionEnabled'):
        subprocess.run(['defaults', 'write', '-g', setting, '-bool', 'false'])
    sources = InputSources()
    sources.enable('com.apple.keylayout.Russian')
    sources.select('en')

    path = os.path.join(tempfile.mkdtemp(), 'test.txt')
    open(path, 'w').close()
    subprocess.run(['open', '-a', 'TextEdit', path], check=True)
    time.sleep(4)

    log = open('switcher.log', 'w')
    switcher = subprocess.Popen([sys.executable, '-u', 'switcher.py'], stdout=log, stderr=subprocess.STDOUT)
    time.sleep(4)
    subprocess.run(['open', '-a', 'TextEdit'])
    time.sleep(1)

    checks = []
    # Английская раскладка, а печатаем русское слово
    type_keys('ghbdtn ')
    checks.append(('ghbdtn → привет', editor_text(), 'привет '))
    refresh()
    checks.append(('раскладка стала русской', sources.current_id(), 'com.apple.keylayout.Russian'))
    # Теперь русская раскладка, а печатаем английское (клавиши h e l l o дают «руддщ»)
    type_keys('hello ')
    checks.append(('руддщ → hello', editor_text(), 'привет hello '))
    refresh()
    checks.append(('раскладка стала английской', sources.current_id(), 'com.apple.keylayout.US'))
    type_keys('z ljvf ')
    checks.append(('z ljvf → я дома', editor_text(), 'привет hello я дома '))
    # Двойной Shift отменяет последнюю замену
    type_keys('hello ')
    shift_tap()
    shift_tap()
    time.sleep(1)
    checks.append(('двойной Shift отменяет замену', editor_text(), 'привет hello я дома руддщ '))

    switcher.terminate()
    log.close()
    print(open('switcher.log').read())
    failed = 0
    for name, actual, expected in checks:
        passed = actual == expected
        failed += not passed
        print(('✅ ' if passed else '❌ ') + f'{name}: {actual!r}' + ('' if passed else f' (ожидалось {expected!r})'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
