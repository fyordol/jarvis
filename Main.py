import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class _StampedLog:
    def __init__(self, stream):
        self._stream = stream
        self._line_start = True

    def write(self, text: str) -> int:
        out = []
        for part in text.splitlines(keepends=True):
            if self._line_start and part.strip():
                out.append(time.strftime("[%H:%M:%S] "))
            out.append(part)
            self._line_start = part.endswith("\n")
        self._stream.write("".join(out))
        return len(text)

    def flush(self) -> None:
        self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()


def _setup_logging() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / "jarvis.log"
    if path.is_file() and path.stat().st_size > 2_000_000:
        path.replace(log_dir / "jarvis.old.log")
    stream = open(path, "a", encoding="utf-8", buffering=1)
    import faulthandler

    faulthandler.enable(stream)
    sys.stdout = sys.stderr = _StampedLog(stream)
    print(f"\n===== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} =====")


def _running_instance_url() -> str:
    import json
    import urllib.request

    from core.config import get

    url = f"http://{get('ui.host', '127.0.0.1')}:{int(get('ui.port', 8765))}/"
    try:
        with urllib.request.urlopen(url + "api/status", timeout=1.5) as r:
            if json.load(r).get("ok") is True:
                return url
    except Exception:
        pass
    return ""


_setup_logging()
_existing = _running_instance_url()
if _existing:
    from skills.search import open_app_window

    print("Джарвис уже запущен — открываю его окно")
    open_app_window(_existing)
    sys.exit(0)

import threading

import speech_recognition as sr

from desktop.commands import execute
from core.config import get
from control.mouse import enable_dpi_awareness
from ui.remote import start_telegram_bot
from core.server import start_server, stop_server
from ui.app import (
    ensure_launcher,
    execute_locked,
    open_chrome_app,
    push_event,
    request_shutdown,
    set_model_ready,
    set_voice_state,
    start_ui_server,
    wait_shutdown,
)
from control.keyboard import esc_down, esc_generation, esc_held, install_escape_hook, tap_escape
from voice.tts import extract_wake_command, normalize_stt, prewarm

enable_dpi_awareness()

ui_url = start_ui_server()
print("Интерфейс:", ui_url)
if open_chrome_app():
    print("Окно Jarvis открыто")
else:
    print("Открой в Chrome:", ui_url)

ensure_launcher()
prewarm()


def _pick_mic() -> "sr.Microphone | None":
    wanted = str(get("voice.mic_name", "") or "").lower()
    try:
        if wanted:
            for i, name in enumerate(sr.Microphone.list_microphone_names()):
                if wanted in name.lower():
                    print(f"Микрофон: {name} (#{i})")
                    return sr.Microphone(device_index=i)
            print(f"Микрофон «{wanted}» не найден, беру системный по умолчанию")
        return sr.Microphone()
    except (OSError, AttributeError) as e:
        print("Микрофон недоступен:", e)
        return None


recognizer = sr.Recognizer()
recognizer.pause_threshold = float(get("voice.pause_threshold", 1.35))
recognizer.non_speaking_duration = float(get("voice.non_speaking_duration", 0.65))
recognizer.dynamic_energy_threshold = True

mic = _pick_mic()
print("Инициализация Джарвиса...")
if mic is not None:
    try:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.6)
        floor = float(get("voice.min_energy", 80))
        recognizer.energy_threshold = max(recognizer.energy_threshold, floor)
        print(f"Порог громкости микрофона: {recognizer.energy_threshold:.0f}")
    except Exception as e:
        print("Микрофон не открылся:", e)
        mic = None
if mic is None:
    set_voice_state("off", "микрофон не найден")
else:
    set_voice_state("armed")


def _boot_model() -> None:
    if start_server():
        set_model_ready(True)
        try:
            import brain.composer as composer_brain

            if composer_brain.enabled():
                print("Сложные команды: Composer 2.5")
            else:
                print("Composer выключен — нет CURSOR_API_KEY, сложные команды идут в локальный мозг")
        except Exception as exc:
            print("Composer:", exc)
        print("Все системы активны.\n")
    else:
        set_model_ready(False, "Не удалось запустить модель")
        print("КРИТИЧЕСКАЯ ОШИБКА: Сервер не запущен. Зрение и ИИ-агент не будут работать!")


