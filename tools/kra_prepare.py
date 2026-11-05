#!/usr/bin/env python3
"""kra_prepare — turn ordinary PNG/JPG files into a kra_build job folder.

``kra_build.py`` runs inside kritarunner, which has no PIL, so its inputs must be
raw 8-bit BGRA tiles plus a manifest. This script produces exactly that from
regular images, and can also render a flat ``preview.png`` so an artist can sign
off on the layout before the layered .kra is built.

Each input image becomes one object group in the .kra: the image is the base
shape and an empty "color - Inherit Alpha" layer is clipped to it.

Examples:
  # Auto-layout three cards on a dark background, canvas sized to fit
  python3 tools/kra_prepare.py ~/kra-build card1.png card2.png card3.png \\
      --bg-color "#1d1a17" --preview

  # Fixed A4 @300dpi canvas, explicit positions (x,y after '@'), image background
  python3 tools/kra_prepare.py ~/kra-build --canvas 2480x3508 --bg paper.jpg \\
      hero.png@200,300 villain.png@1300,300

Then build it (see README):
  KRA_BASE=~/kra-build KRA_OUT=~/out.kra kritarunner -s kra_build

Requires Pillow (``pip install pillow``).
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageColor
except ImportError:  # pragma: no cover - exercised only without Pillow
    sys.exit("kra_prepare needs Pillow: pip install pillow")

SPEC_RE = re.compile(r"^(?P<path>.+?)(?:@(?P<x>-?\d+),(?P<y>-?\d+))?$")


def to_bgra_bytes(image):
    """Pillow image -> raw 8-bit BGRA bytes (Krita's native RGBA/U8 order)."""
    r, g, b, a = image.convert("RGBA").split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def parse_size(text):
    match = re.fullmatch(r"(\d+)[xX](\d+)", text.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {text!r}")
    w, h = int(match.group(1)), int(match.group(2))
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("canvas size must be positive")
    return w, h


def parse_card_spec(text):
    """'card.png' -> (Path, None); 'card.png@10,20' -> (Path, (10, 20))."""
    match = SPEC_RE.match(text)
    path = Path(match.group("path")).expanduser()
    pos = (int(match.group("x")), int(match.group("y"))) if match.group("x") is not None else None
    return path, pos


def auto_layout(sizes, gap=40, margin=40, max_row_width=None):
    """Place boxes left-to-right, wrapping rows. Returns (positions, (W, H))."""
    if not sizes:
        return [], (margin * 2 or 1, margin * 2 or 1)
    if max_row_width is None:
        # Aim for a roughly square sheet.
        total = sum(w for w, _ in sizes) + gap * (len(sizes) - 1)
        rows = max(1, round((total / max(h for _, h in sizes)) ** 0.5 / 1.5))
        max_row_width = max(max(w for w, _ in sizes), total // rows + 1)
    positions = []
    x = y = margin
    row_h = 0
    widest = 0
    for w, h in sizes:
        if x > margin and x + w > margin + max_row_width:
            x = margin
            y += row_h + gap
            row_h = 0
        positions.append((x, y))
        x += w + gap
        row_h = max(row_h, h)
        widest = max(widest, x - gap)
    return positions, (widest + margin, y + row_h + margin)


def unique_name(stem, used):
    name, n = stem, 2
    while name in used:
        name = f"{stem} {n}"
        n += 1
    used.add(name)
    return name


def prepare(out_dir, cards, canvas=None, bg=None, bg_color=None, resolution=300,
            name="kra-build", gap=40, margin=40, preview=False):
    """Write manifest.json + .bgra tiles into out_dir; returns the manifest dict."""
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    loaded = []
    for path, pos in cards:
        if not path.is_file():
            raise FileNotFoundError(f"no such image: {path}")
        loaded.append((path, pos, Image.open(path).convert("RGBA")))

    auto_idx = [i for i, (_, pos, _) in enumerate(loaded) if pos is None]
    positions = {i: pos for i, (_, pos, _) in enumerate(loaded) if pos is not None}
    if auto_idx:
        sizes = [loaded[i][2].size for i in auto_idx]
        row_width = canvas[0] - 2 * margin if canvas else None
        placed, auto_canvas = auto_layout(sizes, gap, margin, row_width)
        positions.update(dict(zip(auto_idx, placed)))
    else:
        auto_canvas = (1, 1)

    if canvas is None:
        fixed_extent = [
            (positions[i][0] + img.width + margin, positions[i][1] + img.height + margin)
            for i, (_, _, img) in enumerate(loaded)
        ]
        canvas = (
            max([auto_canvas[0]] + [w for w, _ in fixed_extent]),
            max([auto_canvas[1]] + [h for _, h in fixed_extent]),
        )
    cw, ch = canvas

    if bg is not None:
        background = Image.open(Path(bg).expanduser()).convert("RGBA").resize((cw, ch), Image.LANCZOS)
    else:
        fill = ImageColor.getrgb(bg_color) if bg_color else (0, 0, 0, 0)
        if len(fill) == 3:
            fill = (*fill, 255)
        background = Image.new("RGBA", (cw, ch), fill)
    (out_dir / "bg.bgra").write_bytes(to_bgra_bytes(background))

    manifest = {
        "name": name,
        "canvas": [cw, ch],
        "resolution": resolution,
        "bg": {"file": "bg.bgra", "w": cw, "h": ch},
        "cards": [],
    }
    used = set()
    composite = background.copy() if preview else None
    for index, (path, _, image) in enumerate(loaded, start=1):
        x, y = positions[index - 1]
        tile = f"card{index:02d}.bgra"
        (out_dir / tile).write_bytes(to_bgra_bytes(image))
        manifest["cards"].append({
            "name": unique_name(path.stem, used),
            "file": tile,
            "x": x, "y": y, "w": image.width, "h": image.height,
        })
        if composite is not None:
            layer = Image.new("RGBA", composite.size)
            layer.paste(image, (x, y))  # paste() clips negative / off-canvas positions
            composite = Image.alpha_composite(composite, layer)

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if composite is not None:
        composite.save(out_dir / "preview.png")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kra_prepare",
        description="Prepare manifest.json + raw BGRA tiles for tools/kra_build.py.",
        epilog="Card syntax: image.png or image.png@X,Y (top-left position on the canvas).",
    )
    parser.add_argument("out_dir", help="job folder to write (becomes KRA_BASE)")
    parser.add_argument("cards", nargs="+", help="base-shape images, one group each")
    parser.add_argument("--canvas", type=parse_size, help="WIDTHxHEIGHT (default: fit the layout)")
    bg = parser.add_mutually_exclusive_group()
    bg.add_argument("--bg", help="background image (stretched to the canvas)")
    bg.add_argument("--bg-color", help="solid background, e.g. '#1d1a17' (default: transparent)")
    parser.add_argument("--resolution", type=float, default=300.0, help="document DPI (default 300)")
    parser.add_argument("--name", default="kra-build", help="document title")
    parser.add_argument("--gap", type=int, default=40, help="auto-layout spacing in px")
    parser.add_argument("--margin", type=int, default=40, help="auto-layout margin in px")
    parser.add_argument("--preview", action="store_true", help="also write a flat preview.png")
    args = parser.parse_args(argv)

    try:
        manifest = prepare(
            args.out_dir, [parse_card_spec(c) for c in args.cards], canvas=args.canvas,
            bg=args.bg, bg_color=args.bg_color, resolution=args.resolution, name=args.name,
            gap=args.gap, margin=args.margin, preview=args.preview,
        )
    except (OSError, ValueError) as exc:
        print(f"kra_prepare: {exc}", file=sys.stderr)
        return 1
    cw, ch = manifest["canvas"]
    print(f"Wrote {len(manifest['cards'])} object(s) on a {cw}x{ch} canvas to {Path(args.out_dir).expanduser()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
