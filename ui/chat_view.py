"""Centre column: header, scrolling conversation, message widgets."""
import os
import re

import customtkinter as ctk

from .theme import C, Fonts, model_label, where


class AutoText(ctk.CTkTextbox):
    """A read-only textbox that grows to fit its content.

    Why a textbox and not a label? Text can be selected and copied, it wraps
    reliably at any width, and tags let us style code blocks and bold text.
    """

    def __init__(self, master, text="", size=14, text_color=None):
        super().__init__(master, wrap="word", height=24, activate_scrollbars=False,
                         fg_color="transparent", border_width=0, border_spacing=0,
                         corner_radius=0, font=Fonts.ui(size),
                         text_color=text_color or C["text"])
        self._size = size
        self._last_h = 0
        self._textbox.configure(padx=0, pady=2, spacing1=1, spacing3=3,
                                insertwidth=0, cursor="arrow")
        self._setup_tags()
        self.configure(state="disabled")
        self._textbox.bind("<Configure>", lambda e: self.fit(), add="+")
        if text:
            self.append(text)

    # ---- content ----
    def append(self, text):
        self.configure(state="normal")
        self.insert("end", text)
        self.configure(state="disabled")

    def get_text(self):
        return self._textbox.get("1.0", "end-1c")

    def fit(self):
        """Resize to the pixel height of the wrapped text."""
        tb = self._textbox
        try:
            px = int(tb.tk.call(tb._w, "count", "-update", "-ypixels", "1.0", "end"))
        except Exception:
            return
        h = max(22, round(px / self._get_widget_scaling()) + 6)
        if abs(h - self._last_h) > 1:  # avoid a resize -> <Configure> -> resize loop
            self._last_h = h
            self.configure(height=h)

    # ---- light markdown: ``` code blocks, `inline`, **bold**, # headings ----
    def _setup_tags(self):
        tb = self._textbox
        s = self._get_widget_scaling()
        mono = (Fonts.mono_family, -round(13 * s))
        tb.tag_configure("code", font=mono, background=C["code_bg"], foreground="#D6DAE0",
                         lmargin1=14, lmargin2=14, rmargin=14, spacing1=0, spacing3=0)
        tb.tag_configure("fence", font=(Fonts.mono_family, -round(11 * s)), background=C["code_bg"],
                         foreground=C["faint"], lmargin1=14, spacing1=6, spacing3=4)
        tb.tag_configure("inline", font=mono, foreground=C["accent_hi"], background=C["raised"])
        tb.tag_configure("bold", font=(Fonts.ui_family, -round(self._size * s), "bold"),
                         foreground="#FFFFFF")
        tb.tag_configure("heading", font=(Fonts.ui_family, -round((self._size + 2) * s), "bold"),
                         foreground="#FFFFFF", spacing1=8)
        tb.tag_configure("hidden", elide=True)
        tb.tag_raise("hidden")

    def highlight(self):
        tb = self._textbox
        for t in ("code", "fence", "inline", "bold", "heading", "hidden"):
            tb.tag_remove(t, "1.0", "end")
        in_code = False
        for i, line in enumerate(self.get_text().split("\n"), start=1):
            if line.lstrip().startswith("```"):
                tb.tag_add("fence", f"{i}.0", f"{i}.end+1c")
                ticks = line.index("```")
                tb.tag_add("hidden", f"{i}.{ticks}", f"{i}.{ticks + 3}")  # keep only the language name
                in_code = not in_code
                continue
            if in_code:
                tb.tag_add("code", f"{i}.0", f"{i}.end+1c")
                continue
            m = re.match(r"(#{1,4} )", line)
            if m:
                tb.tag_add("hidden", f"{i}.0", f"{i}.{len(m.group(1))}")
                tb.tag_add("heading", f"{i}.0", f"{i}.end")
            for m in re.finditer(r"`([^`\n]+)`", line):
                tb.tag_add("hidden", f"{i}.{m.start()}", f"{i}.{m.start() + 1}")
                tb.tag_add("inline", f"{i}.{m.start() + 1}", f"{i}.{m.end() - 1}")
                tb.tag_add("hidden", f"{i}.{m.end() - 1}", f"{i}.{m.end()}")
            for m in re.finditer(r"\*\*([^*\n]+)\*\*", line):
                tb.tag_add("hidden", f"{i}.{m.start()}", f"{i}.{m.start() + 2}")
                tb.tag_add("bold", f"{i}.{m.start() + 2}", f"{i}.{m.end() - 2}")
                tb.tag_add("hidden", f"{i}.{m.end() - 2}", f"{i}.{m.end()}")


