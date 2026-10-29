"""App settings, saved as settings.json in the private data folder.

Every option has a default here, so a missing or partly-written file never
breaks the app; new options added later simply appear with their default.
"""
import copy
import json
import os
import threading

import config


def _default_folders():
    """Desktop, Documents, Downloads — including the OneDrive versions,
    which Windows often uses instead."""
    home = os.path.expanduser("~")
    found = []
    for name in ("Desktop", "Documents", "Downloads"):
        for base in (os.path.join(home, "OneDrive"), home):
            p = os.path.join(base, name)
            if os.path.isdir(p) and p not in found:
                found.append(p)
    return found


DEFAULTS = {
    "model": "local-general",      # chosen in the Run tab; never picked automatically
    "show_meta": True,
    "autoscroll": True,
    # How many tokens the local models can take in at once. Must match Ollama's
    # OLLAMA_CONTEXT_LENGTH setting (8192 suits a 6 GB GPU).
    "context_tokens": 8192,
    # When a message with files or images goes to a cloud model:
    # "ask" = confirm each time, "block" = never, "allow" = send.
    "cloud_files": "ask",
    "access": {
        "folders": None,           # None = the defaults from _default_folders()
        "read_files": True,
        "edit_files": True,
        "screen_capture": True,
        "file_search": True,
        "clipboard": True,
        "allowed_apps": [],
    },
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self, path=None):
        self.path = path or config.SETTINGS_PATH
        self._lock = threading.Lock()
        self.data = _merge(DEFAULTS, self._read())
        if self.data["access"]["folders"] is None:
            self.data["access"]["folders"] = _default_folders()
            self.save()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)  # atomic: never a half-written file

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    @property
    def access(self):
        return self.data["access"]

    def set_access(self, key, value):
        self.data["access"][key] = value
        self.save()
