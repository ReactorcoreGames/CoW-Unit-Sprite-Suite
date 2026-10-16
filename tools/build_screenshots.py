"""Takes the three GUI screenshots in promo/ -- run on a desktop, hands off.

    python tools/build_screenshots.py

Opens each program maximized in its default theme, sets up a representative
view, grabs the window and closes it again. Needs a real display. It moves
the mouse pointer to the screen corner first, so no tooltip lands in the shot.

Writes promo/05_previewer.png, promo/06_wildcard_generator.png and
promo/10_ai_fixer_gui.png (skipped without the AI test sheet).
"""

from __future__ import annotations

import random
import sys
import pathlib

from PIL import ImageGrab

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from approot import ROOT  # noqa: E402

PROMO = ROOT / "promo"
SETTLE_MS = 2500


def _shoot(app, out: pathlib.Path) -> None:
    """Maximize, wait for the layout to settle, grab the window's own rect."""
    app.state("zoomed")
    app.attributes("-topmost", True)
    app.lift()
    app.focus_force()

    # Park the pointer in the screen corner (the taskbar), clear of every
    # tooltip; by grab time any popup it raised has closed again.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.SetCursorPos(app.winfo_screenwidth() - 1,
                                          app.winfo_screenheight() - 1)

    def grab() -> None:
        app.update()
        # The outer frame, title bar included, so the per-program icon shows.
        x, y = app.winfo_rootx(), app.winfo_y()
        w = app.winfo_width()
        h = app.winfo_rooty() - y + app.winfo_height()
        ImageGrab.grab((max(0, x), max(0, y), x + w, y + h)).save(out)
        print(f"{out.name}  {w}x{h}")
        app.after(10, app.quit)

    app.after(SETTLE_MS, grab)
    app.mainloop()
    app.destroy()


def previewer() -> None:
    import previewer as pv

    app = pv.Previewer()
    names = [p.name for p in app.paths]
    app.sprite_i = names.index("megatank+mask.png") if "megatank+mask.png" in names else 0
    app._load_sprite()
    app._guess_frames()                       # one frame of the idle strip
    app.team_i = app.teams.index("blue_moon")
    app.bg_i = app.backgrounds.index("dusk")
    app.zoom_i = pv.ZOOMS.index(16)
    app.render()
    _shoot(app, PROMO / "05_previewer.png")


def wildcard_generator() -> None:
    import wildcard_gui as wg

    app = wg.WildcardGUI()
    app.rng = random.Random(2)                # "orbital military vehicle with a bombard"
    app.randomize()
    app._sync_prompt_controls()
    # copy_prompt fills the hint and status line, but also the clipboard --
    # hand the user's clipboard back afterwards.
    try:
        saved = app.clipboard_get()
    except Exception:
        saved = None
    app.copy_prompt()
    if saved is not None:
        app.clipboard_clear()
        app.clipboard_append(saved)
    _shoot(app, PROMO / "06_wildcard_generator.png")


# A real Nano Banana 2 Lite sheet, as in build_promo.py's 09_ai_fixer.
AI_SHEET = ROOT / "testdata" / "ai sheets" / "Nanobanana Attempts (3).jpg"


def ai_fixer() -> None:
    import aifix_gui as ag

    if not AI_SHEET.is_file():
        print(f"skip  10_ai_fixer_gui.png ({AI_SHEET.name} not present)")
        return
    app = ag.FixerGUI(AI_SHEET)
    app.state("zoomed")
    app.attributes("-topmost", True)

    def ready() -> None:
        # the sheet is cut and snapped on a worker thread; wait for it
        if app.sheet is None or app._busy or app._pending:
            app.after(200, ready)
            return
        app.step_team(1)                      # blue_moon
        app.select(7)
        app.after(10, app.quit)

    app.after(500, ready)
    app.mainloop()
    _shoot(app, PROMO / "10_ai_fixer_gui.png")


def main() -> int:
    previewer()
    wildcard_generator()
    ai_fixer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