def _avatar(master, text=None, assistant=False):
    return ctk.CTkLabel(
        master, text=text or "", width=30, height=30, corner_radius=15,
        fg_color=C["accent_bg"] if assistant else C["border2"],
        text_color=C["accent"] if assistant else C["text"],
        font=Fonts.ui(12, "bold"))


class UserMessage(ctk.CTkFrame):
    def __init__(self, master, text):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        initial = (os.getenv("USERNAME") or "You")[0].upper()
        _avatar(self, initial).grid(row=0, column=0, sticky="n", padx=(0, 12))
        self.body = AutoText(self, text, text_color=C["text"])
        self.body.grid(row=0, column=1, sticky="ew", pady=(3, 0))


class AssistantMessage(ctk.CTkFrame):
    def __init__(self, master, show_meta=True):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        _avatar(self, "◎", assistant=True).grid(row=0, column=0, sticky="n", padx=(0, 12))

        col = ctk.CTkFrame(self, fg_color="transparent")
        col.grid(row=0, column=1, sticky="ew")
        col.grid_columnconfigure(0, weight=1)

        self.meta_row = ctk.CTkFrame(col, fg_color="transparent")
        self.pill = ctk.CTkLabel(self.meta_row, text="", height=22, corner_radius=6,
                                 font=Fonts.ui(11, "bold"))
        self.pill.pack(side="left")
        self.meta = ctk.CTkLabel(self.meta_row, text="", font=Fonts.ui(12), text_color=C["muted"])
        self.meta.pack(side="left", padx=(8, 0))
        if show_meta:
            self.meta_row.grid(row=0, column=0, sticky="w", pady=(4, 4))

        self.body = AutoText(col, text_color="#DADDE2")
        self.body.grid(row=1, column=0, sticky="ew")

    def set_streaming(self, route_name):
        self.pill.configure(text="  ● Streaming  ", fg_color=C["local_bg"], text_color=C["local_fg"])
        if route_name:
            self.meta.configure(text=f"{model_label(route_name)} · {where(route_name).lower()}")

    def set_waiting(self, route_name):
        """route_name None = still deciding which model to use."""
        if route_name is None:
            self.pill.configure(text="  ● Routing  ", fg_color=C["raised"], text_color=C["text2"])
            self.meta.configure(text="choosing a model…")
            return
        self.pill.configure(text="  ● Thinking  ", fg_color=C["accent_bg"], text_color=C["accent_hi"])
        self.meta.configure(text=f"{model_label(route_name)} · {where(route_name).lower()}")

    def set_done(self, route_name, meta_text, stopped=False):
        if stopped:
            self.pill.configure(text="  Stopped  ", fg_color=C["stop_bg"], text_color=C["stop_fg"])
        elif "fallback" in route_name:
            self.pill.configure(text="  Fallback  ", fg_color=C["accent_bg"], text_color=C["warn"])
        else:
            local = where(route_name) == "Local"
            self.pill.configure(text=f"  {where(route_name)}  ",
                                fg_color=C["local_bg"] if local else C["cloud_bg"],
                                text_color=C["local_fg"] if local else C["cloud_fg"])
        self.meta.configure(text=meta_text)

    def append(self, text):
        self.body.append(text)
        self.body.highlight()
        self.body.fit()


