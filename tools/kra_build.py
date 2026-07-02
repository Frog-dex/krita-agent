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
    "resolution": 300,
    "bg":    {"file": "bg.bgra", "w": W, "h": H},
    "cards": [
      {"name": "card 1", "file": "card1.bgra",
       "x": X, "y": Y, "w": W, "h": H},
      ...
    ]
  }

Pixel files are raw 8-bit BGRA bytes (w*h*4). From PIL/numpy:
  np.array(im.convert("RGBA"))[:, :, [2, 1, 0, 3]].tobytes()

Usage:
  cp tools/kra_build.py "~/Library/Application Support/kritarunner/krita/pykrita/"
  KRA_BASE=~/my-assets/_kra KRA_OUT=~/my-assets/out.kra \
    /Applications/krita.app/Contents/MacOS/kritarunner -s kra_build
"""
import json, os
from krita import Krita
from PyQt5.QtCore import QByteArray

BASE = os.environ.get("KRA_BASE", os.path.expanduser("~/kra-build"))
OUT  = os.environ.get("KRA_OUT", os.path.join(BASE, "output.kra"))


def __main__(*args):
    man = json.load(open(os.path.join(BASE, "manifest.json")))
    cw, ch = man["canvas"]

    app = Krita.instance()
    app.setBatchmode(True)
    doc = app.createDocument(cw, ch, man.get("name", "kra-build"),
                             "RGBA", "U8", "", float(man["resolution"]))
    doc.setBatchmode(True)
    root = doc.rootNode()

    bg = doc.createNode("BACKGROUND", "paintlayer")
    root.addChildNode(bg, None)
    bgd = open(os.path.join(BASE, man["bg"]["file"]), "rb").read()
    bg.setPixelData(QByteArray(bgd), 0, 0, man["bg"]["w"], man["bg"]["h"])

    for c in man["cards"]:
        grp = doc.createNode(c["name"], "grouplayer")
        root.addChildNode(grp, None)

        border = doc.createNode("border", "paintlayer")
        grp.addChildNode(border, None)
        d = open(os.path.join(BASE, c["file"]), "rb").read()
        border.setPixelData(QByteArray(d), c["x"], c["y"], c["w"], c["h"])

        paint = doc.createNode("color - Inherit Alpha", "paintlayer")
        grp.addChildNode(paint, border)        # placed ABOVE the border
        paint.setInheritAlpha(True)            # clip paint to the border shape

    doc.refreshProjection()
    ok = doc.saveAs(OUT)
    print("SAVED" if ok else "SAVE-FAILED", OUT)
    doc.close()


if __name__ == "__main__":
    __main__()
