"""Wildcard vehicle generator (Phase 3) -- ttkbootstrap GUI.

Nine slots, one per wildcard list, in plain basename order. Each row carries a
filter box, prev/next, a lock and an enable toggle; the assembled subject string
sits at the bottom with a copy button.

All of the actual logic -- loading, ordering, `__` expansion, randomize -- lives
in `tools/wildcards.py`. This file is the shell, and `tools/test_wildcards.py`
asserts it reimplements none of it.

    Space / Enter   randomize (honours locks and disables)
    Ctrl+C          copy the subject string
    Ctrl+L          unlock every slot
    Esc             leave a text field (never quits)

Run:  python tools/wildcard_gui.py
"""

from __future__ import annotations

import pathlib
import random
import sys
import traceback

import ttkbootstrap as tb
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X, Y, W

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import wildcards as wc  # noqa: E402
import guikit as gk  # noqa: E402  -- icon, light/dark, Guide button, tooltips
from guikit import tip  # noqa: E402

try:
    import prompts as P  # noqa: E402  -- Phase 4, optional
except Exception:  # pragma: no cover - the generator works without it
    P = None

# How many filtered entries the dropdown shows. The lists run to 5521 entries
# and a Combobox holding all of them is unusable -- the filter box is the real
# navigation tool, this is just the window onto its result.
MAX_SHOWN = 300

# gruvbox, light by default -- deliberately unlike the previewer's dark pydata,
# so the two programs never get mistaken for each other.
LIGHT_THEME, DARK_THEME = "gruvbox-light", "gruvbox-dark"

# Help for the prompt choices, shared by each label and its dropdown.
PROMPT_HELP = {
    "View": "Camera angle the generator is asked for. Oblique matches CoW map "
            "units; Slightly elevated side suits battle animations. The Direct "
            "side / front / top views are for other art styles -- total "
            "conversion mods, or a game of your own.",
    "Addon": "Optional extra about the shape: bold and simple (reads well tiny) "
             "or highly detailed.",
    "Layout": "One unit per image, or a sheet of several different designs. "
              "AI sheets go through the AI Sprite Fixer; hand-drawn ones through "
              "Cut sheet in the previewer.",
    "grid": "How many units on a sheet: 2x2 = 4 up to 4x4 = 16. Only used when "
            "Layout is Sheet.",
    "Background": "Chroma green or magenta can be removed automatically by the "
                  "converter. The themed ones are for concept art and have to be "
                  "cut out by hand.",
    "color": "Color theme for the themed backgrounds. Only used when Background "
             "is one of them.",
    "Palette": "Optional color request at the end of the prompt. Keep reference "
               "colors is for the generator's Edit Image mode with an Orange Star "
               "reference loaded -- the recommended route. Described colors is for "
               "generating without a reference, where the model drifts. Colors are "
               "snapped on import either way.",
}


