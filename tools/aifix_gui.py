"""AI sprite fixer -- ttkbootstrap GUI over tools/aifix.py.

Open an AI-generated sheet, check the cut, pick a size, tick which units to
keep, look at them in any army, and export CoW sprites plus army-colored PNGs.

All of the work -- keying, cutting, snapping, color, encoding, writing files --
is `aifix.prepare_sheet`, `aifix.snap_sheet` and `aifix.write_outputs`. This
file is the shell, and `tools/test_aifix.py` asserts it reimplements none of
it. The core runs on a worker thread: a full re-snap takes 1-3 s at native size.

    A / D        previous / next unit        Space   keep / reject that unit
    V            even out that unit          W / S   previous / next army
    R / F        zoom out / in               [ / ]   smaller / bigger (size)
    Ctrl+O       open sheet      Ctrl+E  export      Ctrl+P  export + open in previewer
    Esc          leave a text field (never quits)

Run:  python tools/aifix_gui.py [sheet]
"""

from __future__ import annotations

import pathlib
import queue
import subprocess
import sys
import threading
import traceback

import ttkbootstrap as tb
from PIL import Image, ImageTk
from tkinter import filedialog, messagebox
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X, Y, W

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import aifix  # noqa: E402  -- the fixer core: prepare_sheet, snap_sheet, write_outputs
import cowpalette as cp  # noqa: E402
import guikit as gk  # noqa: E402  -- icon, light/dark, Guide button, tooltips
from guikit import tip  # noqa: E402

from approot import ROOT  # noqa: E402  -- project root, or the exe folder when frozen

OUTPUT = ROOT / "output" / "aifix"
# Where Open sheet starts: the AI test sheets in a source checkout, else the root.
_START = ROOT / "testdata" / "ai sheets"
START_DIR = _START if _START.is_dir() else ROOT

# everforest, dark by default: green for Green Earth (this program's icon), and
# unlike the previewer's pydata-dark and the wildcard generator's gruvbox-light.
LIGHT_THEME, DARK_THEME = "everforest-light", "everforest-dark"

ZOOMS = (1, 2, 3, 4, 6, 8)
DEFAULT_ZOOM = 2
KEYS = ("auto", "green", "magenta")
EXPORT_ARMIES = ("none", "this army", "all armies")
SCALES = (1, 2, 3, 4, 6, 8)
SIZE_STEP = 4          # px of median width per [ / ] press
DEBOUNCE_MS = 350      # wait this long after the last slider move before re-snapping
POLL_MS = 60
CANVAS_STEP = 0.07     # the sprite canvases sit a step off the window background
CARD_PAD = 8


class Card:
    """One unit's tile in the results grid. Owns no state -- the GUI does."""

    def __init__(self, gui: "FixerGUI", parent, index: int):
        self.index = index
        self.frame = tb.Frame(parent, padding=4)
        self.canvas = tb.Canvas(self.frame, highlightthickness=0, borderwidth=0)
        self.canvas.pack(side="top")
        self.canvas.bind("<Button-1>", lambda e: gui.select(index))
        self.canvas.bind("<Double-Button-1>", lambda e: gui.toggle_keep(index))
        tip(self.canvas, "Click to select this unit (A / D also step through them). "
                         "Double-click to keep or reject it.")
        row = tb.Frame(self.frame)
        row.pack(side="top", fill=X, pady=(3, 0))
        # fixed width, so a card measures the same before and after its text is set
        self.label = tb.Label(row, text="", style=gk.MUTED, width=12)
        self.label.pack(side=LEFT)
        self.label_tip = tb.ToolTip(self.label, text="Unit number and its size in pixels.",
                                    delay=gk.TIP_DELAY, wraplength=gk.TIP_WRAP)
        tip(tb.Checkbutton(row, text="even", variable=gui.even[index],
                           bootstyle="warning-round-toggle", takefocus=False,
                           command=lambda: gui.on_even(index)),
            "Even out: pull this unit's body colors toward Orange Star's usual "
            "tones. For a unit the AI drew in the wrong shade (a peach car on a "
            "sheet of red ones). Leave it off for units that already look right "
            "-- it flattens their shading.  V").pack(side=RIGHT)
        tip(tb.Checkbutton(row, text="keep", variable=gui.keep[index],
                           bootstyle="success-round-toggle", takefocus=False,
                           command=lambda: gui.on_keep(index)),
            "Export this unit. Untick the duds: a mangled unit, a wrong view, "
            "a neighbour's leftovers.  Space").pack(side=RIGHT, padx=(0, 8))
        self._photo: ImageTk.PhotoImage | None = None


