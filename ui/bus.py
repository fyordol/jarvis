"""Шина агента: JSON-шаги → быстрое исполнение. Скрин только на «хз»."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from core.config import get
from core.paths import BUS_DIR
from control.mouse import double_click, move_and_click, right_click

INBOX = BUS_DIR / "inbox.json"
RESULT = BUS_DIR / "result.json"
SCREEN = BUS_DIR / "screen.jpg"

NEED_SCREEN_TOKENS = {
    "хз",
    "хз.",
    "не знаю",
    "idk",
    "hz",
    "?",
    "скрин",
    "screenshot",
    "screen",
    "wait_screen",
    "нужен скрин",
}

VISUAL_PREFIXES = (
    "нажми на ",
    "кликни по ",
    "кликни ",
    "выбери ",
    "двойной клик по ",
    "правый клик по ",
)

CLICK_RE = re.compile(
    r"^(?P<kind>кликни|клик|click|двойной клик|правый клик)\s+"
    r"(?P<x>\d+(?:\.\d+)?)\s*[, ]\s*(?P<y>\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
WAIT_RE = re.compile(r"^(?:жди|wait|sleep)\s+(?P<sec>\d+(?:[.,]\d+)?)$", re.IGNORECASE)
HOTKEY_RE = re.compile(r"^(?:hotkey|хоткей|комбо)\s+(?P<keys>.+)$", re.IGNORECASE)

FAIL_MARKERS = (
    "не нашёл",
    "команда не найдена",
    "не уверен",
    "файл не найден",
    "не знаю, как",
)

_last_shot_meta: dict[str, int] = {}
_watcher_started = False


def bus_dir() -> Path:
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    return BUS_DIR


def _is_need_screen(step: str) -> bool:
    s = step.strip().lower().rstrip(".!")
    return s in NEED_SCREEN_TOKENS


def _is_visual_guess(step: str) -> bool:
    s = step.strip().lower()
    if CLICK_RE.match(s):
        return False
    if s.startswith("нажми "):
        key = s[len("нажми "):].strip()
        if key in ("enter", "энтер", "escape", "эскейп", "tab", "таб", "пробел", "space"):
            return False
    return any(s.startswith(p) for p in VISUAL_PREFIXES)


def _failed(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in FAIL_MARKERS)


def save_screenshot() -> dict[str, Any]:
    import base64

    from desktop.vision import invalidate_shot_cache, screenshot_for_vision

    bus_dir()
    invalidate_shot_cache()
    b64, w, h, sw, sh = screenshot_for_vision(force=True)
    SCREEN.write_bytes(base64.b64decode(b64))
    meta = {
        "image_width": int(w),
        "image_height": int(h),
        "screen_width": int(sw),
        "screen_height": int(sh),
        "path": str(SCREEN),
    }
    global _last_shot_meta
    _last_shot_meta = meta
    return meta


def _click_at(x: float, y: float, kind: str = "click") -> str:
    from desktop.vision import to_screen_coords

    img_w = int(_last_shot_meta.get("image_width") or 0)
    img_h = int(_last_shot_meta.get("image_height") or 0)
    sw = int(_last_shot_meta.get("screen_width") or 0)
    sh = int(_last_shot_meta.get("screen_height") or 0)
    if img_w and img_h and sw and sh:
        sx, sy = to_screen_coords(x, y, img_w, img_h, sw, sh)
    else:
        sx, sy = int(x), int(y)
    if kind == "double":
        double_click(sx, sy)
        return f"Двойной клик {sx},{sy}"
    if kind == "right":
        right_click(sx, sy)
        return f"Правый клик {sx},{sy}"
    move_and_click(sx, sy)
    return f"Клик {sx},{sy}"


def _step_from_obj(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return str(item)
    if item.get("хз") or item.get("screen") or item.get("need_screen"):
        return "хз"
    if "click" in item:
        xy = item["click"]
        return f"клик {xy[0]} {xy[1]}"
    if "dblclick" in item:
        xy = item["dblclick"]
        return f"двойной клик {xy[0]} {xy[1]}"
    if "rightclick" in item:
        xy = item["rightclick"]
        return f"правый клик {xy[0]} {xy[1]}"
    if "wait" in item:
        return f"жди {item['wait']}"
    if "cmd" in item:
        return str(item["cmd"]).strip()
    if "step" in item:
        return str(item["step"]).strip()
    return json.dumps(item, ensure_ascii=False)


def parse_payload(raw: Any) -> tuple[str, list[str]]:
    if isinstance(raw, list):
        return "", [_step_from_obj(x) for x in raw if _step_from_obj(x)]
    if not isinstance(raw, dict):
        raise ValueError("нужен JSON-массив шагов или объект с steps")
    steps = raw.get("steps") or raw.get("commands") or raw.get("plan") or []
    if isinstance(steps, str):
        steps = [steps]
    goal = str(raw.get("goal") or raw.get("text") or raw.get("command") or "")
    return goal, [_step_from_obj(x) for x in steps if _step_from_obj(x)]


def _run_known_step(step: str) -> str:
    from desktop.commands import _step_delay, execute_simple
    from control.keyboard import hotkey
    from skills.search import _clean_query, open_platform_search, parse_platform_search

    m = CLICK_RE.match(step)
    if m:
        kind_raw = m.group("kind").lower()
        if "двойной" in kind_raw:
            kind = "double"
        elif "правый" in kind_raw:
            kind = "right"
        else:
            kind = "click"
        result = _click_at(float(m.group("x")), float(m.group("y")), kind)
        _step_delay(step)
        return result

    m = WAIT_RE.match(step)
    if m:
        sec = float(m.group("sec").replace(",", "."))
        time.sleep(min(max(sec, 0.0), 5.0))
        return f"Подождал {sec} с"

    m = HOTKEY_RE.match(step)
    if m:
        keys = [k.strip() for k in re.split(r"[+\s]+", m.group("keys")) if k.strip()]
        if keys:
            hotkey(*keys)
            return f"Хоткей {'+'.join(keys)}"

    parsed = parse_platform_search(step)
    if parsed:
        platform, query = parsed
        result = open_platform_search(platform, _clean_query(query) or query)
        _step_delay(step)
        return result

    if step.startswith("загугли "):
        raw = step[len("загугли "):].strip()
        result = open_platform_search("гугл", _clean_query(raw) or raw)
        _step_delay(step)
        return result

    if step.startswith("найди ") and not step.startswith("найди файл "):
        raw = step[len("найди "):].strip()
        result = open_platform_search("яндекс", _clean_query(raw) or raw)
        _step_delay(step)
        return result

    result = execute_simple(step, skip_memory=True, allow_vision=False)
    if result is None:
        return ""
    _step_delay(step)
    return result


def run_plan(steps: list[str], *, plan_id: str = "", goal: str = "") -> dict[str, Any]:
    plan_id = plan_id or uuid.uuid4().hex[:12]
    done: list[dict[str, str]] = []
    need_screen = False
    stopped = ""

    for step in steps:
        step = step.strip()
        if not step:
            continue
        print(f"-> Шина: {step}")
        if _is_need_screen(step) or _is_visual_guess(step):
            need_screen = True
            stopped = step
            done.append({"step": step, "result": "нужен скрин"})
            break
        result = _run_known_step(step)
        if not result:
            need_screen = True
            stopped = step
            done.append({"step": step, "result": "хз"})
            break
        done.append({"step": step, "result": result})
        if _failed(result):
            need_screen = True
            stopped = step
            break

    shot = save_screenshot() if need_screen else None
    texts = [item["result"] for item in done]
    failed = any(_failed(item["result"]) for item in done)
    payload = {
        "id": plan_id,
        "ok": not failed,
        "goal": goal,
        "need_screen": need_screen,
        "stopped_at": stopped,
        "results": done,
        "summary": " → ".join(texts) if texts else "Пустой план",
        "screen": str(SCREEN) if shot else None,
        "image_width": shot.get("image_width") if shot else None,
        "image_height": shot.get("image_height") if shot else None,
        "screen_width": shot.get("screen_width") if shot else None,
        "screen_height": shot.get("screen_height") if shot else None,
    }
    bus_dir()
    tmp = RESULT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(RESULT)
    return payload


def ingest(raw: Any) -> dict[str, Any]:
    plan_id = ""
    if isinstance(raw, dict):
        plan_id = str(raw.get("id") or "")
    goal, steps = parse_payload(raw)
    if not steps:
        raise ValueError("нет шагов")
    return run_plan(steps, plan_id=plan_id, goal=goal)


def _inbox_stable(path: Path) -> bool:
    if not path.is_file():
        return False
    first = (path.stat().st_mtime_ns, path.stat().st_size)
    time.sleep(0.05)
    if not path.is_file():
        return False
    second = (path.stat().st_mtime_ns, path.stat().st_size)
    return first == second and second[1] > 1


def process_inbox() -> Optional[dict[str, Any]]:
    if not _inbox_stable(INBOX):
        return None
    try:
        raw = json.loads(INBOX.read_text(encoding="utf-8-sig"))
    except OSError:
        return None
    except json.JSONDecodeError as exc:
        bad = INBOX.with_name("inbox.bad.json")
        try:
            INBOX.replace(bad)
        except OSError:
            return None
        payload = {"id": "", "ok": False, "need_screen": False, "summary": f"inbox.json не JSON: {exc}", "results": []}
        RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Шина: битый inbox.json отложен в", bad.name)
        return payload
    try:
        INBOX.unlink(missing_ok=True)
    except OSError:
        return None
    try:
        return ingest(raw)
    except Exception as exc:
        payload = {
            "id": str(raw.get("id") if isinstance(raw, dict) else ""),
            "ok": False,
            "need_screen": False,
            "summary": str(exc),
            "results": [],
        }
        RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Шина:", exc)
        return payload


def start_bus_watcher(drain) -> None:
    global _watcher_started
    if _watcher_started:
        return
    if not get("bus.enabled", True):
        return
    bus_dir()
    _watcher_started = True
    interval = max(int(get("bus.poll_ms", 60)), 30) / 1000.0

    def loop() -> None:
        while True:
            try:
                drain()
            except Exception as exc:
                print("Шина:", exc)
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="jarvis-bus").start()
    print("Шина агента:", INBOX)
