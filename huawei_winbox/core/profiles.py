"""Lista de equipamentos salvos (como a aba *Managed* do WinBox).

Os perfis ficam em ``~/.huaweibox/devices.json``. A senha é armazenada
ofuscada em base64 apenas por conveniência — para guardar a senha de
forma segura, deixe o campo em branco e digite-a a cada conexão.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, asdict
from pathlib import Path

_CONFIG_DIR = Path.home() / ".huaweibox"
_DEVICES_FILE = _CONFIG_DIR / "devices.json"


@dataclass
class DeviceProfile:
    name: str
    host: str
    username: str
    password: str = ""
    port: int = 22
    save_password: bool = False


def _encode(password: str) -> str:
    return base64.b64encode(password.encode()).decode()


def _decode(blob: str) -> str:
    try:
        return base64.b64decode(blob.encode()).decode()
    except Exception:
        return ""


def load_profiles() -> list[DeviceProfile]:
    if not _DEVICES_FILE.exists():
        return []
    try:
        data = json.loads(_DEVICES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    profiles = []
    for item in data:
        item["password"] = _decode(item.get("password", ""))
        profiles.append(DeviceProfile(**item))
    return profiles


def save_profiles(profiles: list[DeviceProfile]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = []
    for p in profiles:
        item = asdict(p)
        item["password"] = _encode(p.password) if p.save_password else ""
        data.append(item)
    _DEVICES_FILE.write_text(json.dumps(data, indent=2))


def upsert_profile(profile: DeviceProfile) -> None:
    profiles = load_profiles()
    profiles = [p for p in profiles if not (p.host == profile.host and
                                            p.username == profile.username)]
    profiles.insert(0, profile)
    save_profiles(profiles)


def remove_profile(host: str, username: str) -> None:
    profiles = [p for p in load_profiles()
                if not (p.host == host and p.username == username)]
    save_profiles(profiles)
