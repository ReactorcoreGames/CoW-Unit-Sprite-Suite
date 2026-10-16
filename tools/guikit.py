"""Window chrome shared by both GUIs: icon, light/dark switch, Guide button,
tooltips and a muted text style.

Presentation only -- no tool logic lives here. The previewer and the wildcard
generator each pick their own theme family; this module just wires up the bits
that behave the same in both.
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser

import ttkbootstrap as tb

from approot import ROOT

GUIDE = ROOT / "guide.md"
# The hand-drawn reference sheet the AI route edits, with the class sheets and
# the 91-unit gallery beside it.
REFERENCE = ROOT / "pipeline" / "reference"

# Hints, counters and the status line. Bootstrap's `secondary` used to be a
# grey in litera; in the 2.x themes it is an accent (purple in pydata, orange
# in gruvbox), so quiet text gets its own style mixed from the theme's fg/bg.
MUTED = "Muted.TLabel"
MUTED_SMALL = "MutedSmall.TLabel"

# Tooltips wait a moment so they do not flicker past while the mouse travels.
TIP_DELAY = 450
TIP_WRAP = 340


def mix(a: str, b: str, t: float) -> str:
    """Hex color `t` of the way from a to b."""
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def setup(win: tb.Window, icon: str, on_theme=None) -> None:
    """Icon plus the theme-dependent styles; `on_theme(colors)` re-runs on
    every light/dark switch, for the widgets a style cannot reach (canvases)."""
    set_icon(win, icon)

    def restyle(style) -> None:
        c = style.colors
        muted = mix(c.fg, c.bg, 0.32)
        style.configure(MUTED, foreground=muted)
        style.configure(MUTED_SMALL, foreground=muted, font=("", 8))
        if on_theme:
            on_theme(c)

    win.style.on_theme_change(restyle)


def set_icon(win, stem: str) -> None:
    """The exe carries its own icon (cow_suite.spec); from source, promo/ has it.

    Without this the title bar shows ttkbootstrap's feather. Set on the window
    itself and as the default (so dialogs inherit it). iconphoto, tried first,
    shows a blank placeholder on Windows; the .ico must hold BMP entries, which
    build_promo.py writes. Cosmetic, so a missing file is never an error.
    """
    src = sys.executable if getattr(sys, "frozen", False) else ROOT / "promo" / f"{stem}.ico"
    try:
        win.iconbitmap(str(src))
        win.iconbitmap(default=str(src))
    except Exception:
        pass


def tip(widget, text: str):
    """Hover help. Returns the widget so it can wrap a constructor inline."""
    tb.ToolTip(widget, text=text, delay=TIP_DELAY, wraplength=TIP_WRAP)
    return widget


CREDIT_URL = "https://reactorcoregames.github.io/"


def credit_label(parent) -> tb.Label:
    """The "Made by Reactorcore" credit, a link to the author's site."""
    lbl = tb.Label(parent, text="Made by Reactorcore", style=MUTED_SMALL,
                   cursor="hand2")
    lbl.bind("<Button-1>", lambda e: webbrowser.open(CREDIT_URL))
    return tip(lbl, f"Open {CREDIT_URL}")


def open_guide(status=None) -> None:
    """Open guide.md in whatever the system uses for Markdown, else Notepad.

    Windows asks which app to use when nothing is associated with .md; that
    is still better than failing, and Notepad covers the error case.
    """
    if not GUIDE.is_file():
        if status:
            status.set(f"guide.md not found next to the program ({GUIDE.parent}).")
        return
    try:
        if sys.platform == "win32":
            os.startfile(GUIDE)  # noqa: S606 -- a local doc the user asked to open
        else:
            subprocess.Popen(["xdg-open", str(GUIDE)])
    except OSError:
        subprocess.Popen(["notepad.exe", str(GUIDE)])
    if status:
        status.set("Opened guide.md.")


def open_folder(folder, status=None) -> None:
    """Show `folder` in the system file browser (Explorer on Windows)."""
    if not folder.is_dir():
        if status:
            status.set(f"{folder.name} not found ({folder}).")
        return
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 -- a local folder the user asked to open
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    if status:
        status.set(f"Opened {folder}.")


def theme_button(parent, win: tb.Window) -> tb.Button:
    """Light/dark switch. The label names the mode a click switches *to*."""
    btn = tb.Button(parent, bootstyle="secondary-outline", width=6)

    def label(_style=None) -> None:
        dark = win.theme_mode == "dark"
        btn.configure(text="Light" if dark else "Dark")
        tb.apply_icon(btn, "sun" if dark else "moon-stars")

    btn.configure(command=win.toggle_theme)
    win.style.on_theme_change(label)
    return tip(btn, "Switch between the light and dark look. Only changes the "
                    "colors of this window -- sprites and exports are unaffected.")


def guide_button(parent, status=None) -> tb.Button:
    btn = tb.Button(parent, text="Guide", icon="book", bootstyle="info-outline",
                    command=lambda: open_guide(status))
    return tip(btn, "Open the user guide (guide.md): step-by-step instructions "
                    "for drawing, converting, previewing and generating units.")
