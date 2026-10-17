"""Локальное окно Jarvis: HTTP UI на localhost и ярлык приложения."""
from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from ui.bus import INBOX, RESULT, SCREEN, ingest, process_inbox, start_bus_watcher
from desktop.commands import execute
from core.config import get
from core.paths import REPO_ROOT, WEB_DIR
from skills.search import open_app_window
from voice.tts import extract_wake_command, normalize_stt, speak

ICON_PATH = WEB_DIR / "jarvis.ico"
VBS_PATH = REPO_ROOT / "Jarvis.vbs"

_busy = False
_busy_lock = threading.Lock()
_model_ready = False
_model_error = ""
_shutdown = threading.Event()
_httpd: Optional[ThreadingHTTPServer] = None
_events: list[dict] = []
_events_lock = threading.Lock()
_event_seq = 0
_voice: dict = {"state": "off", "text": ""}
mimetypes.add_type("image/x-icon", ".ico")
mimetypes.add_type("application/manifest+json", ".webmanifest")


def set_model_ready(ok: bool, error: str = "") -> None:
    global _model_ready, _model_error
    _model_ready = bool(ok)
    _model_error = error if not ok else ""


def push_event(role: str, text: str) -> None:
    """Сообщение в ленту окна (голосовые команды и ответы)."""
    global _event_seq
    if not text:
        return
    with _events_lock:
        _event_seq += 1
        _events.append({"id": _event_seq, "role": role, "text": str(text)})
        del _events[:-100]


def set_voice_state(state: str, text: str = "") -> None:
    _voice["state"] = state
    _voice["text"] = text


def _events_since(since: int) -> list[dict]:
    with _events_lock:
        return [e for e in _events if e["id"] > since]


def request_shutdown() -> None:
    _shutdown.set()
    httpd = _httpd
    if httpd is not None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()


def wait_shutdown() -> None:
    _shutdown.wait()


def ui_host_port() -> tuple[str, int]:
    return str(get("ui.host", "127.0.0.1")), int(get("ui.port", 8765))


