"""Records the trailer's two GUI clips -- run on a desktop, hands off.

    python tools/record_trailer_clips.py [previewer | fixer]

Drives the real previewer and AI Sprite Fixer (from source, so the same
windows as the exes), maximized and topmost, and grabs one PNG per UI state
instead of filming in real time: the trailer decides how long each state is
held, so the clip is the same on every run. Needs a 1920x1080 display. The
pointer is parked in the screen corner first, so no tooltip lands in a frame.

Writes promo/trailer/clips/previewer/NNN.png (stepping armies, backgrounds
and sprites, as W/S, Q/E and A/D do) and promo/trailer/clips/fixer/NNN.png
(stepping armies, then the size slider down and back to native; skipped
without the AI test sheet). tools/build_trailer.py uses them when present and
falls back to the still screenshots when not.
"""

from __future__ import annotations

import pathlib
import sys

from PIL import ImageGrab

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from approot import ROOT  # noqa: E402

CLIPS = ROOT / "promo" / "trailer" / "clips"
AI_SHEET = ROOT / "testdata" / "ai sheets" / "Nanobanana Attempts (3).jpg"
SETTLE_MS = 2500
STEP_MS = 350


def _grab(app, out: pathlib.Path) -> None:
    app.update()
    x, y = app.winfo_rootx(), app.winfo_y()
    w = app.winfo_width()
    h = app.winfo_rooty() - y + app.winfo_height()
    ImageGrab.grab((max(0, x), max(0, y), x + w, y + h)).save(out)


def _stage(app, folder: pathlib.Path) -> None:
    """Maximize and clear out last run's frames (moved aside, never deleted)."""
    folder.mkdir(parents=True, exist_ok=True)
    old = sorted(folder.glob("*.png"))
    if old:
        aside = folder.parent / f"{folder.name}.old"
        aside.mkdir(exist_ok=True)
        for p in old:
            p.replace(aside / p.name)
    app.state("zoomed")
    app.attributes("-topmost", True)
    app.lift()
    app.focus_force()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.SetCursorPos(app.winfo_screenwidth() - 1,
                                          app.winfo_screenheight() - 1)


def _run(app, folder: pathlib.Path, steps: list, ready=lambda: True) -> None:
    """Grab, apply the next step, wait until `ready()`, grab again, ..."""
    n = [0]

    def shoot() -> None:
        if not ready():
            app.after(100, shoot)
            return
        app.after(STEP_MS, take)

    def take() -> None:
        _grab(app, folder / f"{n[0]:03d}.png")
        if n[0] >= len(steps):
            print(f"{folder.name}: {n[0] + 1} frames")
            app.after(10, app.quit)
            return
        steps[n[0]]()
        n[0] += 1
        shoot()

    app.after(SETTLE_MS, shoot)
    app.mainloop()
    app.destroy()


def previewer() -> None:
    import previewer as pv

    app = pv.Previewer()
    names = [p.name for p in app.paths]
    app.sprite_i = names.index("megatank+mask.png") if "megatank+mask.png" in names else 0
    app._load_sprite()
    app._guess_frames()
    app.team_i = 0
    app.bg_i = app.backgrounds.index("day")
    app.zoom_i = pv.ZOOMS.index(16)
    app.render()
    _stage(app, CLIPS / "previewer")
    steps = ([lambda: app.step_team(1)] * 7          # S: through the armies
             + [lambda: app.step_bg(1)] * 3          # E: dusk, night, key green
             + [lambda: app.step_bg(-1)] * 2         # Q: back to dusk
             + [lambda: app.step_sprite(1)] * 4)     # D: the next sprites
    _run(app, CLIPS / "previewer", steps)


def ai_fixer() -> None:
    import aifix_gui as ag

    if not AI_SHEET.is_file():
        print(f"skip  fixer clip ({AI_SHEET.name} not present)")
        return
    app = ag.FixerGUI(AI_SHEET)
    _stage(app, CLIPS / "fixer")

    def idle() -> bool:
        return app.sheet is not None and not app._busy and not app._pending \
            and not app._debounce

    def size(w: int):
        def go() -> None:
            app.width_var.set(w)
            app._on_size_drag()               # debounced snap; idle() waits for it
        return go

    def native() -> None:
        app.size_native()

    def setup() -> None:
        app.select(7)

    steps = [setup] + [lambda: app.step_team(1)] * 6
    # the size slider: 64, 48, 32 px wide, then back to native
    steps += [size(64), size(48), size(32), native]
    _run(app, CLIPS / "fixer", steps, ready=idle)


def main(argv: list[str]) -> int:
    # One process per window: a second Tk app in the same process can have the
    # first one's variables garbage-collected on the fixer's worker thread,
    # which kills Tcl.
    if argv:
        {"previewer": previewer, "fixer": ai_fixer}[argv[0]]()
        return 0
    import subprocess
    for which in ("previewer", "fixer"):
        subprocess.run([sys.executable, __file__, which], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
