"""Переключатель раскладки как приложение в строке меню macOS.

Из этого файла собирается LayoutSwitcher.app (см. build_mac_app.sh). Терминал не нужен:
значок ⌨ в строке меню показывает, что переключатель работает.
"""
import os

import AppKit
import Foundation
import objc

from detector import Detector
from macos import MacKeyboardHook
from switcher import Engine

SETTINGS_ACCESSIBILITY = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
SETTINGS_INPUT_MONITORING = 'x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent'
SETTINGS_KEYBOARD = 'x-apple.systempreferences:com.apple.Keyboard-Settings.extension'

PERMISSIONS_TEXT = (
    'Чтобы исправлять слова, приложению нужен доступ к клавиатуре.\n\n'
    'В окне «Конфиденциальность и безопасность» включите «LayoutSwitcher» в двух списках:\n'
    '  1. «Универсальный доступ»\n'
    '  2. «Мониторинг ввода»\n\n'
    'Как только оба переключателя включены, значок ⌨ в строке меню станет без «!». '
    'Если этого не произошло — в меню значка выберите «Перезапустить».'
)


def open_url(url: str):
    AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.URLWithString_(url))


def login_item():
    """Объект «Открывать при входе в систему» (macOS 13+) или None."""
    try:
        from ServiceManagement import SMAppService
        return SMAppService.mainAppService()
    except Exception:
        return None


class AppDelegate(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.engine = Engine(Detector())
        self.hook = MacKeyboardHook(self.engine)

        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength)
        self.menu = AppKit.NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self)
        self.status_item.setMenu_(self.menu)

        if self.hook.start(prompt=True):
            self.enable_login_item_once()
        else:
            self.show_permissions_alert()
        self.refresh_(None)
        # Раз в секунду: обновить значок и, если прав ещё нет, попробовать подключиться снова
        Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, 'refresh:', None, True)

    def refresh_(self, timer):
        if not self.hook.running and self.hook.start(prompt=False):
            self.enable_login_item_once()
        if not self.hook.running:
            title = '⌨!'
        elif not self.engine.enabled:
            title = '⌨⏸'
        else:
            title = '⌨'
        self.status_item.button().setTitle_(title)

    # --- меню ---

    def menuNeedsUpdate_(self, menu):
        menu.removeAllItems()
        if not self.hook.running:
            self.add_item('⚠️ Нет доступа к клавиатуре', None)
            self.add_item('Открыть настройки доступа…', 'openPermissions:')
            self.add_item('Перезапустить', 'restart:')
        elif not self.engine.enabled:
            self.add_item('⏸ Автопереключение выключено', None)
        else:
            self.add_item('✅ Работает: «ghbdtn» + пробел → «привет»', None)
        if self.hook.missing_layouts:
            self.add_item('⚠️ Включите русскую и английскую раскладки…', 'openKeyboard:')
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self.add_item('Автопереключение', 'toggle:', checked=self.engine.enabled)
        service = login_item()
        if service is not None:
            self.add_item('Запускать при входе в систему', 'toggleLoginItem:', checked=service.status() == 1)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.add_item('Shift два раза — отменить замену', None)
        self.add_item('Ctrl+Shift+X — выключить / включить', None)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.add_item('Выход', 'terminate:', target=AppKit.NSApplication.sharedApplication())

    @objc.python_method
    def add_item(self, title, action, checked=None, target=None):
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, '')
        item.setTarget_(target or self)
        item.setEnabled_(action is not None)
        if checked is not None:
            item.setState_(AppKit.NSControlStateValueOn if checked else AppKit.NSControlStateValueOff)
        self.menu.addItem_(item)

    def toggle_(self, sender):
        self.engine.toggle()
        self.refresh_(None)

    def toggleLoginItem_(self, sender):
        service = login_item()
        if service.status() == 1:
            service.unregisterAndReturnError_(None)
        else:
            service.registerAndReturnError_(None)

    def openPermissions_(self, sender):
        self.show_permissions_alert()

    def openKeyboard_(self, sender):
        open_url(SETTINGS_KEYBOARD)

    def restart_(self, sender):
        bundle = AppKit.NSBundle.mainBundle().bundlePath()
        os.system(f'(sleep 1; open "{bundle}") &')
        AppKit.NSApplication.sharedApplication().terminate_(None)

    # --- права и автозапуск ---

    @objc.python_method
    def show_permissions_alert(self):
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_('Разрешите доступ к клавиатуре')
        alert.setInformativeText_(PERMISSIONS_TEXT)
        alert.addButtonWithTitle_('Открыть «Универсальный доступ»')
        alert.addButtonWithTitle_('Открыть «Мониторинг ввода»')
        alert.addButtonWithTitle_('Позже')
        answer = alert.runModal()
        if answer == AppKit.NSAlertFirstButtonReturn:
            open_url(SETTINGS_ACCESSIBILITY)
        elif answer == AppKit.NSAlertSecondButtonReturn:
            open_url(SETTINGS_INPUT_MONITORING)

    @objc.python_method
    def enable_login_item_once(self):
        """При первом удачном запуске сразу включаем автозапуск (выключается в меню)."""
        defaults = Foundation.NSUserDefaults.standardUserDefaults()
        service = login_item()
        if service is None or defaults.boolForKey_('loginItemOffered'):
            return
        defaults.setBool_forKey_(True, 'loginItemOffered')
        if service.status() != 1:
            service.registerAndReturnError_(None)


def main():
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # без значка в Dock
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()


if __name__ == '__main__':
    main()
