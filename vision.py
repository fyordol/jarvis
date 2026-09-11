import base64
import io
import json
import re
import time
from typing import Optional

import pyautogui
from PIL import Image

from config import get
from llm_client import chat_completion
from memory import build_memory_hints, set_last_click
from ocr import find_text_ocr
from mouse_control import enable_dpi_awareness

enable_dpi_awareness()

def _min_confidence() -> float:
    return float(get("vision.min_confidence", 0.45))

ORDER_HINTS = {
    "первое": "Выбери ПЕРВОЕ подходящее сверху (или слева, если ряд горизонтальный).",
    "первый": "Выбери ПЕРВЫЙ подходящий сверху (или слева, если ряд горизонтальный).",
    "первая": "Выбери ПЕРВУЮ подходящую сверху (или слева, если ряд горизонтальный).",
    "первую": "Выбери ПЕРВУЮ подходящую сверху (или слева, если ряд горизонтальный).",
    "второе": "Выбери ВТОРОЕ подходящее сверху (или слева направо).",
    "второй": "Выбери ВТОРОЙ подходящий сверху (или слева направо).",
    "вторая": "Выбери ВТОРУЮ подходящую сверху (или слева направо).",
    "третье": "Выбери ТРЕТЬЕ подходящее сверху (или слева направо).",
    "третий": "Выбери ТРЕТИЙ подходящий сверху (или слева направо).",
    "последнее": "Выбери ПОСЛЕДНЕЕ подходящее снизу (или справа, если ряд горизонтальный).",
    "верхнее": "Выбери САМОЕ ВЕРХНЕЕ подходящее.",
    "нижнее": "Выбери САМОЕ НИЖНЕЕ подходящее.",
}

POSITION_HINTS = {
    "справа": "Объект должен быть в правой части экрана.",
    "слева": "Объект должен быть в левой части экрана.",
    "сверху": "Объект должен быть в верхней части экрана.",
    "снизу": "Объект должен быть в нижней части экрана.",
    "в центре": "Объект должен быть ближе к центру экрана.",
    "по центру": "Объект должен быть ближе к центру экрана.",
}

AREA_HINTS = {
    "в ленте": "Ищи в основной ленте контента, не в боковом меню и не в панели браузера.",
    "в списке": "Ищи среди элементов списка в основной области контента.",
    "в меню": "Ищи в выпадающем или боковом меню.",
    "на панели": "Ищи на панели инструментов или вкладок.",
    "в браузере": "Ищи в области страницы браузера, не в системных элементах ОС.",
}

TYPE_HINTS = {
    "видео": "Тип: карточка/превью видео. Кликай по центру превью, а не по тексту заголовка.",
    "кнопка": "Тип: кнопка. Кликай по центру кнопки.",
    "иконка": "Тип: иконка. Кликай по центру иконки.",
    "картинка": "Тип: изображение. Кликай по центру картинки.",
    "поле": "Тип: поле ввода. Кликай по центру поля.",
    "вкладка": "Тип: вкладка. Кликай по центру вкладки.",
    "ссылка": "Тип: ссылка. Кликай по центру кликабельной области ссылки.",
}


def screenshot_for_vision():
    """Скриншот экрана + размеры для корректного пересчёта координат."""
    screen_w, screen_h = pyautogui.size()
    img = pyautogui.screenshot()

    max_size = 1280
    img_w, img_h = img.size
    if max(img_w, img_h) > max_size:
        scale = max_size / max(img_w, img_h)
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        img_w, img_h = new_w, new_h

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=95)
    image_b64 = base64.b64encode(buffer.getvalue()).decode()

    return image_b64, img_w, img_h, screen_w, screen_h


def _img_to_screen_coords(
    x_raw: float, y_raw: float, img_w: int, img_h: int, screen_w: int, screen_h: int
) -> tuple[int, int]:
    """Переводит координаты скриншота в пиксели экрана."""
    if x_raw <= 1000 and y_raw <= 1000 and (x_raw > img_w or y_raw > img_h):
        x_img = (x_raw / 1000.0) * img_w
        y_img = (y_raw / 1000.0) * img_h
    else:
        x_img = x_raw
        y_img = y_raw

    x_img = max(0, min(img_w - 1, x_img))
    y_img = max(0, min(img_h - 1, y_img))

    x_px = int(x_img * screen_w / img_w)
    y_px = int(y_img * screen_h / img_h)

    x_px = max(0, min(screen_w - 1, x_px))
    y_px = max(0, min(screen_h - 1, y_px))

    return x_px, y_px


def _parse_coords_legacy(answer: str, img_w: int, img_h: int, screen_w: int, screen_h: int):
    """Парсит координаты из старого формата [X, Y]."""
    match = re.search(r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]", answer)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", answer)
    if not match:
        return None

    x_raw = float(match.group(1))
    y_raw = float(match.group(2))
    return _img_to_screen_coords(x_raw, y_raw, img_w, img_h, screen_w, screen_h)


