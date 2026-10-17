import sys
import time
from typing import Optional

import pyautogui

from core.config import get

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0
try:
    pyautogui.MINIMUM_DURATION = 0
    pyautogui.MINIMUM_SLEEP = 0
except Exception:
    pass


def enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


enable_dpi_awareness()


def _duration() -> float:
    return float(get("mouse.move_duration", 0.0))


def click(x: int, y: int, button: str = "left"):
    pyautogui.click(int(x), int(y), button=button, duration=_duration())


def move(x: int, y: int, duration: Optional[float] = None):
    if duration is None:
        duration = _duration()
    pyautogui.moveTo(int(x), int(y), duration=duration)


def move_and_click(x: int, y: int, duration: Optional[float] = None):
    if duration is None:
        duration = _duration()
    x, y = int(x), int(y)
    if duration > 0:
        pyautogui.moveTo(x, y, duration=duration)
    pyautogui.click(x, y)


def double_click(x: int, y: int):
    pyautogui.doubleClick(int(x), int(y), duration=_duration())


def right_click(x: int, y: int):
    pyautogui.rightClick(int(x), int(y), duration=_duration())


def get_mouse_position():
    return pyautogui.position()


def scroll(amount: int):
    pyautogui.scroll(amount)


def drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.15):
    pyautogui.moveTo(int(start_x), int(start_y), duration=0)
    pyautogui.dragTo(int(end_x), int(end_y), duration=duration, button="left")


if __name__ == "__main__":
    while True:
        x, y = pyautogui.position()
        print(f"\rX={x} Y={y}", end="")
        time.sleep(0.05)
