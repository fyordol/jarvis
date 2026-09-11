import subprocess
import time
import os

import requests

from config import get

LLAMA_DIR = get("paths.llama_dir", r"C:\llama-cpp")
MODELS_DIR = get("paths.models_dir", os.path.join(LLAMA_DIR, "models"))
LLAMA_QUIET = get("llama.quiet", True)
LLAMA_LOG_VERBOSITY = get("llama.log_verbosity", 1)

SERVER_CMD = [
    os.path.join(LLAMA_DIR, "llama-server.exe"),
    "-m", os.path.join(MODELS_DIR, "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"),
    "--mmproj", os.path.join(MODELS_DIR, "mmproj-F16.gguf"),
    "-c", "4096",
    "--host", "0.0.0.0",
    "--port", "8080",
    "--log-verbosity", str(LLAMA_LOG_VERBOSITY),
]
# -----------------------

server_process = None

def is_server_running():
    try:
        response = requests.get("http://127.0.0.1:8080/health", timeout=2)
        return response.status_code == 200
    except:
        return False

def start_server():
    global server_process
    
    if is_server_running():
        print("Сервер нейросети уже запущен.")
        return True

    print("Запускаю локальный сервер с Qwen...")
    try:
        popen_kwargs = {"cwd": LLAMA_DIR}
        if LLAMA_QUIET:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL

        server_process = subprocess.Popen(SERVER_CMD, **popen_kwargs)

        print("Ожидаю загрузки модели (это может занять 10-30 секунд)...")
        for _ in range(60):
            time.sleep(1)
            if is_server_running():
                print("Сервер успешно запущен и готов к работе!")
                return True

        print("Внимание: Процесс запущен, но сервер пока не отвечает.")
        return True

    except FileNotFoundError:
        print("ОШИБКА: Файл 'llama-server.exe' не найден в папке C:\llama-cpp")
        return False
    except Exception as e:
        print(f"Ошибка при запуске сервера: {e}")
        return False

def stop_server():
    """Убивает сервер при закрытии Джарвиса"""
    global server_process
    if server_process is None:
        return
    
    pid = server_process.pid
    print(f"Останавливаю сервер нейросети (PID: {pid})...", end=" ")
    try:
        # /T убивает дочерние процессы, /F убивает принудительно
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        print("Готово.")
    except Exception as e:
        print(f"Ошибка при остановке: {e}")
    finally:
        server_process = None

if __name__ == "__main__":
    start_server()
    input("Нажми Enter для выхода (сервер продолжит работать в фоне)...")