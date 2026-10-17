import base64
import io
import json
import re
import time
from typing import Optional

import pyautogui
from PIL import Image

from core.config import get
from core.llm import chat_completion
from core.memory import build_memory_hints, set_last_click
from control.mouse import enable_dpi_awareness
from desktop.ocr import find_text_ocr
from desktop.ui_intents import expand_queries, vision_intent_hint
from desktop.ui_tree import find_ui_element

enable_dpi_awareness()

_shot_cache: Optional[tuple] = None


def _min_confidence() -> float:
    return float(get("vision.min_confidence", 0.35))


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
    "кнопка": "Тип: кнопка или иконка-кнопка. Кликай по центру, даже если текста нет.",
    "иконка": "Тип: иконка. Кликай по центру иконки.",
    "картинка": "Тип: изображение. Кликай по центру картинки.",
    "поле": "Тип: поле ввода. Кликай по центру поля.",
    "вкладка": "Тип: вкладка. Кликай по центру вкладки.",
    "ссылка": "Тип: ссылка. Кликай по центру кликабельной области ссылки.",
}


def invalidate_shot_cache() -> None:
    global _shot_cache
    _shot_cache = None


def screenshot_for_vision(force: bool = False):
    """Скриншот экрана + размеры для корректного пересчёта координат."""
    global _shot_cache
    ttl = float(get("vision.shot_ttl_sec", 0.2))
    now = time.time()
    if not force and _shot_cache and now - _shot_cache[0] < ttl:
        return _shot_cache[1:]

    screen_w, screen_h = pyautogui.size()
    img = pyautogui.screenshot()

    max_size = int(get("vision.max_size", 1280))
    img_w, img_h = img.size
    if max(img_w, img_h) > max_size:
        scale = max_size / max(img_w, img_h)
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
        img_w, img_h = new_w, new_h

    buffer = io.BytesIO()
    quality = int(get("vision.jpeg_quality", 80))
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    image_b64 = base64.b64encode(buffer.getvalue()).decode()
    packed = (image_b64, img_w, img_h, screen_w, screen_h)
    _shot_cache = (now, *packed)
    return packed


def to_screen_coords(
    x_raw: float, y_raw: float, img_w: int, img_h: int, screen_w: int, screen_h: int
) -> tuple[int, int]:
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


def _img_to_screen_coords(
    x_raw: float, y_raw: float, img_w: int, img_h: int, screen_w: int, screen_h: int
) -> tuple[int, int]:
    return to_screen_coords(x_raw, y_raw, img_w, img_h, screen_w, screen_h)


def _parse_coords_legacy(answer: str, img_w: int, img_h: int, screen_w: int, screen_h: int):
    match = re.search(r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]", answer)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", answer)
    if not match:
        return None

    x_raw = float(match.group(1))
    y_raw = float(match.group(2))
    return to_screen_coords(x_raw, y_raw, img_w, img_h, screen_w, screen_h)


def _extract_json(answer: str) -> Optional[dict]:
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

        coords = to_screen_coords(float(x_val), float(y_val), img_w, img_h, screen_w, screen_h)
        return {"coords": coords, "confidence": confidence, "reason": reason}

    coords = _parse_coords_legacy(answer, img_w, img_h, screen_w, screen_h)
    if coords is not None:
        return {"coords": coords, "confidence": 0.7, "reason": "legacy [X, Y] формат"}

    return {"coords": None, "confidence": 0.0, "reason": "не удалось распарсить ответ модели"}


def _build_object_hints(target: str) -> str:
    target_lower = target.lower()
    hints = [f"- {vision_intent_hint(target)}"]

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

    return "Подсказки:\n" + "\n".join(hints)


def _find_screen_object_once(target: str) -> dict:
    target = target.strip()
    if not target:
        return {"coords": None, "confidence": 0.0, "reason": "пустое описание объекта"}

    image_b64, img_w, img_h, screen_w, screen_h = screenshot_for_vision()
    hints = _build_object_hints(target)
    memory_hints = build_memory_hints(target)

    system_prompt = f"""Ты визуальный навигатор по экрану. Найди элемент, который ВЫПОЛНЯЕТ нужное действие.

Правила:
- Ищи по назначению, не по точному тексту. Иконка без подписи — нормальная цель, если она делает то, что просят.
- «скачать» = стрелка вниз в лоток / облако со стрелкой / Save / Download, даже без слова на кнопке.
- «видео» — центр превью, не заголовок.
- Порядок (первое/второе) и позиция (справа/слева) обязательны, если указаны.

Размер скриншота: {img_w} x {img_h}. Координаты — пиксели этого изображения.

Ответ ТОЛЬКО JSON:
{{"found": true/false, "x": number|null, "y": number|null, "confidence": 0..1, "reason": "коротко"}}"""

    prompt = f"""Найди объект: "{target}".

{hints}

{memory_hints}

Центр кликабельной области в пикселях {img_w}x{img_h}."""

    print(f"Vision ищет объект: '{target}'")

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
            timeout=int(get("vision.timeout_sec", 45)),
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


def _remember(target: str, result: dict) -> dict:
    coords = result.get("coords")
    if coords is not None:
        screen_w = result.get("screen_w") or pyautogui.size()[0]
        screen_h = result.get("screen_h") or pyautogui.size()[1]
        set_last_click(target, coords, screen_w, screen_h, result.get("confidence", 0.0))
    return result


def find_screen_object(target: str) -> dict:
    target = target.strip()
    if not target:
        return {"coords": None, "confidence": 0.0, "reason": "пустое описание объекта"}

    uia = find_ui_element(target)
    if uia and uia.get("coords"):
        screen_w, screen_h = pyautogui.size()
        uia["screen_w"] = screen_w
        uia["screen_h"] = screen_h
        return _remember(target, uia)

    retries = int(get("vision.retry_count", 0)) + 1
    last = {"coords": None, "confidence": 0.0, "reason": "не найдено"}

    for attempt in range(retries):
        last = _find_screen_object_once(target)
        if last.get("coords") is not None:
            return last
        if attempt < retries - 1:
            print(f"Vision retry {attempt + 1}/{retries - 1}")
            time.sleep(float(get("llm.retry_delay_sec", 0.4)))

    for query in expand_queries(target)[:8]:
        ocr_coords = find_text_ocr(query)
        if ocr_coords:
            screen_w, screen_h = pyautogui.size()
            return _remember(
                target,
                {
                    "coords": ocr_coords,
                    "confidence": 0.65,
                    "reason": f"OCR «{query}»",
                    "screen_w": screen_w,
                    "screen_h": screen_h,
                },
            )

    return last


def find_text(target: str):
    result = find_screen_object(target)
    return result["coords"]
