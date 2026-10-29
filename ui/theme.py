"""Colours and fonts for the "Premium Dark Workspace" look (Concept B).

Keeping every colour in one place means the rest of the UI never hard-codes
a hex value, so a light theme later is a matter of adding a second palette.
"""
import tkinter.font as tkfont

import customtkinter as ctk

C = {
    # surfaces, darkest to lightest
    "bg": "#0F1115",
    "panel": "#13161B",
    "card": "#171A20",
    "raised": "#1A1D24",
    "code_bg": "#0B0D10",
    "border": "#232831",
    "border2": "#2A303A",
    # text
    "text": "#E8EAED",
    "text2": "#C4CAD3",
    "muted": "#9AA3AF",
    "faint": "#5D6673",
    # accent (warm amber)
    "accent": "#E3B062",
    "accent_hi": "#F4CD8C",
    "accent_bg": "#241F15",
    "accent_sel": "#2A2416",
    "accent_border": "#3A3222",
    "on_accent": "#1A1408",
    # where a model runs
    "local_bg": "#13261D",
    "local_fg": "#86EFAC",
    "cloud_bg": "#152036",
    "cloud_fg": "#93C5FD",
    # status
    "ok": "#4ADE80",
    "warn": "#FBBF24",
    "err": "#F87171",
    "stop_bg": "#241818",
    "stop_border": "#3A2A2A",
    "stop_fg": "#FCA5A5",
}


def _first_available(candidates, fallback):
    try:
        families = set(tkfont.families())
    except Exception:
        return fallback
    for name in candidates:
        if name in families:
            return name
    return fallback


class Fonts:
    """Created once, after the root window exists (tk needs a root to list fonts)."""

    ui_family = "Segoe UI"
    mono_family = "Consolas"

    @classmethod
    def init(cls):
        cls.ui_family = _first_available(
            ["Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica"], "TkDefaultFont")
        cls.mono_family = _first_available(
            ["Cascadia Code", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"], "TkFixedFont")

    @classmethod
    def ui(cls, size=14, weight="normal"):
        return ctk.CTkFont(family=cls.ui_family, size=size, weight=weight)

    @classmethod
    def mono(cls, size=13, weight="normal"):
        return ctk.CTkFont(family=cls.mono_family, size=size, weight=weight)


# Friendly names for the router's model keys
MODEL_INFO = {
    "local-general": {"name": "Llama 3.1 8B", "desc": "General chat, private", "where": "Local"},
    "local-coder": {"name": "Qwen Coder 7B", "desc": "Code, refactors", "where": "Local"},
    "cloud-gemini": {"name": "Gemini 2.0 Flash", "desc": "Long context, research", "where": "Cloud"},
    "cloud-groq": {"name": "Groq · Llama 3.3 70B", "desc": "Fast, high quality", "where": "Cloud"},
}


def model_label(route_name):
    """'local-general (fallback)' -> 'Llama 3.1 8B (fallback)'."""
    base = route_name.split(" ")[0]
    info = MODEL_INFO.get(base)
    if not info:
        return route_name
    suffix = route_name[len(base):]
    return info["name"] + suffix


def where(route_name):
    return MODEL_INFO.get(route_name.split(" ")[0], {}).get("where", "Local")


def section_label(master, text):
    return ctk.CTkLabel(master, text=text.upper(), font=Fonts.ui(11, "bold"),
                        text_color=C["muted"], anchor="w")
