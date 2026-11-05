"""Run via kritarunner. Builds a native .kra where each object is a GROUP:
   [ color - Inherit Alpha ]   <- empty paint layer, inherits alpha of the base
   [ base  ]                   <- the base/silhouette art (border, cutout, shape)
so painting on the top layer lands only on the base shape (clipping-mask).

Configuration (kritarunner inherits the environment):
  KRA_BASE  directory containing manifest.json + raw .bgra pixel files
            (default: ~/kra-build)
  KRA_OUT   output .kra path (default: <KRA_BASE>/output.kra)

manifest.json schema:
  {
    "name": "My Document",              // optional document title
    "canvas": [width, height],
    "resolution": 300,                  // optional, default 300
    "bg":    {"file": "bg.bgra", "w": W, "h": H},   // optional
    "cards": [
      {"name": "card 1", "file": "card1.bgra",
       "x": X, "y": Y, "w": W, "h": H},
      ...
    ]
  }

Pixel files are raw 8-bit BGRA bytes (w*h*4). Easiest: let
tools/kra_prepare.py make them from ordinary PNGs. From PIL/numpy by hand:
  np.array(im.convert("RGBA"))[:, :, [2, 1, 0, 3]].tobytes()

kritarunner swallows print(), so the result is also written next to the output
as <KRA_OUT>.result.json: {"ok": true|false, "out": ..., "layers": N, "error": ...}

Usage:
  cp tools/kra_build.py "~/Library/Application Support/kritarunner/krita/pykrita/"
  KRA_BASE=~/my-assets/_kra KRA_OUT=~/my-assets/out.kra \
    /Applications/krita.app/Contents/MacOS/kritarunner -s kra_build
"""
import json
import os

from krita import Krita
from PyQt5.QtCore import QByteArray

INHERIT_ALPHA_LAYER = "color - Inherit Alpha"


def config():
    base = os.path.expanduser(os.environ.get("KRA_BASE", "~/kra-build"))
    out = os.path.expanduser(os.environ.get("KRA_OUT", os.path.join(base, "output.kra")))
    return base, out


def load_manifest(base):
    with open(os.path.join(base, "manifest.json")) as handle:
        manifest = json.load(handle)
    if "canvas" not in manifest or len(manifest["canvas"]) != 2:
        raise ValueError("manifest.canvas must be [width, height]")
    for i, card in enumerate(manifest.get("cards", [])):
        missing = [k for k in ("file", "x", "y", "w", "h") if k not in card]
        if missing:
            raise ValueError(f"cards[{i}] is missing {', '.join(missing)}")
    return manifest


def read_tile(base, spec):
    """Read a raw BGRA tile and check it matches its declared size."""
    path = os.path.join(base, spec["file"])
    with open(path, "rb") as handle:
        data = handle.read()
    expected = int(spec["w"]) * int(spec["h"]) * 4
    if len(data) != expected:
        raise ValueError(
            f"{spec['file']}: {len(data)} bytes, expected {expected} "
            f"({spec['w']}x{spec['h']} BGRA)"
        )
    return data


def build(base, out):
    manifest = load_manifest(base)
    cw, ch = manifest["canvas"]

    app = Krita.instance()
    app.setBatchmode(True)
    doc = app.createDocument(int(cw), int(ch), manifest.get("name", "kra-build"),
                             "RGBA", "U8", "", float(manifest.get("resolution", 300)))
    doc.setBatchmode(True)
    root = doc.rootNode()
    layers = 0

    bg_spec = manifest.get("bg")
    if bg_spec:
        bg = doc.createNode("BACKGROUND", "paintlayer")
        root.addChildNode(bg, None)
        bg.setPixelData(QByteArray(read_tile(base, bg_spec)), 0, 0, int(bg_spec["w"]), int(bg_spec["h"]))
        layers += 1

    for i, card in enumerate(manifest.get("cards", []), start=1):
        group = doc.createNode(card.get("name") or f"object {i}", "grouplayer")
        root.addChildNode(group, None)

        border = doc.createNode("border", "paintlayer")
        group.addChildNode(border, None)
        border.setPixelData(QByteArray(read_tile(base, card)),
                            int(card["x"]), int(card["y"]), int(card["w"]), int(card["h"]))

        paint = doc.createNode(INHERIT_ALPHA_LAYER, "paintlayer")
        group.addChildNode(paint, border)        # placed ABOVE the border
        paint.setInheritAlpha(True)              # clip paint to the border shape
        layers += 3

    doc.refreshProjection()
    ok = bool(doc.saveAs(out))
    doc.close()
    return ok, layers


def write_result(out, payload):
    try:
        with open(out + ".result.json", "w") as handle:
            json.dump(payload, handle, indent=2)
    except OSError:
        pass


def __main__(*args):
    base, out = config()
    try:
        ok, layers = build(base, out)
        payload = {"ok": ok, "out": out, "layers": layers}
        if not ok:
            payload["error"] = "Document.saveAs returned False"
    except Exception as exc:  # noqa: BLE001 - report every failure in the result file
        payload = {"ok": False, "out": out, "error": f"{type(exc).__name__}: {exc}"}
    write_result(out, payload)
    print("SAVED" if payload["ok"] else "SAVE-FAILED", out, payload.get("error", ""))
    return payload


if __name__ == "__main__":
    __main__()
