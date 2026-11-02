"""Code window: understand a project, especially code an AI wrote for you.

Left: your projects and the selected project's map (files → classes →
functions). Right: actions. Each action starts a chat in the main window
(Qwen Coder by default) with the prepared material, so you can ask follow-ups.
"""
import os
import time
from tkinter import filedialog

import customtkinter as ctk

import codeexplain as cx

from .theme import C, Fonts, section_label

DEPTHS = {"Summary": "summary", "Walkthrough": "walkthrough", "Line by line": "line"}


class CodeWindow(ctk.CTkToplevel):
    def __init__(self, master, settings, gate, on_start, blocked):
        """on_start(title, display_text, build_fn, project, journal_changes=None)
        blocked(path) -> reason or '' (secrets etc.)"""
        super().__init__(master, fg_color=C["panel"])
        self.settings, self.gate, self.on_start, self.blocked = settings, gate, on_start, blocked
        self.title("Code")
        self.geometry("1120x740")
        self.minsize(900, 560)
        self.transient(master)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.project = None
        self.pmap = None
        self.selection = None   # (rel, item or None)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(14, 8))
        ctk.CTkLabel(top, text="Code", font=Fonts.ui(18, "bold"), text_color=C["text"]).pack(side="left")
        ctk.CTkLabel(top, text="   Understand your projects, including code an AI wrote. Everything runs locally.",
                     font=Fonts.ui(12), text_color=C["muted"]).pack(side="left")
        self.add_btn = self._btn(top, "+ Add project folder", self._add_project)
        self.add_btn.pack(side="right")
        self.picker = ctk.CTkOptionMenu(top, values=["(no projects)"], width=260, height=32, corner_radius=8,
                                        font=Fonts.ui(12), fg_color=C["card"], button_color=C["raised"],
                                        button_hover_color=C["border2"], text_color=C["text"],
                                        command=self._pick_project)
        self.picker.pack(side="right", padx=8)

        # ---- left: map ----
        left = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=12, border_width=1,
                            border_color=C["border"], width=380)
        left.grid(row=1, column=0, sticky="ns", padx=(18, 8), pady=(0, 16))
        left.grid_propagate(False)
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)
        self.map_title = ctk.CTkLabel(left, text="No project selected", font=Fonts.ui(13, "bold"),
                                      text_color=C["text"], anchor="w")
        self.map_title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        self.filter = ctk.CTkEntry(left, placeholder_text="Filter files and functions…", height=30, corner_radius=8,
                                   fg_color=C["bg"], border_color=C["border2"], text_color=C["text"],
                                   font=Fonts.ui(12))
        self.filter.grid(row=1, column=0, sticky="ew", padx=10, pady=8)
        self.filter.bind("<KeyRelease>", lambda e: self._fill_map())
        self.tree = ctk.CTkScrollableFrame(left, fg_color="transparent", scrollbar_button_color=C["border2"])
        self.tree.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 8))

        # ---- right: actions ----
        right = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=C["border2"])
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 18), pady=(0, 16))
        right.grid_columnconfigure(0, weight=1)
        self.right = right
        r = 0

        def card(title, desc):
            nonlocal r
            box = ctk.CTkFrame(right, fg_color=C["card"], corner_radius=12, border_width=1, border_color=C["border"])
            box.grid(row=r, column=0, sticky="ew", pady=(0, 10))
            box.grid_columnconfigure(0, weight=1)
            r += 1
            ctk.CTkLabel(box, text=title, font=Fonts.ui(14, "bold"), text_color=C["text"], anchor="w"
                         ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
            ctk.CTkLabel(box, text=desc, font=Fonts.ui(12), text_color=C["muted"], anchor="w", justify="left",
                         wraplength=560).grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 8))
            return box

        b = card("What changed?", "After an AI tool (or you) edits the project: every change since your last "
                                  "review, explained file by file, with anything risky and what to test.")
        self.changes_lbl = ctk.CTkLabel(b, text="", font=Fonts.ui(12), text_color=C["text2"], anchor="w")
        self.changes_lbl.grid(row=2, column=0, sticky="ew", padx=14)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=14, pady=(6, 12))
        self.changes_btn = self._btn(row, "Explain the changes", self._explain_changes, main=True)
        self.changes_btn.pack(side="left")
        self.review_btn = self._btn(row, "Mark as reviewed", self._mark_reviewed)
        self.review_btn.pack(side="left", padx=8)
        self._btn(row, "Refresh", self._refresh_changes).pack(side="left")

        b = card("Explain the selection", "Pick a file or function on the left, then choose how deep.")
        self.sel_lbl = ctk.CTkLabel(b, text="Nothing selected", font=Fonts.mono(12), text_color=C["accent_hi"],
                                    anchor="w")
        self.sel_lbl.grid(row=2, column=0, sticky="ew", padx=14)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=14, pady=(6, 12))
        self.depth = ctk.CTkSegmentedButton(row, values=list(DEPTHS), height=32, corner_radius=8,
                                            font=Fonts.ui(12), fg_color=C["bg"], selected_color=C["accent_sel"],
                                            selected_hover_color=C["accent_sel"], unselected_color=C["bg"],
                                            unselected_hover_color=C["raised"], text_color=C["accent_hi"])
        self.depth.set("Walkthrough")
        self.depth.pack(side="left")
        self._btn(row, "Explain", self._explain_selection, main=True).pack(side="left", padx=8)

        b = card("Trace a feature", "Ask how something works, e.g. “how does saving a chat work?”. "
                                    "Finds the relevant functions and walks through them in order.")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        row.grid_columnconfigure(0, weight=1)
        self.trace_q = ctk.CTkEntry(row, placeholder_text="How does … work?", height=32, corner_radius=8,
                                    fg_color=C["bg"], border_color=C["border2"], text_color=C["text"], font=Fonts.ui(12))
        self.trace_q.grid(row=0, column=0, sticky="ew")
        self.trace_q.bind("<Return>", lambda e: self._trace())
        self._btn(row, "Trace", self._trace, main=True).grid(row=0, column=1, padx=(8, 0))

        b = card("Overview and quiz", "A tour of the whole project, or 5 questions on your own code, one at a "
                                      "time — good before a viva or interview.")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        self._btn(row, "Project overview", self._overview, main=True).pack(side="left")
        self._btn(row, "Quiz me", self._quiz).pack(side="left", padx=8)

        b = card("Explain an error", "Paste a traceback; it's matched to the lines in your project.")
        self.err = ctk.CTkTextbox(b, height=90, font=Fonts.mono(11), fg_color=C["bg"], border_width=1,
                                  border_color=C["border2"], text_color=C["text"], corner_radius=8)
        self.err.grid(row=2, column=0, sticky="ew", padx=14)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=14, pady=(6, 12))
        self._btn(row, "Explain the error", self._error, main=True).pack(side="left")

        section_label(right, "Change journal").grid(row=r, column=0, sticky="ew", padx=4, pady=(8, 4))
        r += 1
        self.journal = ctk.CTkFrame(right, fg_color="transparent")
        self.journal.grid(row=r, column=0, sticky="ew")
        self.journal.grid_columnconfigure(0, weight=1)

        self._load_projects()
        self.after(60, self.lift)
        self.focus_force()

    # ================= helpers =================
    def _btn(self, master, text, cmd, main=False):
        return ctk.CTkButton(master, text=text, height=32, corner_radius=8, font=Fonts.ui(12, "bold" if main else "normal"),
                             fg_color=C["accent"] if main else "transparent",
                             hover_color=C["accent_hi"] if main else C["raised"],
                             text_color=C["on_accent"] if main else C["text2"], border_width=0 if main else 1,
                             border_color=C["border2"], command=cmd)

    def _chars(self):
        return cx.budget_chars(int(self.settings.get("context_tokens", 8192)))

    def _need_project(self):
        if not self.project:
            self.map_title.configure(text="Add or pick a project first")
            return False
        return True

    # ================= projects =================
    def _projects(self):
        return [p for p in self.settings.get("code_projects", []) if os.path.isdir(p)]

    def _load_projects(self):
        projects = self._projects()
        if not projects:
            self.picker.configure(values=["(no projects)"])
            self.picker.set("(no projects)")
            return
        names = [self._label(p) for p in projects]
        self.picker.configure(values=names)
        last = self.settings.get("code_last")
        pick = last if last in projects else projects[0]
        self.picker.set(self._label(pick))
        self._open(pick)

    @staticmethod
    def _label(path):
        name = os.path.basename(path.rstrip("\\/")) or path
        return f"{name}  —  {path if len(path) < 40 else '…' + path[-38:]}"

    def _pick_project(self, label):
        for p in self._projects():
            if self._label(p) == label:
                self._open(p)

    def _add_project(self):
        folder = filedialog.askdirectory(parent=self, title="Pick a project folder")
        if not folder:
            return
        folder = os.path.normpath(folder)
        ok, reason = self.gate.check(folder, "read")
        if not ok and self.gate.needs_prompt(reason):
            self.gate.allow_always(folder)  # adding a project = allowing its folder
            ok, reason = self.gate.check(folder, "read")
        if not ok:
            self.map_title.configure(text=f"Can't use this folder: {reason}")
            return
        projects = self._projects()
        if folder not in projects:
            projects.insert(0, folder)
            self.settings.set("code_projects", projects)
        self._load_projects()
        self.picker.set(self._label(folder))
        self._open(folder)

    def _open(self, path):
        self.settings.set("code_last", path)
        self.project = cx.Project(path)
        self.map_title.configure(text=f"Scanning {self.project.name}…")
        self.update_idletasks()
        try:
            self.pmap = self.project.scan(self.blocked)
        except Exception as e:
            self.pmap = None
            self.map_title.configure(text=f"Couldn't read the project: {e}")
            return
        n_fn = sum(len(i.get("methods", [])) + 1 for f in self.pmap["files"] for i in f.get("items", []))
        self.map_title.configure(text=f"{self.project.name}  ·  {len(self.pmap['files'])} files  ·  {n_fn} definitions")
        self.selection = None
        self.sel_lbl.configure(text="Nothing selected")
        self._fill_map()
        self._refresh_changes()
        self._fill_journal()

    # ================= map =================
    def _fill_map(self):
        for w in self.tree.winfo_children():
            w.destroy()
        if not self.pmap:
            return
        q = self.filter.get().strip().lower()
        shown = 0
        for f in self.pmap["files"]:
            names = [i["name"] for i in f.get("items", [])] + [m["name"] for i in f.get("items", [])
                                                                for m in i.get("methods", [])]
            if q and q not in f["path"].lower() and not any(q in n.lower() for n in names):
                continue
            shown += 1
            if shown > 400:
                ctk.CTkLabel(self.tree, text="… more files: use the filter", font=Fonts.ui(11),
                             text_color=C["faint"]).pack(anchor="w", padx=8)
                break
            self._tree_btn(f"{f['path']}", (f["path"], None), level=0, extra=f"{f['lines']} lines")
            for it in f.get("items", []):
                if q and q not in f["path"].lower() and q not in it["name"].lower() and not any(
                        q in m["name"].lower() for m in it.get("methods", [])):
                    continue
                prefix = "class " if it["kind"] == "class" else ""
                self._tree_btn(f"{prefix}{it['name']}", (f["path"], it), level=1, extra=f"L{it['line']}")
                for m in it.get("methods", []):
                    if q and q not in f["path"].lower() and q not in it["name"].lower() and q not in m["name"].lower():
                        continue
                    self._tree_btn(f".{m['name']}()", (f["path"], {**m, "class": it["name"]}), level=2,
                                   extra=f"L{m['line']}")

    def _tree_btn(self, text, sel, level, extra=""):
        active = self.selection is not None and self.selection[0] == sel[0] and (
            (self.selection[1] or {}).get("name"), (self.selection[1] or {}).get("line")) == (
            (sel[1] or {}).get("name"), (sel[1] or {}).get("line"))
        b = ctk.CTkButton(self.tree, text=f"{text}   {extra}", anchor="w", height=26 if level else 28,
                          corner_radius=6, font=Fonts.mono(11) if level else Fonts.ui(12, "bold"),
                          fg_color=C["raised"] if active else "transparent", hover_color=C["raised"],
                          text_color=C["text"] if level == 0 else C["text2"],
                          command=lambda s=sel: self._select(s))
        b.pack(fill="x", padx=(6 + 16 * level, 4), pady=0)

    def _select(self, sel):
        self.selection = sel
        rel, it = sel
        if it is None:
            self.sel_lbl.configure(text=rel)
        else:
            name = f"{it['class']}.{it['name']}" if it.get("class") else it["name"]
            self.sel_lbl.configure(text=f"{name}   ({rel}, line {it['line']})")
        self._fill_map()

    # ================= changes + journal =================
    def _refresh_changes(self):
        if not self.project:
            self.changes_lbl.configure(text="")
            return
        try:
            ch = self.project.changes(self.blocked)
        except Exception as e:
            self.changes_lbl.configure(text=f"Couldn't compare: {e}")
            return
        self._changes = ch
        if ch is None:
            self.changes_lbl.configure(text="Not tracked yet. Press “Mark as reviewed” to save the current state; "
                                            "changes after that can be explained.")
            self.changes_btn.configure(state="disabled")
        elif not ch:
            self.changes_lbl.configure(text="No changes since your last review.")
            self.changes_btn.configure(state="disabled")
        else:
            add = sum(c["added"] for c in ch)
            rem = sum(c["removed"] for c in ch)
            files = ", ".join(c["path"] for c in ch[:4]) + (f" and {len(ch) - 4} more" if len(ch) > 4 else "")
            self.changes_lbl.configure(text=f"{len(ch)} file{'s' if len(ch) != 1 else ''} changed (+{add} −{rem}): {files}")
            self.changes_btn.configure(state="normal")

    def _mark_reviewed(self):
        if not self._need_project():
            return
        ch = getattr(self, "_changes", None)
        self.project.mark_reviewed(self.blocked, changes=ch or None, explanation="marked as reviewed")
        self._refresh_changes()
        self._fill_journal()

    def _fill_journal(self):
        for w in self.journal.winfo_children():
            w.destroy()
        entries = self.project.journal()[:12] if self.project else []
        if not entries:
            ctk.CTkLabel(self.journal, text="No reviewed changes yet.", font=Fonts.ui(12), text_color=C["faint"],
                         anchor="w").grid(row=0, column=0, sticky="w", padx=4)
        for i, e in enumerate(entries):
            box = ctk.CTkFrame(self.journal, fg_color=C["card"], corner_radius=8, border_width=1,
                               border_color=C["border"])
            box.grid(row=i, column=0, sticky="ew", pady=2)
            when = time.strftime("%d %b %Y, %H:%M", time.localtime(e["time"]))
            files = ", ".join(f"{f['path']} (+{f['added']} −{f['removed']})" for f in e["files"][:5])
            ctk.CTkLabel(box, text=f"{when}  ·  {files}", font=Fonts.ui(11), text_color=C["muted"], anchor="w",
                         wraplength=560, justify="left").pack(fill="x", padx=12, pady=(6, 0))
            text = (e.get("explanation") or "").strip()
            if text and text != "marked as reviewed":
                short = text if len(text) < 400 else text[:400].rsplit(" ", 1)[0] + "…"
                ctk.CTkLabel(box, text=short, font=Fonts.ui(12), text_color=C["text2"], anchor="w",
                             wraplength=560, justify="left").pack(fill="x", padx=12, pady=(2, 8))
            else:
                ctk.CTkLabel(box, text="", height=4).pack()

    # ================= actions =================
    def _start(self, title, display, build, journal=None):
        self.on_start(title, display, build, self.project, journal)
        self.destroy()

    def _explain_changes(self):
        if not self._need_project():
            return
        ch = self.project.changes(self.blocked)
        if not ch:
            self._refresh_changes()
            return
        pmap, chars = self.pmap, self._chars()
        n = len(ch)
        self._start(f"Changes in {self.project.name}",
                    f"Explain what changed in {self.project.name} since my last review ({n} file{'s' if n != 1 else ''}).",
                    lambda: cx.prompt_changes(pmap, ch, chars), journal=ch)

    def _explain_selection(self):
        if not self._need_project():
            return
        if not self.selection:
            self.sel_lbl.configure(text="Pick a file or function on the left first")
            return
        rel, it = self.selection
        depth = DEPTHS[self.depth.get()]
        name = rel if it is None else (f"{it['class']}.{it['name']}" if it.get("class") else it["name"])
        pmap, proj, chars = self.pmap, self.project, self._chars()
        self._start(f"Explain {name}", f"Explain {name} ({self.depth.get().lower()}).",
                    lambda: cx.prompt_explain(pmap, proj, rel, it, depth, chars))

    def _trace(self):
        q = self.trace_q.get().strip()
        if not self._need_project() or not q:
            return
        pmap, proj, chars = self.pmap, self.project, self._chars()
        embed = getattr(self.master, "embed", None)
        self._start(f"Trace: {q}", f"In {self.project.name}: {q}",
                    lambda: cx.prompt_trace(pmap, proj, q, embed, chars))

    def _overview(self):
        if not self._need_project():
            return
        pmap, chars = self.pmap, self._chars()
        self._start(f"Overview of {self.project.name}", f"Give me an overview of {self.project.name}.",
                    lambda: cx.prompt_project(pmap, chars))

    def _quiz(self):
        if not self._need_project():
            return
        pmap, proj, chars = self.pmap, self.project, self._chars()
        self._start(f"Quiz on {self.project.name}", f"Quiz me on {self.project.name}.",
                    lambda: cx.prompt_quiz(pmap, proj, chars))

    def _error(self):
        tb = self.err.get("1.0", "end").strip()
        if not self._need_project() or not tb:
            return
        pmap, proj = self.pmap, self.project
        first = tb.strip().splitlines()[-1][:60]
        self._start(f"Error: {first}", "Explain this error:\n" + tb[-1500:], lambda: cx.prompt_error(pmap, proj, tb))
