"""Громкость, медиа, раскладка, вкладки браузера, окна."""
import re
import sys
from typing import Optional

from keyboard_control import hotkey, press


def _get_volume_steps(command: str) -> int:
    match = re.search(r"на\s+(\d+)\s*%?", command)
    if match:
        return max(1, int(match.group(1)) // 2)
    return 1


def _focus_win32(title_part: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        target_hwnd = None
        title_lower = title_part.lower()

        def enum_callback(hwnd, _):
            nonlocal target_hwnd
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_lower in buf.value.lower():
                target_hwnd = hwnd
                return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        if target_hwnd:
            user32.ShowWindow(target_hwnd, 9)
            user32.SetForegroundWindow(target_hwnd)
            return True
    except Exception as e:
        print(f"focus window: {e}")
    return False


def try_media_command(command: str) -> Optional[str]:
    command = command.strip().lower()

    if command in ("новая вкладка", "открой новую вкладку", "вкладка"):
        hotkey("ctrl", "t")
        return "Новая вкладка"
    if command in ("закрой вкладку", "закрыть вкладку"):
        hotkey("ctrl", "w")
        return "Закрыл вкладку"
    if command in ("следующая вкладка", "переключи вкладку", "вкладка вправо"):
        hotkey("ctrl", "tab")
        return "Следующая вкладка"
    if command in ("предыдущая вкладка", "вкладка влево"):
        hotkey("ctrl", "shift", "tab")
        return "Предыдущая вкладка"

    if command in ("переключи окно", "alt tab", "альт таб"):
        hotkey("alt", "tab")
        return "Переключил окно"
    if command.startswith("переключись на ") or command.startswith("активируй "):
        prefix = "переключись на " if command.startswith("переключись на ") else "активируй "
        title = command[len(prefix):].strip()
        if not title:
            return "На какое окно переключиться?"
        if _focus_win32(title):
            return f"Активировал окно «{title}»"
        return f"Не нашёл окно «{title}»"
    if command in ("сверни окно", "минимизируй"):
        hotkey("win", "down")
        return "Свернул окно"
    if command in ("разверни окно", "максимизируй"):
        hotkey("win", "up")
        return "Развернул окно"

    if any(w in command for w in ("громче", "прибавь громкость", "увеличь громкость", "погромче", "volume up")):
        steps = _get_volume_steps(command)
        for _ in range(steps):
            press("volumeup")
        return f"Громче на {steps * 2}%" if steps > 1 else "Громче"

    if any(w in command for w in ("тише", "убавь громкость", "уменьши громкость", "потише", "volume down")):
        steps = _get_volume_steps(command)
        for _ in range(steps):
            press("volumedown")
        return f"Тише на {steps * 2}%" if steps > 1 else "Тише"

    if command in ("выключи звук", "без звука", "mute", "замолчи", "заглуши"):
        press("volumemute")
        return "Звук выключен"

    if command in ("пауза", "play", "плей", "воспроизведи", "останови музыку", "play pause"):
        press("playpause")
        return "Пауза / воспроизведение"

    if command in ("следующий трек", "следующая песня", "дальше", "next"):
        press("nexttrack")
        return "Следующий трек"

    if command in ("предыдущий трек", "предыдущая песня", "назад", "previous"):
        press("prevtrack")
        return "Предыдущий трек"

    if command in (
        "переключи раскладку", "смени раскладку", "раскладка",
        "english", "русская раскладка", "английская раскладка",
    ):
        hotkey("alt", "shift")
        return "Переключил раскладку"

    return None
