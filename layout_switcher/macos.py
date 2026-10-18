"""Перехват клавиатуры и переключение раскладки на macOS.

Работает напрямую через Quartz Event Tap и Text Input Sources, без pynput:
всё, что трогает раскладки, выполняется в главном потоке — иначе новые
версии macOS аварийно завершают программу.
"""
import ctypes
import os
import queue
import signal
import time

import HIServices
import Quartz

from detector import EN, RU, set_key_tables
from switcher import WordTracker, process_task

KEY_SPACE = 49
KEY_DELETE = 51
KEY_X = 7
SHIFT_KEYS = {56, 60}
DOUBLE_SHIFT_INTERVAL = 0.4
# Коды клавиш основного блока Mac: буквы, цифры, знаки (последний — § на европейских клавиатурах)
LETTER_KEYS = (50, 18, 19, 20, 21, 23, 22, 26, 28, 25, 29, 27, 24,
               12, 13, 14, 15, 17, 16, 32, 34, 31, 35, 33, 30, 42,
               0, 1, 2, 3, 5, 4, 38, 40, 37, 41, 39,
               6, 7, 8, 9, 11, 45, 46, 43, 47, 44, 10)
DEBUG = bool(os.environ.get('LAYOUT_SWITCHER_DEBUG'))
MAGIC = 0x52555345  # метка на наших собственных нажатиях, чтобы не принять их за пользовательские

PERMISSIONS_HELP = '''
❌ macOS не даёт программе видеть нажатия клавиш.

   Откройте  Системные настройки → Конфиденциальность и безопасность  и включите
   приложение, из которого запускаете переключатель (обычно «Терминал»), в двух списках:
     1. «Универсальный доступ»
     2. «Мониторинг ввода»
   Если Терминала там нет — нажмите «+» и добавьте его из папки Программы → Утилиты.

   Затем полностью закройте Терминал (Cmd+Q) и запустите переключатель снова.
'''


