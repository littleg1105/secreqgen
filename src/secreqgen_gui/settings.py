"""Settings of the GUI and the place of its data folder."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

HOME_ENV = "SECREQGEN_GUI_HOME"
HOST_ENV = "SECREQGEN_GUI_HOST"
PORT_ENV = "SECREQGEN_GUI_PORT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


@dataclass
class GuiSettings:
    """The settings that the person can change on the settings page."""

    base_url: str = "http://localhost:11434"
    writer_model: str = "gemma4:26b"
    checker_model: str = "qwen3.8:27b"
    requirements_model: str = "gemma4:26b"


def data_home() -> Path:
    """Return the data folder. It is made on first use.

    The environment variable SECREQGEN_GUI_HOME names it. Without it, the
    folder is .secreqgen in the home folder of the person.
    """
    value = os.environ.get(HOME_ENV, "").strip()
    home = Path(value) if value else Path.home() / ".secreqgen"
    home.mkdir(parents=True, exist_ok=True)
    return home


def prepare_environment() -> None:
    """Point the storage of NiceGUI into the data folder.

    Call this before the first import of nicegui. Without it, NiceGUI writes
    a .nicegui folder into the current folder.
    """
    os.environ.setdefault("NICEGUI_STORAGE_PATH", str(data_home() / "nicegui"))


def _settings_file() -> Path:
    return data_home() / "settings.json"


def load_settings() -> GuiSettings:
    """Read the settings. A missing or broken file gives the defaults."""
    settings = GuiSettings()
    try:
        data = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(data, dict):
        return settings
    for field in fields(GuiSettings):
        value = data.get(field.name)
        if isinstance(value, str) and value.strip():
            setattr(settings, field.name, value.strip())
    return settings


def save_settings(settings: GuiSettings) -> None:
    """Write the settings. The write is atomic."""
    target = _settings_file()
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(
        json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, target)


def make_config(settings: GuiSettings | None = None):
    """Return the tool settings with the model server address of the GUI."""
    from secreqgen.config import get_config

    settings = settings or load_settings()
    cfg = get_config()
    cfg.llm.base_url = settings.base_url
    return cfg


def server_host() -> str:
    """Return the address to listen on. The default is this computer only."""
    return os.environ.get(HOST_ENV, "").strip() or DEFAULT_HOST


def server_port() -> int:
    """Return the port to listen on."""
    value = os.environ.get(PORT_ENV, "").strip()
    try:
        return int(value) if value else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT
