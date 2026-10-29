"""Small modal dialogs in the app's dark style.

    choice = ask(root, "Allow access?", "notes.pdf is outside …",
                 [("once", "Allow once"), ("always", "Always allow this folder"),
                  ("deny", "Deny")], primary="once")

Returns the key of the button pressed, or None if the window was closed.
"""
import customtkinter as ctk

from .theme import C, Fonts


def ask(master, title, message, buttons, primary=None, detail=None):
    win = ctk.CTkToplevel(master, fg_color=C["panel"])
    win.title(title)
    win.resizable(False, False)
    win.transient(master)
    result = {"key": None}

    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=22, pady=(18, 16))
    ctk.CTkLabel(body, text=title, font=Fonts.ui(16, "bold"), text_color=C["text"],
                 anchor="w").pack(fill="x")
    ctk.CTkLabel(body, text=message, font=Fonts.ui(13), text_color=C["text2"], wraplength=440,
                 justify="left", anchor="w").pack(fill="x", pady=(8, 0))
    if detail:
        ctk.CTkLabel(body, text=detail, font=Fonts.mono(11), text_color=C["muted"], wraplength=440,
                     justify="left", anchor="w").pack(fill="x", pady=(8, 0))

    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(fill="x", pady=(18, 0))

    def pick(key):
        result["key"] = key
        win.destroy()

    for key, label in reversed(buttons):  # packed right to left
        main = key == primary
        ctk.CTkButton(
            row, text=label, height=34, corner_radius=8, font=Fonts.ui(13, "bold" if main else "normal"),
            fg_color=C["accent"] if main else "transparent",
            hover_color=C["accent_hi"] if main else C["raised"],
            text_color=C["on_accent"] if main else C["text2"],
            border_width=0 if main else 1, border_color=C["border2"],
            command=lambda k=key: pick(k),
        ).pack(side="right", padx=(8, 0))

    win.bind("<Escape>", lambda e: pick(None))
    win.protocol("WM_DELETE_WINDOW", lambda: pick(None))

    # centre over the main window
    win.update_idletasks()
    x = master.winfo_rootx() + (master.winfo_width() - win.winfo_width()) // 2
    y = master.winfo_rooty() + (master.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.after(50, win.lift)
    try:  # a grab before the window is on screen fails on some systems
        win.wait_visibility()
        win.grab_set()
    except Exception:
        pass
    win.focus_force()
    master.wait_window(win)
    return result["key"]


def info(master, title, message, detail=None):
    ask(master, title, message, [("ok", "OK")], primary="ok", detail=detail)
