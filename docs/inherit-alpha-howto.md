# Krita How-To — Inherit Alpha, Layers & Editing

> Goal: paint **on a shape only** (e.g. a marble, a card border) without color
> spilling onto the background. The trick is a paint layer set to **Inherit Alpha**
> stacked on top of a "base shape" layer. Written so a human *or* an AI agent
> driving Krita can follow it step by step.

---

## 0. The one idea that makes this work — Inherit Alpha

**Inherit Alpha = a built-in clipping mask.** A layer with Inherit Alpha turned ON
can only show pixels where the layer(s) *below it in the same group* already have
pixels. Paint outside that shape simply doesn't appear.

So the pattern is always:

```
GROUP
 ├─ paint layer   ← Inherit Alpha ON   (you paint here; it clips to the shape)
 └─ base shape    ← the silhouette (the marble circle, the card border, etc.)
```

Paint on the top layer → it lands only on the shape below. The base shape stays
untouched, so it's fully non-destructive.

---

## 1. The Layers docker

Everything happens in the **Layers docker** (right side by default; if hidden:
`Settings ▸ Dockers ▸ Layers`).

Each layer row, left → right:
- **Eye icon** = show/hide the layer.
- **Thumbnail + name** = double-click the name to rename.
- **Right-side icons** = per-layer toggles, including **Inherit Alpha** and
  **Alpha Lock** (see §3).
- **Top of the docker**: a **blend-mode dropdown** (reads "Normal") and an
  **Opacity** slider.
- **Bottom row of buttons**: `+` (new layer), group, duplicate, move up/down,
  delete (trash).

---

## 2. Add a layer

Any of these:
- Click the **`+` (New Paint Layer)** button at the bottom-left of the Layers docker, **or**
- Menu: `Layer ▸ New ▸ Paint Layer`, **or**
- Shortcut: **Insert**.

New layers appear **above** the currently selected layer. Drag layers up/down in
the docker to reorder; the one on top is drawn on top.

---

## 3. Turn a layer into an "Inherit Alpha" layer

1. **Select** the layer you want to paint on (click its row).
2. On the **right end of that layer's row**, click the **Inherit Alpha** icon
   (the small **α** button). When it's ON the icon is highlighted/active.
   - It sits next to the **Alpha Lock** icon — don't confuse them:
     - **Inherit Alpha** = clip to the shape on the layer(s) *below* (use this).
     - **Alpha Lock** = lock *this layer's own* existing pixels (paint only where
       this layer already has paint).
3. (Alternative) Right-click the layer ▸ **Properties** also exposes it on some builds.

Now painting on this layer is clipped to the shape beneath it inside the group.

---

## 4. Group layers (keeps each object's clip self-contained)

- Select the base layer + the paint layer (Ctrl-click to multi-select), then
  **`Ctrl+G`** (or `Layer ▸ Group Layers`). 
- Inherit Alpha inherits from layers **below it inside the same group**, so one
  group per object keeps marbles from clipping onto each other.

---

## 5. Pick a brush — and avoid the gray-white trap

⚠️ **Important:** a textured/painterly brush (e.g. any "GDquest Flat" preset)
never paints 100% opaque, so **pure white comes out gray** when clipped. Fix:

- Open the **Brush Presets** docker, choose a **hard round** preset
  (`Basic-1`, `Ink-2 Fineliner`, or `Pixel Art`).
- Top toolbar: set **Opacity = 100%** and **Flow = 100%** (Flow is the hidden one
  that causes washed-out color — open the brush editor with **F5** if you don't
  see it).
- For a perfectly solid fill, don't brush at all — use **Fill** (see §7).

---

## 6. Set your color

- Use the **Advanced Color Selector** docker (the wheel + triangle).
- For an exact color, double-click the **foreground swatch** (top-left toolbar)
  to open the color dialog and type the hex/RGB.
- Pure white = top of the value triangle / `#FFFFFF`.

---

## 7. Paint or Fill the shape

With the Inherit-Alpha layer selected:

- **Brush** it in by hand (hard brush, 100% flow) — for shading and detail.
- **Solid fill** the whole shape: `Edit ▸ Fill with Foreground Color`
  (or `Fill with Background Color`). This is always 100% opaque → no gray.
  The shortcut is shown next to the menu item.
- **Fill a region** with the **Fill tool** (the paint-bucket, shortcut `F`).

---

## 8. Blend modes — tint vs. cover

The blend-mode dropdown at the top of the Layers docker changes how your paint
layer combines with the texture below:

- **Normal** → covers it flat (good for solid color, hides texture underneath).
- **Color** or **Overlay** → **tints** the layer below, so a metallic/marble
  texture shows *through* your color. Use this to recolor without flattening.
- **Multiply** → darkens (great for shadows / the dark side of a marble).
- **Add** / **Screen** → brightens (great for glossy highlights / glints).

Tip: keep shadows on a Multiply layer and highlights on an Add layer — both
Inherit Alpha — so you can tune each independently.

---

## 9. Opacity & erasing

- **Opacity slider** (top of docker) fades the whole layer.
- **Eraser**: press **E** to toggle the current brush into eraser mode. On an
  Inherit-Alpha layer the eraser just removes your paint (the base shape stays).

---

## 10. Worked example — paint a glass marble

1. **New Paint Layer**, name it `marble base`.
2. Make the silhouette: **Ellipse selection tool** → hold **Shift** drag a circle
   → `Edit ▸ Fill with Foreground Color` (any color) → `Select ▸ Deselect`
   (`Ctrl+Shift+A`). This circle is your alpha shape.
3. **New Paint Layer** above it, name `base color`, turn **Inherit Alpha ON**,
   blend **Normal** → fill it with the marble's main color.
4. **New Paint Layer** `shadow`, **Inherit Alpha ON**, blend **Multiply** →
   soft-brush a darker crescent on the lower-right (the side away from the light).
5. **New Paint Layer** `swirl`, **Inherit Alpha ON** → paint the glass swirl /
   inner color streaks.
6. **New Paint Layer** `highlight`, **Inherit Alpha ON**, blend **Add** (or Normal)
   → small **hard white dot** top-left for the glossy catch-light, plus a faint
   soft reflection bottom-edge.
7. Group all of them (`Ctrl+G`) → name the group `marble 1`. Duplicate the group
   for more marbles; recolor the `base color` layer per marble.

Because every paint layer Inherits Alpha from `marble base`, nothing you paint
ever leaves the circle.

---

## Quick reference

| Action | How |
|---|---|
| New paint layer | `+` button / `Layer ▸ New ▸ Paint Layer` / **Insert** |
| Inherit Alpha | select layer → click **α** icon on the layer row |
| Group selected layers | **Ctrl+G** |
| Fill with foreground color | `Edit ▸ Fill with Foreground Color` |
| Fill tool (bucket) | **F** |
| Eraser toggle | **E** |
| Brush editor (Flow etc.) | **F5** |
| Deselect | **Ctrl+Shift+A** |
| Blend mode / Opacity | top of Layers docker |

## Gotchas (we hit these)
- Pure white painting gray → textured brush or Flow < 100%. Use a **hard brush, Flow 100%**, or **Fill**.
- Color spilling outside the shape → the paint layer's **Inherit Alpha is OFF**, or it's **not inside the group** above the base shape.
- A "line" you can't erase → it's baked into the **base shape layer**, not your paint layer. Edit the base layer (or paint over on an Inherit-Alpha layer above).
