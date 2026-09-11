import ctypes
import sys
import threading
import time

import pyautogui
import pyperclip

from mouse_control import enable_dpi_awareness

enable_dpi_awareness()
pyautogui.PAUSE = 0.03

KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56


def _clipboard_set(text: str) -> bool:
    """Копирует текст в буфер с повторными попытками."""
    for attempt in range(3):
        try:
            pyperclip.copy(text)
            time.sleep(0.12 + attempt * 0.05)
            if pyperclip.paste() == text:
                return True
        except pyperclip.PyperclipException:
            time.sleep(0.1)
    return False


def _paste_windows_native() -> bool:
    """Ctrl+V через WinAPI — надёжнее pyautogui на Windows."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.04)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.04)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)
        return True
    except Exception as e:
        print(f"WinAPI paste ошибка: {e}")
        return False


def _paste_via_ctrl_v() -> None:
    if _paste_windows_native():
        return
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.1)


def _restore_clipboard_later(old: str, delay: float = 1.0) -> None:
    if not old:
        return

    def _restore():
        time.sleep(delay)
        try:
            pyperclip.copy(old)
        except pyperclip.PyperclipException:
            pass

    threading.Thread(target=_restore, daemon=True).start()


def write(text: str):
    if not text:
        return

    preview = text if len(text) <= 50 else text[:50] + "..."
    print(f"Вставка текста: {preview}")

    old = ""
    try:
        old = pyperclip.paste()
    except pyperclip.PyperclipException:
        pass

    if not _clipboard_set(text):
        print("Буфер обмена недоступен, пробую напечатать напрямую")
        try:
            pyautogui.write(text, interval=0.02)
        except Exception as e:
            print(f"Не удалось ввести текст: {e}")
        return

    # Пауза: приложение должно быть в фокусе (не терминал Jarvis)
    time.sleep(0.15)
    _paste_via_ctrl_v()
    print("Отправил Ctrl+V")

    # Восстанавливаем старый буфер с задержкой, чтобы вставка успела пройти
    _restore_clipboard_later(old)


def press(key: str):
    pyautogui.press(key)


def hotkey(*keys):
    if not keys:
        return
    if len(keys) == 1:
        pyautogui.press(keys[0])
        return

    for key in keys[:-1]:
        pyautogui.keyDown(key)
        time.sleep(0.02)
    pyautogui.press(keys[-1])
    time.sleep(0.02)
    for key in reversed(keys[:-1]):
        pyautogui.keyUp(key)


# Голосовые команды клавиатуры (без аргументов)
COMMANDS = {
    "энтер": lambda: press("enter"),
    "enter": lambda: press("enter"),
    "эскейп": lambda: press("esc"),
    "escape": lambda: press("esc"),
    "таб": lambda: press("tab"),
    "пробел": lambda: press("space"),
    "скопируй": lambda: hotkey("ctrl", "c"),
    "вставь": lambda: hotkey("ctrl", "v"),
    "вырежи": lambda: hotkey("ctrl", "x"),
    "выдели всё": lambda: hotkey("ctrl", "a"),
}
