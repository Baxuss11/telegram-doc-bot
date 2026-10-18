"""Автопереключатель раскладки RU <-> EN (как Punto Switcher).

Набрали «ghbdtn» и нажали пробел — слово заменится на «привет», а раскладка
переключится на русскую. И наоборот: «руддщ» -> «hello» + английская раскладка.

Горячие клавиши:
    Pause        — отменить последнюю замену или перевести слово вручную
    Shift+Pause  — выключить / включить автопереключение
    Ctrl+C в окне консоли — выход
"""
import platform
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from detector import EN, RU, Detector, convert, detect_layout, split_punctuation


@dataclass
class Replacement:
    erase: int              # сколько символов стереть перед курсором
    text: str               # что напечатать вместо них
    layout: Optional[str]   # на какую раскладку переключиться


class Engine:
    """Решает, что перепечатать. Ничего не знает о клавиатуре — так проще тестировать."""

    def __init__(self, detector: Detector):
        self.detector = detector
        self.enabled = True
        self._prev_word = None      # предыдущее слово, если после него был ровно один пробел
        self._last_change = None    # (было, стало, раскладка до замены) — для отмены

    def reset(self):
        """Курсор ушёл в другое место (Enter, стрелки, клик мышью)."""
        self._prev_word = None
        self._last_change = None

    def on_word(self, word: str, adjacent: bool, check: bool = True) -> Optional[Replacement]:
        """Слово набрано и после него нажат пробел. check=False — слово уже переведено вручную."""
        prev, self._prev_word = (self._prev_word if adjacent else None), word
        self._last_change = None
        if not self.enabled or not check:
            return None

        fixed = self.detector.fix(word)
        if fixed is None:
            return None
        source = detect_layout(word)
        target = RU if source == EN else EN
        self._prev_word = fixed

        before, after = word + ' ', fixed + ' '
        prev_fixed = self.detector.fix_previous(prev, source) if prev else None
        if prev_fixed:
            before, after = f'{prev} {before}', f'{prev_fixed} {after}'
        self._last_change = (before, after, source)
        return Replacement(len(before), after, target)

    def on_manual(self, word: str) -> Optional[Replacement]:
        """Нажата Pause. word — недонабранное слово перед курсором (может быть пустым)."""
        if word:
            source = detect_layout(word)
            if source is None:
                return None
            self._last_change = None
            return Replacement(len(word), convert(word, source), RU if source == EN else EN)

        if self._last_change:
            # Пользователь отменил замену: запомним слово, чтобы больше его не трогать
            before, after, source = self._last_change
            typed = before.split()[-1]
            self.detector.exceptions.add(split_punctuation(typed, source)[1].lower())
            self.reset()
            return Replacement(len(after), before, source)

        if self._prev_word:
            # Автозамена не сработала — переводим последнее слово вручную
            word, self._prev_word = self._prev_word, None
            source = detect_layout(word)
            if source is None:
                return None
            fixed = convert(word, source)
            self._last_change = (word + ' ', fixed + ' ', source)
            return Replacement(len(word) + 1, fixed + ' ', RU if source == EN else EN)
        return None

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        self.reset()
        return self.enabled


# --- Переключение системной раскладки ---

def switch_layout(lang: str) -> None:
    system = platform.system()
    if system == 'Windows':
        _switch_layout_windows(lang)
    elif system == 'Linux' and shutil.which('xkb-switch'):
        subprocess.run(['xkb-switch', '-s', 'ru' if lang == RU else 'us'], check=False)
    # На остальных системах слово всё равно исправляется, просто раскладка не меняется


def _switch_layout_windows(lang: str) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetKeyboardLayoutList.argtypes = (ctypes.c_int, ctypes.POINTER(wintypes.HKL))
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.GetForegroundWindow.restype = wintypes.HWND

    wm_inputlangchangerequest = 0x0050
    primary_language = 0x19 if lang == RU else 0x09  # LANG_RUSSIAN / LANG_ENGLISH

    count = user32.GetKeyboardLayoutList(0, None)
    layouts = (wintypes.HKL * count)()
    user32.GetKeyboardLayoutList(count, layouts)
    for hkl in layouts:
        if hkl and (hkl & 0x3FF) == primary_language:
            user32.PostMessageW(user32.GetForegroundWindow(), wm_inputlangchangerequest, 0,
                                ctypes.c_ssize_t(hkl).value)
            return
    print(f'⚠️  В системе не установлена {"русская" if lang == RU else "английская"} раскладка')


