import os
import subprocess
import time

import requests

from core.config import get

DEFAULT_LLAMA_DIRS = (
    r"C:\llama-cpp",
    r"C:\llama.cpp",
    os.path.expandvars(r"%USERPROFILE%\llama-cpp"),
    os.path.expandvars(r"%USERPROFILE%\Documents\llama-cpp"),
    r"D:\llama-cpp",
)


def _llama_dir() -> str:
    configured = str(get("paths.llama_dir", "") or "")
    candidates = [configured, *DEFAULT_LLAMA_DIRS]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "llama-server.exe")):
            return path
    return configured or DEFAULT_LLAMA_DIRS[0]


def _models_dir(llama_dir: str) -> str:
    configured = str(get("paths.models_dir", "") or "")
    if configured and os.path.isdir(configured):
        return configured
    return os.path.join(llama_dir, "models")


def _build_server_cmd(llama_dir: str, *, flash_attn: bool) -> list[str]:
    models_dir = _models_dir(llama_dir)
    model = str(get("llama.model", "Qwen3VL-8B-Instruct-Q8_0.gguf"))
    mmproj = str(get("llama.mmproj", "mmproj-Qwen3VL-8B-Instruct-F16.gguf"))
    cmd = [
        os.path.join(llama_dir, "llama-server.exe"),
        "-m", os.path.join(models_dir, model),
        "--mmproj", os.path.join(models_dir, mmproj),
        "-c", str(get("llama.ctx", 8192)),
        "-ngl", str(get("llama.ngl", 99)),
        "-t", str(get("llama.threads", 8)),
        "--host", str(get("llama.host", "127.0.0.1")),
        "--port", str(get("llama.port", 8080)),
        "--log-verbosity", str(get("llama.log_verbosity", 1)),
        "--reasoning", "off",
    ]
    if flash_attn:
        cmd.extend(["-fa", "on"])
    batch = get("llama.batch", 512)
    if batch:
        cmd.extend(["-b", str(batch)])
    return cmd


LLAMA_DIR = _llama_dir()
MODELS_DIR = _models_dir(LLAMA_DIR)
LLAMA_QUIET = get("llama.quiet", True)
LLAMA_LOG_VERBOSITY = get("llama.log_verbosity", 1)

server_process = None


def is_server_running():
    try:
        response = requests.get("http://127.0.0.1:8080/health", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def start_server():
    global server_process

    if is_server_running():
        print("Сервер нейросети уже запущен.")
        return True

    llama_dir = _llama_dir()
    exe = os.path.join(llama_dir, "llama-server.exe")
    if not os.path.isfile(exe):
        print(f"ОШИБКА: llama-server.exe не найден. Проверь paths.llama_dir в config.yaml (сейчас: {llama_dir})")
        return False

    print("Запускаю локальный сервер с Qwen на GPU...")
    use_fa = bool(get("llama.flash_attn", True))
    last_err = None
    for flash in ((use_fa, False) if use_fa else (False,)):
        try:
            cmd = _build_server_cmd(llama_dir, flash_attn=flash)
            popen_kwargs = {"cwd": llama_dir}
            if LLAMA_QUIET:
                popen_kwargs["stdout"] = subprocess.DEVNULL
                popen_kwargs["stderr"] = subprocess.DEVNULL
            server_process = subprocess.Popen(cmd, **popen_kwargs)
            print("Ожидаю загрузки модели...")
            for _ in range(90):
                time.sleep(1)
                if is_server_running():
                    print("Сервер успешно запущен и готов к работе!")
                    return True
                if server_process.poll() is not None:
                    last_err = f"llama-server вышел с кодом {server_process.returncode}"
                    print(last_err + ("" if flash else " (без flash-attn)"))
                    break
            else:
                print("Внимание: процесс запущен, но сервер пока не отвечает.")
                return True
        except FileNotFoundError:
            print(f"ОШИБКА: Файл llama-server.exe не найден в {llama_dir}")
            return False
        except Exception as e:
            last_err = str(e)
            print(f"Ошибка при запуске сервера: {e}")
    if last_err:
        print(f"Не удалось запустить llama-server: {last_err}")
    return False


def stop_server():
    global server_process
    if server_process is None:
        return

    pid = server_process.pid
    print(f"Останавливаю сервер нейросети (PID: {pid})...", end=" ")
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Готово.")
    except Exception as e:
        print(f"Ошибка при остановке: {e}")
    finally:
        server_process = None


if __name__ == "__main__":
    start_server()
    input("Нажми Enter для выхода (сервер продолжит работать в фоне)...")
