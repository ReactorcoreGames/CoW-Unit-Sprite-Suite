"""Batch conversion: a folder of raw generator output -> finished CoW sprites.

All the logic lives in `convert.py`; this is the command-line skin over it, and
the previewer calls the same functions directly.

    python tools/batch_convert.py IN OUT --class all

Anything the grid detector is not confident about goes to `OUT/needs-review/`
with its intermediates beside it, rather than being guessed at -- a wrong grid
produces a sprite that looks plausible and is silently wrong, which is the one
outcome worth spending a manual review to avoid.

Re-run a reviewed file with an explicit pitch to override detection:

    python tools/batch_convert.py IN OUT --class ship --pitch 60.25
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from PIL import Image

import convert
import cowpalette

SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".bmp"}


def build_palette(args) -> convert.ClassPalette:
    """Pick the quantization target from the command line.

    Two sources, deliberately kept distinct:

    * `--class` reads `pipeline/classes.json` -- colors observed in real AW2
      art. This is the default because it is what the reference sheets fed to
      the generator were built from, so the art coming back should already be
      close to it.
    * `--band` reads the colortable directly, giving every color CoW defines
      for that band. Use it to match stock CoW sprites (which use `unit`), or
      when the AW2 sample was too thin for the art in hand.
    """
    if args.band:
        return convert.table_class_palette(args.band, quadrants=args.quadrants)
    return convert._resolve_class(args.unit_class)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=pathlib.Path, help="folder of raw generator output")
    ap.add_argument("output", type=pathlib.Path, help="folder to write finished sprites to")

    tgt = ap.add_mutually_exclusive_group()
    tgt.add_argument("--class", dest="unit_class", default=convert.ALL_CLASSES,
                     help="unit class from pipeline/classes.json, or 'all' for the union "
                          "of every class (default: all)")
    tgt.add_argument("--band", nargs="+", metavar="BAND",
                     help="colortable band(s) instead, e.g. --band unit, "
                          f"from {list(cowpalette.UNIT_CLASSES)}")
    ap.add_argument("--quadrants", nargs="+",
                    default=["ui", "tertiary", "secondary", "primary"],
                    help="quadrants to draw from when using --band")

    ap.add_argument("--key", default="green", choices=sorted(convert.CHROMA_KEYS),
                    help="chroma key color (default: green)")
    ap.add_argument("--tolerance", type=int, default=90,
                    help="chroma key radius in RGB distance (default: 90)")
    ap.add_argument("--fringe", type=int, default=2,
                    help="fringe cleanup radius in px (default: 2)")

    ap.add_argument("--confidence", type=float, default=0.55,
                    help="below this, route to needs-review/ (default: 0.55)")
    ap.add_argument("--pitch", type=float,
                    help="override grid detection with this pitch, in source px")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="grid offset to pair with --pitch (default: 0)")

    ap.add_argument("--stages", action="store_true",
                    help="also write the keyed/snapped/quantized intermediates")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.input.is_dir():
        ap.error(f"{args.input} is not a folder")

    files = sorted(p for p in args.input.iterdir()
                   if p.suffix.lower() in SUFFIXES and p.is_file())
    if not files:
        print(f"no images in {args.input}", file=sys.stderr)
        return 1

    try:
        pal = build_palette(args)
    except (KeyError, ValueError) as exc:
        ap.error(str(exc))

    args.output.mkdir(parents=True, exist_ok=True)
    review_dir = args.output / "needs-review"

    done = flagged = failed = 0
    for path in files:
        try:
            with Image.open(path) as im:
                img = im.copy()
        except OSError as exc:
            failed += 1
            print(f"  !! {path.name}: cannot read ({exc})", file=sys.stderr)
            continue

        grid = None
        if args.pitch:
            # A user-supplied pitch is trusted outright: they measured it, and
            # convert_image skips the confidence gate when given a grid.
            cols = max(1, round((img.width - args.offset) / args.pitch))
            rows = max(1, round((img.height - args.offset) / args.pitch))
            grid = convert.Grid(args.pitch, args.pitch, args.offset, args.offset,
                                cols, rows, 1.0)

        try:
            res = convert.convert_image(
                img, pal, name=path.stem, key=args.key,
                tolerance=args.tolerance, fringe=args.fringe,
                confidence_floor=args.confidence, grid=grid)
        except convert.EncodeError as exc:
            failed += 1
            print(f"  !! {path.name}: {exc}", file=sys.stderr)
            continue

        if res.needs_review:
            flagged += 1
            review_dir.mkdir(parents=True, exist_ok=True)
            _write_stages(res, review_dir, always=True)
            (review_dir / f"{res.name}.txt").write_text(
                f"{res.reason}\n", encoding="utf-8")
            if not args.quiet:
                print(f"  ?? {path.name} -> needs-review: {res.reason}")
            continue

        # `+mask` is CoW's own suffix for the index-encoded form.
        out = args.output / f"{res.name}+mask.png"
        res.encoded.save(out)
        if args.stages:
            _write_stages(res, args.output)
        done += 1
        if not args.quiet:
            g = res.grid
            print(f"  ok {path.name} -> {out.name}  "
                  f"{res.encoded.width}x{res.encoded.height}  "
                  f"pitch {g.pitch_x:.2f}x{g.pitch_y:.2f}  conf {g.confidence:.2f}")

    print(f"\n{done} converted, {flagged} needs review, {failed} failed "
          f"(target {pal.name}, {len(pal)} colors)")
    return 1 if failed else 0


def _write_stages(res: convert.ConversionResult, folder: pathlib.Path,
                  always: bool = False) -> None:
    for tag, img in (("keyed", res.keyed), ("snapped", res.snapped),
                     ("quantized", res.quantized), ("encoded", res.encoded)):
        if img is not None:
            img.save(folder / f"{res.name}.{tag}.png")


if __name__ == "__main__":
    raise SystemExit(main())