# --- Перехват клавиатуры ---

XK_BREAK = 0xFF6B


class KeyboardHook:
    def __init__(self, engine: Engine):
        from pynput import keyboard, mouse

        self.keyboard = keyboard
        self.mouse = mouse
        self.engine = engine
        self.controller = keyboard.Controller()
        self.tasks = queue.Queue()

        self.word = []              # символы текущего слова
        self.adjacent = False       # перед словом был ровно один пробел после другого слова
        self.after_space = False
        self.manual = False         # текущее слово уже переведено по Pause — не проверять
        self.pressed_modifiers = set()
        self.busy = False           # идёт перепечатка — свои нажатия не считаем

        self._modifiers = {
            keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
            keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr,
            keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r,
        }
        self._shift = {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}
        self._ignored = self._shift | {keyboard.Key.caps_lock}

    # Вызывается в потоке перехвата: только запоминаем, тяжёлую работу делает worker
    def on_press(self, key, injected=False):
        if injected or self.busy:
            return
        Key = self.keyboard.Key

        if key in self._modifiers:
            self.pressed_modifiers.add(key)
            return
        if key in self._ignored:
            if key in self._shift:
                self.pressed_modifiers.add(key)
            return

        # В X11 Shift+Pause приходит как отдельная клавиша Break
        if key == getattr(Key, 'pause', None) or getattr(key, 'vk', None) == XK_BREAK:
            if self.pressed_modifiers & self._shift:
                self.tasks.put(('toggle',))
            else:
                word = ''.join(self.word)
                self.tasks.put(('manual', word))
                source = detect_layout(word)
                if source:
                    self.word = list(convert(word, source))
                    self.manual = True
            return

        if self.pressed_modifiers - self._shift:
            # Ctrl+C, Alt+Tab и т.п. — курсор мог уехать
            self._reset()
            return

        if key == Key.space:
            if self.word:
                self.tasks.put(('word', ''.join(self.word), self.adjacent, not self.manual))
                self.word = []
                self.manual = False
                self.adjacent = self.after_space = True
            else:
                self._reset()  # два пробела подряд — новое предложение/фрагмент
            return

        if key == Key.backspace:
            if self.word:
                self.word.pop()
            else:
                self._reset()
            return

        char = getattr(key, 'char', None)
        if char and char.isprintable():
            if not self.word and not self.after_space:
                self.adjacent = False
            self.after_space = False
            self.word.append(char)
            return

        # Enter, Tab, стрелки, Home, Delete, F1...
        self._reset()

    def on_release(self, key, injected=False):
        self.pressed_modifiers.discard(key)

    def on_click(self, x, y, button, pressed, injected=False):
        if pressed:
            self._reset()

    def _reset(self):
        self.word = []
        self.adjacent = self.after_space = self.manual = False
        self.tasks.put(('reset',))

    def worker(self):
        while True:
            task = self.tasks.get()
            try:
                self._handle(task)
            except Exception as error:  # переключатель не должен падать из-за одного слова
                print(f'Ошибка: {error!r}')

    def _handle(self, task):
        kind = task[0]
        if kind == 'reset':
            self.engine.reset()
        elif kind == 'toggle':
            print('✅ Автопереключение включено' if self.engine.toggle() else '⏸  Автопереключение выключено')
        elif kind == 'word':
            self._apply(self.engine.on_word(*task[1:]))
        elif kind == 'manual':
            self._apply(self.engine.on_manual(task[1]))

    def _apply(self, replacement: Optional[Replacement]):
        if replacement is None:
            return
        self.busy = True
        try:
            for _ in range(replacement.erase):
                self.controller.tap(self.keyboard.Key.backspace)
            self.controller.type(replacement.text)
            if replacement.layout:
                switch_layout(replacement.layout)
            print(f'  → {replacement.text.strip()}')
        finally:
            time.sleep(0.05)  # даём системе доставить наши нажатия, чтобы не принять их за свои
            self.busy = False

    def run(self):
        threading.Thread(target=self.worker, daemon=True).start()
        with self.mouse.Listener(on_click=self.on_click), \
                self.keyboard.Listener(on_press=self.on_press, on_release=self.on_release) as listener:
            listener.join()


def main():
    print('Загружаю словари...')
    engine = Engine(Detector())
    print('✅ Переключатель раскладки запущен. Pause — отменить замену, Shift+Pause — вкл/выкл.')
    print('   Закройте это окно или нажмите Ctrl+C, чтобы выйти.')
    try:
        KeyboardHook(engine).run()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
