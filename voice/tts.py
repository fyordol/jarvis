"""Голос: STT-нормализация и TTS. Офлайн-нейроголоса Silero/Piper, онлайн edge-tts, запасной pyttsx3."""
import asyncio
import hashlib
import queue
import re
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

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

from core.paths import BUS_DIR, TTS_MODELS_DIR

CACHE_DIR = BUS_DIR / "tts_cache"
MODELS_DIR = TTS_MODELS_DIR

LATIN_WORDS = {
    "discord": "дискорд", "telegram": "телеграм", "youtube": "ютуб", "steam": "стим", "chrome": "хром",
    "google": "гугл", "github": "гитхаб", "python": "пайтон", "jarvis": "джарвис", "gta": "гэ тэ а",
    "windows": "виндоус", "spotify": "спотифай", "cursor": "курсор", "obs": "о бэ эс", "osu": "осу",
    "ok": "окей", "pdf": "пэ дэ эф", "usb": "ю эс би", "wifi": "вай фай", "ps5": "пэ эс пять",
    "xbox": "иксбокс", "series": "сириес", "rockstar": "рокстар", "games": "геймс", "lofi": "лоу фай",
    "vs": "вэ эс", "code": "код", "txt": "тэ икс тэ", "md": "эм дэ", "exe": "экзешник", "mb": "мегабайт",
    "gb": "гигабайт", "ram": "оперативка", "cpu": "процессор", "gpu": "видеокарта", "i": "ай", "the": "зе",
}
LATIN_LETTERS = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
    "u": "у", "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
}
LATIN_NAMES = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай",
    "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
    "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл ю", "x": "икс", "y": "уай", "z": "зед",
}
CYR_NAMES = {
    "Б": "бэ", "В": "вэ", "Г": "гэ", "Д": "дэ", "Ж": "жэ", "З": "зэ", "К": "ка", "Л": "эль", "М": "эм",
    "Н": "эн", "П": "пэ", "Р": "эр", "С": "эс", "Т": "тэ", "Ф": "эф", "Х": "ха", "Ц": "цэ", "Ч": "че",
    "Ш": "ша", "Щ": "ща",
}
UNITS = (
    (r"°\s*[CС]", " градусов"), (r"\bкм/ч\b", " километров в час"), (r"\bГБ\b", " гигабайт"),
    (r"\bМБ\b", " мегабайт"), (r"\bКБ\b", " килобайт"), (r"\bруб\.?(?=\s|$)", " рублей"),
    (r"%", " процентов"), (r"№\s*", "номер "), (r"\bт\.е\.", "то есть"), (r"\bт\.д\.", "так далее"),
)
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")

_queue: "queue.Queue[tuple[str, threading.Event]]" = queue.Queue()
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_stop = threading.Event()
_speaking = threading.Event()
_silero = None
_piper = None
_pyttsx = None
_failed_engines: set[str] = set()


def normalize_stt(text: str) -> str:
    text = text.lower().strip()
    for pattern, repl in _REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    return re.sub(r"\s+", " ", text).strip()


WAKE_VARIANTS = (
    "джарвис", "жарвис", "джарвиз", "джервис", "джарвес", "джарвисс", "жарвиз", "дарвис", "гарвис",
    "джарви", "jarvis", "jervis", "jarvi", "арвис", "чарвис", "джавис", "джарис",
)


def _wake_score(token: str, words: tuple[str, ...]) -> float:
    from difflib import SequenceMatcher

    return max(SequenceMatcher(None, token, w).ratio() for w in words)