class ChatView(ctk.CTkFrame):
    SUGGESTIONS = [
        "Explain how self-attention works, intuitively",
        "Write a Python function to chunk a PDF for RAG",
        "Compare FAISS, Chroma and Qdrant for a laptop",
    ]

    def __init__(self, master, on_toggle_panel, on_suggestion):
        super().__init__(master, corner_radius=0, fg_color=C["bg"])
        self.on_suggestion = on_suggestion
        self.show_meta = True
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- header ---
        header = ctk.CTkFrame(self, fg_color="transparent", height=64)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w", padx=28, pady=12)
        self.title = ctk.CTkLabel(titles, text="New conversation", font=Fonts.ui(15, "bold"),
                                  text_color=C["text"], anchor="w", height=20)
        self.title.pack(anchor="w")
        self.subtitle = ctk.CTkLabel(titles, text="", font=Fonts.ui(12), text_color=C["muted"],
                                     anchor="w", height=16)
        self.subtitle.pack(anchor="w")

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=20)
        self.mode_pill = ctk.CTkLabel(right, text="", height=30, corner_radius=15,
                                      fg_color=C["raised"], font=Fonts.ui(12), text_color=C["text2"])
        self.mode_pill.pack(side="left", padx=(0, 8))
        self.panel_btn = ctk.CTkButton(
            right, text="Hide panel", width=96, height=32, corner_radius=8,
            fg_color="transparent", hover_color=C["raised"], border_width=1,
            border_color=C["border2"], text_color=C["text2"], font=Fonts.ui(12),
            command=on_toggle_panel)
        self.panel_btn.pack(side="left")
        ctk.CTkFrame(self, height=1, fg_color=C["border"]).grid(row=0, column=0, sticky="sew")

        # --- conversation ---
        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color=C["bg"], corner_radius=0,
            scrollbar_button_color=C["border2"], scrollbar_button_hover_color=C["faint"])
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        self._rows = 0
        self.empty = None
        self.show_empty_state()

    # ---- header ----
    def set_title(self, title, subtitle=""):
        self.title.configure(text=title)
        self.subtitle.configure(text=subtitle)

    def set_mode(self, mode):
        label = {"auto": "Auto", "local": "Local only", "cloud": "Cloud"}[mode]
        self.mode_pill.configure(text=f"   Mode · {label}   ")

    def set_panel_visible(self, visible):
        self.panel_btn.configure(text="Hide panel" if visible else "Show panel")

    # ---- messages ----
    def clear(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        self._rows = 0
        self.empty = None

    def show_empty_state(self):
        self.clear()
        self.empty = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.empty.grid(row=0, column=0, pady=(120, 0))
        ctk.CTkLabel(self.empty, text="What are we working on?", font=Fonts.ui(22, "bold"),
                     text_color=C["text"]).pack()
        ctk.CTkLabel(self.empty, text="Pick a mode on the right, or just ask — Auto routes each prompt.",
                     font=Fonts.ui(13), text_color=C["muted"]).pack(pady=(6, 18))
        for s in self.SUGGESTIONS:
            ctk.CTkButton(self.empty, text=s, height=40, width=380, corner_radius=10,
                          fg_color=C["card"], hover_color=C["raised"], border_width=1,
                          border_color=C["border"], text_color=C["text2"], font=Fonts.ui(13),
                          command=lambda t=s: self.on_suggestion(t)).pack(pady=4)

    def _place(self, widget):
        if self.empty is not None:
            self.empty.destroy()
            self.empty = None
        widget.grid(row=self._rows, column=0, sticky="ew", padx=28, pady=(22 if self._rows == 0 else 0, 22))
        self._rows += 1

    def add_user(self, text):
        msg = UserMessage(self.scroll, text)
        self._place(msg)
        self.scroll_to_bottom(force=True)
        return msg

    def add_assistant(self):
        msg = AssistantMessage(self.scroll, show_meta=self.show_meta)
        self._place(msg)
        self.scroll_to_bottom(force=True)
        return msg

    def is_near_bottom(self):
        try:
            return self.scroll._parent_canvas.yview()[1] > 0.97
        except Exception:
            return True

    def go_bottom(self):
        # Let pending layout (textbox heights, scroll region) settle first,
        # otherwise we jump to where the bottom *used* to be.
        self.scroll.update_idletasks()
        self.scroll._parent_canvas.yview_moveto(1.0)

    def scroll_to_bottom(self, force=False):
        if force or self.is_near_bottom():
            self.after(30, self.go_bottom)
