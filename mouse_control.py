import sys
import time

import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.03


def enable_dpi_awareness():
    """Windows DPI awareness — pyautogui coordinates match real screen pixels."""
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


def click(x: int, y: int, button: str = "left"):
    pyautogui.click(int(x), int(y), button=button)


def move(x: int, y: int, duration: float = 0.15):
    pyautogui.moveTo(int(x), int(y), duration=duration)


def move_and_click(x: int, y: int, duration: float = 0.15):
    x, y = int(x), int(y)
    pyautogui.moveTo(x, y, duration=duration)
    time.sleep(0.05)
    pyautogui.mouseDown(x, y)
    time.sleep(0.03)
    pyautogui.mouseUp(x, y)


def double_click(x: int, y: int):
    pyautogui.doubleClick(int(x), int(y))


def right_click(x: int, y: int):
    pyautogui.rightClick(int(x), int(y))


def get_mouse_position():
    return pyautogui.position()


def scroll(amount: int):
    pyautogui.scroll(amount)


def drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.3):
    pyautogui.moveTo(int(start_x), int(start_y), duration=0.1)
    pyautogui.mouseDown()
    pyautogui.moveTo(int(end_x), int(end_y), duration=duration)
    pyautogui.mouseUp()


if __name__ == "__main__":
    while True:
        x, y = pyautogui.position()
        print(f"\rX={x} Y={y}", end="")
        time.sleep(0.05)
