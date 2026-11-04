"""Review window: the current file and the proposed version side by side.

    DiffWindow(master, path, old_text, new_text, rows, (added, removed), warnings,
               on_apply=..., on_discard=...)

Both panes have exactly one line per row (rows come from editor.diff_rows),
so a single scrollbar keeps them aligned.
"""
import tkinter as tk

import customtkinter as ctk

from .theme import C, Fonts

DEL_BG, DEL_FG = "#3A1F22", "#F3C1C1"
ADD_BG, ADD_FG = "#15301F", "#BDEBCB"
CHG_BG = "#2B2615"
SKIP_BG = "#161A21"


class DiffWindow(ctk.CTkToplevel):
    def __init__(self, master, path, rows, counts, warnings, on_apply, on_discard,
                 new_text="", title_name=None):
        super().__init__(master, fg_color=C["panel"])
        self.on_apply, self.on_discard = on_apply, on_discard
        self.new_text = new_text
        name = title_name or path.replace("\\", "/").rsplit("/", 1)[-1]
        self.title(f"Review changes · {name}")
        self.geometry("1180x760")
        self.minsize(800, 480)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ---- header ----
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=f"Review changes to {name}", font=Fonts.ui(17, "bold"),
                     text_color=C["text"], anchor="w").grid(row=0, column=0, sticky="w")
        added, removed = counts
        stat = ctk.CTkFrame(head, fg_color="transparent")
        stat.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(stat, text=f"+{added}", font=Fonts.mono(13, "bold"), text_color=C["ok"]).pack(side="left")
        ctk.CTkLabel(stat, text=f"  −{removed}", font=Fonts.mono(13, "bold"), text_color=C["err"]).pack(side="left")
        ctk.CTkLabel(head, text=path, font=Fonts.mono(11), text_color=C["muted"], anchor="w"
                     ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # ---- warnings ----
        self.has_block = any(level == "block" for level, _ in warnings)
        self.has_error = any(level in ("error", "block") for level, _ in warnings)
        warn_box = ctk.CTkFrame(self, fg_color="transparent")
        warn_box.grid(row=1, column=0, sticky="ew", padx=20)
        for level, msg in warnings:
            box = ctk.CTkFrame(warn_box, corner_radius=8, border_width=1,
                               fg_color=C["stop_bg"] if level in ("error", "block") else C["accent_bg"],
                               border_color=C["stop_border"] if level in ("error", "block") else C["accent_border"])
            box.pack(fill="x", pady=(0, 6))
            ctk.CTkLabel(box, text=("⛔ " if level in ("error", "block") else "⚠ ") + msg, font=Fonts.ui(12),
                         text_color=C["stop_fg"] if level in ("error", "block") else C["warn"], justify="left",
                         anchor="w", wraplength=1080).pack(fill="x", padx=12, pady=7)

        # ---- the two panes ----
        body = ctk.CTkFrame(self, fg_color=C["code_bg"], corner_radius=10, border_width=1,
                            border_color=C["border"])
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(4, 10))
        body.grid_columnconfigure((0, 2), weight=1, uniform="pane")
        body.grid_rowconfigure(1, weight=1)
        for col, label in ((0, "Current file"), (2, "Proposed")):
            ctk.CTkLabel(body, text=label, font=Fonts.ui(12, "bold"), text_color=C["muted"],
                         anchor="w").grid(row=0, column=col, sticky="ew", padx=14, pady=(8, 4))
        ctk.CTkFrame(body, width=1, fg_color=C["border"]).grid(row=0, column=1, rowspan=2, sticky="ns")

        mono = (Fonts.mono_family, 11)
        opts = dict(font=mono, bg=C["code_bg"], fg="#D6DAE0", bd=0, highlightthickness=0,
                    wrap="none", padx=6, pady=4, cursor="arrow", insertwidth=0,
                    selectbackground="#2D3A4F", spacing1=1, spacing3=1)
        self.left = tk.Text(body, **opts)
        self.right = tk.Text(body, **opts)
        self.left.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 6))
        self.right.grid(row=1, column=2, sticky="nsew", padx=(0, 0), pady=(0, 6))
        self.vbar = ctk.CTkScrollbar(body, command=self._yview, button_color=C["border2"],
                                     button_hover_color=C["faint"])
        self.vbar.grid(row=1, column=3, sticky="ns", pady=(0, 6))
        for t in (self.left, self.right):
            t.configure(yscrollcommand=self._on_scroll)
            t.bind("<MouseWheel>", self._wheel)
            t.bind("<Button-4>", lambda e: self._yview("scroll", -3, "units"))
            t.bind("<Button-5>", lambda e: self._yview("scroll", 3, "units"))
            t.tag_configure("num", foreground=C["faint"])
            t.tag_configure("delete", background=DEL_BG, foreground=DEL_FG)
            t.tag_configure("insert", background=ADD_BG, foreground=ADD_FG)
            t.tag_configure("change", background=CHG_BG)
            t.tag_configure("skip", background=SKIP_BG, foreground=C["muted"], justify="center")
            t.tag_configure("blank", background="#12151B")
        self._fill(rows)

        # ---- buttons ----
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 16))
        ctk.CTkLabel(bar, text="A backup of the current file is saved before anything is written. "
                               "You can undo from the chat or Settings.",
                     font=Fonts.ui(12), text_color=C["muted"]).pack(side="left")
        if not self.has_block:
            ctk.CTkButton(bar, text="Apply anyway" if self.has_error else "Apply", width=130, height=38,
                          corner_radius=8, font=Fonts.ui(13, "bold"),
                          fg_color=C["stop_fg"] if self.has_error else C["accent"],
                          hover_color="#FFC4C4" if self.has_error else C["accent_hi"],
                          text_color=C["on_accent"], command=self._apply).pack(side="right")
        ctk.CTkButton(bar, text="Discard", width=100, height=38, corner_radius=8, font=Fonts.ui(13),
                      fg_color="transparent", hover_color=C["raised"], border_width=1,
                      border_color=C["border2"], text_color=C["text2"],
                      command=self._discard).pack(side="right", padx=(0, 8))
        ctk.CTkButton(bar, text="Copy proposed", width=120, height=38, corner_radius=8,
                      font=Fonts.ui(13), fg_color="transparent", hover_color=C["raised"],
                      border_width=1, border_color=C["border2"], text_color=C["text2"],
                      command=self._copy).pack(side="right", padx=(0, 8))

        self.after(60, self.lift)
        self.focus_force()

    # ---------- filling ----------
    def _fill(self, rows):
        width = len(str(max([r[1] or 0 for r in rows] + [r[3] or 0 for r in rows] + [1])))
        for t in (self.left, self.right):
            t.configure(state="normal")
            t.delete("1.0", "end")
        for tag, ln, ltext, rn, rtext in rows:
            if tag == "skip":
                for t in (self.left, self.right):
                    t.insert("end", f"⋯ {ltext} ⋯\n", "skip")
                continue
            self._line(self.left, ln, ltext, width,
                       "delete" if tag in ("delete", "replace") and ln else
                       "blank" if ln is None else "")
            self._line(self.right, rn, rtext, width,
                       "insert" if tag in ("insert", "replace") and rn else
                       "blank" if rn is None else "")
        for t in (self.left, self.right):
            t.configure(state="disabled")
        first = next((i for i, r in enumerate(rows) if r[0] not in ("equal", "skip")), 0)
        self.after(80, lambda: self._yview("moveto", max(first - 3, 0) / max(len(rows), 1)))

    @staticmethod
    def _line(t, num, text, width, tag):
        start = t.index("end-1c")
        t.insert("end", (f"{num:>{width}}  " if num else " " * (width + 2)), "num")
        t.insert("end", text.expandtabs(4) + "\n")
        if tag:
            t.tag_add(tag, start, t.index("end-1c"))

    # ---------- scrolling both panes together ----------
    def _yview(self, *args):
        self.left.yview(*args)
        self.right.yview(*args)

    def _on_scroll(self, first, last):
        self.vbar.set(first, last)
        # keep the other pane exactly in step (e.g. after keyboard scrolling)
        for t in (self.left, self.right):
            if abs(t.yview()[0] - float(first)) > 1e-6:
                t.yview_moveto(first)

    def _wheel(self, event):
        self._yview("scroll", int(-event.delta / 40) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    # ---------- buttons ----------
    def _apply(self):
        self.destroy()
        self.on_apply()

    def _discard(self):
        self.destroy()
        self.on_discard()

    def _close(self):
        self.destroy()  # closing the window = decide later; the button stays in the chat

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.new_text)