class SlotRow:
    """One slot's widgets. Owns no state -- the Slot object is the truth."""

    def __init__(self, parent, slot: wc.Slot, on_change):
        self.slot = slot
        self.on_change = on_change

        self.frame = tb.Frame(parent, padding=(0, 3))
        self.frame.pack(fill=X)

        self.enabled = tb.BooleanVar(value=slot.enabled)
        self.locked = tb.BooleanVar(value=slot.locked)
        self.filter_text = tb.StringVar(value="")
        self.pick = tb.StringVar(value=slot.current)

        tip(tb.Checkbutton(self.frame, variable=self.enabled, bootstyle="round-toggle",
                           command=self._toggle_enabled, width=2),
            "On: this slot's pick is part of the subject text. Off: the slot is "
            "skipped entirely.").pack(side=LEFT)

        where = (f"From the list file '{slot.path.name}'. Edit that file to change "
                 "the entries -- one per line, each ending in __ (a space). Add or "
                 "remove .txt files to add or remove slots.")
        tip(tb.Label(self.frame, text=slot.key, font=("", 10, "bold"),
                     width=3), where).pack(side=LEFT, padx=(2, 0))
        tip(tb.Label(self.frame, text=slot.label, style=gk.MUTED,
                     width=24, anchor=W), where).pack(side=LEFT, padx=(4, 0))

        for icon, delta, word in (("chevron-left", -1, "Previous"),
                                  ("chevron-right", 1, "Next")):
            tip(tb.Button(self.frame, icon=icon, icon_only=True,
                          bootstyle="secondary-ghost", takefocus=False,
                          command=lambda d=delta: self._step(d)),
                f"{word} entry in this list. With a filter typed, only steps "
                "through the entries that match it."
                ).pack(side=LEFT, padx=(4 if delta < 0 else 0, 0))

        self.combo = tb.Combobox(self.frame, textvariable=self.pick, width=34)
        self.combo.pack(side=LEFT, padx=(8, 0), fill=X, expand=True)
        tip(self.combo, "This slot's current pick. Open the list to choose one, or "
                        "type an exact entry and press Enter.")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._select())
        self.combo.bind("<Return>", lambda e: self._select())
        self.combo.bind("<Escape>", lambda e: self.frame.winfo_toplevel().focus_set())

        tb.Label(self.frame, text="filter").pack(side=LEFT, padx=(8, 3))
        ent = tb.Entry(self.frame, textvariable=self.filter_text, width=14)
        ent.pack(side=LEFT)
        tip(ent, "Type a word (e.g. 'tank') to narrow this slot's list and the "
                 "< > buttons to entries containing it. Randomize ignores it. "
                 "Esc to leave the box.")
        ent.bind("<Escape>", lambda e: self.frame.winfo_toplevel().focus_set())
        self.filter_text.trace_add("write", lambda *a: self._refilter())

        self.count = tip(tb.Label(self.frame, text="", style=gk.MUTED, width=10),
                         "Entries matching the filter / entries in the list. A + "
                         "means the dropdown shows only the first "
                         f"{MAX_SHOWN} -- type more to narrow it.")
        self.count.pack(side=LEFT, padx=(6, 0))

        tip(tb.Checkbutton(self.frame, text="lock", variable=self.locked,
                           bootstyle="warning-round-toggle",
                           command=self._toggle_locked),
            "Keep this pick when you press Randomize. Unlock all (Ctrl+L) "
            "frees every slot.").pack(side=RIGHT, padx=(8, 0))

        self._refilter()

    # ---------------------------------------------------------------- events

    def _toggle_enabled(self) -> None:
        self.slot.enabled = self.enabled.get()
        self.on_change()

    def _toggle_locked(self) -> None:
        self.slot.locked = self.locked.get()

    def _step(self, delta: int) -> None:
        """Prev/next moves within the *filtered* view when a filter is active.

        Stepping through 5521 entries one at a time is useless; stepping through
        the 12 that matched "tank" is the point of having both controls.
        """
        matches = self.slot.matches(self.filter_text.get())
        if not matches:
            return
        indices = [i for i, _ in matches]
        try:
            pos = indices.index(self.slot.index)
        except ValueError:
            pos = -1 if delta > 0 else 0
        self.slot.index = indices[(pos + delta) % len(indices)]
        self.refresh()
        self.on_change()

    def _select(self) -> None:
        """Commit whatever the combobox shows, if it is a real entry."""
        if self.slot.set_current(self.pick.get()):
            self.on_change()
        else:
            self.pick.set(self.slot.current)
        self.frame.winfo_toplevel().focus_set()

    def _refilter(self) -> None:
        matches = self.slot.matches(self.filter_text.get())
        self.combo["values"] = [e for _, e in matches[:MAX_SHOWN]]
        n = len(matches)
        total = len(self.slot.entries)
        shown = f"{n}/{total}" if n != total else f"{total}"
        if n > MAX_SHOWN:
            shown += "+"
        self.count.configure(text=shown)

    def refresh(self) -> None:
        """Push the Slot's state back into the widgets after a randomize."""
        self.pick.set(self.slot.current)
        self.enabled.set(self.slot.enabled)
        self.locked.set(self.slot.locked)


