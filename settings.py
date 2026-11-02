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
    "memory": {
        "enabled": True,           # search past-session summaries for context
        "cloud": False,            # also send that context to cloud models
        "k": 3,                    # at most this many summaries per message
        "threshold": 0.5,          # how similar a summary must be (0-1); eval_memory.py suggests one
        "idle_minutes": 30,        # a chat untouched this long counts as ended
    },
    "memory_setup_done": False,    # the one-time question about chats saved before memory existed
    "memory_keep_ids": [],         # chats you chose to keep in full
    "code_projects": [],           # folders added in the Code window
    "code_last": None,
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
        self._upgrade()
        if self.data["access"]["folders"] is None:
            self.data["access"]["folders"] = _default_folders()
            self.save()

    def _upgrade(self):
        """Adjust values saved by older versions."""
        v = self.data.get("settings_version", 0)
        if v < 1:  # memory threshold default lowered from 0.55 to 0.5
            if self.data.get("memory", {}).get("threshold") == 0.55:
                self.data["memory"]["threshold"] = 0.5
            self.data["settings_version"] = 1
            if os.path.exists(self.path):
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
