import ctypes
import sys
import threading
import time

import pyautogui
import pyperclip

from control.mouse import enable_dpi_awareness

enable_dpi_awareness()
pyautogui.PAUSE = 0

KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56


def _clipboard_set(text: str) -> bool:
    """Копирует текст в буфер с повторными попытками."""
    for attempt in range(3):
        try:
            pyperclip.copy(text)
            time.sleep(0.06 + attempt * 0.04)
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
        time.sleep(0.02)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.01)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
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
    time.sleep(0.08)
    _paste_via_ctrl_v()
    print("Отправил Ctrl+V")

    # Восстанавливаем старый буфер с задержкой, чтобы вставка успела пройти
    _restore_clipboard_later(old)


VK_ESCAPE = 0x1B
LLKHF_INJECTED = 0x10
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

_esc_down = threading.Event()
_esc_guard = threading.Lock()
_esc_gen = 0
_esc_at = 0.0
_esc_held = 0.0
_esc_ignore = 0
_esc_hook = None
_esc_proc = None


def esc_down() -> threading.Event:
    return _esc_down


def esc_generation() -> int:
    with _esc_guard:
        return _esc_gen


def esc_held() -> float:
    """Сколько секунд длится текущее или только что отпущенное удержание Esc."""
    with _esc_guard:
        if _esc_down.is_set():
            return time.monotonic() - _esc_at
        return _esc_held


def _esc_set(down: bool) -> None:
    global _esc_gen, _esc_at, _esc_held
    now = time.monotonic()
    with _esc_guard:
        if down:
            if _esc_down.is_set():
                return
            _esc_gen += 1
            _esc_at = now
            _esc_down.set()
            return
        if _esc_down.is_set():
            _esc_held = now - _esc_at
        _esc_down.clear()


def install_escape_hook() -> bool:
    """Физический Esc: удержание включает запись, короткий тап проходит в активное окно."""
    global _esc_hook, _esc_proc
    if sys.platform != "win32":
        return False
    if _esc_hook:
        return True
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p

        class KBDLL(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.wintypes.DWORD),
                ("scanCode", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

        def _proc(nCode, wParam, lParam):
            global _esc_ignore
            if nCode >= 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLL)).contents
                if kb.vkCode == VK_ESCAPE:
                    with _esc_guard:
                        passed = _esc_ignore > 0
                        if passed:
                            _esc_ignore -= 1
                    if passed or (kb.flags & LLKHF_INJECTED):
                        return user32.CallNextHookEx(None, nCode, wParam, lParam)
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        _esc_set(True)
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                        _esc_set(False)
                    return 1
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        _esc_proc = HOOKPROC(_proc)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.UINT, ctypes.wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        ready = threading.Event()

        def _pump() -> None:
            global _esc_hook
            module = kernel32.GetModuleHandleW(None)
            hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _esc_proc, module, 0)
            if not hook:
                hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _esc_proc, None, 0)
            if not hook:
                print("Не удалось перехватить Esc:", kernel32.GetLastError())
                ready.set()
                return
            _esc_hook = hook
            ready.set()
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        threading.Thread(target=_pump, daemon=True, name="jarvis-esc").start()
        ready.wait(3)
        return bool(_esc_hook)
    except Exception as exc:
        print("Перехват Esc:", exc)
        return False


def tap_escape() -> None:
    """Короткое нажатие Esc в активное окно. Хук его не считает записью."""
    global _esc_ignore
    if sys.platform != "win32":
        press("esc")
        return
    with _esc_guard:
        _esc_ignore += 2
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_ESCAPE, 0x01, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_ESCAPE, 0x01, KEYEVENTF_KEYUP, 0)


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