def _vbs_literal(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def ensure_icon() -> Path:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    favicon = WEB_DIR / "favicon.ico"
    if ICON_PATH.is_file():
        if not favicon.is_file():
            favicon.write_bytes(ICON_PATH.read_bytes())
        return ICON_PATH
    from PIL import Image, ImageDraw, ImageFont

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    gold = (226, 181, 58, 255)
    draw.ellipse((12, 12, size - 12, size - 12), outline=gold, width=10)
    draw.ellipse((36, 36, size - 36, size - 36), outline=(226, 181, 58, 110), width=3)
    font = None
    for font_path in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        if Path(font_path).is_file():
            font = ImageFont.truetype(font_path, 132)
            break
    if font is None:
        font = ImageFont.load_default()
    text = "J"
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 18), text, fill=gold, font=font)
    img.save(ICON_PATH, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    favicon.write_bytes(ICON_PATH.read_bytes())
    return ICON_PATH


def _pythonw() -> Path:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    if candidate.is_file():
        return candidate
    return Path(sys.executable)


def ensure_launcher() -> None:
    ensure_icon()
    pythonw = _pythonw()
    main_py = REPO_ROOT / "Main.py"
    run_cmd = f'"{pythonw}" "{main_py}"'
    vbs = (
        "Set sh = CreateObject(\"WScript.Shell\")\n"
        f"sh.CurrentDirectory = {_vbs_literal(str(REPO_ROOT))}\n"
        f"sh.Run {_vbs_literal(run_cmd)}, 0, False\n"
    )
    VBS_PATH.write_text(vbs, encoding="utf-8")

    desks = [Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"]
    for desk in desks:
        if desk.is_dir():
            _write_shortcut(desk / "Jarvis.lnk")
            break


def _write_shortcut(lnk: Path) -> None:
    ps = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$s = $ws.CreateShortcut({json.dumps(str(lnk))})\n"
        f"$s.TargetPath = {json.dumps(str(VBS_PATH))}\n"
        f"$s.WorkingDirectory = {json.dumps(str(REPO_ROOT))}\n"
        f"$s.IconLocation = {json.dumps(str(ICON_PATH))}\n"
        "$s.Description = \"Jarvis\"\n"
        "$s.WindowStyle = 7\n"
        "$s.Save()\n"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def open_chrome_app() -> bool:
    if not get("ui.open_window", True):
        return False
    host, port = ui_host_port()
    return open_app_window(f"http://{host}:{port}/")


def drain_bus() -> None:
    global _busy
    if not INBOX.is_file():
        return
    with _busy_lock:
        if _busy:
            return
        _busy = True
        try:
            process_inbox()
        finally:
            _busy = False


def execute_locked(text: str) -> str:
    global _busy
    text = normalize_stt(text or "")
    found, command = extract_wake_command(text)
    if found:
        text = command or text
    if not text:
        return ""
    with _busy_lock:
        _busy = True
        try:
            return execute(text)
        finally:
            _busy = False


def _run_command(text: str) -> tuple[int, dict]:
    text = normalize_stt(text or "")
    found, command = extract_wake_command(text)
    if found:
        text = command or text
    if not text:
        return 400, {"ok": False, "error": "Пустая команда"}
    if _busy:
        return 409, {"ok": False, "error": "Уже выполняю другую команду"}
    try:
        result = execute_locked(text)
        try:
            speak(result)
        except Exception:
            pass
        return 200, {"ok": True, "result": str(result), "command": text}
    except Exception as exc:
        return 500, {"ok": False, "error": str(exc)}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._json(200, {
                "ok": True,
                "model_ready": _model_ready,
                "model_error": _model_error,
                "busy": _busy,
                "bus": INBOX.is_file() or RESULT.is_file(),
                "model": str(get("llama.model", "")).replace(".gguf", ""),
                "voice": _voice,
            })
            return
        if parsed.path == "/api/events":
            try:
                since = int(parse_qs(parsed.query).get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._json(200, {"ok": True, "events": _events_since(since), "voice": _voice, "last": _event_seq})
            return
        if parsed.path == "/api/bus":
            if not RESULT.is_file():
                self._json(404, {"ok": False, "error": "нет результата"})
                return
            try:
                self._json(200, json.loads(RESULT.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/bus/screen":
            if not SCREEN.is_file():
                self.send_error(404)
                return
            self._send(200, SCREEN.read_bytes(), "image/jpeg")
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        global _busy
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            data = {}
        if parsed.path == "/api/command":
            code, payload = _run_command(str(data.get("text") or ""))
            self._json(code, payload)
            return
        if parsed.path in ("/api/plan", "/api/bus"):
            with _busy_lock:
                if _busy:
                    self._json(409, {"ok": False, "error": "Уже выполняю другую команду"})
                    return
                _busy = True
                try:
                    payload = ingest(data)
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:
                    self._json(500, {"ok": False, "error": str(exc)})
                    return
                finally:
                    _busy = False
            self._json(200, payload)
            return
        if parsed.path == "/api/shutdown":
            self._json(200, {"ok": True})
            threading.Thread(target=request_shutdown, daemon=True).start()
            return
        self._json(404, {"ok": False, "error": "not found"})

    def _serve_static(self, url_path: str) -> None:
        rel = unquote(url_path).lstrip("/")
        if not rel or rel == "/":
            rel = "index.html"
        target = (WEB_DIR / rel).resolve()
        if not target.is_relative_to(WEB_DIR.resolve()):
            self._json(403, {"ok": False, "error": "forbidden"})
            return
        if not target.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)


def start_ui_server() -> str:
    global _httpd
    ensure_icon()
    host, port = ui_host_port()
    try:
        _httpd = ThreadingHTTPServer((host, port), _Handler)
    except OSError as exc:
        raise SystemExit(f"Не удалось открыть интерфейс на http://{host}:{port}/ — порт занят? ({exc})") from exc
    thread = threading.Thread(target=_httpd.serve_forever, daemon=True)
    thread.start()
    start_bus_watcher(drain_bus)
    return f"http://{host}:{port}/"


if __name__ == "__main__":
    url = start_ui_server()
    set_model_ready(True)
    print("UI", url)
    open_chrome_app()
    wait_shutdown()
