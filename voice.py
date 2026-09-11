"""Голос: STT-нормализация и TTS."""
import re
import threading

WAKE_WORDS = ("джарвис", "жарвис", "jarvis", "арвис")

_REPLACEMENTS = [
    (r"\bpython\s+minus\b", "python --version"),
    (r"\bpython\s+version\b", "python --version"),
    (r"\bgit\s+status\b", "git status"),
    (r"\b(\d+)\s*%\s*от\b", r"\1 процентов от"),
    (r"\b(\d+)\s*%\b", r"\1 процентов"),
    (r"\bминус\s+минус\b", "--"),
    (r"\bдва\s+минуса\b", "--"),
]

_tts_engine = None
_tts_lock = threading.Lock()


def normalize_stt(text: str) -> str:
    text = text.lower().strip()
    for pattern, repl in _REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    return re.sub(r"\s+", " ", text).strip()


def extract_wake_command(text: str, wake_words: tuple[str, ...] = WAKE_WORDS) -> tuple[bool, str]:
    text = normalize_stt(text)
    for name in wake_words:
        if name in text:
            parts = text.split(name, 1)
            command = parts[1].strip() if len(parts) > 1 else ""
            return True, command
    return False, text


def _get_tts_engine():
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine
    try:
        import pyttsx3
        _tts_engine = pyttsx3.init()
        from config import get
        _tts_engine.setProperty("rate", int(get("voice.tts_rate", 180)))
        for voice in _tts_engine.getProperty("voices"):
            if "ru" in voice.id.lower() or "russian" in voice.name.lower():
                _tts_engine.setProperty("voice", voice.id)
                break
    except Exception as e:
        print(f"TTS недоступен: {e}")
        _tts_engine = None
    return _tts_engine


def speak(text: str, *, block: bool = False) -> None:
    if not text or not text.strip():
        return
    from config import get
    if not get("voice.tts_enabled", True):
        return

    def _run():
        with _tts_lock:
            engine = _get_tts_engine()
            if engine is None:
                return
            try:
                engine.say(text[:500])
                engine.runAndWait()
            except Exception as e:
                print(f"TTS ошибка: {e}")

    if block:
        _run()
    else:
        threading.Thread(target=_run, daemon=True).start()
