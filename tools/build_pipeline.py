"""Build the per-class pipeline materials from the AW2 Orange Star sprites.

Outputs, all under pipeline/:
  classes.json                 the six class palettes + encode tables
  reference/<class>.png        6x upscaled safety-green reference sheet per class
  reference/<class>_1x.png     the same sheet at native size
  palette/<class>_swatch.png   the class palette as labelled swatches
"""
import collections
import json
import pathlib
import sys

import numpy as np
from PIL import Image

PROJ = pathlib.Path(__file__).resolve().parent.parent
SRC = PROJ / 'pipeline' / 'source'
OUT = PROJ / 'pipeline'
sys.path.insert(0, str(PROJ / 'tools'))
import cowpalette as cp  # noqa: E402

GREEN = (0, 255, 0, 255)

# AW2 unit -> CoW unit class. Recon has its own row in the colortable; the
# remaining ground units split by size the way CoW's own rows do.
CLASS = {
    'Soldier Rifle': 'infantry', 'Soldier Bazooka': 'infantry',
    'Recon Car': 'recon',
    'APC': 'smallvehicle', 'Tank Light': 'smallvehicle',
    'Mobile AA Gun': 'smallvehicle', 'Mobile AA Missle': 'smallvehicle',
    'Mobile Arty Shell': 'smallvehicle', 'Mobile Arty Rocket': 'smallvehicle',
    'Tank Heavy': 'largevehicle', 'Neo Tank': 'largevehicle',
    'Plane Fighter': 'airvehicle', 'Planet Bomber': 'airvehicle',
    'Heli Battle': 'airvehicle', 'Heli Transport': 'airvehicle',
    'Battleship': 'ship', 'Cruiser': 'ship', 'Lander Boat': 'ship',
    'Submarine (Surfaced)': 'ship',
}
ORDER = ['infantry', 'recon', 'smallvehicle', 'largevehicle', 'airvehicle', 'ship']

(OUT / 'reference').mkdir(parents=True, exist_ok=True)
(OUT / 'palette').mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- table index
tab = np.array(Image.open(PROJ / 'reference/colortables/orange_star.png').convert('RGBA'))
cells = collections.defaultdict(list)
for y in range(256):
    for x in range(256):
        if tab[y, x, 3]:
            cells[tuple(int(v) for v in tab[y, x, :3])].append((x, y))
table_colors = np.array(list(cells), dtype=np.int32)


# Colors whose nearest Orange Star cell is the wrong *material*. Nearest-RGB is
# measured in one team only, and for these AW2 water/glass blues it picked
# cells that turn grey, green or gold in other armies. CoW's tables hold no
# blue that is constant across teams, so each is pinned to the cell CoW's own
# art uses for the same material: (145,4) is the water cell in all 7 stock
# ship sprites and is blue in all 32 teams (pale cyan in 23, mid blue in 9);
# (139,7) is dark teal-blue in 31 of 32. Light and dark stay distinct.
PINNED = {
    (0xc0, 0xe0, 0xf8): (145, 4),   # was (138,3): green in 9 teams, red in 1
    (0x70, 0x98, 0xe8): (145, 4),   # was (7,150): grey in 18 teams
    (0x58, 0x60, 0x98): (139, 7),   # was (141,9): gold in 16 teams, red in 1
}


def nearest_cell(rgb):
    d = ((table_colors - np.array(rgb, dtype=np.int32)) ** 2).sum(axis=1)
    i = int(d.argmin())
    c = tuple(int(v) for v in table_colors[i])
    return c, float(d[i]) ** 0.5


# ---------------------------------------------------------------- gather
per_class = collections.defaultdict(collections.Counter)
files_by_class = collections.defaultdict(list)
for f in sorted(SRC.glob('Orange Star *.png')):
    unit = f.stem.replace('Orange Star ', '')
    cls = CLASS.get(unit)
    if not cls:
        continue
    files_by_class[cls].append((unit, f))
    a = np.array(Image.open(f).convert('RGBA'))
    for px in a[a[:, :, 3] > 0][:, :3]:
        per_class[cls][tuple(int(v) for v in px)] += 1

