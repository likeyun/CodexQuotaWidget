"""Small Windows integrations for the desktop widget.

Settings contain presentation preferences only. Login credentials are owned by
Codex and must never be copied into this file or into the startup command.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Sequence


STARTUP_NAME = "CodexQuota"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULT_SETTINGS: dict[str, Any] = {
    "pinned": True,
    "interval": 60,
    "opacity": 1.0,
    "compact": False,
    "theme": "dark",
}


def settings_path() -> Path:
    """Return the per-user preferences path without creating it."""
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "CodexQuota" / "settings.json"


def _sanitize_settings(values: object) -> dict[str, Any]:
    """Keep the small, explicitly supported set of non-secret UI preferences."""
    if not isinstance(values, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("pinned", "compact"):
        if isinstance(values.get(key), bool):
            result[key] = values[key]
    for key in ("x", "y"):
        value = values.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= 100_000:
            result[key] = value
    position = values.get("position")
    if (
        isinstance(position, (list, tuple))
        and len(position) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) and abs(n) <= 100_000 for n in position)
    ):
        result["position"] = list(position)
    interval = values.get("interval")
    if isinstance(interval, int) and not isinstance(interval, bool) and 15 <= interval <= 3600:
        result["interval"] = interval
    opacity = values.get("opacity")
    if isinstance(opacity, (int, float)) and not isinstance(opacity, bool):
        if math.isfinite(opacity) and 0.3 <= opacity <= 1.0:
            result["opacity"] = float(opacity)
    if values.get("theme") in ("dark", "light", "system"):
        result["theme"] = values["theme"]
    return result


def load_settings() -> dict[str, Any]:
    """Load preferences, falling back to defaults for missing/corrupt files."""
    settings = DEFAULT_SETTINGS.copy()
    try:
        path = settings_path()
        if path.stat().st_size > 64 * 1024:
            return settings
        settings.update(_sanitize_settings(json.loads(path.read_text(encoding="utf-8-sig"))))
    except (OSError, ValueError, TypeError, RecursionError):
        pass
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    """Atomically save supported UI preferences, dropping all other fields.

    OSError is left to the caller so the UI can report a write failure if useful.
    The previous settings file survives an interrupted or failed write.
    """
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = DEFAULT_SETTINGS.copy()
    payload.update(_sanitize_settings(settings))
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix="settings-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def enable_glass(hwnd: int, dark: bool = True) -> bool:
    """Request documented DWM blur only inside the widget's rounded surface.

    The blur region is inset slightly from the antialiased QPainter contour.
    Windows versions that ignore legacy blur retain the painter's translucent
    glass fallback. No private composition interfaces or HWND region masks are
    used, so transparent corners keep their smooth edge.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    from ctypes import wintypes

    handle = wintypes.HWND(int(hwnd))
    region = None
    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        set_attribute = dwm.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long
        corner_preference = ctypes.c_int(1)  # DWMWCP_DONOTROUND; QPainter draws the smooth edge.
        set_attribute(handle, 33, ctypes.byref(corner_preference), ctypes.sizeof(corner_preference))
        dark_value = wintypes.BOOL(bool(dark))
        if set_attribute(handle, 20, ctypes.byref(dark_value), ctypes.sizeof(dark_value)) != 0:
            set_attribute(handle, 19, ctypes.byref(dark_value), ctypes.sizeof(dark_value))

        class DwmBlurBehind(ctypes.Structure):
            _fields_ = [
                ("flags", wintypes.DWORD),
                ("enable", wintypes.BOOL),
                ("region", wintypes.HRGN),
                ("transition_on_maximized", wintypes.BOOL),
            ]

        class Rect(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_client_rect = user32.GetClientRect
        get_client_rect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
        get_client_rect.restype = wintypes.BOOL
        client = Rect()
        if not get_client_rect(handle, ctypes.byref(client)):
            return False
        width, height = client.right - client.left, client.bottom - client.top
        compact = width > height * 2
        scale = width / (200 if compact else 320)
        margin = round((4 if compact else 7) * scale)
        radius = round((31 if compact else 49) * scale)
        inset = 1
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        create_round_rect = gdi.CreateRoundRectRgn
        create_round_rect.argtypes = [ctypes.c_int] * 6
        create_round_rect.restype = wintypes.HRGN
        region = create_round_rect(margin + inset, margin + inset,
                                   width - margin - inset, height - margin - inset,
                                   max(2, 2 * (radius - inset)), max(2, 2 * (radius - inset)))
        if not region:
            return False
        blur = DwmBlurBehind(0x1 | 0x2, True, region, False)  # Enable + rounded blur region.
        enable_blur = dwm.DwmEnableBlurBehindWindow
        enable_blur.argtypes = [wintypes.HWND, ctypes.POINTER(DwmBlurBehind)]
        enable_blur.restype = ctypes.c_long
        return enable_blur(handle, ctypes.byref(blur)) == 0
    except (OSError, AttributeError, TypeError):
        return False
    finally:
        if region:
            try:
                ctypes.WinDLL("gdi32", use_last_error=True).DeleteObject(region)
            except (OSError, AttributeError):
                pass

def _startup_argv(executable: str | os.PathLike[str] | Sequence[str] | None) -> list[str]:
    if executable is not None:
        if isinstance(executable, (str, os.PathLike)):
            return [os.fspath(executable)]
        values = [os.fspath(value) for value in executable]
        if not values:
            raise ValueError("The startup command cannot be empty")
        return values
    if getattr(sys, "frozen", False):
        return [sys.executable]
    interpreter = Path(sys.executable)
    pythonw = interpreter.with_name("pythonw.exe")
    if pythonw.is_file():
        interpreter = pythonw
    # The absolute launcher works from Windows Run's arbitrary working directory.
    # It imports the same package as `pythonw -m quota_widget` in a source checkout.
    launcher = Path(__file__).resolve().parent.parent / "main.py"
    return [str(interpreter), str(launcher)]


def toggle_startup(
    enabled: bool,
    executable: str | os.PathLike[str] | Sequence[str] | None = None,
) -> bool:
    """Update this user's Windows Run entry, only after an explicit UI action.

    An optional executable/argv allows a packaged launcher or a custom command.
    No administrator permissions or machine-wide registry changes are needed.
    Return False when Windows registry access is unavailable or fails.
    """
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                command = subprocess.list2cmdline(_startup_argv(executable))
                winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except (OSError, ValueError, TypeError):
        return False


def is_startup_enabled() -> bool:
    """Check whether this user's widget startup entry is present."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, STARTUP_NAME)
            return kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and bool(value)
    except OSError:
        return False

