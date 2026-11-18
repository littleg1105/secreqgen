"""Settings of the GUI and the place of its data folder."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

HOME_ENV = "SECREQGEN_GUI_HOME"
HOST_ENV = "SECREQGEN_GUI_HOST"
PORT_ENV = "SECREQGEN_GUI_PORT"
ALLOWED_HOSTS_ENV = "SECREQGEN_GUI_ALLOWED_HOSTS"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


@dataclass
class GuiSettings:
    """The settings that the person can change on the settings page."""

    base_url: str = "http://localhost:11434"
    writer_model: str = "gemma4:26b"
    checker_model: str = "qwen3.8:27b"
    requirements_model: str = "gemma4:26b"


def expand_path(text: str | Path) -> Path:
    """Make a path from typed text. A leading ~ means the home folder.

    The links are not resolved and the path need not exist.
    """
    return Path(str(text).strip()).expanduser()


def show_path(path: str | Path | None) -> str:
    """Return the text of a path for the person.

    The home folder and the paths below it start with ~. Any other path stays
    as it is. An empty value gives an empty text. This function never raises.
    """
    try:
        text = str(path).strip() if path is not None else ""
        if not text:
            return ""
        value = Path(text)
        home = Path.home()
        if value == home:
            return "~"
        if home in value.parents:
            return "~/" + value.relative_to(home).as_posix()
        return text
    except (OSError, RuntimeError, ValueError):
        return str(path) if path is not None else ""


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


_SAVES = [0]  # how many times this process saved the settings


def saves() -> int:
    """Return how many times the settings were saved since the GUI started.

    A page that shows a value of the settings keeps this number. When the
    number changes, the page reads the settings again. This costs no file
    read.
    """
    return _SAVES[0]


def save_settings(settings: GuiSettings) -> None:
    """Write the settings. The write is atomic."""
    target = _settings_file()
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(
        json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, target)
    _SAVES[0] += 1


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


def allowed_hosts() -> list[str]:
    """Return the host names that a request can name in its Host header.

    The server refuses a request with a different name. So a page of a
    different website cannot reach the GUI through a name that points to
    this computer (DNS rebinding). The list holds the two local names, the
    address that the server listens on, and the names of the environment
    variable SECREQGEN_GUI_ALLOWED_HOSTS (separated by commas).
    """
    names = list(LOCAL_HOSTS)
    host = server_host()
    more = os.environ.get(ALLOWED_HOSTS_ENV, "").split(",")
    for name in [host, *more]:
        name = name.strip()
        # An address that means "every network card" is not a name of a request.
        if name and name not in ("0.0.0.0", "::") and name not in names:
            names.append(name)
    return names


def listens_on_this_computer_only() -> bool:
    """True when the server listens on a local address only."""
    return server_host() in LOCAL_HOSTS


def packed_examples_dir() -> Path:
    """Return the folder of the example files that come with the GUI."""
    return Path(__file__).resolve().parent / "examples"


def server_port() -> int:
    """Return the port to listen on."""
    value = os.environ.get(PORT_ENV, "").strip()
    try:
        return int(value) if value else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT
