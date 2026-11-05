# Krita Agent Skill — drive Krita by hand or from code

Reference for driving Krita for card/asset work — both manually and
programmatically. Written so an AI agent (or a human) can follow it.
The full numbered walkthrough (with a worked marble example) is in
[`inherit-alpha-howto.md`](inherit-alpha-howto.md).

## 1. The core move — Inherit Alpha = clipping mask

A paint layer with **Inherit Alpha ON** only shows where the layer(s) **below it
in the same group** already have pixels. So paint never spills off the shape.

Pattern, always:

```
GROUP (one per object)
 ├─ paint layer   ← Inherit Alpha ON  (paint here; clips to the shape)
 └─ base shape    ← the silhouette (marble circle, card border, line-art cutout)
```

Enable it: select the layer → click the **α** icon on the right of its row in the
Layers docker. (Don't confuse with **Alpha Lock** = lock this layer's own pixels.)

## 2. Layer ops

- New paint layer: `+` in Layers docker / `Layer ▸ New ▸ Paint Layer` / **Insert**.
- Group selected: **Ctrl+G** (keeps each object's clip self-contained).
- Reorder by dragging; top layer draws on top.
- Blend mode + Opacity: top of the Layers docker.

## 3. Editing gotchas (hard-won — don't repeat)

- **Pure white paints gray** → the brush is textured or **Flow < 100%**. Use a
  HARD round (`Basic-1`, `Ink-2 Fineliner`, `Pixel Art`), Opacity 100, Flow 100
  (**F5** opens the brush editor). For solid color, skip the brush: `Edit ▸ Fill
  with Foreground Color` (always 100%).
- **Color spills outside the shape** → Inherit Alpha is OFF, or the layer isn't
  inside the group above the base shape.
- **A line you can't erase** → it's baked into the base-shape layer, not your
  paint layer. Paint over it on an Inherit-Alpha layer above, or edit the base.
- **White halo on a cutout** → the alpha edge needs to hug the object; erode the
  semi-transparent light fringe pixels before compositing on dark backgrounds.

## 4. Blend modes — tint vs cover

- **Normal** = covers flat (hides texture below).
- **Color / Overlay** = tints, so metallic/marble texture shows through.
- **Multiply** = shadows; **Add / Screen** = highlights/glints.

Keep shadows on a Multiply layer and highlights on an Add layer, both Inherit Alpha.

## 5. Card-asset workflow

- **Backgrounds behind transparent cards/borders**: add a bottom layer (outside
  the object groups) filled with a dark/contrast color so the art is visible.
- **Visibility fix**: line art with poor contrast on its background → duplicate the
  art layer and convert to solid **white** or **black** (Filter ▸ Adjust ▸
  Desaturate, then Levels to pure black/white) so it reads on any background.
- Real transparent assets: keep the cutout (white bg removed) on its own layer;
  put color/shading on Inherit-Alpha layers above it.

## 6. Build .kra files programmatically (kritarunner) — the automation

Don't hand-write `.kra` (custom binary tiles). Use `kritarunner`:
`/Applications/krita.app/Contents/MacOS/kritarunner -s <module>` (macOS; on
Linux the binary ships next to `krita`).

Hard-won gotchas:

- `-s` imports a **module by name** — it ignores `PYTHONPATH`/abs paths. Copy the
  script to kritarunner's own dir:
  `~/Library/Application Support/kritarunner/krita/pykrita/`.
- Wrap logic in `def __main__(*args):` (kritarunner calls the `__main__` function).
- `print()` output is **not surfaced** — verify via the saved file, not stdout.
- Krita API: `app=Krita.instance(); doc=app.createDocument(w,h,name,"RGBA","U8","",300.0)`;
  `g=doc.createNode(name,"grouplayer")`; `n=doc.createNode(name,"paintlayer")`;
  `parent.addChildNode(child, aboveOrNone)`; `n.setInheritAlpha(True)`;
  `n.setPixelData(QByteArray(bytes),x,y,w,h)`; `doc.saveAs(path); doc.close()`.
- `setPixelData` wants **8-bit BGRA** byte order (not RGBA). From PIL/numpy:
  `np.array(im.convert("RGBA"))[:,:,[2,1,0,3]].tobytes()`. To avoid PIL inside
  Krita's python, precompute raw `.bgra` + a `manifest.json` in normal `python3`,
  then have the kritarunner script read raw bytes.
- In the saved `maindoc.xml`, **Inherit Alpha = `channelflags="1110"`** on the layer.
- Pass per-run config via env vars (kritarunner inherits env): e.g.
  `KRA_BASE=... KRA_OUT=... kritarunner -s kra_build`.

The working script is [`tools/kra_build.py`](../tools/kra_build.py). It builds
groups + Inherit-Alpha layers from a manifest of raw BGRA tiles, and writes
`<KRA_OUT>.result.json` (`{"ok", "out", "layers", "error"}`) because `print()` is
swallowed. Make the tiles + manifest from ordinary PNGs with
[`tools/kra_prepare.py`](../tools/kra_prepare.py) (`--preview` renders a flat
`preview.png` for sign-off first).

## 6b. Drive a live Krita (the bridge)

With the *Krita Image Uploader* plugin enabled, use
[`tools/krita_bridge.py`](../tools/krita_bridge.py) rather than writing command
files by hand. It waits for Krita's answer and exits 0 (ok), 1 (failed) or 2 (timeout):

```
python3 tools/krita_bridge.py ping
python3 tools/krita_bridge.py insert render.png --name "AI pass 1"
python3 tools/krita_bridge.py export --max-side 1024   # read krita/latest.png
```

Same golden rule: insert into a *new layer* or a *new document*, never paint over
the artist's layers.

## 7. Golden rule for agents

**Never edit the artist's live Krita session/file.** Always render to NEW
separate files they can open and compare. Render previews and share them for
sign-off before committing to a final `.kra`.