class FixerGUI(tb.Window):
    def __init__(self, sheet_path: pathlib.Path | None = None):
        super().__init__(themename=DARK_THEME, light_theme=LIGHT_THEME,
                         dark_theme=DARK_THEME, title="CoW AI Sprite Fixer")
        self.geometry("1280x800")
        self.minsize(980, 620)

        self.palettes = cp.load_palettes()
        self.teams = self.palettes.team_names()
        self.team_i = 0
        self.zoom_i = ZOOMS.index(DEFAULT_ZOOM)

        self.path: pathlib.Path | None = None
        self.image: Image.Image | None = None
        self.sheet: aifix.Sheet | None = None
        # What the results grid shows: one dict per unit, copied off the worker
        # when a snap finishes, so a snap in progress never half-updates the view.
        self.units: list[dict] = []
        self.selected = 0
        self.keep: dict[int, tb.BooleanVar] = {}
        self.even: dict[int, tb.BooleanVar] = {}
        self.cards: list[Card] = []
        self._card_cols = 0
        self.exported: pathlib.Path | None = None

        self.rows = tb.IntVar(value=4)
        self.cols = tb.IntVar(value=4)
        self.auto_layout = True           # layout comes from the sheet until edited
        self.key = tb.StringVar(value="auto")
        self.width_target: int | None = None   # None = the sheet's native size
        self.width_var = tb.DoubleVar(value=aifix.MIN_WIDTH)
        self.team_lock = tb.BooleanVar(value=True)
        self.smooth = tb.DoubleVar(value=aifix.DEFAULT_SMOOTH)
        self.export_armies = tb.StringVar(value="this army")
        self.export_scale = tb.StringVar(value="1")
        self.status = tb.StringVar(value="Open an AI sheet to start (Ctrl+O).")

        # one worker at a time; a request made while it runs is remembered and
        # run next with whatever the settings are by then
        self._jobs: queue.Queue = queue.Queue()
        self._busy = False
        self._pending: str | None = None
        self._debounce: dict[str, str] = {}
        self._sheet_photo: ImageTk.PhotoImage | None = None
        self._sheet_fit: tuple[float, int, int] = (1.0, 0, 0)
        self._colors = None

        self._build()
        gk.setup(self, "icon_aifix", self._on_theme)
        self._bind_keys()
        self.after(POLL_MS, self._poll)
        if sheet_path:
            self.after(50, lambda: self.open_sheet(sheet_path))

    # ------------------------------------------------------------------ ui

    def _build(self) -> None:
        top = tb.Frame(self, padding=(10, 8))
        top.pack(side="top", fill=X)

        def button(text, icon, style, cmd, help_, pad=6):
            b = tb.Button(top, text=text, icon=icon, bootstyle=style, command=cmd)
            b.pack(side=LEFT, padx=(pad, 0))
            return tip(b, help_)

        button("Open sheet", "folder2-open", "primary", self.pick_sheet,
               "Open a sheet of units from an image generator (JPEG, PNG or WebP): a "
               "grid of units in Orange Star red on a green or magenta backdrop. "
               "It is cut into units, snapped to pixels and turned into CoW "
               "sprites.  Ctrl+O", pad=0)
        self.btn_export = button(
            "Export", "download", "success", self.export,
            "Save the kept units to output/aifix/<sheet name>/: each unit's "
            "+mask.png (what CoW reads), its Orange Star art in rgb/ for touching "
            "up, and -- per the Export settings -- army-colored PNGs in armies/.  "
            "Ctrl+E", pad=10)
        self.btn_previewer = button(
            "Open in previewer", "eye", "info-outline", self.open_in_previewer,
            "Export, then open the exported +mask.png files in the CoW Sprite "
            "Previewer, for backgrounds, collages and the rest of its tools.  Ctrl+P")

        gk.theme_button(top, self).pack(side=RIGHT)
        gk.guide_button(top, self.status).pack(side=RIGHT, padx=(0, 6))

        # ---- settings, two rows
        row1 = tb.Frame(self, padding=(10, 2))
        row1.pack(side="top", fill=X)
        row2 = tb.Frame(self, padding=(10, 4))
        row2.pack(side="top", fill=X)

        layout_help = ("How many rows and columns of units the sheet holds. Guessed "
                       "when a sheet opens; the AI sometimes returns 4x3 or 4x5 "
                       "instead of what was asked, so check the cut lines over the "
                       "sheet and correct it here.")
        tip(tb.Label(row1, text="Layout"), layout_help).pack(side=LEFT)
        self.spin_rows = self._spinner(row1, self.rows, layout_help + " Rows.")
        tb.Label(row1, text="x").pack(side=LEFT, padx=3)
        self.spin_cols = self._spinner(row1, self.cols, layout_help + " Columns.")
        tip(tb.Button(row1, text="Guess", bootstyle="secondary-outline",
                      command=self.guess),
            "Go back to the layout read from the sheet's gaps.").pack(side=LEFT, padx=(6, 0))

        tip(tb.Label(row1, text="Backdrop"),
            "The background color to remove.").pack(side=LEFT, padx=(24, 4))
        key = tb.Combobox(row1, width=8, state="readonly", values=KEYS,
                          textvariable=self.key)
        key.pack(side=LEFT)
        key.bind("<<ComboboxSelected>>", lambda e: (self.focus_set(), self._request("prepare")))
        tip(key, "auto reads the backdrop from the sheet's border, which suits AI "
                 "output: a generator's 'pure green' never quite is. Pick green or "
                 "magenta to force the exact chroma color.")

        size_help = ("Size of the sprites, as the width of the sheet's middle-sized "
                     "unit. Starts at native: one pixel per pixel the AI drew, which "
                     "is as big as the art supports -- going bigger would only "
                     "invent pixels. Slide left for smaller sprites. CoW's own "
                     f"battle sprites are about {aifix.COW_BATTLE_WIDTH} px wide.  [ / ]")
        tip(tb.Label(row1, text="Size"), size_help).pack(side=LEFT, padx=(24, 4))
        self.size_scale = tb.Scale(row1, from_=aifix.MIN_WIDTH, to=aifix.MIN_WIDTH + 1,
                                   variable=self.width_var, length=260,
                                   command=lambda v: self._on_size_drag())
        self.size_scale.pack(side=LEFT)
        tip(self.size_scale, size_help)
        tip(tb.Button(row1, text="Native", bootstyle="secondary-outline",
                      command=self.size_native),
            "Back to the sheet's native size, the largest the art supports."
            ).pack(side=LEFT, padx=(6, 0))
        self.size_label = tb.Label(row1, text="", style=gk.MUTED)
        self.size_label.pack(side=LEFT, padx=(10, 0))
        tip(self.size_label, "Middle unit's width now; native size and the AI's "
                             "pixel grain it comes from; the narrowest and widest "
                             "unit at this size.")

        lock = tb.Checkbutton(row2, text="Team lock", variable=self.team_lock,
                              bootstyle="round-toggle",
                              command=lambda: self._request("snap"))
        lock.pack(side=LEFT)
        tip(lock, "On: every red-orange body pixel lands on a color that follows "
                  "the team, so no orange patch survives into Blue Moon or Green "
                  "Earth. Off: keeps gold and wood accents the AI drew, at the risk "
                  "of orange patches in other armies.")

        smooth_help = ("Folds specks of up to 3 pixels into a similar neighbour. "
                       f"{aifix.DEFAULT_SMOOTH:g} is the measured sweet spot; higher "
                       "flattens more of the AI's grain but starts eating real "
                       "detail. 0 = off.")
        tip(tb.Label(row2, text="Smooth"), smooth_help).pack(side=LEFT, padx=(24, 4))
        sm = tb.Scale(row2, from_=0, to=90, variable=self.smooth, length=160,
                      command=lambda v: self._on_smooth_drag())
        sm.pack(side=LEFT)
        tip(sm, smooth_help)
        self.smooth_label = tb.Label(row2, text="", width=3, style=gk.MUTED)
        self.smooth_label.pack(side=LEFT, padx=(6, 0))
        tip(tb.Button(row2, text="Default", bootstyle="secondary-outline",
                      command=self.smooth_default),
            f"Back to {aifix.DEFAULT_SMOOTH:g}.").pack(side=LEFT, padx=(4, 0))

        tb.Separator(row2, orient="vertical").pack(side=LEFT, fill=Y, padx=16)
        tip(tb.Label(row2, text="Export armies"),
            "Army-colored PNGs to write next to the CoW files, for using the "
            "sprites outside CoW.").pack(side=LEFT, padx=(0, 4))
        arm = tb.Combobox(row2, width=10, state="readonly", values=EXPORT_ARMIES,
                          textvariable=self.export_armies)
        arm.pack(side=LEFT)
        arm.bind("<<ComboboxSelected>>", lambda e: self.focus_set())
        tip(arm, "none: only the CoW files. this army: the army shown below "
                 "(W / S). all armies: every one of CoW's 32. Named "
                 "sprite+army.png, in armies/.")
        tip(tb.Label(row2, text="at"),
            "Scale of the army PNGs.").pack(side=LEFT, padx=(8, 4))
        sc = tb.Combobox(row2, width=3, state="readonly", values=[str(s) for s in SCALES],
                         textvariable=self.export_scale)
        sc.pack(side=LEFT)
        sc.bind("<<ComboboxSelected>>", lambda e: self.focus_set())
        tip(sc, "Blow the army PNGs up by a whole number, nearest neighbour, so "
                "every pixel stays a crisp square -- for a game or tool that wants "
                "bigger sprites. They go to 'armies Nx/'. The +mask.png for CoW "
                "is always 1x.")
        tb.Label(row2, text="x").pack(side=LEFT, padx=(3, 0))

        # ---- bottom bar and readout (packed before the body so they keep their space)
        bar = tb.Frame(self, padding=(10, 6))
        bar.pack(side="bottom", fill=X)
        tb.Label(bar, textvariable=self.status, style=gk.MUTED).pack(side=LEFT, anchor=W)
        self.progress = tb.Progressbar(bar, mode="indeterminate", length=120,
                                       bootstyle="success")
        gk.credit_label(bar).pack(side=RIGHT, padx=(16, 0))
        tip(tb.Label(bar, text="A/D unit   Space keep   V even   W/S army   R/F zoom   [ ] size",
                     style=gk.MUTED),
            "Keyboard shortcuts. The arrow keys also work: left/right for unit, "
            "up/down for army. Esc leaves a text box so the shortcuts work again."
            ).pack(side=RIGHT)

        read = tb.Frame(self, padding=(10, 2, 10, 0))
        read.pack(side="bottom", fill=X)

        def stepper(back, fwd, help_, minus="chevron-left", plus="chevron-right",
                    side=LEFT, pad=0, **label):
            parts = [
                tip(tb.Button(read, icon=minus, icon_only=True, bootstyle="secondary-ghost",
                              takefocus=False, command=back), help_),
                tip(tb.Label(read, text="-", **label), help_),
                tip(tb.Button(read, icon=plus, icon_only=True, bootstyle="secondary-ghost",
                              takefocus=False, command=fwd), help_),
            ]
            for i, w in enumerate(parts if side == LEFT else parts[::-1]):
                w.pack(side=side, padx=4 if i == 1 else ((pad, 0) if i == 0 else 0))
            return parts[1]

        self.lbl_unit = stepper(
            lambda: self.step_unit(-1), lambda: self.step_unit(1),
            "Previous / next unit. The selected one is outlined on the sheet and "
            "in the results.  A / D or Left / Right.", font=("", 11, "bold"))
        self.lbl_team = stepper(
            lambda: self.step_team(-1), lambda: self.step_team(1),
            "Army the results are shown in, through CoW's own color tables. 'mask' "
            "shows the file as CoW stores it.  W / S or Up / Down.",
            pad=18, bootstyle="primary")
        self.lbl_zoom = stepper(
            lambda: self.step_zoom(-1), lambda: self.step_zoom(1),
            "Zoom of the results. Whole steps only, so pixels stay sharp. Display "
            "only -- export size is set by Size and Export at.  R / F.",
            minus="zoom-out", plus="zoom-in", side=RIGHT)
        self.lbl_counts = tb.Label(read, text="", style=gk.MUTED)
        self.lbl_counts.pack(side=RIGHT, padx=(0, 18))
        tip(self.lbl_counts, "Units kept for export, and units that failed to "
                             "convert (shown with a !).")

        # ---- body: the sheet on the left, the results on the right
        body = tb.Panedwindow(self, orient="horizontal")
        body.pack(side="top", fill=BOTH, expand=True, padx=10, pady=(6, 4))

        left = tb.Labelframe(body, text="Sheet", padding=4)
        self.sheet_canvas = tb.Canvas(left, highlightthickness=0)
        self.sheet_canvas.pack(fill=BOTH, expand=True)
        self.sheet_canvas.bind("<Configure>", lambda e: self._later("sheet", self.draw_sheet, 80))
        self.sheet_canvas.bind("<Button-1>", self._sheet_click)
        self.sheet_canvas.bind("<Double-Button-1>", self._sheet_double)
        tip(self.sheet_canvas, "The sheet as opened, with the cut lines. Each "
                               "numbered cell becomes one unit. Click a cell to "
                               "select it, double-click to keep or reject it. If "
                               "lines cut through units, fix the Layout.")
        body.add(left, weight=2)

        right = tb.Labelframe(body, text="Results", padding=4)
        self.vbar = tb.Scrollbar(right, orient="vertical", bootstyle="secondary-round")
        self.vbar.pack(side=RIGHT, fill=Y)
        self.results_canvas = tb.Canvas(right, highlightthickness=0, borderwidth=0)
        self.results_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.results_canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.configure(command=self.results_canvas.yview)
        self.grid_frame = tb.Frame(self.results_canvas)
        self._grid_window = self.results_canvas.create_window((0, 0), window=self.grid_frame,
                                                              anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.results_canvas.configure(
            scrollregion=self.results_canvas.bbox("all")))
        self.results_canvas.bind("<Configure>", lambda e: self._later("cards", self._layout_cards, 80))
        self.results_canvas.bind("<Enter>", lambda e: self.results_canvas.bind_all(
            "<MouseWheel>", self._on_wheel))
        self.results_canvas.bind("<Leave>", lambda e: self.results_canvas.unbind_all("<MouseWheel>"))
        body.add(right, weight=3)

        self._set_busy(False)
        self._refresh_labels()

    def _spinner(self, parent, var, help_) -> tb.Spinbox:
        sp = tb.Spinbox(parent, from_=1, to=aifix.MAX_LAYOUT, width=3, textvariable=var,
                        command=self._on_layout)
        sp.pack(side=LEFT)
        sp.bind("<Return>", lambda e: (self._on_layout(), self.focus_set()))
        sp.bind("<KP_Enter>", lambda e: (self._on_layout(), self.focus_set()))
        sp.bind("<Escape>", lambda e: self.focus_set())
        return tip(sp, help_)

    def _on_theme(self, colors) -> None:
        self._colors = colors
        step = gk.mix(colors.bg, colors.fg, CANVAS_STEP)
        self.sheet_canvas.configure(background=step)
        self.results_canvas.configure(background=colors.bg)
        self.draw_sheet()
        self._draw_cards()

    def _on_wheel(self, event):
        self.results_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    # ---------------------------------------------------------------- keys

    def _typing(self) -> bool:
        try:
            w = self.focus_get()
        except KeyError:
            return False
        return isinstance(w, (tb.Spinbox, tb.Entry, tb.Combobox))

    def _nav(self, fn):
        def handler(event=None):
            if self._typing():
                return None
            fn()
            return "break"
        return handler

    def _bind_keys(self) -> None:
        for key, fn in (
            ("a", lambda: self.step_unit(-1)), ("d", lambda: self.step_unit(1)),
            ("w", lambda: self.step_team(-1)), ("s", lambda: self.step_team(1)),
            ("r", lambda: self.step_zoom(-1)), ("f", lambda: self.step_zoom(1)),
            ("v", lambda: self.toggle_even(self.selected_index)),
            ("space", lambda: self.toggle_keep(self.selected_index)),
            ("bracketleft", lambda: self.step_size(-SIZE_STEP)),
            ("bracketright", lambda: self.step_size(SIZE_STEP)),
        ):
            h = self._nav(fn)
            self.bind(f"<KeyPress-{key}>", h)
            if len(key) == 1:
                self.bind(f"<KeyPress-{key.upper()}>", h)
        self.bind("<Left>", self._nav(lambda: self.step_unit(-1)))
        self.bind("<Right>", self._nav(lambda: self.step_unit(1)))
        self.bind("<Up>", self._nav(lambda: self.step_team(-1)))
        self.bind("<Down>", self._nav(lambda: self.step_team(1)))
        self.bind("<Control-o>", lambda e: self.pick_sheet())
        self.bind("<Control-e>", lambda e: self.export())
        self.bind("<Control-p>", lambda e: self.open_in_previewer())
        # Esc leaves a text field. It deliberately does NOT quit, as in the
        # other two programs.
        self.bind("<Escape>", lambda e: self.focus_set())
        self.sheet_canvas.bind("<Button-1>", lambda e: self.focus_set(), add="+")

    def _later(self, name: str, fn, ms: int = DEBOUNCE_MS) -> None:
        """Run `fn` once, `ms` after the last call with this name."""
        if name in self._debounce:
            self.after_cancel(self._debounce[name])
        self._debounce[name] = self.after(ms, lambda: (self._debounce.pop(name, None), fn()))

    # ------------------------------------------------------------- worker

    def _request(self, kind: str) -> None:
        """Ask for a re-run: "prepare" (key, cut, isolate -- then snap) or
        "snap" (size, color, cleanup). Coalesces while the worker is busy."""
        if self.image is None:
            return
        if kind == "snap" and self.sheet is None:
            kind = "prepare"
        if self._busy:
            if self._pending != "prepare":
                self._pending = kind
            return
        self._start(kind)

    def _start(self, kind: str) -> None:
        image, name = self.image, self.path.stem if self.path else "sheet"
        layout = None if self.auto_layout else (self.rows.get(), self.cols.get())
        key = self.key.get()
        sheet = self.sheet if kind == "snap" else None
        width = self.width_target
        even = {i: 1.0 for i, v in self.even.items() if v.get()}
        lock = self.team_lock.get()
        smooth = round(self.smooth.get())

        def work():
            try:
                s = sheet
                if s is None:
                    s = aifix.prepare_sheet(image, name=name, layout=layout, key=key)
                pitch = s.pitch_for_width(width) if width else s.native_pitch
                aifix.snap_sheet(s, pitch, even=even, team_lock=lock, smooth=smooth)
                units = [{"index": u.index, "name": u.name,
                          "encoded": u.result.encoded.copy() if u.result else None,
                          "size": u.size, "ok": bool(u.result and u.result.ok),
                          "reason": u.result.reason if u.result else "empty cell"}
                         for u in s.units]
                self._jobs.put(("done", kind, s, units))
            except Exception as exc:  # reported in the status line, never fatal
                traceback.print_exc()
                self._jobs.put(("error", kind, exc, None))

        self._set_busy(True, "Cutting and measuring the sheet..." if kind == "prepare"
                       else "Snapping and coloring...")
        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                msg, kind, payload, units = self._jobs.get_nowait()
                self._finish(msg, kind, payload, units)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll)

    def _finish(self, msg: str, kind: str, payload, units) -> None:
        self._set_busy(False)
        if msg == "error":
            self.status.set(f"Could not process the sheet: {payload}")
        else:
            new_sheet = payload is not self.sheet
            self.sheet = payload
            self.exported = None
            if new_sheet:
                rows, cols = self.sheet.layout
                self.rows.set(rows)
                self.cols.set(cols)
                self.size_scale.configure(from_=aifix.MIN_WIDTH,
                                          to=max(aifix.MIN_WIDTH + 1, self.sheet.max_width))
            fresh = new_sheet or len(self.units) != len(units)
            self.units = units
            if fresh:
                # a new cut means new units, so every tick starts over
                self.keep = {u["index"]: tb.BooleanVar(value=u["ok"]) for u in units}
                self.even = {u["index"]: tb.BooleanVar(value=False) for u in units}
                self.selected = 0
                self._make_cards()
            self.width_var.set(self.sheet.width_at(self.sheet.pitch))
            self.draw_sheet()
            self._draw_cards()
            self._refresh_labels()
            g = f"grain {self.sheet.grain:.2f} px" if self.sheet.grain else "no grain found"
            rows, cols = self.sheet.layout
            self.status.set(f"{self.path.name}: {rows}x{cols} units, {g}, "
                            f"snapped at {self.sheet.pitch:.2f} px per pixel.")
        if self._pending:
            kind, self._pending = self._pending, None
            self._start(kind)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        if busy:
            self.progress.pack(side=LEFT, padx=(12, 0))
            self.progress.start(12)
            self.status.set(text)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    # -------------------------------------------------------------- loading

    def pick_sheet(self) -> None:
        p = filedialog.askopenfilename(
            title="AI-generated sheet of units",
            initialdir=str(self.path.parent if self.path else START_DIR),
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if p:
            self.open_sheet(pathlib.Path(p))

    def open_sheet(self, path: pathlib.Path) -> None:
        try:
            with Image.open(path) as im:
                image = im.convert("RGB")
        except Exception as exc:
            messagebox.showerror("Could not open", f"{path.name}: {exc}")
            return
        self.path, self.image = pathlib.Path(path), image
        self.sheet, self.units = None, []
        self.auto_layout = True
        self.width_target = None
        self._make_cards()
        self.title(f"CoW AI Sprite Fixer - {self.path.name}")
        self.draw_sheet()
        self._request("prepare")

    # ------------------------------------------------------------- settings

    def _on_layout(self) -> None:
        try:
            r, c = int(self.rows.get()), int(self.cols.get())
        except Exception:
            return
        if not (1 <= r <= aifix.MAX_LAYOUT and 1 <= c <= aifix.MAX_LAYOUT):
            return
        if self.sheet and (r, c) == self.sheet.layout and not self.auto_layout:
            return
        self.auto_layout = False
        self._later("layout", lambda: self._request("prepare"))

    def guess(self) -> None:
        self.auto_layout = True
        self._request("prepare")

    def _on_size_drag(self) -> None:
        if self.sheet is None:
            return
        w = int(round(self.width_var.get()))
        self.width_target = None if w == self.sheet.native_width else w
        self._refresh_labels()
        self._later("size", lambda: self._request("snap"))

    def step_size(self, d: int) -> None:
        if self.sheet is None:
            return
        cur = self.width_target or self.sheet.native_width
        w = max(aifix.MIN_WIDTH, min(self.sheet.max_width, cur + d))
        self.width_var.set(w)
        self._on_size_drag()

    def size_native(self) -> None:
        if self.sheet is None:
            return
        self.width_var.set(self.sheet.native_width)
        self._on_size_drag()

    def _on_smooth_drag(self) -> None:
        self._refresh_labels()
        self._later("smooth", lambda: self._request("snap"))

    def smooth_default(self) -> None:
        self.smooth.set(aifix.DEFAULT_SMOOTH)
        self._on_smooth_drag()

    def on_keep(self, index: int) -> None:
        self.select(index)
        self.draw_sheet()
        self._draw_card(self._card(index))
        self._refresh_labels()

    def on_even(self, index: int) -> None:
        self.select(index)
        self._request("snap")

    def toggle_keep(self, index: int | None) -> None:
        if index in self.keep:
            self.keep[index].set(not self.keep[index].get())
            self.on_keep(index)

    def toggle_even(self, index: int | None) -> None:
        if index in self.even:
            self.even[index].set(not self.even[index].get())
            self.on_even(index)

    # ----------------------------------------------------------- navigation

    @property
    def selected_index(self) -> int | None:
        return self.units[self.selected]["index"] if self.units else None

    def select(self, index: int) -> None:
        for i, u in enumerate(self.units):
            if u["index"] == index:
                self.selected = i
        self.draw_sheet()
        self._draw_cards(outline_only=True)
        self._refresh_labels()

    def step_unit(self, d: int) -> None:
        if self.units:
            self.select(self.units[(self.selected + d) % len(self.units)]["index"])
            card = self._card(self.selected_index)
            if card:
                self._scroll_to(card)

    def step_team(self, d: int) -> None:
        self.team_i = (self.team_i + d) % len(self.teams)
        self._draw_cards()
        self._refresh_labels()

    def step_zoom(self, d: int) -> None:
        self.zoom_i = max(0, min(len(ZOOMS) - 1, self.zoom_i + d))
        self._layout_cards(force=True)
        self._refresh_labels()

    @property
    def team(self) -> str:
        return self.teams[self.team_i]

    def _refresh_labels(self) -> None:
        self.lbl_team.config(text=f"army: {self.team}  [{self.team_i + 1}/{len(self.teams)}]")
        self.lbl_zoom.config(text=f"zoom {ZOOMS[self.zoom_i]}x")
        self.smooth_label.config(text=f"{round(self.smooth.get())}")
        if not self.units:
            self.lbl_unit.config(text="-")
            self.lbl_counts.config(text="")
            self.size_label.config(text="")
            return
        u = self.units[self.selected]
        size = "%dx%d px" % u["size"] if u["size"] else "empty"
        state = "kept" if self.keep[u["index"]].get() else "rejected"
        self.lbl_unit.config(text=f"unit {u['index']:02d}/{len(self.units)}   {size}   {state}")
        kept = sum(v.get() for v in self.keep.values())
        bad = sum(1 for x in self.units if not x["ok"])
        self.lbl_counts.config(text=f"{kept} kept" + (f", {bad} failed" if bad else ""))
        s = self.sheet
        if s:
            target = self.width_target or s.native_width
            widths = [x["size"][0] for x in self.units if x["size"]]
            grain = f"grain {s.grain:.2f} px" if s.grain else "no grain found"
            span = f"   units {min(widths)}-{max(widths)} px wide" if widths else ""
            self.size_label.config(text=f"{target} px   (native {s.native_width}, "
                                        f"{grain}){span}")

    # -------------------------------------------------------------- drawing

    def draw_sheet(self) -> None:
        """The sheet fitted to its canvas, with the cut grid, cell numbers,
        rejected cells hatched and the selected cell outlined."""
        c = self.sheet_canvas
        c.delete("all")
        if self.image is None:
            return
        cw, ch = max(1, c.winfo_width()), max(1, c.winfo_height())
        k = min(cw / self.image.width, ch / self.image.height)
        w, h = max(1, int(self.image.width * k)), max(1, int(self.image.height * k))
        x0, y0 = (cw - w) // 2, (ch - h) // 2
        self._sheet_fit = (k, x0, y0)
        self._sheet_photo = ImageTk.PhotoImage(self.image.resize((w, h), Image.LANCZOS))
        c.create_image(x0, y0, image=self._sheet_photo, anchor="nw")
        s = self.sheet
        if s is None:
            return
        col = self._colors
        line = col.light if col else "#ffffff"
        accent = col.warning if col else "#ffcc00"
        danger = col.danger if col else "#ff4040"
        for x in s.cuts_x[1:-1]:
            c.create_line(x0 + x * k, y0, x0 + x * k, y0 + h, fill=line, dash=(6, 4), width=2)
        for y in s.cuts_y[1:-1]:
            c.create_line(x0, y0 + y * k, x0 + w, y0 + y * k, fill=line, dash=(6, 4), width=2)
        for u in s.units:
            bx0, by0, bx1, by1 = (x0 + u.box[0] * k, y0 + u.box[1] * k,
                                  x0 + u.box[2] * k, y0 + u.box[3] * k)
            kept = self.keep.get(u.index)
            if kept is not None and not kept.get():
                c.create_rectangle(bx0, by0, bx1, by1, fill="#000000", stipple="gray50",
                                   outline="")
                c.create_line(bx0, by0, bx1, by1, fill=danger, width=2)
                c.create_line(bx0, by1, bx1, by0, fill=danger, width=2)
            c.create_text(bx0 + 6, by0 + 4, text=f"{u.index}", anchor="nw",
                          fill="#ffffff", font=("", 10, "bold"))
            if u.index == self.selected_index:
                c.create_rectangle(bx0 + 2, by0 + 2, bx1 - 2, by1 - 2, outline=accent, width=3)

    def _sheet_cell(self, event) -> int | None:
        if self.sheet is None:
            return None
        k, x0, y0 = self._sheet_fit
        x, y = (event.x - x0) / k, (event.y - y0) / k
        for u in self.sheet.units:
            if u.box[0] <= x < u.box[2] and u.box[1] <= y < u.box[3]:
                return u.index
        return None

    def _sheet_click(self, event) -> None:
        i = self._sheet_cell(event)
        if i is not None:
            self.select(i)
            card = self._card(i)
            if card:
                self._scroll_to(card)

    def _sheet_double(self, event) -> None:
        i = self._sheet_cell(event)
        if i is not None:
            self.toggle_keep(i)

    def _make_cards(self) -> None:
        for card in self.cards:
            card.frame.destroy()
        self.cards = [Card(self, self.grid_frame, u["index"]) for u in self.units]
        self._card_cols = 0
        self._layout_cards(force=True)

    def _card(self, index: int | None) -> Card | None:
        return next((c for c in self.cards if c.index == index), None)

    def _cell_size(self) -> tuple[int, int]:
        """One card's sprite area: the largest unit at the current zoom."""
        z = ZOOMS[self.zoom_i]
        ws = [u["size"][0] for u in self.units if u["size"]] or [32]
        hs = [u["size"][1] for u in self.units if u["size"]] or [32]
        return max(ws) * z + 2 * CARD_PAD, max(hs) * z + 2 * CARD_PAD

    def _layout_cards(self, force: bool = False) -> None:
        if not self.cards:
            return
        cw, chh = self._cell_size()
        for card in self.cards:
            card.canvas.configure(width=cw, height=chh)
        self.update_idletasks()
        # a card is as wide as its sprite area or its label row, whichever wins
        need = max(c.frame.winfo_reqwidth() for c in self.cards) + 4
        width = max(1, self.results_canvas.winfo_width())
        self.results_canvas.itemconfigure(self._grid_window, width=width)
        cols = max(1, width // need)
        if cols == self._card_cols and not force:
            return
        self._card_cols = cols
        for i, card in enumerate(self.cards):
            card.frame.grid(row=i // cols, column=i % cols, sticky="n", padx=2, pady=2)
        self._draw_cards()

    def _draw_cards(self, outline_only: bool = False) -> None:
        for card in self.cards:
            self._draw_card(card, outline_only)

    def _draw_card(self, card: Card | None, outline_only: bool = False) -> None:
        if card is None:
            return
        col = self._colors
        accent = col.warning if col else "#ffcc00"
        plain = gk.mix(col.bg, col.fg, CANVAS_STEP) if col else "#333333"
        c = card.canvas
        if outline_only:
            self._draw_outline(card, accent)
            return
        c.delete("all")
        c.configure(background=plain)
        u = next((x for x in self.units if x["index"] == card.index), None)
        if u is None:
            return
        w, h = int(c.cget("width")), int(c.cget("height"))
        if u["encoded"] is not None:
            z = ZOOMS[self.zoom_i]
            # CoW's own recolor, then a whole-number zoom: display only
            shown = cp.recolor(u["encoded"], self.team, self.palettes)
            shown = shown.resize((shown.width * z, shown.height * z), Image.NEAREST)
            card._photo = ImageTk.PhotoImage(shown)
            c.create_image(w // 2, h // 2, image=card._photo)
        if not self.keep[card.index].get():
            c.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50", outline="")
            c.create_text(w // 2, h // 2, text="rejected", fill="#ffffff",
                          font=("", 12, "bold"))
        self._draw_outline(card, accent)
        size = "%dx%d" % u["size"] if u["size"] else "empty"
        mark = "" if u["ok"] else "  !"
        card.label.configure(text=f"{u['index']:02d}   {size}{mark}")
        card.label_tip.text = (f"{u['name']}: {size} px." + (
            "" if u["ok"] else f" Did not convert cleanly: {u['reason']}."))

    def _draw_outline(self, card: Card, accent: str) -> None:
        c = card.canvas
        c.delete("sel")
        if card.index == self.selected_index:
            w, h = int(c.cget("width")), int(c.cget("height"))
            c.create_rectangle(2, 2, w - 1, h - 1, outline=accent, width=3, tags="sel")

    def _scroll_to(self, card: Card) -> None:
        self.update_idletasks()
        total = max(1, self.grid_frame.winfo_height())
        y = card.frame.winfo_y()
        top, bottom = (v * total for v in self.results_canvas.yview())
        if y < top or y + card.frame.winfo_height() > bottom:
            self.results_canvas.yview_moveto(max(0.0, y - 8) / total)

    # --------------------------------------------------------------- export

    def _export_teams(self) -> list[str]:
        choice = self.export_armies.get()
        if choice == "this army":
            return [self.team] if self.team != cp.MASK else []
        if choice == "all armies":
            return [t for t in self.teams if t != cp.MASK]
        return []

    def export(self) -> pathlib.Path | None:
        if self.sheet is None or not self.units:
            self.status.set("Nothing to export yet -- open a sheet first.")
            return None
        if self._busy or self._pending:
            self.status.set("Still working on the last change -- export again in a moment.")
            return None
        keep = {i for i, v in self.keep.items() if v.get()}
        if not keep:
            self.status.set("Every unit is rejected -- nothing to export.")
            return None
        dest = OUTPUT / self.sheet.name
        scale = int(self.export_scale.get())
        teams = self._export_teams()
        try:
            n = aifix.write_outputs(self.sheet, dest, keep=keep, teams=teams, scale=scale,
                                    palettes=self.palettes)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return None
        self.exported = dest
        armies = (f", {len(teams)} arm{'y' if len(teams) == 1 else 'ies'}"
                  f"{f' at {scale}x' if scale > 1 else ''}") if teams else ""
        self.status.set(f"Exported {n} units{armies} -> output/aifix/{self.sheet.name}/")
        return dest

    def open_in_previewer(self) -> None:
        """Export, then start the previewer on the export folder."""
        dest = self.export()
        if dest is None:
            return
        if getattr(sys, "frozen", False):
            cmd = [str(ROOT / "CoW Sprite Previewer.exe"), str(dest)]
        else:
            cmd = [sys.executable, str(ROOT / "tools" / "previewer.py"), str(dest)]
        try:
            subprocess.Popen(cmd)
        except OSError as exc:
            messagebox.showerror("Could not start the previewer", str(exc))
            return
        self.status.set(f"Exported and opened in the previewer: output/aifix/{dest.name}/")


def main(argv: list[str]) -> int:
    path = pathlib.Path(argv[1]) if len(argv) > 1 else None
    if path and not path.is_file():
        print(f"not a file: {path}")
        return 2
    try:
        FixerGUI(path).mainloop()
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
