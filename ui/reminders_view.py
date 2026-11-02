"""Reminders window (reminders, timetable, daily brief) and the notification pop-up."""
import datetime as dt
from tkinter import filedialog

import customtkinter as ctk

import reminders as R

from .theme import C, Fonts, section_label


def _btn(master, text, cmd, main=False, danger=False, width=None):
    kw = {"width": width} if width else {}
    return ctk.CTkButton(master, text=text, height=30, corner_radius=8, font=Fonts.ui(12, "bold" if main else "normal"),
                         fg_color=C["accent"] if main else "transparent",
                         hover_color=C["accent_hi"] if main else (C["stop_bg"] if danger else C["raised"]),
                         text_color=C["on_accent"] if main else (C["stop_fg"] if danger else C["text2"]),
                         border_width=0 if main else 1, border_color=C["stop_border"] if danger else C["border2"],
                         command=cmd, **kw)


class RemindersWindow(ctk.CTkToplevel):
    def __init__(self, master, rs, settings, on_add_text, on_read_timetable, on_show_brief, on_changed):
        """on_add_text(text) -> (ok, message)   on_read_timetable(path, done(rows, error))"""
        super().__init__(master, fg_color=C["panel"])
        self.rs, self.settings = rs, settings
        self.on_add_text, self.on_read_timetable = on_add_text, on_read_timetable
        self.on_show_brief, self.on_changed = on_show_brief, on_changed
        self.title("Reminders")
        self.geometry("880x660")
        self.minsize(720, 480)
        self.transient(master)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 8))
        ctk.CTkLabel(top, text="Reminders", font=Fonts.ui(18, "bold"), text_color=C["text"]).pack(side="left")
        self.view = ctk.CTkSegmentedButton(
            top, values=["Reminders", "Timetable", "Daily brief"], height=32, corner_radius=8, font=Fonts.ui(12, "bold"),
            fg_color=C["bg"], selected_color=C["accent_sel"], selected_hover_color=C["accent_sel"],
            unselected_color=C["bg"], unselected_hover_color=C["raised"], text_color=C["accent_hi"],
            command=self._show)
        self.view.pack(side="right")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 16))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(1, weight=1)
        self.view.set("Reminders")
        self._show("Reminders")
        self.after(60, self.lift)
        self.focus_force()

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _show(self, name):
        self._clear()
        {"Reminders": self._reminders, "Timetable": self._timetable, "Daily brief": self._brief}[name]()

    # ================= reminders =================
    def _reminders(self):
        add = ctk.CTkFrame(self.body, fg_color="transparent")
        add.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        add.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(add, height=36, corner_radius=10, font=Fonts.ui(13), fg_color=C["card"],
                                  border_color=C["border2"], text_color=C["text"],
                                  placeholder_text="e.g. DSA assignment due Friday 5pm · every Monday 10am team meeting")
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda e: self._add())
        _btn(add, "Add", self._add, main=True, width=80).grid(row=0, column=1, padx=(8, 0))
        self.msg = ctk.CTkLabel(add, text="Write it the way you'd say it. You can also type “remind me …” in any chat, "
                                          "on the laptop or the phone.", font=Fonts.ui(12), text_color=C["muted"], anchor="w")
        self.msg.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.list = ctk.CTkScrollableFrame(self.body, fg_color="transparent", scrollbar_button_color=C["border2"])
        self.list.grid(row=1, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)
        self._fill()

    def _add(self):
        text = self.entry.get().strip()
        if not text:
            return
        if not R.is_reminder_request(text):
            text = "remind me " + text
        self.msg.configure(text="Working it out…", text_color=C["muted"])
        self.update_idletasks()
        ok, message = self.on_add_text(text)
        self.msg.configure(text=message, text_color=C["ok"] if ok else C["warn"])
        if ok:
            self.entry.delete(0, "end")
            self._fill()

    def _fill(self):
        for w in self.list.winfo_children():
            w.destroy()
        items = self.rs.upcoming()
        if not items:
            ctk.CTkLabel(self.list, text="No reminders.", font=Fonts.ui(12), text_color=C["faint"]).grid(row=0, column=0, sticky="w")
        now = dt.datetime.now()
        for i, r in enumerate(items):
            late = r["due_dt"] < now and r["repeat"] == "none"
            row = ctk.CTkFrame(self.list, fg_color=C["card"], corner_radius=10, border_width=1,
                               border_color=C["stop_border"] if late else C["border"])
            row.grid(row=i, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=R.describe(r["due_dt"], r["repeat"], now), width=150, anchor="w",
                         font=Fonts.mono(12), text_color=C["stop_fg"] if late else C["accent_hi"]
                         ).grid(row=0, column=0, padx=(12, 6), pady=8, sticky="w")
            ctk.CTkLabel(row, text=r["text"], font=Fonts.ui(13), text_color=C["text"], anchor="w", justify="left",
                         wraplength=440).grid(row=0, column=1, sticky="w")
            if r.get("source") == "phone":
                ctk.CTkLabel(row, text="phone", font=Fonts.ui(11), text_color=C["muted"]).grid(row=0, column=2, padx=6)
            _btn(row, "Done", lambda rid=r["id"]: self._done(rid), width=60).grid(row=0, column=3, padx=4)
            _btn(row, "✕", lambda rid=r["id"]: self._delete(rid), danger=True, width=34).grid(row=0, column=4, padx=(0, 10))

    def _done(self, rid):
        self.rs.complete(rid)
        self._fill()
        self.on_changed()

    def _delete(self, rid):
        self.rs.delete(rid)
        self._fill()
        self.on_changed()

    # ================= timetable =================
    def _timetable(self):
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(bar, text="Your weekly classes, shown in the daily brief.", font=Fonts.ui(12),
                     text_color=C["muted"]).pack(side="left")
        _btn(bar, "Read from a photo", self._from_photo, main=True).pack(side="right")
        self.tt_msg = ctk.CTkLabel(bar, text="", font=Fonts.ui(12), text_color=C["muted"])
        self.tt_msg.pack(side="right", padx=8)
        grid = ctk.CTkScrollableFrame(self.body, fg_color="transparent", scrollbar_button_color=C["border2"])
        grid.grid(row=1, column=0, sticky="nsew")
        grid.grid_columnconfigure(0, weight=1)
        self.tt_grid = grid
        add = ctk.CTkFrame(self.body, fg_color=C["card"], corner_radius=10, border_width=1, border_color=C["border"])
        add.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.tt_day = ctk.CTkOptionMenu(add, values=[d.title() for d in R.DAYS], width=120, height=30,
                                        fg_color=C["bg"], button_color=C["raised"], text_color=C["text"], font=Fonts.ui(12))
        self.tt_day.pack(side="left", padx=(10, 4), pady=8)
        self.tt_start = self._entry(add, "09:00", 70)
        self.tt_end = self._entry(add, "10:00", 70)
        self.tt_title = self._entry(add, "Class", 220)
        self.tt_place = self._entry(add, "Room", 110)
        _btn(add, "Add class", self._add_class, width=90).pack(side="left", padx=(4, 10))
        self._fill_tt()

    def _entry(self, master, ph, w):
        e = ctk.CTkEntry(master, width=w, height=30, corner_radius=8, placeholder_text=ph, fg_color=C["bg"],
                         border_color=C["border2"], text_color=C["text"], font=Fonts.ui(12))
        e.pack(side="left", padx=4)
        return e

    def _fill_tt(self, preview=None):
        g = self.tt_grid
        for w in g.winfo_children():
            w.destroy()
        rows = preview if preview is not None else self.rs.classes()
        if preview is not None:
            box = ctk.CTkFrame(g, fg_color=C["accent_bg"], corner_radius=10, border_width=1, border_color=C["accent_border"])
            box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            ctk.CTkLabel(box, text=f"Read {len(rows)} classes from the photo. Check them, then save (this replaces "
                                   f"your current timetable).", font=Fonts.ui(12), text_color=C["accent_hi"]
                         ).pack(side="left", padx=12, pady=8)
            _btn(box, "Discard", lambda: self._fill_tt()).pack(side="right", padx=(4, 10))
            _btn(box, "Save timetable", lambda: self._save_preview(rows), main=True).pack(side="right")
        r0 = 1
        for d in range(7):
            day_rows = [c for c in rows if c["day"] == d]
            if not day_rows:
                continue
            section_label(g, R.DAYS[d]).grid(row=r0, column=0, sticky="w", pady=(10, 2))
            r0 += 1
            for c in day_rows:
                row = ctk.CTkFrame(g, fg_color=C["card"], corner_radius=8, border_width=1, border_color=C["border"])
                row.grid(row=r0, column=0, sticky="ew", pady=2)
                r0 += 1
                when = c["start"] + (f"–{c['end']}" if c.get("end") else "")
                ctk.CTkLabel(row, text=when, width=110, anchor="w", font=Fonts.mono(12), text_color=C["accent_hi"]
                             ).pack(side="left", padx=(12, 6), pady=6)
                ctk.CTkLabel(row, text=c["title"] + (f"  ·  {c['place']}" if c.get("place") else ""), font=Fonts.ui(13),
                             text_color=C["text"], anchor="w").pack(side="left")
                if preview is None:
                    _btn(row, "✕", lambda cid=c["id"]: (self.rs.delete_class(cid), self._fill_tt()), danger=True,
                         width=34).pack(side="right", padx=8)
        if not rows:
            ctk.CTkLabel(g, text="No classes yet. Add them below, or read them from a photo of your timetable.",
                         font=Fonts.ui(12), text_color=C["faint"]).grid(row=r0, column=0, sticky="w")

    def _add_class(self):
        title = self.tt_title.get().strip()
        if not title:
            return
        try:
            self.rs.add_class(R.DAYS.index(self.tt_day.get().lower()), self.tt_start.get() or "09:00", title,
                              self.tt_end.get() or None, self.tt_place.get().strip() or None)
        except ValueError as e:
            self.tt_msg.configure(text=str(e), text_color=C["warn"])
            return
        self.tt_title.delete(0, "end")
        self.tt_place.delete(0, "end")
        self._fill_tt()

    def _from_photo(self):
        path = filedialog.askopenfilename(parent=self, title="Photo or screenshot of your timetable",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if not path:
            return
        self.tt_msg.configure(text="Reading the timetable with the vision model… (can take a minute)",
                              text_color=C["muted"])

        def done(rows, error):
            if not self.winfo_exists():
                return
            if error or not rows:
                self.tt_msg.configure(text=error or "No classes found in that image.", text_color=C["warn"])
                return
            self.tt_msg.configure(text="")
            self._fill_tt(preview=rows)

        self.on_read_timetable(path, done)

    def _save_preview(self, rows):
        self.rs.replace_timetable(rows)
        self._fill_tt()
        self.on_changed()

    # ================= brief =================
    def _brief(self):
        cfg = self.settings.get("brief", {})
        box = ctk.CTkFrame(self.body, fg_color=C["card"], corner_radius=12, border_width=1, border_color=C["border"])
        box.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(box, text="Each day at the time you pick, a short brief opens: today's classes, what's due, "
                               "what you left open in recent sessions, and code changes waiting for review. It's also "
                               "on your phone with /brief.", font=Fonts.ui(12), text_color=C["muted"], wraplength=780,
                     justify="left", anchor="w").pack(fill="x", padx=14, pady=(12, 8))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 12))
        var = ctk.BooleanVar(value=cfg.get("enabled", True))
        ctk.CTkSwitch(row, text="Show the daily brief at", variable=var, font=Fonts.ui(13), text_color=C["text"],
                      progress_color=C["accent"], command=lambda: self._set_brief("enabled", var.get())).pack(side="left")
        t = ctk.CTkEntry(row, width=70, height=30, corner_radius=8, font=Fonts.mono(12), fg_color=C["bg"],
                         border_color=C["border2"], text_color=C["text"])
        t.insert(0, cfg.get("time", "08:00"))
        t.pack(side="left", padx=8)
        t.bind("<FocusOut>", lambda e: self._set_brief("time", t.get()))
        _btn(row, "Show today's brief now", self.on_show_brief, main=True).pack(side="right")

    def _set_brief(self, key, value):
        cfg = dict(self.settings.get("brief", {}))
        if key == "time":
            try:
                value = R._hhmm(value)
            except ValueError:
                return
        cfg[key] = value
        self.settings.set("brief", cfg)