threading.Thread(target=_boot_model, daemon=True).start()

if start_telegram_bot(execute):
    print("Telegram remote: активен")


def _wake_words() -> tuple[str, ...]:
    words = get("voice.wake_words", ["джарвис", "жарвис", "jarvis"])
    if isinstance(words, list):
        return tuple(str(w) for w in words)
    return ("джарвис", "жарвис", "jarvis")


def _recognize(audio) -> str:
    data = recognizer.recognize_google(audio, language="ru-RU", show_all=True)
    alts = []
    if isinstance(data, dict):
        alts = [a.get("transcript", "") for a in data.get("alternative", []) if a.get("transcript")]
    elif isinstance(data, str):
        alts = [data]
    if not alts:
        return ""
    return normalize_stt(alts[0].lower())


def _process_voice(raw: str) -> bool:
    if not raw:
        set_voice_state("armed", "не разобрал")
        return False
    print("Услышал:", raw)
    hit, stripped = extract_wake_command(raw, _wake_words())
    command = raw
    if hit:
        command = stripped
    if not command:
        set_voice_state("armed", "не разобрал")
        return False

    print("Команда:", command)
    push_event("you", command)
    set_voice_state("working", command)
    result = execute_locked(command)
    print(result)
    push_event("bot", result)
    return True


def _record_hold(source) -> "sr.AudioData | None":
    """Пишет звук, пока Esc зажат. Короткий тап возвращает None и проходит в окно."""
    tap_sec = float(get("voice.ptt_tap_sec", 0.28))
    gen = esc_generation()
    frames: list[bytes] = []
    started = time.monotonic()
    announced = False
    print("Esc зажат, пишу")
    while esc_down().is_set() and esc_generation() == gen:
        if not announced and time.monotonic() - started >= tap_sec:
            set_voice_state("listening")
            announced = True
        frames.append(source.stream.read(source.CHUNK))
    if time.monotonic() - started < tap_sec:
        set_voice_state("armed")
        tap_escape()
        return None
    if not frames:
        return None
    return sr.AudioData(b"".join(frames), source.SAMPLE_RATE, source.SAMPLE_WIDTH)


def _ptt_loop() -> None:
    if not install_escape_hook():
        set_voice_state("error", "не смог перехватить Esc")
        wait_shutdown()
        return
    print("Голос: зажми Esc и говори, отпусти — команда уйдёт. Короткий Esc как обычно.")
    set_voice_state("armed")
    seen = 0
    while True:
        try:
            with mic as source:
                while True:
                    if esc_down().is_set() and esc_generation() == seen:
                        while esc_down().is_set():
                            time.sleep(0.03)
                    if not esc_down().is_set() and esc_generation() != seen:
                        seen = esc_generation()
                        if esc_held() < float(get("voice.ptt_tap_sec", 0.28)):
                            tap_escape()
                    esc_down().wait()
                    gen = esc_generation()
                    if gen == seen or not esc_down().is_set():
                        time.sleep(0.02)
                        continue
                    seen = gen
                    audio = _record_hold(source)
                    if audio is None:
                        continue
                    set_voice_state("recognizing")
                    try:
                        text = _recognize(audio)
                    except sr.UnknownValueError:
                        text = ""
                    except sr.RequestError as exc:
                        print("Распознавание недоступно:", exc)
                        set_voice_state("error", "нет связи с распознаванием речи")
                        time.sleep(1)
                        set_voice_state("armed")
                        continue
                    if _process_voice(text):
                        set_voice_state("armed")
        except Exception as exc:
            print("Микрофон:", repr(exc))
            set_voice_state("error", "микрофон отвалился, переподключаю")
            time.sleep(1)


try:
    if mic is not None:
        _ptt_loop()
    else:
        wait_shutdown()

except KeyboardInterrupt:
    print("\nПолучен сигнал остановки. Завершаю работу...")
    request_shutdown()

finally:
    try:
        from brain.composer import close as close_composer

        close_composer()
    except Exception:
        pass
    stop_server()
    print("Джарвис отключен. До свидания.")
