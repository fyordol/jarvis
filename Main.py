import speech_recognition as sr

from commands import execute
from config import get
from mouse_control import enable_dpi_awareness
from remote import start_telegram_bot
from server_manager import start_server, stop_server
from voice import extract_wake_command, normalize_stt, speak

enable_dpi_awareness()

recognizer = sr.Recognizer()
recognizer.pause_threshold = 2.1
recognizer.non_speaking_duration = 1
mic = sr.Microphone()

print("Инициализация Джарвиса...")
server_ready = start_server()

if not server_ready:
    print("КРИТИЧЕСКАЯ ОШИБКА: Сервер не запущен. Зрение и ИИ-агент не будут работать!")
else:
    print("Все системы активны.\n")

if start_telegram_bot(execute):
    print("Telegram remote: активен")


def listen() -> str:
    with mic as source:
        print("Слушаю...")
        audio = recognizer.listen(source)
    try:
        text = recognizer.recognize_google(audio, language="ru-RU")
        return normalize_stt(text.lower())
    except Exception:
        return ""


def _wake_words() -> tuple[str, ...]:
    words = get("voice.wake_words", ["джарвис", "жарвис", "jarvis"])
    if isinstance(words, list):
        return tuple(str(w) for w in words)
    return ("джарвис", "жарвис", "jarvis")


def _process_voice(raw: str) -> None:
    print("Услышал:", raw)
    if not raw:
        return

    require_wake = get("voice.require_wake_word", True)
    if require_wake:
        found, command = extract_wake_command(raw, _wake_words())
        if not found:
            print("(нет wake word, игнорирую)")
            return
        if not command:
            speak("Да сэр, слушаю")
            print("Да сэр, слушаю...")
            command = listen()
            if not command:
                return
    else:
        command = raw

    print("Команда:", command)
    result = execute(command)
    print(result)
    speak(result)


try:
    while True:
        _process_voice(listen())

except KeyboardInterrupt:
    print("\nПолучен сигнал остановки. Завершаю работу...")

finally:
    stop_server()
    print("Джарвис отключен. До свидания, сэр.")