class WildcardGUI(tb.Window):
    def __init__(self):
        super().__init__(themename=LIGHT_THEME, light_theme=LIGHT_THEME,
                         dark_theme=DARK_THEME, title="CoW Wildcard Vehicle Generator")
        # 640 rather than 560: nine rows measure 378px and the old height left
        # only 363px of canvas, so the default window scrolled by 15px -- the
        # scrollbar appeared for no visible reason. This leaves real headroom.
        self.geometry("1180x640")
        self.minsize(900, 420)

        self.rng = random.Random()
        # A broken config must not stop the program launching -- the lists are
        # the point, the config is a convenience. Report it in the status bar.
        self._config_error = ""
        try:
            self.slots = wc.load_slots()
        except wc.ConfigError as exc:
            self._config_error = str(exc)
            self.slots = wc.load_slots(wc.LIST_DIRS)
        try:
            on = wc.default_keys(self.slots)
        except wc.ConfigError:
            on = wc.default_keys(self.slots, {})
        for s in self.slots:
            s.enabled = s.key in on
        self.subject = tb.StringVar(value="")
        self.status = tb.StringVar(value="")

        self._build()
        gk.setup(self, "icon_wildcards", self._on_theme)
        self._bind_keys()
        self._update()

    # ------------------------------------------------------------------ ui

    def _build(self) -> None:
        top = tb.Frame(self, padding=(10, 8))
        top.pack(side="top", fill=X)

        def button(text, icon, style, cmd, help_, pad=8):
            b = tb.Button(top, text=text, icon=icon, bootstyle=style, command=cmd)
            b.pack(side=LEFT, padx=(pad, 0))
            return tip(b, help_)

        button("Randomize", "shuffle", "primary", self.randomize,
               "Roll a new random pick in every slot that is switched on and not "
               "locked.  Space or Enter.", pad=0)
        button("Copy subject", "clipboard", "success", self.copy,
               "Copy the subject text (the box near the bottom) to the clipboard.  "
               "Ctrl+C")
        button("Unlock all", "unlock", "warning-outline", self.unlock_all,
               "Release every lock, so the next Randomize changes every switched-on "
               "slot.  Ctrl+L")
        button("Enable all", "toggles", "secondary-outline",
               lambda: self.set_all_enabled(True),
               "Switch every slot on. Makes long, busy subjects -- switch some back "
               "off for something simpler.")
        # The documented unarmed-civilian recipe (RESOLVED-6): drop the
        # connector and both weapon groups, keep the vehicle description.
        button("Unarmed civilian", "truck", "info-outline", self.preset_unarmed,
               "Preset: keep only the vehicle-description slots and switch off the "
               "connector and weapon slots, for tractors, buses, ambulances and "
               "other unarmed vehicles.")

        gk.theme_button(top, self).pack(side=RIGHT)
        gk.guide_button(top, self.status).pack(side=RIGHT, padx=(0, 6))
        ref = tb.Button(top, text="Reference sheets", icon="folder2-open",
                        bootstyle="secondary-outline",
                        command=lambda: gk.open_folder(gk.REFERENCE, self.status))
        ref.pack(side=RIGHT, padx=(0, 6))
        tip(ref, "Open pipeline/reference/: the hand-drawn sheet to load as the image "
                 "to edit (AW2Style Units 4x green safety BG.png), plus the class "
                 "sheets and the 91-unit gallery.")

        tb.Label(self, text="  switch = include in subject        lock = keep through "
                            "Randomize        hover anything for help",
                 style=gk.MUTED, padding=(10, 0)).pack(side="top", fill=X)

        # Scrolling body. Nine slots fit at the default size, but the slot list
        # is data-driven -- drop more .txt files in and there are more rows --
        # so the window must cope with more than fit on screen.
        #
        # The scrollbar is packed BEFORE the canvas and into the same frame, so
        # it reserves its own column rather than floating over the content. The
        # lock toggles sit at the far right of every row and would otherwise be
        # the first thing a scrollbar covered.
        outer = tb.Frame(self, padding=(10, 4))
        outer.pack(side="top", fill=BOTH, expand=True)

        self.vbar = tb.Scrollbar(outer, orient="vertical", bootstyle="secondary-round")
        self.vbar.pack(side=RIGHT, fill=Y)

        self.body_canvas = tb.Canvas(outer, highlightthickness=0, borderwidth=0)
        self.body_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.body_canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.configure(command=self.body_canvas.yview)

        body = tb.Frame(self.body_canvas)
        self._body_window = self.body_canvas.create_window((0, 0), window=body, anchor="nw")

        self.rows = [SlotRow(body, s, self._update) for s in self.slots]

        # Keep the inner frame as wide as the canvas so rows fill the width,
        # and keep the scroll region matched to the content height.
        def _fit(_event=None):
            self.body_canvas.itemconfigure(self._body_window,
                                           width=self.body_canvas.winfo_width())
            self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))
            self._sync_scrollbar()

        body.bind("<Configure>", _fit)
        self.body_canvas.bind("<Configure>", _fit)
        self._body_frame = body
        self._bind_wheel()

        out = tb.Labelframe(self, text="Subject string", padding=(10, 8))
        out.pack(side="top", fill=X, padx=10, pady=(4, 0))
        entry = tb.Entry(out, textvariable=self.subject, font=("", 11))
        entry.pack(fill=X)
        entry.bind("<Escape>", lambda e: self.focus_set())
        tip(entry, "The vehicle description: every switched-on slot's pick, in "
                   "order. You can edit it here before copying. Paste it into "
                   "an image generator, or use Copy full prompt below.")

        # Phase 4: turn the subject into a full generator prompt. Optional --
        # the generator is useful on its own, so a missing prompts.py just
        # hides this row rather than breaking the program.
        if P is not None:
            data = P.load()
            row1 = tb.Frame(out, padding=(0, 8, 0, 0))
            row1.pack(fill=X)
            row2 = tb.Frame(out, padding=(0, 6, 0, 0))
            row2.pack(fill=X)

            def combo(parent, text, values, initial, width, first=False):
                lbl = tb.Label(parent, text=text)
                lbl.pack(side=LEFT, padx=(0 if first else 10, 0))
                var = tb.StringVar(value=initial)
                cb = tb.Combobox(parent, width=width, state="readonly", textvariable=var,
                                 values=values)
                cb.pack(side=LEFT, padx=(6, 0))
                tip(lbl, PROMPT_HELP[text])
                tip(cb, PROMPT_HELP[text])
                cb.bind("<<ComboboxSelected>>", lambda e: (self._sync_prompt_controls(),
                                                           self.focus_set()))
                return var, cb

            # The choice comboboxes show labels; `_prompt_keys` maps each part's
            # label back to the key prompts.build() takes.
            self._prompt_keys: dict[str, dict[str, str]] = {}
            self._prompt_vars: dict[str, tb.StringVar] = {}
            for part, text, parent, width in (("view", "View", row1, 24),
                                               ("addon", "Addon", row1, 16),
                                               ("layout", "Layout", row1, 7),
                                               ("background", "Background", row2, 22),
                                               ("palette", "Palette", row2, 21)):
                opts = P.options(part, data)
                self._prompt_keys[part] = {o["label"]: k for k, o in opts.items()}
                if part == "background":
                    self.grid_size, self._grid_cb = combo(
                        row1, "grid", P.grids(data), data["layout"]["default_grid"], 5)
                if part == "palette":
                    # Color and Outlines belong with the background; the color
                    # request is the prompt's last part, so it comes after them.
                    self.bg_color, self._color_cb = combo(
                        row2, "color", P.colors(data), data["background"]["default_color"], 8)
                    self.outlines = tb.BooleanVar(value=data["outlines"]["default"])
                    tip(tb.Checkbutton(row2, text="Outlines", variable=self.outlines,
                                       bootstyle="round-toggle"),
                        "Ask for thick dark outlines, like the Advance Wars sprites."
                        ).pack(side=LEFT, padx=(12, 0))
                var, _ = combo(parent, text, [o["label"] for o in opts.values()],
                               opts[P.default(part, data)]["label"], width,
                               first=(part in ("view", "background")))
                self._prompt_vars[part] = var

            tip(tb.Button(row2, text="Copy full prompt", icon="clipboard-check",
                          bootstyle="success-outline", command=self.copy_prompt),
                "Copy a complete image-generator prompt: the subject plus every "
                "choice on these two rows. Use it with the generator's Edit Image "
                "on a battle-scale reference sheet of Orange Star units (see the "
                "Guide)."
                ).pack(side=LEFT, padx=(12, 0))
            self.prompt_hint = tb.Label(row2, text="", style=gk.MUTED)
            self.prompt_hint.pack(side=LEFT, padx=(10, 0))
            self._sync_prompt_controls()

        bar = tb.Frame(self, padding=(10, 6))
        bar.pack(side="bottom", fill=X)
        tb.Label(bar, textvariable=self.status, style=gk.MUTED).pack(side=LEFT, anchor=W)
        gk.credit_label(bar).pack(side=RIGHT, padx=(16, 0))
        tip(tb.Label(bar, text="Space randomize   Ctrl+C copy   Ctrl+L unlock all",
                     style=gk.MUTED),
            "Keyboard shortcuts. They pause while you type in a box; press Esc "
            "to leave the box.").pack(side=RIGHT)

    def _on_theme(self, colors) -> None:
        # A plain canvas keeps whatever background it was made with; match the
        # window so the slot list does not sit on a stale-colored panel.
        self.body_canvas.configure(background=colors.bg)

    def _sync_scrollbar(self) -> None:
        """Show the scrollbar only when the rows actually overflow.

        At the default size all nine slots fit, and a permanently visible
        scrollbar would just be a dead grey stripe next to the lock toggles.
        Unpacking it also hands its width back to the rows.
        """
        bbox = self.body_canvas.bbox("all")
        if not bbox:
            return
        needed = bbox[3] - bbox[1] > self.body_canvas.winfo_height()
        shown = bool(self.vbar.winfo_ismapped())
        if needed and not shown:
            self.vbar.pack(side=RIGHT, fill=Y, before=self.body_canvas)
        elif not needed and shown:
            self.vbar.pack_forget()

    def _bind_wheel(self) -> None:
        """Wheel scrolls the row list whenever the pointer is over it.

        Bound on the canvas and its children rather than globally: a global
        bind would steal the wheel from the comboboxes, where it is the
        natural way to move through a filtered list.
        """
        def on_wheel(event):
            bbox = self.body_canvas.bbox("all")
            if not bbox or bbox[3] - bbox[1] <= self.body_canvas.winfo_height():
                return None
            self.body_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        self.body_canvas.bind("<Enter>",
                              lambda e: self.body_canvas.bind_all("<MouseWheel>", on_wheel))
        self.body_canvas.bind("<Leave>",
                              lambda e: self.body_canvas.unbind_all("<MouseWheel>"))

    def _typing(self) -> bool:
        try:
            w = self.focus_get()
        except KeyError:
            return False
        return isinstance(w, (tb.Entry, tb.Combobox, tb.Spinbox))

    def _bind_keys(self) -> None:
        def randomize_key(event=None):
            if self._typing():
                return None
            self.randomize()
            return "break"

        self.bind("<space>", randomize_key)
        self.bind("<Return>", randomize_key)
        self.bind("<Control-c>", lambda e: self.copy())
        self.bind("<Control-l>", lambda e: self.unlock_all())
        # Esc leaves a field; it deliberately does not quit, matching previewer.py.
        self.bind("<Escape>", lambda e: self.focus_set())

    # ------------------------------------------------------------- actions

    def _update(self) -> None:
        self.subject.set(wc.assemble(self.slots))
        on = sum(1 for s in self.slots if s.enabled)
        locked = sum(1 for s in self.slots if s.locked)
        msg = f"{on}/{len(self.slots)} slots enabled, {locked} locked"
        if self._config_error:
            msg += f"   |  config ignored: {self._config_error}"
        self.status.set(msg)

    def randomize(self) -> None:
        wc.randomize(self.slots, self.rng)
        for r in self.rows:
            r.refresh()
        self._update()

    def copy(self) -> None:
        text = self.subject.get()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()  # keep the clipboard alive after the window closes
        self.status.set(f"Copied {len(text)} chars to clipboard.")

    def copy_prompt(self) -> None:
        """Copy the full generator prompt, and say what to do with it.

        The status line reminds the user to load a reference sheet with the
        generator's Edit Image and says whether the background can be
        chroma-keyed or has to be cut out by hand.
        """
        if P is None:
            return
        subject = self.subject.get().strip()
        if not subject:
            self.status.set("Nothing to build a prompt from -- every slot is disabled.")
            return
        try:
            # Grid and color only apply to some choices; build() ignores them
            # otherwise, so they can be passed unconditionally.
            built = P.build(subject, **self._prompt_choices(), grid=self.grid_size.get(),
                            color=self.bg_color.get(), outlines=self.outlines.get())
        except Exception as exc:
            self.status.set(f"Prompt failed: {exc}")
            return
        self.clipboard_clear()
        self.clipboard_append(built["text"])
        self.update()
        key = (f"backdrop {built['key']} -- the fixer keys it out" if built["key"]
               else "background can't be removed automatically -- cut it out by hand")
        self.prompt_hint.configure(text=key)
        over = "  OVER BUDGET" if built["chars"] > built["budget"] else ""
        self.status.set(f"Copied prompt ({built['chars']}/{built['budget']} chars{over}). "
                        f"Paste it into Edit Image on a reference sheet; {key}.")

    def _prompt_choices(self) -> dict[str, str]:
        return {part: self._prompt_keys[part][var.get()]
                for part, var in self._prompt_vars.items()}

    def _sync_prompt_controls(self) -> None:
        """Grey out grid unless the layout is a sheet, color unless the
        background has a {color} slot -- they would be silently ignored."""
        picks = self._prompt_choices()
        self._grid_cb.configure(state="readonly" if picks["layout"] == "sheet" else "disabled")
        self._color_cb.configure(
            state="readonly" if P.needs_color(picks["background"]) else "disabled")

    def unlock_all(self) -> None:
        for s in self.slots:
            s.locked = False
        for r in self.rows:
            r.refresh()
        self._update()

    def set_all_enabled(self, on: bool) -> None:
        for s in self.slots:
            s.enabled = on
        for r in self.rows:
            r.refresh()
        self._update()

    def preset_unarmed(self) -> None:
        # Which slots count as "civilian" is config-driven -- see
        # wildcards.civilian_keys. The built-in fallback is the `a` group
        # minus the connector, which is what this did before it was moddable.
        keep = wc.civilian_keys(self.slots)
        for s in self.slots:
            s.enabled = s.key in keep
        for r in self.rows:
            r.refresh()
        self._update()


def main(argv: list[str]) -> int:
    try:
        WildcardGUI().mainloop()
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
