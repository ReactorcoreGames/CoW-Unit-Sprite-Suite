"""Prompt template assembly (Phase 4).

Turns `pipeline/prompts.json` into a finished prompt string for an AI image
generator.

**Revision 4** is the user's own format. A prompt is eight parts in a fixed
order, each chosen independently:

    subject     "<subject> as a game sprite in the style of Advance Wars."
    addon       bold simple shape / (none) / highly detailed
    view        side, front, top, elevated side, 3/4 orthographic, oblique
    layout      one subject, or a 2x2 / 3x3 / 4x4 grid of distinct designs
    background  chroma green or magenta, or a {color}-themed backdrop
    style       the fixed style suffix
    outlines    optional "Thick dark outlines."
    palette     optional color request, last: none, "keep the reference's
                colors" (edit mode, the default), or a described red scheme

Two things are deliberately *not* here:

* **Aspect ratio.** The image generator sets it (a 16:9 canvas favours side
  views); nothing in this module chooses, suggests or mentions one.
* **Unit class.** The wording is the same for a tank and a frigate. Which
  reference image to load is the user's pick.

The recommended route (October 2026) is edit mode: the generator edits a
reference sheet of Orange Star units, and the default palette part tells it
to keep that sheet's colors and style. A hex-code palette option existed and
was removed -- the model painted the codes on as car paint (cars.jpeg).

Run:
    python tools/prompts.py --list
    python tools/prompts.py -s "bulldozer vehicle" --view oblique --layout sheet --grid 2x2
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from approot import ROOT  # project root from source, exe folder when frozen
PROMPTS = ROOT / "pipeline" / "prompts.json"
REFERENCE_DIR = ROOT / "pipeline" / "reference"  # loaded by hand; see guide.md

# The parts that are a pick from a list, in the order the CLI and GUI show them.
CHOICE_PARTS = ("addon", "view", "layout", "background", "palette")


def load() -> dict:
    return json.loads(PROMPTS.read_text(encoding="utf-8"))


def options(part: str, data: dict | None = None) -> dict[str, dict]:
    """The `{key: {label, text, ...}}` options for one choice part."""
    return (data or load())[part]["options"]


def option_keys(part: str, data: dict | None = None) -> list[str]:
    return list(options(part, data))


def default(part: str, data: dict | None = None) -> str:
    return (data or load())[part]["default"]


def grids(data: dict | None = None) -> list[str]:
    return list((data or load())["layout"]["grids"])


def colors(data: dict | None = None) -> list[str]:
    return list((data or load())["background"]["colors"])


def needs_color(background: str, data: dict | None = None) -> bool:
    """Whether a background option has a {color} slot to fill."""
    return "{color}" in options("background", data)[background]["text"]


def build(
    subject: str,
    *,
    addon: str | None = None,
    view: str | None = None,
    layout: str | None = None,
    grid: str | None = None,
    background: str | None = None,
    color: str | None = None,
    outlines: bool | None = None,
    palette: str | None = None,
    data: dict | None = None,
) -> dict:
    """Assemble one prompt. Any choice left as None takes its default.

    Returns the prompt text plus what a caller needs to act on it: the chroma
    key the background was asked in (None when the converter cannot key it),
    and the character count against the budget.
    """
    data = data or load()

    subject = " ".join(subject.split())
    if not subject:
        raise ValueError("subject is empty -- the prompt leads with it")

    picks = {
        "addon": addon or default("addon", data),
        "view": view or default("view", data),
        "layout": layout or default("layout", data),
        "background": background or default("background", data),
        "palette": palette or default("palette", data),
    }
    for part, key in picks.items():
        if key not in data[part]["options"]:
            raise KeyError(f"unknown {part} {key!r}; have {option_keys(part, data)}")
    if outlines is None:
        outlines = data["outlines"]["default"]

    # Sheets are capped. Krea returned 85 units when nothing said otherwise,
    # and every one was an unreadable blob -- the pixel budget per unit was
    # fine, but the model's attention was not.
    chosen_grid = None
    layout_text = data["layout"]["options"][picks["layout"]]["text"]
    if "{grid}" in layout_text:
        table = data["layout"]["grids"]
        chosen_grid = grid or data["layout"]["default_grid"]
        if chosen_grid not in table:
            raise KeyError(f"unknown grid {chosen_grid!r}; have {list(table)}")
        layout_text = layout_text.format(count=_spell(table[chosen_grid]),
                                         grid=chosen_grid.replace("x", " by "))

    bg = data["background"]["options"][picks["background"]]
    bg_text = bg["text"]
    chosen_color = None
    if "{color}" in bg_text:
        chosen_color = color or data["background"]["default_color"]
        if chosen_color not in data["background"]["colors"]:
            raise KeyError(f"unknown color {chosen_color!r}; have {colors(data)}")
        bg_text = bg_text.format(color=chosen_color)

    palette_text = data["palette"]["options"][picks["palette"]]["text"]

    # Capitalize the first letter only. `str.capitalize()` would lowercase the
    # rest, which mangles any subject containing an internal capital.
    lead = data["subject"].format(subject=subject)
    parts = {
        "subject": lead[:1].upper() + lead[1:],
        "addon": data["addon"]["options"][picks["addon"]]["text"],
        "view": data["view"]["options"][picks["view"]]["text"],
        "layout": layout_text,
        "background": bg_text,
        "style": data["style"],
        "outlines": data["outlines"]["text"] if outlines else "",
        "palette": palette_text,
    }
    # One paragraph. An empty part (addon "none", outlines off) just drops out.
    text = " ".join(parts[k] for k in data["order"] if parts[k])

    return {
        "subject": subject,
        **picks,
        "grid": chosen_grid,
        "color": chosen_color,
        "outlines": outlines,
        "key": bg.get("key"),
        "text": text,
        "chars": len(text),
        "budget": data.get("budget", 1000),
    }


def _spell(n: int) -> str:
    """Small numbers read better as words in a prompt than as digits."""
    return {4: "four", 9: "nine", 16: "sixteen"}.get(n, str(n))


def format_cli(built: dict) -> str:
    over = "   OVER BUDGET" if built["chars"] > built["budget"] else ""
    layout = built["layout"] + (f" {built['grid']}" if built["grid"] else "")
    bg = built["background"] + (f" ({built['color']})" if built["color"] else "")
    key = (f"convert with --key {built['key']}" if built["key"]
           else "not chroma-keyable -- cut out by hand")
    lines = [
        f"view       {built['view']}   addon {built['addon']}   "
        f"outlines {'on' if built['outlines'] else 'off'}   palette {built['palette']}",
        f"layout     {layout}",
        f"background {bg}   ({key})",
        "reference  load a battle-scale Orange Star sheet with the generator's Edit Image",
        f"length     {built['chars']} / {built['budget']} chars{over}",
        "",
        "-" * 70,
        built["text"],
        "-" * 70,
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    data = load()
    ap = argparse.ArgumentParser(description="Assemble a sprite-generation prompt.")
    ap.add_argument("--subject", "-s", default="",
                    help="the subject string, e.g. from the wildcard generator")
    ap.add_argument("--addon", choices=option_keys("addon", data), default=None)
    ap.add_argument("--view", "-v", choices=option_keys("view", data), default=None)
    ap.add_argument("--layout", choices=option_keys("layout", data), default=None)
    ap.add_argument("--grid", "-g", choices=grids(data), default=None,
                    help="sheet grid size (sheet layout only)")
    ap.add_argument("--bg", "-b", dest="background",
                    choices=option_keys("background", data), default=None)
    ap.add_argument("--color", choices=colors(data), default=None,
                    help="fills {color} in the themed backgrounds")
    ap.add_argument("--no-outlines", dest="outlines", action="store_false", default=None)
    ap.add_argument("--palette", "-p", choices=option_keys("palette", data), default=None,
                    help="optional color request at the end of the prompt")
    ap.add_argument("--list", "-l", action="store_true", help="list every option")
    args = ap.parse_args(argv[1:])

    if args.list or not args.subject:
        for part in CHOICE_PARTS:
            print(f"{part} (default {default(part, data)}):")
            for key, opt in options(part, data).items():
                print(f"  {key:16} {opt['text'] or '(nothing)'}")
        print(f"\ngrids:   {' '.join(grids(data))}  (default {data['layout']['default_grid']})")
        print(f"colors:  {' '.join(colors(data))}  (default {data['background']['default_color']})")
        if not args.list:
            print("\nerror: --subject is required (the prompt leads with it)", file=sys.stderr)
            return 2
        return 0

    print(format_cli(build(args.subject, addon=args.addon, view=args.view,
                           layout=args.layout, grid=args.grid, background=args.background,
                           color=args.color, outlines=args.outlines,
                           palette=args.palette, data=data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
