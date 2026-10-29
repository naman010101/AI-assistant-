"""Memory window: browse, search, edit and delete what the assistant remembers.

Two views:
  Sessions  one summary per past chat: title, points, open items, tags.
            Edit and Save, Delete, or Continue in a new chat.
  Facts     short facts about you (and your projects). Pinned facts are
            always given to the model; you can add, edit, pin or delete.
"""
import time

import customtkinter as ctk

from .theme import C, Fonts, section_label


class MemoryWindow(ctk.CTkToplevel):
    def __init__(self, master, mem, on_continue, on_changed, on_save_summary, select=None):
        super().__init__(master, fg_color=C["panel"])
        self.mem = mem
        self.on_continue, self.on_changed, self.on_save_summary = on_continue, on_changed, on_save_summary
        self.title("Memory")
        self.geometry("1040x700")
        self.minsize(820, 520)
        self.transient(master)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.current = None

        # ---- top bar ----
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(14, 8))
        ctk.CTkLabel(top, text="Memory", font=Fonts.ui(18, "bold"), text_color=C["text"]).pack(side="left")
        ctk.CTkLabel(top, text="   Summaries of past sessions and facts about you. Stored only on this laptop.",
                     font=Fonts.ui(12), text_color=C["muted"]).pack(side="left")
        self.view = ctk.CTkSegmentedButton(
            top, values=["Sessions", "Facts"], height=32, corner_radius=8, font=Fonts.ui(12, "bold"),
            fg_color=C["bg"], selected_color=C["accent_sel"], selected_hover_color=C["accent_sel"],
            unselected_color=C["bg"], unselected_hover_color=C["raised"], text_color=C["accent_hi"],
            command=self._switch)
        self.view.pack(side="right")

        # ---- left: list ----
        left = ctk.CTkFrame(self, fg_color="transparent", width=300)
        left.grid(row=1, column=0, sticky="ns", padx=(18, 8), pady=(0, 16))
        left.grid_propagate(False)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        self.search = ctk.CTkEntry(left, placeholder_text="Search memory…", height=34, corner_radius=8,
                                   fg_color=C["card"], border_color=C["border2"], text_color=C["text"],
                                   font=Fonts.ui(13))
        self.search.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.search.bind("<KeyRelease>", lambda e: self._fill_list())
        self.list = ctk.CTkScrollableFrame(left, fg_color="transparent", scrollbar_button_color=C["border2"])
        self.list.grid(row=1, column=0, sticky="nsew")

        # ---- right: detail ----
        self.right = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=12, border_width=1,
                                  border_color=C["border"])
        self.right.grid(row=1, column=1, sticky="nsew", padx=(8, 18), pady=(0, 16))
        self.right.grid_columnconfigure(0, weight=1)

        self.view.set("Sessions")
        self._fill_list()
        if select:
            self.show_summary(select)
        else:
            first = self.mem.summaries(limit=1)
            self.show_summary(first[0]["id"]) if first else self._empty("No past sessions yet. "
                                                                         "When you finish a chat, its summary appears here.")
        self.after(60, self.lift)
        self.focus_force()

    # ================= list =================
    def _switch(self, value):
        self.search.delete(0, "end")
        self._fill_list()
        if value == "Facts":
            self._show_facts()
        else:
            first = self.mem.summaries(limit=1)
            self.show_summary(first[0]["id"]) if first else self._empty("No past sessions yet.")

    def _fill_list(self):
        for w in self.list.winfo_children():
            w.destroy()
        if self.view.get() == "Facts":
            ctk.CTkLabel(self.list, text="Pinned facts are always given to the model.\n"
                                         "Other facts about you: the newest 15.",
                         font=Fonts.ui(12), text_color=C["muted"], justify="left", anchor="w"
                         ).pack(fill="x", padx=4, pady=4)
            return
        q = self.search.get().strip().lower()
        items = self.mem.summaries()
        if q:
            items = [s for s in items if q in (s["title"] + " " + " ".join(
                s["bullets"] + s["facts"] + s["tags"] + s["open_items"] + s["files"])).lower()]
        if not items:
            ctk.CTkLabel(self.list, text="Nothing found." if q else "No past sessions yet.",
                         font=Fonts.ui(12), text_color=C["faint"]).pack(anchor="w", padx=6, pady=6)
        for s in items:
            active = self.current == s["id"]
            when = time.strftime("%d %b %Y · %H:%M", time.localtime(s.get("started") or s["created"]))
            b = ctk.CTkButton(self.list, text=f"{s['title']}\n{when}", anchor="w", height=50,
                              corner_radius=8, font=Fonts.ui(12), fg_color=C["raised"] if active else "transparent",
                              hover_color=C["raised"], text_color=C["text"] if active else C["text2"],
                              command=lambda sid=s["id"]: self.show_summary(sid))
            b.pack(fill="x", pady=1)

    # ================= a summary =================
    def _clear_right(self):
        for w in self.right.winfo_children():
            w.destroy()

    def _empty(self, text):
        self._clear_right()
        ctk.CTkLabel(self.right, text=text, font=Fonts.ui(13), text_color=C["muted"], wraplength=520
                     ).grid(row=0, column=0, padx=24, pady=40)

    def show_summary(self, sid):
        s = self.mem.get_summary(sid)
        if not s:
            return self._empty("That summary no longer exists.")
        if self.view.get() != "Sessions":
            self.view.set("Sessions")
        self.current = sid
        self._fill_list()
        self._clear_right()
        r = self.right
        r.grid_rowconfigure(3, weight=3)
        r.grid_rowconfigure(5, weight=1)

        self.title_entry = ctk.CTkEntry(r, height=36, corner_radius=8, font=Fonts.ui(15, "bold"),
                                        fg_color=C["bg"], border_color=C["border2"], text_color=C["text"])
        self.title_entry.insert(0, s["title"])
        self.title_entry.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
        when = time.strftime("%A %d %B %Y, %H:%M", time.localtime(s.get("started") or s["created"]))
        files = ("   ·   files: " + ", ".join(s["files"])) if s["files"] else ""
        ctk.CTkLabel(r, text=when + files, font=Fonts.ui(12), text_color=C["muted"], anchor="w"
                     ).grid(row=1, column=0, sticky="ew", padx=20)

        section_label(r, "What happened (one point per line)").grid(row=2, column=0, sticky="ew", padx=20, pady=(14, 4))
        self.bullets_box = self._box(r, "\n".join(s["bullets"]), 3)
        section_label(r, "Left open").grid(row=4, column=0, sticky="ew", padx=20, pady=(12, 4))
        self.open_box = self._box(r, "\n".join(s["open_items"]), 5)
        section_label(r, "Tags (comma separated)").grid(row=6, column=0, sticky="ew", padx=20, pady=(12, 4))
        self.tags_entry = ctk.CTkEntry(r, height=32, corner_radius=8, font=Fonts.ui(12), fg_color=C["bg"],
                                       border_color=C["border2"], text_color=C["text"])
        self.tags_entry.insert(0, ", ".join(s["tags"]))
        self.tags_entry.grid(row=7, column=0, sticky="ew", padx=18)

        bar = ctk.CTkFrame(r, fg_color="transparent")
        bar.grid(row=8, column=0, sticky="ew", padx=18, pady=16)
        self._btn(bar, "Continue in a new chat", lambda: self._continue(sid), main=True).pack(side="right")
        self._btn(bar, "Save changes", lambda: self._save(sid)).pack(side="right", padx=(0, 8))
        self._btn(bar, "Delete", lambda: self._delete(sid), danger=True).pack(side="left")

    def _box(self, master, text, row):
        box = ctk.CTkTextbox(master, wrap="word", font=Fonts.ui(13), fg_color=C["bg"], border_width=1,
                             border_color=C["border2"], text_color=C["text"], corner_radius=8)
        box.insert("1.0", text)
        box.grid(row=row, column=0, sticky="nsew", padx=18)
        return box

    def _btn(self, master, text, cmd, main=False, danger=False):
        return ctk.CTkButton(
            master, text=text, height=34, corner_radius=8, font=Fonts.ui(13, "bold" if main else "normal"),
            fg_color=C["accent"] if main else "transparent",
            hover_color=C["accent_hi"] if main else (C["stop_bg"] if danger else C["raised"]),
            text_color=C["on_accent"] if main else (C["stop_fg"] if danger else C["text2"]),
            border_width=0 if main else 1, border_color=C["stop_border"] if danger else C["border2"],
            command=cmd)

    @staticmethod
    def _lines(box):
        return [l.strip().lstrip("-•* ").strip() for l in box.get("1.0", "end").splitlines() if l.strip()]

    def _save(self, sid):
        s = self.mem.get_summary(sid)
        data = {"bullets": self._lines(self.bullets_box), "open_items": self._lines(self.open_box),
                "tags": [t.strip().lower() for t in self.tags_entry.get().split(",") if t.strip()],
                "facts": s["facts"], "files": s["files"]}
        self.on_save_summary(sid, self.title_entry.get().strip() or "Untitled session", data)
        self._fill_list()
        self.on_changed()

    def _delete(self, sid):
        self.mem.delete_summary(sid)
        self.current = None
        self.on_changed()
        self._fill_list()
        first = self.mem.summaries(limit=1)
        self.show_summary(first[0]["id"]) if first else self._empty("No past sessions left.")

    def _continue(self, sid):
        self.on_continue(sid)
        self.destroy()

    # ================= facts =================
    def _show_facts(self):
        self.current = None
        self._clear_right()
        r = self.right
        r.grid_rowconfigure(1, weight=1)
        add = ctk.CTkFrame(r, fg_color="transparent")
        add.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        add.grid_columnconfigure(0, weight=1)
        self.new_fact = ctk.CTkEntry(add, placeholder_text="Add a fact, e.g. “I study B.Tech CSE (Data Science) at MUJ”",
                                     height=34, corner_radius=8, font=Fonts.ui(13), fg_color=C["bg"],
                                     border_color=C["border2"], text_color=C["text"])
        self.new_fact.grid(row=0, column=0, sticky="ew")
        self.new_fact.bind("<Return>", lambda e: self._add_fact())
        self._btn(add, "Add (pinned)", self._add_fact, main=True).grid(row=0, column=1, padx=(8, 0))

        lst = ctk.CTkScrollableFrame(r, fg_color="transparent", scrollbar_button_color=C["border2"])
        lst.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        lst.grid_columnconfigure(0, weight=1)
        facts = self.mem.facts()
        if not facts:
            ctk.CTkLabel(lst, text="No facts yet. They're collected from your sessions, or add your own above.",
                         font=Fonts.ui(12), text_color=C["faint"]).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        for i, f in enumerate(facts):
            row = ctk.CTkFrame(lst, fg_color=C["bg"], corner_radius=8, border_width=1,
                               border_color=C["accent_border"] if f["pinned"] else C["border"])
            row.grid(row=i, column=0, sticky="ew", pady=3, padx=4)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="you" if f["kind"] == "user" else "project", width=56, font=Fonts.ui(11),
                         text_color=C["accent_hi"] if f["kind"] == "user" else C["cloud_fg"]
                         ).grid(row=0, column=0, padx=(10, 4), pady=6)
            entry = ctk.CTkEntry(row, height=30, corner_radius=6, font=Fonts.ui(13), fg_color="transparent",
                                 border_width=0, text_color=C["text"])
            entry.insert(0, f["text"])
            entry.grid(row=0, column=1, sticky="ew")
            entry.bind("<FocusOut>", lambda e, fid=f["id"], en=entry: self._edit_fact(fid, en))
            entry.bind("<Return>", lambda e, fid=f["id"], en=entry: self._edit_fact(fid, en))
            var = ctk.BooleanVar(value=f["pinned"])
            ctk.CTkCheckBox(row, text="pin", variable=var, width=60, font=Fonts.ui(12), text_color=C["text2"],
                            fg_color=C["accent"], hover_color=C["accent_hi"], border_color=C["faint"],
                            checkbox_width=16, checkbox_height=16,
                            command=lambda fid=f["id"], v=var: self._pin(fid, v.get())).grid(row=0, column=2, padx=6)
            ctk.CTkButton(row, text="✕", width=28, height=26, corner_radius=6, font=Fonts.ui(11),
                          fg_color="transparent", hover_color=C["stop_bg"], text_color=C["muted"],
                          command=lambda fid=f["id"]: self._del_fact(fid)).grid(row=0, column=3, padx=(0, 8))

    def _add_fact(self):
        text = self.new_fact.get().strip()
        if text:
            self.mem.add_fact(text, "user", pinned=True)
            self._show_facts()

    def _edit_fact(self, fid, entry):
        text = entry.get().strip()
        if text:
            self.mem.update_fact(fid, text)
        else:
            self.mem.delete_fact(fid)
            self._show_facts()

    def _pin(self, fid, pinned):
        self.mem.set_pinned(fid, pinned)
        self._show_facts()

    def _del_fact(self, fid):
        self.mem.delete_fact(fid)
        self._show_facts()