class InputSources:
    """Переключение раскладки через Text Input Sources (Carbon)."""

    RUSSIAN = ('com.apple.keylayout.Russian', 'com.apple.keylayout.RussianWin')
    ENGLISH = ('com.apple.keylayout.ABC', 'com.apple.keylayout.US', 'com.apple.keylayout.USExtended',
               'com.apple.keylayout.British', 'com.apple.keylayout.British-PC',
               'com.apple.keylayout.USInternational-PC', 'com.apple.keylayout.Australian',
               'com.apple.keylayout.Canadian', 'com.apple.keylayout.Irish')

    def __init__(self):
        carbon = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/Carbon.framework/Carbon')
        cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        carbon.TISCreateInputSourceList.argtypes = (ctypes.c_void_p, ctypes.c_bool)
        carbon.TISCreateInputSourceList.restype = ctypes.c_void_p
        carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
        carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = ctypes.c_void_p
        carbon.TISGetInputSourceProperty.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
        carbon.TISSelectInputSource.argtypes = (ctypes.c_void_p,)
        carbon.TISSelectInputSource.restype = ctypes.c_int32
        carbon.TISEnableInputSource.argtypes = (ctypes.c_void_p,)
        carbon.TISEnableInputSource.restype = ctypes.c_int32
        carbon.LMGetKbdType.restype = ctypes.c_uint8
        carbon.UCKeyTranslate.argtypes = (
            ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_uint16))
        carbon.UCKeyTranslate.restype = ctypes.c_int32
        cf.CFDataGetBytePtr.argtypes = (ctypes.c_void_p,)
        cf.CFDataGetBytePtr.restype = ctypes.c_void_p
        cf.CFArrayGetCount.argtypes = (ctypes.c_void_p,)
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = (ctypes.c_void_p, ctypes.c_long)
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFStringGetCString.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32)
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFBooleanGetValue.argtypes = (ctypes.c_void_p,)
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        cf.CFRelease.argtypes = (ctypes.c_void_p,)
        self._carbon, self._cf = carbon, cf
        self._id_key = ctypes.c_void_p.in_dll(carbon, 'kTISPropertyInputSourceID').value
        self._ascii_key = ctypes.c_void_p.in_dll(carbon, 'kTISPropertyInputSourceIsASCIICapable').value
        self._selectable_key = ctypes.c_void_p.in_dll(carbon, 'kTISPropertyInputSourceIsSelectCapable').value
        self._layout_key = ctypes.c_void_p.in_dll(carbon, 'kTISPropertyUnicodeKeyLayoutData').value
        self._current = (None, None, None)  # (id, источник, данные раскладки) — кэш для translate_key

    def _source_id(self, source) -> str:
        value = self._carbon.TISGetInputSourceProperty(source, self._id_key)
        buffer = ctypes.create_string_buffer(256)
        if value and self._cf.CFStringGetCString(value, buffer, 256, 0x08000100):  # UTF-8
            return buffer.value.decode()
        return ''

    def _flag(self, source, key) -> bool:
        value = self._carbon.TISGetInputSourceProperty(source, key)
        return bool(value) and self._cf.CFBooleanGetValue(value)

    def _with_sources(self, action, include_all=False):
        sources = self._carbon.TISCreateInputSourceList(None, include_all)
        try:
            items = [self._cf.CFArrayGetValueAtIndex(sources, i) for i in range(self._cf.CFArrayGetCount(sources))]
            return action({self._source_id(s): s for s in items})
        finally:
            self._cf.CFRelease(sources)

    def find(self, lang: str, sources: dict):
        """Подходящая включённая раскладка для языка: её id или None."""
        preferred = self.RUSSIAN if lang == RU else self.ENGLISH
        for source_id in preferred:
            if source_id in sources and self._flag(sources[source_id], self._selectable_key):
                return source_id
        if lang == EN:  # любая латинская раскладка, если стандартных нет
            for source_id, source in sources.items():
                if 'keylayout' in source_id and self._flag(source, self._ascii_key) \
                        and self._flag(source, self._selectable_key):
                    return source_id
        return None

    def available(self, lang: str) -> bool:
        return self._with_sources(lambda sources: self.find(lang, sources)) is not None

    def select(self, lang: str) -> bool:
        def action(sources):
            source_id = self.find(lang, sources)
            return source_id is not None and self._carbon.TISSelectInputSource(sources[source_id]) == 0
        return self._with_sources(action)

    def key_map(self, lang: str) -> dict:
        """Что печатает каждая клавиша в раскладке языка: {код клавиши: (без Shift, с Shift)}."""
        def action(sources):
            source_id = self.find(lang, sources)
            data = source_id and self._carbon.TISGetInputSourceProperty(sources[source_id], self._layout_key)
            if not data:
                return {}
            layout = self._cf.CFDataGetBytePtr(data)
            return {code: (self._translate(layout, code, 0), self._translate(layout, code, 0x02))
                    for code in LETTER_KEYS}
        return self._with_sources(action)

    def translate_key(self, keycode: int, flags: int) -> str:
        """Символ клавиши в текущей раскладке.

        Строке из самого события верить нельзя: после переключения раскладки
        macOS какое-то время кладёт туда символы прежней раскладки.
        """
        source = self._carbon.TISCopyCurrentKeyboardLayoutInputSource()
        if not source:
            return ''
        source_id = self._source_id(source)
        if source_id == self._current[0]:
            self._cf.CFRelease(source)
        else:
            if self._current[1]:
                self._cf.CFRelease(self._current[1])
            data = self._carbon.TISGetInputSourceProperty(source, self._layout_key)
            self._current = (source_id, source, data and self._cf.CFDataGetBytePtr(data))
        layout = self._current[2]
        if not layout:
            return ''
        modifiers = 0
        if flags & Quartz.kCGEventFlagMaskShift:
            modifiers |= 0x02
        if flags & Quartz.kCGEventFlagMaskAlphaShift:
            modifiers |= 0x04
        if flags & Quartz.kCGEventFlagMaskAlternate:
            modifiers |= 0x08
        return self._translate(layout, keycode, modifiers)

    def _translate(self, layout, keycode: int, modifiers: int) -> str:
        dead_keys = ctypes.c_uint32(0)
        length = ctypes.c_ulong(0)
        chars = (ctypes.c_uint16 * 4)()
        self._carbon.UCKeyTranslate(layout, keycode, 0, modifiers, self._carbon.LMGetKbdType(), 1,
                                    ctypes.byref(dead_keys), 4, ctypes.byref(length), chars)
        return bytes(chars)[:length.value * 2].decode('utf-16-le', errors='ignore')

    def enable(self, source_id: str) -> bool:
        """Включает установленную раскладку (нужно только для самопроверки)."""
        return self._with_sources(
            lambda sources: source_id in sources and self._carbon.TISEnableInputSource(sources[source_id]) == 0,
            include_all=True)

    def enabled_ids(self):
        return self._with_sources(lambda sources: [i for i in sources if 'keylayout' in i])

    def current_id(self) -> str:
        source = self._carbon.TISCopyCurrentKeyboardInputSource()
        try:
            return self._source_id(source)
        finally:
            self._cf.CFRelease(source)


