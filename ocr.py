"""OCR fallback для поиска текста на экране (опционально EasyOCR)."""
from typing import Optional

import pyautogui


_ocr_reader = None


def _get_reader():
    global _ocr_reader
    if _ocr_reader is not False and _ocr_reader is not None:
        return _ocr_reader
    try:
        import easyocr
        _ocr_reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
        return _ocr_reader
    except Exception as e:
        print(f"OCR недоступен (easyocr): {e}")
        _ocr_reader = False
        return None


def find_text_ocr(target: str) -> Optional[tuple[int, int]]:
    """Ищет текст на экране через OCR. Возвращает центр совпадения."""
    from config import get
    if not get("vision.ocr_enabled", True):
        return None

    reader = _get_reader()
    if reader is None:
        return None

    target = target.strip().lower()
    if not target:
        return None

    img = pyautogui.screenshot()
    import numpy as np
    results = reader.readtext(np.array(img))
    screen_w, screen_h = pyautogui.size()
    img_w, img_h = img.size

    best = None
    best_score = 0.0
    for bbox, text, conf in results:
        if target not in text.lower() and text.lower() not in target:
            continue
        if conf < 0.3:
            continue
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cx = int(sum(xs) / len(xs) * screen_w / img_w)
        cy = int(sum(ys) / len(ys) * screen_h / img_h)
        score = conf * (2 if target == text.lower() else 1)
        if score > best_score:
            best_score = score
            best = (cx, cy)

    if best:
        print(f"OCR нашёл «{target}» at {best} (score={best_score:.2f})")
    return best