def extract_wake_command(text: str, wake_words: tuple[str, ...] = WAKE_WORDS) -> tuple[bool, str]:
    """Ищет обращение «Джарвис» (с поправкой на ошибки распознавания) и возвращает команду после него."""
    text = normalize_stt(text)
    words = tuple(dict.fromkeys((*wake_words, *WAKE_VARIANTS)))
    for name in sorted(words, key=len, reverse=True):
        idx = text.find(name)
        if idx != -1:
            end = text.find(" ", idx + len(name))
            command = text[end:].strip(" ,.!?") if end != -1 else ""
            return True, command
    tokens = text.split()
    for i, raw in enumerate(tokens[:4]):
        token = raw.strip(",.!?")
        if len(token) >= 5 and _wake_score(token, words) >= 0.78:
            return True, " ".join(tokens[i + 1:]).strip(" ,.!?")
    return False, text


def _cfg(key: str, default):
    from core.config import get

    return get(key, default)


def clean_for_speech(text: str, limit: Optional[int] = None) -> str:
    """Убирает то, что глупо звучит вслух: markdown, ссылки, пути, стрелки."""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[A-Za-z]:[\\/][^\s,;]+", " ", t)
    t = re.sub(r"[*_#`>|]+", " ", t)
    t = re.sub(r"(?i)\bсэр\b", " ", t)
    t = t.replace("→", ", ").replace(" — ", ", ").replace("…", ".")
    t = re.sub(r"\((?:\+\d+/-\d+|[^()]{0,3})\)", " ", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r",\s*([.!?])", r"\1", t)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip(" ,")
    limit = limit or int(_cfg("voice.tts_max_chars", 350))
    if len(t) > limit:
        cut = t[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[: end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "."
    return t


def _n2w(raw: str) -> str:
    from num2words import num2words

    raw = raw.replace(",", ".")
    try:
        if "." in raw:
            whole, frac = raw.split(".", 1)
            return f"{num2words(int(whole), lang='ru')} и {num2words(int(frac), lang='ru')}" if frac else num2words(int(whole), lang="ru")
        return num2words(int(raw), lang="ru")
    except (ValueError, OverflowError):
        return raw


def speechify(text: str) -> str:
    """Готовит текст для Silero: числа и латиница словами, единицы измерения."""
    t = text
    for pattern, repl in UNITS:
        t = re.sub(pattern, repl, t)

    def date_repl(m: re.Match) -> str:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{_n2w(str(day))} {MONTHS[month - 1]}"
        return m.group(0)

    t = re.sub(r"\b(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\b", date_repl, t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{_n2w(m.group(1))} {'ноль ' + _n2w(m.group(2)[1]) if m.group(2).startswith('0') and m.group(2) != '00' else ('ровно' if m.group(2) == '00' else _n2w(m.group(2)))}", t)
    def latin_word(w: str) -> str:
        w = w.lower()
        if w in LATIN_WORDS:
            return LATIN_WORDS[w]
        if len(w) <= 3:
            return " ".join(LATIN_NAMES.get(c, c) for c in w)
        for a, b in (("sh", "ш"), ("ch", "ч"), ("th", "з"), ("ph", "ф"), ("oo", "у"), ("ee", "и")):
            w = w.replace(a, b)
        return "".join(LATIN_LETTERS.get(c, c) for c in w)

    def latin_token(m: re.Match) -> str:
        letters, digits = m.group(1), m.group(2)
        whole = (letters + digits).lower()
        if whole in LATIN_WORDS:
            return f" {LATIN_WORDS[whole]} "
        return f" {latin_word(letters)} {digits} "

    def cyr_abbr(m: re.Match) -> str:
        w = m.group(0)
        if sum(c in "АЕЁИОУЫЭЮЯ" for c in w) > 1:
            return w
        return " ".join(CYR_NAMES.get(c, c.lower()) for c in w)

    t = re.sub(r"([A-Za-z]+)(\d*)", latin_token, t)
    t = re.sub(r"\b[А-ЯЁ]{2,4}\b", cyr_abbr, t)
    t = re.sub(r"(?<![\w.])\+(\d)", r"плюс \1", t)
    t = re.sub(r"(?<![\w.])[-−](\d)", r"минус \1", t)
    t = re.sub(r"\d+(?:[.,]\d+)?", lambda m: f" {_n2w(m.group(0))} ", t)
    t = re.sub(r"[^\w\s.,!?:;\-ёЁ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _cache_path(engine: str, text: str, ext: str) -> Path:
    key = hashlib.md5(f"{engine}|{_engine_signature(engine)}|{text}".encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.{ext}"


def _engine_signature(engine: str) -> str:
    if engine == "silero":
        return str(_cfg("voice.silero_speaker", "aidar"))
    if engine == "piper":
        return str(_cfg("voice.piper_voice", "ru_RU-dmitri-medium"))
    if engine == "edge":
        return f"{_cfg('voice.edge_voice', 'ru-RU-DmitryNeural')}{_cfg('voice.edge_rate', '+8%')}{_cfg('voice.edge_pitch', '-4Hz')}"
    return ""


def _write_wav(path: Path, samples: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples)


def _load_silero():
    global _silero
    if _silero is None:
        import torch

        path = MODELS_DIR / str(_cfg("voice.silero_model", "silero_v4_ru.pt"))
        if not path.is_file():
            raise FileNotFoundError(path)
        torch.set_num_threads(int(_cfg("voice.silero_threads", 4)))
        _silero = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        _silero.to(torch.device("cpu"))
    return _silero


def _synth_silero(text: str, path: Path) -> None:
    model = _load_silero()
    rate = 48000
    chunks = [c for c in re.split(r"(?<=[.!?])\s+", speechify(text)) if re.search(r"\w", c)]
    pcm = b""
    for chunk in chunks:
        audio = model.apply_tts(
            text=chunk[:900], speaker=str(_cfg("voice.silero_speaker", "aidar")),
            sample_rate=rate, put_accent=True, put_yo=True,
        )
        pcm += (audio.clamp(-1, 1) * 32767).short().numpy().tobytes() + b"\x00\x00" * int(rate * 0.12)
    if not pcm:
        raise ValueError("пустой текст")
    _write_wav(path, pcm, rate)


def _synth_piper(text: str, path: Path) -> None:
    global _piper
    if _piper is None:
        from piper import PiperVoice

        model = MODELS_DIR / f"{_cfg('voice.piper_voice', 'ru_RU-dmitri-medium')}.onnx"
        if not model.is_file():
            raise FileNotFoundError(model)
        _piper = PiperVoice.load(str(model))
    with wave.open(str(path), "wb") as w:
        _piper.synthesize_wav(text, w)


def _synth_edge(text: str, path: Path) -> None:
    import edge_tts

    async def run() -> None:
        await edge_tts.Communicate(
            text, str(_cfg("voice.edge_voice", "ru-RU-DmitryNeural")),
            rate=str(_cfg("voice.edge_rate", "+8%")), pitch=str(_cfg("voice.edge_pitch", "-4Hz")),
        ).save(str(path))

    last: Optional[Exception] = None
    for _ in range(int(_cfg("voice.edge_retries", 2)) + 1):
        try:
            asyncio.run(asyncio.wait_for(run(), timeout=float(_cfg("voice.edge_timeout_sec", 10))))
            if path.is_file() and path.stat().st_size > 0:
                return
        except Exception as e:
            last = e
    raise last or RuntimeError("edge-tts без звука")


SYNTH: dict[str, tuple[Callable[[str, Path], None], str]] = {
    "silero": (_synth_silero, "wav"),
    "piper": (_synth_piper, "wav"),
    "edge": (_synth_edge, "mp3"),
}


def _engines() -> list[str]:
    raw = _cfg("voice.tts_engines", ["silero", "piper", "edge", "pyttsx3"])
    if isinstance(raw, str):
        raw = [raw]
    return [str(e).strip().lower() for e in raw if str(e).strip()]


_synth_lock = threading.Lock()


def synthesize(text: str) -> Optional[Path]:
    """Синтезирует фразу первым рабочим движком. Возвращает путь к аудио или None (тогда pyttsx3)."""
    with _synth_lock:
        return _synthesize(text)


def _synthesize(text: str) -> Optional[Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for engine in _engines():
        if engine not in SYNTH or engine in _failed_engines:
            continue
        fn, ext = SYNTH[engine]
        path = _cache_path(engine, text, ext)
        if path.is_file() and path.stat().st_size > 0:
            return path
        tmp = path.with_name(path.stem + ".part." + ext)
        try:
            fn(text, tmp)
            tmp.replace(path)
            return path
        except Exception as e:
            print(f"TTS {engine} не сработал: {e}")
            tmp.unlink(missing_ok=True)
            if isinstance(e, (ImportError, FileNotFoundError)):
                _failed_engines.add(engine)
    return None


def _mci(cmd: str) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(128)
    ctypes.windll.winmm.mciSendStringW(cmd, buf, 127, 0)
    return buf.value


def _play(path: Path) -> None:
    alias = f"jv{threading.get_ident()}"
    _mci(f'open "{path}" type mpegvideo alias {alias}')
    volume = int(_cfg("voice.tts_volume", 1000))
    _mci(f"setaudio {alias} volume to {max(0, min(volume, 1000))}")
    _mci(f"play {alias}")
    try:
        time.sleep(0.05)
        while not _stop.is_set() and _mci(f"status {alias} mode") == "playing":
            time.sleep(0.04)
    finally:
        _mci(f"stop {alias}")
        _mci(f"close {alias}")


def _say_pyttsx3(text: str) -> None:
    global _pyttsx
    try:
        if _pyttsx is None:
            import pyttsx3

            _pyttsx = pyttsx3.init()
            _pyttsx.setProperty("rate", int(_cfg("voice.tts_rate", 180)))
            for v in _pyttsx.getProperty("voices"):
                if "ru" in v.id.lower() or "russian" in v.name.lower():
                    _pyttsx.setProperty("voice", v.id)
                    break
        _pyttsx.say(text)
        _pyttsx.runAndWait()
    except Exception as e:
        print(f"TTS pyttsx3 ошибка: {e}")


def _say_now(text: str) -> None:
    path = synthesize(text)
    if path is not None:
        _play(path)
    elif "pyttsx3" in _engines():
        _say_pyttsx3(text)


def _loop() -> None:
    while True:
        text, done = _queue.get()
        try:
            if not _stop.is_set():
                _speaking.set()
                _say_now(text)
        except Exception as e:
            print(f"TTS ошибка: {e}")
        finally:
            _speaking.clear()
            done.set()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, daemon=True, name="jarvis-tts")
            _worker.start()


def is_speaking() -> bool:
    return _speaking.is_set() or not _queue.empty()


def stop_speaking() -> None:
    _stop.set()
    while not _queue.empty():
        try:
            _, done = _queue.get_nowait()
            done.set()
        except queue.Empty:
            break
    time.sleep(0.1)
    _stop.clear()


def wait_speech(timeout: float = 30.0) -> None:
    end = time.time() + timeout
    while is_speaking() and time.time() < end:
        time.sleep(0.05)


def speak(text: str, *, block: bool = False) -> None:
    if not _cfg("voice.tts_enabled", True):
        return
    clean = clean_for_speech(text)
    if not clean:
        return
    _ensure_worker()
    done = threading.Event()
    _queue.put((clean, done))
    if block:
        done.wait(timeout=60)


def narrate(text: str) -> None:
    """Короткий комментарий по ходу работы, если включён voice.narrate."""
    if _cfg("voice.narrate", True):
        speak(clean_for_speech(text, 140))


def prewarm(phrases: tuple[str, ...] = (
    "Да, слушаю", "Секунду", "Ищу в сети", "Выполняю", "Смотрю на экран", "Готово",
)) -> None:
    """Грузит модель и заранее синтезирует частые фразы, чтобы они звучали мгновенно."""
    if not _cfg("voice.tts_enabled", True):
        return

    def run() -> None:
        for p in phrases:
            synthesize(clean_for_speech(p))

    threading.Thread(target=run, daemon=True, name="jarvis-tts-prewarm").start()