def key_tables(input_sources: InputSources):
    """Таблицы перевода EN<->RU по реально включённым раскладкам Mac.

    В маковской «Русской» раскладке, например, точка и запятая стоят не там, где в Windows.
    """
    en, ru = input_sources.key_map(EN), input_sources.key_map(RU)
    en_to_ru, ru_to_en = {}, {}
    for code in LETTER_KEYS:
        if code not in en or code not in ru:
            continue
        for en_char, ru_char in zip(en[code], ru[code]):
            if en_char and ru_char and en_char != ru_char:
                en_to_ru.setdefault(en_char, ru_char)
                ru_to_en.setdefault(ru_char, en_char)
    return en_to_ru, ru_to_en


def check_permissions(prompt: bool = True) -> bool:
    """Есть ли у программы права видеть и нажимать клавиши. prompt=True — попросить их у системы."""
    trusted = HIServices.AXIsProcessTrustedWithOptions({HIServices.kAXTrustedCheckOptionPrompt: prompt})
    listen = True
    if hasattr(Quartz, 'CGPreflightListenEventAccess'):
        listen = Quartz.CGPreflightListenEventAccess()
        if not listen and prompt:
            Quartz.CGRequestListenEventAccess()
    return bool(trusted and listen)


class MacKeyboardHook:
    HOTKEYS = 'Shift два раза подряд — отменить замену, Ctrl+Shift+X — вкл/выкл'

    def __init__(self, engine):
        self.engine = engine
        self.tasks = queue.Queue()
        self.tracker = WordTracker(self.tasks)
        self.input_sources = InputSources()
        self._event_source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        self._tap = None
        self._seen_keys = False
        self._shift_down_at = None   # когда нажат Shift (если после этого не было других клавиш)
        self._last_shift_tap = 0.0

        missing = [lang for lang in (RU, EN) if not self.input_sources.available(lang)]
        for lang in missing:
            name = 'русская' if lang == RU else 'английская (ABC или U.S.)'
            print(f'⚠️  Не включена {name} раскладка: Системные настройки → Клавиатура → Источники ввода')
        if not missing:
            set_key_tables(*key_tables(self.input_sources))

    # --- события клавиатуры (главный поток) ---

    def _on_event(self, proxy, event_type, event, refcon):
        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == MAGIC or \
                Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) == os.getpid():
            return event  # это мы сами печатаем исправленное слово

        if event_type == Quartz.kCGEventFlagsChanged:
            self._on_modifiers(event)
        elif event_type == Quartz.kCGEventKeyDown:
            if not self._seen_keys:
                self._seen_keys = True
                print('⌨️  Нажатия клавиш видны — переключатель работает.')
            self._on_key(event)
        else:  # клик мышью
            self.tracker.reset()
        return event

    def _on_key(self, event):
        self._shift_down_at = None
        self._last_shift_tap = 0.0
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event)
        shortcut = flags & (Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskControl)

        if keycode == KEY_X and shortcut == Quartz.kCGEventFlagMaskControl and flags & Quartz.kCGEventFlagMaskShift:
            self.tracker.on_toggle()
        elif shortcut:
            self.tracker.reset()  # Cmd+V, Cmd+Z, Ctrl+A...
        elif keycode == KEY_SPACE:
            self.tracker.on_space()
        elif keycode == KEY_DELETE:
            self.tracker.on_backspace()
        else:
            chars = self.input_sources.translate_key(keycode, flags)
            if not chars:
                _, chars = Quartz.CGEventKeyboardGetUnicodeString(event, 4, None, None)
            if chars and chars.isprintable():
                self.tracker.on_char(chars)
            else:
                self.tracker.reset()  # Enter, Tab, Esc, стрелки, F1...

    def _on_modifiers(self, event):
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        if keycode not in SHIFT_KEYS:
            self._shift_down_at = None
            return
        now = time.monotonic()
        if Quartz.CGEventGetFlags(event) & Quartz.kCGEventFlagMaskShift:
            self._shift_down_at = now
        elif self._shift_down_at is not None and now - self._shift_down_at < 0.3:
            # Shift нажат и отпущен сам по себе
            if now - self._last_shift_tap < DOUBLE_SHIFT_INTERVAL:
                self._last_shift_tap = 0.0
                self.tracker.on_manual()
            else:
                self._last_shift_tap = now
            self._shift_down_at = None

    # --- обработка задач (главный поток, по таймеру) ---

    def _on_timer(self, timer, info):
        while True:
            try:
                task = self.tasks.get_nowait()
            except queue.Empty:
                return
            if DEBUG:
                print('  [debug]', task)
            try:
                replacement = process_task(self.engine, task)
                if replacement:
                    self._type(replacement.erase, replacement.text)
                    if replacement.layout and not self.input_sources.select(replacement.layout):
                        print('⚠️  Не удалось переключить раскладку')
                    print(f'  → {replacement.text.strip()}')
            except Exception as error:  # переключатель не должен падать из-за одного слова
                print(f'Ошибка: {error!r}')

    def _post_key(self, keycode: int, text: str = None):
        for key_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(self._event_source, keycode, key_down)
            if text:
                Quartz.CGEventKeyboardSetUnicodeString(event, len(text.encode('utf-16-le')) // 2, text)
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, MAGIC)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.003)

    def _type(self, erase: int, text: str):
        for _ in range(erase):
            self._post_key(KEY_DELETE)
        for char in text:
            self._post_key(KEY_SPACE if char == ' ' else 0, None if char == ' ' else char)

    # --- запуск ---

    def run(self):
        if not check_permissions(prompt=True):
            print(PERMISSIONS_HELP)
            return
        mask = 0
        for event_type in (Quartz.kCGEventKeyDown, Quartz.kCGEventFlagsChanged, Quartz.kCGEventLeftMouseDown,
                           Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown):
            mask |= Quartz.CGEventMaskBit(event_type)
        self._tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                            Quartz.kCGEventTapOptionListenOnly, mask, self._on_event, None)
        if self._tap is None:
            print(PERMISSIONS_HELP)
            return

        loop = Quartz.CFRunLoopGetCurrent()
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        timer = Quartz.CFRunLoopTimerCreate(None, Quartz.CFAbsoluteTimeGetCurrent(), 0.02, 0, 0,
                                            self._on_timer, None)
        Quartz.CFRunLoopAddTimer(loop, timer, Quartz.kCFRunLoopCommonModes)
        # Ctrl+C: обработчик сработает при следующем срабатывании таймера и остановит цикл
        signal.signal(signal.SIGINT, lambda *_: Quartz.CFRunLoopStop(loop))
        print('   Проверьте: напишите «ghbdtn» и пробел, например в Заметках или Telegram.')
        Quartz.CFRunLoopRun()