# ---------------------------------------------------------------- classes.json
data = {
    'source': 'pipeline/source (Advance Wars 2, Orange Star)',
    'table': 'reference/colortables/orange_star.png',
    'note': ('Per-class quantization targets. Encode maps an authored RGB to the '
             '(red, green) coordinate a CoW sprite stores; recolor then reads '
             'table[green, red] for any team.'),
    'classes': {},
}

for cls in ORDER:
    counter = per_class[cls]
    total = sum(counter.values())
    entries = []
    for rgb, n in counter.most_common():
        exact = rgb in cells
        if rgb in PINNED:
            x, y = PINNED[rgb]
            target = tuple(int(v) for v in tab[y, x, :3])
            dist = float(sum((a - b) ** 2 for a, b in zip(rgb, target))) ** 0.5
        else:
            target, dist = (rgb, 0.0) if exact else nearest_cell(rgb)
            x, y = cells[target][0]
        entries.append({
            'rgb': '#%02x%02x%02x' % rgb,
            'pixels': n,
            'share': round(n / total, 5),
            'exact': exact,
            'encode_rgb': '#%02x%02x%02x' % target,
            'red': x, 'green': y,
            'quadrant': cp.quadrant_of(x, y),
            'distance': round(dist, 1),
        })
    n_exact = sum(1 for e in entries if e['exact'])
    px_exact = sum(e['pixels'] for e in entries if e['exact'])
    data['classes'][cls] = {
        'units': [u for u, _ in files_by_class[cls]],
        'files': len(files_by_class[cls]),
        'colors': len(entries),
        'pixels': total,
        'exact_colors': n_exact,
        'exact_pixel_share': round(px_exact / total, 4),
        'palette': entries,
    }

(OUT / 'classes.json').write_text(json.dumps(data, indent=2))
print('wrote pipeline/classes.json')

# ---------------------------------------------------------------- ref sheets
for cls in ORDER:
    items = files_by_class[cls]
    ims = [Image.open(f).convert('RGBA') for _, f in items]
    cw = max(i.width for i in ims) + 4
    ch = max(i.height for i in ims) + 4
    cols = min(len(ims), 5)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new('RGBA', (cols * cw, rows * ch), GREEN)
    for i, im in enumerate(ims):
        x = (i % cols) * cw + (cw - im.width) // 2
        y = (i // cols) * ch + (ch - im.height) // 2
        sheet.paste(im, (x, y), im)
    sheet.convert('RGB').save(OUT / 'reference' / f'{cls}_1x.png')
    big = sheet.resize((sheet.width * 6, sheet.height * 6), Image.NEAREST)
    big.convert('RGB').save(OUT / 'reference' / f'{cls}.png')
    print(f'  reference/{cls}.png  {big.size}  ({len(ims)} units)')

# ---------------------------------------------------------------- swatches
SW, PAD = 16, 2
for cls in ORDER:
    entries = data['classes'][cls]['palette']
    cols = 8
    rows = (len(entries) + cols - 1) // cols
    w = cols * (SW + PAD) + PAD
    h = rows * (SW + PAD) + PAD
    img = Image.new('RGB', (w, h), (24, 24, 24))
    for i, e in enumerate(entries):
        rgb = tuple(int(e['rgb'][j:j + 2], 16) for j in (1, 3, 5))
        x = PAD + (i % cols) * (SW + PAD)
        y = PAD + (i // cols) * (SW + PAD)
        img.paste(Image.new('RGB', (SW, SW), rgb), (x, y))
        if not e['exact']:            # mark inexact with a corner dot
            img.paste(Image.new('RGB', (3, 3), (255, 0, 255)), (x, y))
    img.resize((img.width * 3, img.height * 3), Image.NEAREST).save(
        OUT / 'palette' / f'{cls}_swatch.png')

print('\nsummary')
print(f'{"class":14s} {"units":>5s} {"colors":>7s} {"exact":>6s} {"px exact":>9s}')
for cls in ORDER:
    c = data['classes'][cls]
    print(f'{cls:14s} {c["files"]:5d} {c["colors"]:7d} {c["exact_colors"]:6d} '
          f'{c["exact_pixel_share"] * 100:8.1f}%')
