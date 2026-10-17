"""Системное уведомление, когда Джарвис ждёт подтверждения."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from core.config import get
from core.paths import WEB_DIR

ICON = WEB_DIR / "jarvis.ico"

CONFIRM_MARKERS = (
    "подтверд",
    "скажи «да",
    "скажи «нет",
    'скажи "да',
    'скажи "нет',
    "скажи «отправить",
    "скажи «да удали",
    "да удали",
    "да, выполни",
    "да выполни",
    "выполнить?",
    "удалить ",
    "удалить?",
    "отправить»",
    "отправить или",
    "отправь»",
    "проверь чат",
    "ты уверен",
    "точно ",
    "жду подтвержд",
)


def looks_like_confirm(text: str) -> bool:
    low = (text or "").lower()
    if not low:
        return False
    if any(m in low for m in CONFIRM_MARKERS):
        return True
    if "?" in low and any(w in low for w in ("да", "нет", "скажи", "удал", "отправ")):
        return True
    return False


def notify(title: str, text: str) -> None:
    if not get("ui.notify_confirm", True):
        return
    body = " ".join((text or "").split())
    if len(body) > 180:
        body = body[:177] + "..."
    threading.Thread(target=_show, args=(title, body), daemon=True, name="jarvis-toast").start()
    try:
        from ui.app import set_voice_state

        set_voice_state("confirm", body)
    except Exception:
        pass
    try:
        from skills.search import _raise_window_later

        _raise_window_later("JARVIS", wait_sec=2.5)
    except Exception:
        pass


def maybe_confirm(text: str) -> None:
    if looks_like_confirm(text):
        print("Жду подтверждение:", text)
        notify("JARVIS — подтверди", text)


def _show(title: str, body: str) -> None:
    if sys.platform != "win32":
        print(title, body)
        return
    try:
        import winsound

        winsound.MessageBeep(0x00000030)
    except Exception:
        pass
    if _toast(title, body):
        return
    _balloon(title, body)


def _ps(script: str) -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=12,
        )
        return r.returncode == 0
    except Exception as exc:
        print("уведомление:", exc)
        return False


def _toast(title: str, body: str) -> bool:
    title_xml = _xml(title)
    body_xml = _xml(body)
    icon = ICON.as_posix() if ICON.is_file() else ""
    image = f'<image placement="appLogoOverride" src="file:///{icon}"/>' if icon else ""
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
<toast scenario="reminder">
  <visual>
    <binding template="ToastGeneric">
      <text>{title_xml}</text>
      <text>{body_xml}</text>
      {image}
    </binding>
  </visual>
  <audio src="ms-winsoundevent:Notification.Reminder"/>
</toast>
'@)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Jarvis').Show($toast)
"""
    return _ps(script)


def _balloon(title: str, body: str) -> None:
    title_ps = title.replace("'", "''")
    body_ps = body.replace("'", "''")
    icon = str(ICON).replace("'", "''") if ICON.is_file() else ""
    icon_line = (
        f"$n.Icon = New-Object System.Drawing.Icon('{icon}')"
        if icon
        else "$n.Icon = [System.Drawing.SystemIcons]::Warning"
    )
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
{icon_line}
$n.Visible = $true
$n.BalloonTipTitle = '{title_ps}'
$n.BalloonTipText = '{body_ps}'
$n.BalloonTipIcon = 'Warning'
$n.ShowBalloonTip(12000)
Start-Sleep -Seconds 12
$n.Dispose()
"""
    _ps(script)


def _xml(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
