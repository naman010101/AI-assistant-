"""The one gate every feature asks before touching a file.

    ok, reason = gate.check(path, "read")     # or "edit"

Rules, in order:
  1. The feature switch must be on (Settings → Access: read files / edit files).
  2. Secrets and system locations are ALWAYS refused — even inside an allowed
     folder, even if you click "allow": .env and key files, ~/.ssh, the app's
     own private data folder, Windows and Program Files.
  3. The path must be inside an allowed folder, or allowed for this session
     ("Allow once"), or the caller asks the user (see `needs_prompt`).

Paths are resolved first (symlinks, "..", case on Windows), so a path can't
sneak out of an allowed folder with tricks like C:\\Users\\me\\Documents\\..\\.ssh.
"""
import fnmatch
import os

import config

# File names that hold secrets. Matched case-insensitively against the name.
SECRET_NAMES = [
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.pfx", "*.p12", "*.kdbx", "*.ppk",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", ".git-credentials", ".netrc", ".pgpass",
    "credentials", "credentials.*", "*credentials*.json", "secrets.*", "*.secrets", "token.json",
    "client_secret*.json", "service-account*.json", "phone_token.txt", "*.keystore", "*.jks",
]
# Folder names that are never entered (anywhere in the path).
SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker"}


def _norm(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path))))


def _inside(path, folder):
    """True if `path` is `folder` or anywhere under it (both already _norm'ed)."""
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives on Windows
        return False


def _always_blocked_roots():
    roots = [config.DATA_DIR]
    for var in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData",
                "APPDATA", "LOCALAPPDATA"):
        v = os.getenv(var)
        if v:
            roots.append(v)
    return [_norm(r) for r in roots]


class AccessGate:
    def __init__(self, settings):
        self.settings = settings
        self.session_allowed = set()  # "Allow once" folders, forgotten on restart

    # ---------- the check ----------
    def check(self, path, action="read"):
        """Return (ok, reason). reason explains a refusal in plain words."""
        acc = self.settings.access
        switch = {"read": "read_files", "edit": "edit_files"}.get(action)
        if switch and not acc.get(switch, False):
            what = "reading" if action == "read" else "editing"
            return False, f"File {what} is turned off (Settings → Access)."

        p = _norm(path)
        blocked = self.blocked_reason(p)
        if blocked:
            return False, blocked
        if not os.path.exists(p):
            return False, f"File not found: {path}"
        if self.is_allowed(p):
            return True, ""
        return False, "outside"  # caller may ask the user (needs_prompt)

    @staticmethod
    def needs_prompt(reason):
        return reason == "outside"

    def blocked_reason(self, p):
        name = os.path.basename(p).lower()
        if any(fnmatch.fnmatch(name, pat.lower()) for pat in SECRET_NAMES):
            return f"“{os.path.basename(p)}” looks like a secrets file, so it is always blocked."
        parts = {part.lower() for part in p.replace("\\", "/").split("/")}
        if parts & SECRET_DIRS:
            return "Folders holding keys and credentials (like .ssh) are always blocked."
        for root in _always_blocked_roots():
            if _inside(p, root):
                if root == _norm(config.DATA_DIR):
                    return "The assistant's own private data folder is always blocked."
                return "System and app-data folders are always blocked."
        return ""

    def is_allowed(self, p):
        folders = [_norm(f) for f in self.settings.access.get("folders", [])]
        folders += list(self.session_allowed)
        return any(_inside(p, f) for f in folders)

    # ---------- answers from the "outside allowed folders" prompt ----------
    def allow_once(self, path):
        self.session_allowed.add(_norm(os.path.dirname(_norm(path)) if os.path.isfile(path) else path))

    def allow_always(self, path):
        folder = os.path.dirname(os.path.abspath(path)) if os.path.isfile(path) else os.path.abspath(path)
        folders = list(self.settings.access.get("folders", []))
        if _norm(folder) not in {_norm(f) for f in folders}:
            folders.append(folder)
            self.settings.set_access("folders", folders)

    def add_folder(self, folder):
        self.allow_always(folder)

    def remove_folder(self, folder):
        folders = [f for f in self.settings.access.get("folders", []) if _norm(f) != _norm(folder)]
        self.settings.set_access("folders", folders)
