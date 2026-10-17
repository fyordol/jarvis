"""Громкость, медиа, раскладка, вкладки браузера, окна."""
import re
import sys
from typing import Optional

from control.keyboard import hotkey, press


def _get_volume_steps(command: str) -> int:
    match = re.search(r"на\s+(\d+)\s*%?", command)
    if match:
        return max(1, int(match.group(1)) // 2)
    return 1


def _focus_win32(title_part: str, exact: bool = False) -> bool:
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
            title = buf.value.lower()
            if (title == title_lower) if exact else (title_lower in title):
                target_hwnd = hwnd
                return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return _focus_hwnd(target_hwnd)
    except Exception as e:
        print(f"focus window: {e}")
    return False


def _focus_hwnd(hwnd) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        handle = ctypes.c_void_p(hwnd)
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos(handle, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0003)
        user32.SetWindowPos(handle, ctypes.c_void_p(-2), 0, 0, 0, 0, 0x0003)
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
        return True
    except Exception as e:
        print(f"focus window: {e}")
    return False


_SKIP_TITLES = {"jarvis", "program manager", "nvidia geforce overlay"}

_APP_HINTS: dict[str, dict] = {
    "discord": {"proc": ["discord"], "title": ["discord"]},
    "telegram": {"proc": ["telegram"], "title": ["telegram"]},
    "steam": {"proc": ["steam.exe"], "title": ["steam"]},
    "spotify": {"proc": ["spotify"], "title": ["spotify"]},
    "хром": {"proc": ["chrome.exe"], "title": ["google chrome"]},
    "chrome": {"proc": ["chrome.exe"], "title": ["google chrome"]},
    "яндекс": {"proc": ["browser.exe"], "title": ["яндекс"]},
    "яндекс браузер": {"proc": ["browser.exe"], "title": ["яндекс"]},
    "браузер": {"proc": ["browser.exe"], "title": ["яндекс"]},
    "cursor": {"proc": ["cursor.exe"], "title": ["cursor"]},
    "obs": {"proc": ["obs64", "obs32", "obs.exe"], "title": ["obs "]},
    "osu": {"proc": ["osu!"], "title": ["osu!"]},
    "visual studio code": {"proc": ["code.exe"], "title": ["visual studio code"]},
    "блокнот": {"proc": ["notepad.exe"], "title": ["блокнот", "notepad"]},
    "notepad": {"proc": ["notepad.exe"], "title": ["блокнот", "notepad"]},
    "калькулятор": {"proc": ["calculator"], "title": ["калькулятор"]},
    "проводник": {"proc": [], "title": ["проводник"]},
}


def _windows() -> list[tuple]:
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    found: list[tuple] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title or title.lower() in _SKIP_TITLES:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, title, pid.value))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(proc(cb), 0)
    return found


def _pids(parts: list[str]) -> set[int]:
    import psutil

    want = [p.lower() for p in parts if p]
    found: set[int] = set()
    if not want:
        return found
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
        except (psutil.Error, KeyError):
            continue
        if any(part in name for part in want):
            found.add(int(proc.info["pid"]))
    return found


def running_hwnd(name: str):
    """Окно уже запущенного приложения или None."""
    key = (name or "").lower().strip()
    hint = _APP_HINTS.get(key)
    if hint is None:
        if len(key) < 4:
            return None
        hint = {"proc": [key], "title": [key]}
    pids = _pids(hint.get("proc") or [])
    titles = [t.lower() for t in hint.get("title") or []]
    for hwnd, title, pid in _windows():
        low = title.lower()
        if titles and any(part in low for part in titles):
            return hwnd
        if pid in pids:
            return hwnd
    return None


def focus_running(name: str) -> bool:
    return _focus_hwnd(running_hwnd(name))


def _is_yandex_page(title: str) -> bool:
    cleaned = title.replace("яндекс браузер", "").replace("yandex browser", "")
    return "яндекс" in cleaned or "yandex.ru" in cleaned or "ya.ru" in cleaned


def _is_google_page(title: str) -> bool:
    cleaned = title.replace("google chrome", "")
    return "google" in cleaned or "гугл" in cleaned


_SITE_TITLE = {
    "ютуб": ("youtube",),
    "youtube": ("youtube",),
    "вк": ("вконтакте", "vk.com"),
    "вконтакте": ("вконтакте", "vk.com"),
    "дискорд": ("discord",),
    "discord": ("discord",),
    "твич": ("twitch",),
    "twitch": ("twitch",),
    "github": ("github",),
    "гитхаб": ("github",),
    "стим": ("steam",),
    "чатгпт": ("chatgpt",),
}


def _browser_pids() -> set[int]:
    from core.config import get

    raw = str(get("search.browser", "yandex")).strip().lower()
    if raw in ("chrome", "хром", "google"):
        return _pids(["chrome.exe"])
    return _pids(["browser.exe"])


def focus_site(name: str) -> bool:
    """Вкладка или окно сайта уже на экране — переключиться, не открывать новое."""
    key = (name or "").lower().strip()
    if key in ("яндекс", "yandex"):
        pred = _is_yandex_page
    elif key in ("гугл", "google"):
        pred = _is_google_page
    else:
        parts = _SITE_TITLE.get(key)
        if not parts:
            return False
        pred = lambda title, parts=parts: any(part in title for part in parts)
    pids = _browser_pids()
    for hwnd, title, pid in _windows():
        if pids and pid not in pids:
            continue
        if pred(title.lower()):
            return _focus_hwnd(hwnd)
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
