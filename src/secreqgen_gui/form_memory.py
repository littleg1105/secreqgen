"""The memory of the setup forms: the values stay when the person leaves.

The values are in the file forms.json of the data folder. The file has one
block for the inputs that mean the same in both forms ("common"), and one
block for each form. A missing or broken file gives an empty memory. A
problem with the file never breaks a page.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from secreqgen_gui.settings import data_home

COMMON = "common"  # inputs that mean the same in both forms
THREATS = "threats"
REQUIREMENTS = "requirements"

_LOCK = threading.Lock()


def _file() -> Path:
    return data_home() / "forms.json"


def _load() -> dict:
    """Read the whole memory. A missing or broken file gives an empty one."""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save(data: dict) -> None:
    """Write the memory through a temporary file. A failure is ignored."""
    try:
        target = _file()
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temp, target)
    except (OSError, ValueError, TypeError):
        pass


def recall(block: str, name: str, default=None):
    """Return the stored value, or the default when there is none."""
    value = _load().get(block, {}).get(name)
    return default if value is None else value


def remember(block: str, name: str, value) -> None:
    """Store a value. None or an empty text removes the entry."""
    with _LOCK:
        data = _load()
        entries = data.get(block, {})
        if value is None or value == "":
            if name not in entries:
                return
            del entries[name]
        else:
            if name in entries and entries[name] == value:
                return
            entries[name] = value
        if entries:
            data[block] = entries
        else:
            data.pop(block, None)
        _save(data)


def clear() -> None:
    """Empty the memory."""
    with _LOCK:
        _save({})