def _extract_json(answer: str) -> Optional[dict]:
    """Извлекает JSON из ответа модели."""
    text = answer.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _parse_vision_response(
    answer: str, img_w: int, img_h: int, screen_w: int, screen_h: int
) -> dict:
    """
    Парсит ответ vision-модели.
    Возвращает: {"coords": (x,y)|None, "confidence": float, "reason": str}
    """
    data = _extract_json(answer)
    if data is not None:
        found = bool(data.get("found", False))
        confidence = float(data.get("confidence", 0.0) or 0.0)
        reason = str(data.get("reason", "") or "")

        if not found:
            return {"coords": None, "confidence": confidence, "reason": reason or "объект не найден"}

        x_val = data.get("x")
        y_val = data.get("y")
        if x_val is None or y_val is None:
            return {
                "coords": None,
                "confidence": confidence,
                "reason": reason or "координаты не указаны",
            }

        coords = _img_to_screen_coords(float(x_val), float(y_val), img_w, img_h, screen_w, screen_h)
        return {"coords": coords, "confidence": confidence, "reason": reason}

    coords = _parse_coords_legacy(answer, img_w, img_h, screen_w, screen_h)
    if coords is not None:
        return {"coords": coords, "confidence": 0.7, "reason": "legacy [X, Y] формат"}

    return {"coords": None, "confidence": 0.0, "reason": "не удалось распарсить ответ модели"}


def _build_object_hints(target: str) -> str:
    """Строит подсказки для prompt из описания объекта."""
    target_lower = target.lower()
    hints = []

    for word, hint in ORDER_HINTS.items():
        if word in target_lower:
            hints.append(f"- Порядок: {hint}")
            break

    for word, hint in POSITION_HINTS.items():
        if word in target_lower:
            hints.append(f"- Позиция: {hint}")

    for word, hint in AREA_HINTS.items():
        if word in target_lower:
            hints.append(f"- Область: {hint}")

    for word, hint in TYPE_HINTS.items():
        if word in target_lower:
            hints.append(f"- {hint}")

    if not hints:
        return ""

    return "Подсказки:\n" + "\n".join(hints)


def _find_screen_object_once(target: str) -> dict:
    """
    Ищет визуальный объект на экране по описанию.
    Возвращает: {"coords": (x,y)|None, "confidence": float, "reason": str}
    """
    target = target.strip()
    if not target:
        return {"coords": None, "confidence": 0.0, "reason": "пустое описание объекта"}

    image_b64, img_w, img_h, screen_w, screen_h = screenshot_for_vision()
    hints = _build_object_hints(target)
    memory_hints = build_memory_hints(target)

    system_prompt = f"""Ты визуальный навигатор по экрану. Твоя задача — найти объект на скриншоте по описанию пользователя.

Ты НЕ OCR. Ищи визуальные объекты: кнопки, карточки видео, иконки, картинки, поля ввода, вкладки, пункты меню, ссылки.
Если пользователь говорит «видео» — выбирай центр превью/карточки, а не текст заголовка рядом.
Если указан порядок (первое, второе, последнее) — выбирай среди подходящих объектов.
Если указана позиция (справа, слева, сверху, снизу) — учитывай расположение на экране.
Если указана область (в ленте, в списке, в меню) — ищи в соответствующей зоне интерфейса.

Размер скриншота: {img_w} x {img_h} пикселей.
Координаты x, y — в пикселях этого скриншота: X от 0 до {img_w - 1}, Y от 0 до {img_h - 1}.

Ответ — ТОЛЬКО JSON, без текста вокруг:
{{"found": true/false, "x": number|null, "y": number|null, "confidence": 0..1, "reason": "коротко"}}"""

    prompt = f"""Найди на скриншоте объект по описанию: "{target}".

{hints}

{memory_hints}

Верни координаты центра объекта в пикселях скриншота {img_w}x{img_h}.
JSON: {{"found": true/false, "x": number|null, "y": number|null, "confidence": 0..1, "reason": "коротко"}}"""

    print(f"Vision ищет объект: '{target}'")
    if hints:
        print(hints)
    if memory_hints:
        print(memory_hints)

    try:
        answer = chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                },
            ],
            role="vision",
            temperature=0.0,
            timeout=120,
        )
    except Exception as e:
        print(f"Ошибка связи с сервером зрения: {e}")
        return {"coords": None, "confidence": 0.0, "reason": f"ошибка сервера: {e}"}

    print(f"Qwen ответ: {answer}")

    result = _parse_vision_response(answer, img_w, img_h, screen_w, screen_h)
    print(
        f"Результат: coords={result['coords']}, "
        f"confidence={result['confidence']:.2f}, reason={result['reason']}"
    )

    min_conf = _min_confidence()
    if result["coords"] is not None and result["confidence"] < min_conf:
        print(f"Confidence {result['confidence']:.2f} ниже порога {min_conf}")
        return {
            "coords": None,
            "confidence": result["confidence"],
            "reason": result["reason"] or "низкая уверенность",
        }

    if result["coords"] is not None:
        x, y = result["coords"]
        print(f"Координаты для клика: X={x}, Y={y} (экран {screen_w}x{screen_h})")
        set_last_click(target, (x, y), screen_w, screen_h, result["confidence"])
        result["screen_w"] = screen_w
        result["screen_h"] = screen_h

    return result


def find_screen_object(target: str) -> dict:
    """Ищет объект с retry и OCR fallback."""
    retries = int(get("llm.retry_count", 2)) + 1
    last = {"coords": None, "confidence": 0.0, "reason": "не найдено"}

    for attempt in range(retries):
        last = _find_screen_object_once(target)
        if last.get("coords") is not None:
            return last
        if attempt < retries - 1:
            print(f"Vision retry {attempt + 1}/{retries - 1}")
            time.sleep(float(get("llm.retry_delay_sec", 1.0)))

    ocr_coords = find_text_ocr(target)
    if ocr_coords:
        screen_w, screen_h = pyautogui.size()
        set_last_click(target, ocr_coords, screen_w, screen_h, 0.65)
        return {"coords": ocr_coords, "confidence": 0.65, "reason": "найдено через OCR"}

    return last


def find_text(target: str):
    """Совместимый wrapper: возвращает (x, y) или None."""
    result = find_screen_object(target)
    return result["coords"]