class NotifyPopup(ctk.CTkToplevel):
    """A small always-on-top card in the corner of the screen."""
    stack = []

    def __init__(self, master, title, text, buttons=(), seconds=None):
        super().__init__(master, fg_color=C["card"])
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        w, h = 380, 130
        n = len([p for p in NotifyPopup.stack if p.winfo_exists()])
        x = self.winfo_screenwidth() - w - 18
        y = self.winfo_screenheight() - (h + 12) * (n + 1) - 60
        self.geometry(f"{w}x{h}+{x}+{y}")
        NotifyPopup.stack.append(self)
        frame = ctk.CTkFrame(self, fg_color=C["card"], border_width=1, border_color=C["accent_border"], corner_radius=12)
        frame.pack(fill="both", expand=True)
        ctk.CTkLabel(frame, text="⏰  " + title, font=Fonts.ui(13, "bold"), text_color=C["accent_hi"], anchor="w"
                     ).pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(frame, text=text, font=Fonts.ui(13), text_color=C["text"], anchor="w", justify="left",
                     wraplength=350).pack(fill="x", padx=14, pady=(2, 6))
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))
        for label, cmd in buttons:
            _btn(row, label, lambda c=cmd: (c(), self.destroy()), main=label == buttons[0][0]).pack(side="left", padx=(0, 6))
        _btn(row, "Close", self.destroy).pack(side="right")
        if seconds:
            self.after(seconds * 1000, lambda: self.winfo_exists() and self.destroy())
        self.bell()


class BriefWindow(ctk.CTkToplevel):
    def __init__(self, master, markdown):
        super().__init__(master, fg_color=C["panel"])
        from .chat_view import AutoText
        self.title("Daily brief")
        self.geometry("560x560")
        self.transient(master)
        self.bind("<Escape>", lambda e: self.destroy())
        box = ctk.CTkScrollableFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        text = AutoText(box, text_color="#DADDE2")
        text.pack(fill="x")
        text.append(markdown.replace("\n- ", "\n•  "))
        text.highlight()
        self.after(100, text.fit)
        _btn(self, "Close", self.destroy, main=True).pack(pady=(0, 14))
        self.after(60, self.lift)
