"""Tiny JSON-file inventory of known devices and app settings."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("AVDASH_DATA", Path(__file__).resolve().parent.parent / "data"))
DEVICES = DATA_DIR / "devices.json"
SETTINGS = DATA_DIR / "settings.json"
_lock = threading.Lock()

DEFAULT_SETTINGS = {
    # Credentials tried (in order) on devices we have no password for.
    # Avigilon H4-era cameras ship as admin/admin; newer ones have no ONVIF user until one is created.
    "default_credentials": [
        {"username": "admin", "password": "admin"},
        {"username": "admin", "password": ""},
        {"username": "administrator", "password": ""},
    ],
    "discovery_timeout": 3,
    "scan_targets": [],
    "avigilon_only": False,
}


def device_id(urn: str) -> str:
    return hashlib.sha1(urn.encode()).hexdigest()[:12]


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def load_devices() -> dict:
    with _lock:
        return _read(DEVICES, {})


def save_devices(devs: dict):
    with _lock:
        _write(DEVICES, devs)


def update_device(dev_id: str, **fields) -> dict:
    with _lock:
        devs = _read(DEVICES, {})
        d = devs.setdefault(dev_id, {"id": dev_id})
        d.update(fields)
        _write(DEVICES, devs)
        return d


def delete_device(dev_id: str):
    with _lock:
        devs = _read(DEVICES, {})
        devs.pop(dev_id, None)
        _write(DEVICES, devs)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(_read(SETTINGS, {}))
    return s


def save_settings(s: dict):
    with _lock:
        _write(SETTINGS, s)
