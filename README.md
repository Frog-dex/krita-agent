# krita-agent

AI + agent tooling for [Krita](https://krita.org): generate images from cloud providers directly onto the canvas, drive Krita from external scripts/agents through a file-based bridge, and build layered `.kra` files programmatically with the Inherit-Alpha (clipping mask) workflow.

## What's inside

| Component | Path | What it does |
|---|---|---|
| **AI Image docker** | `pykrita/grok_image/` | Multi-provider image generation inside Krita: Grok (xAI), Nano Banana (Gemini 2.5 Flash Image, img2img), Imagen 4, or defer to local krita-ai-diffusion. Results land as new paint layers, sized to your selection. |
| **Krita Image Uploader** | `pykrita/krita_image_uploader/` | A file-based bridge so an external agent (or script) can command Krita: insert images as layers, export canvas snapshots, open projects — via JSON files in `~/.krita-image-uploader/`. |
| **`.kra` builder** | `tools/kra_build.py` | Headless `kritarunner` script that builds a native layered `.kra` from a manifest of raw BGRA tiles — one group per object with an Inherit-Alpha paint layer clipped to the base shape. |
| **Docs** | `docs/` | The Inherit-Alpha how-to (clipping-mask painting, blend modes, brush gotchas) and an agent-oriented Krita skill reference. |

## Features

- **Text-to-image and image-to-image** without leaving Krita. Nano Banana can take your current canvas **or just the active selection** as the input image, and the result is scaled back into that exact region.
- **No dependencies.** Both plugins use only Krita's bundled Python (PyQt5 + stdlib `urllib`) — nothing to pip-install.
- **Non-blocking UI.** All API calls run on `QThread` workers; Krita stays responsive.
- **Agent-friendly bridge.** Drop a JSON command file, Krita picks it up within 500 ms, moves it to `done/` (or writes an `.error.json`) with a status and timestamp.
- **Programmatic layered files.** `kra_build.py` produces real `.kra` documents with group layers and Inherit-Alpha clipping — the same structure you'd build by hand for card art, marbles, or any paint-on-a-shape workflow.

## Quick Start

### 1. Install the plugins

Copy the contents of `pykrita/` into Krita's resource folder (find yours via `Settings ▸ Manage Resources ▸ Open Resource Folder`):

```bash
# macOS
cp -R pykrita/* ~/Library/Application\ Support/krita/pykrita/

# Linux
cp -R pykrita/* ~/.local/share/krita/pykrita/

# Windows (PowerShell)
Copy-Item -Recurse pykrita\* "$env:APPDATA\krita\pykrita\"
```

Then in Krita: `Settings ▸ Configure Krita ▸ Python Plugin Manager` → enable **AI Image (Grok / Gemini / Imagen / Local)** and **Krita Image Uploader** → restart Krita. The dockers appear under `Settings ▸ Dockers`.

### 2. Add your API keys

**No keys ship in this repo.** The AI Image docker reads keys (in order) from:

1. `~/.config/grok-image/config.json`
2. The `XAI_API_KEY` / `GOOGLE_API_KEY` environment variables

```bash
mkdir -p ~/.config/grok-image
cat > ~/.config/grok-image/config.json <<'EOF'
{
  "XAI_API_KEY": "your-xai-key-here",
  "GOOGLE_API_KEY": "your-google-key-here"
}
EOF
chmod 600 ~/.config/grok-image/config.json
```

Grok needs `XAI_API_KEY` (console.x.ai); Nano Banana and Imagen 4 need `GOOGLE_API_KEY` (aistudio.google.com). The "Local" provider needs no key — it points you to the krita-ai-diffusion docker.

### 3. Generate

Open the **AI Image** docker → pick a provider → type a prompt → **Generate**. Each result is added as a new paint layer. For img2img: make a selection, check *"Use canvas / selection as input image"* (Nano Banana only), and the output is scaled into your selection bounds.

### 4. Drive Krita from an agent (optional)

With the Uploader plugin enabled, Krita polls `~/.krita-image-uploader/commands/` every 500 ms:

```bash
# Insert an image as a new layer in the active document
cat > ~/.krita-image-uploader/commands/insert-$(date +%s).json <<'EOF'
{"action": "insert_image", "path": "~/Pictures/result.png", "name": "AI result", "scale": 1.0}
EOF

# Export a snapshot of the current canvas (max 1024 px on the long side)
cat > ~/.krita-image-uploader/commands/export-$(date +%s).json <<'EOF'
{"action": "export_canvas", "max_side": 1024}
EOF

# Open (or focus) a project
cat > ~/.krita-image-uploader/commands/open-$(date +%s).json <<'EOF'
{"action": "open_project", "path": "~/art/mycard.kra"}
EOF
```

Results: processed commands move to `commands/done/` (failures become `*.error.json`), canvas snapshots land in `~/.krita-image-uploader/krita/snapshots/` with metadata in `latest.json`, and the open-project list refreshes every 5 s in `projects.json`.

### 5. Build a `.kra` headlessly (optional)

```bash
# kritarunner only imports modules from its own pykrita dir (macOS path shown)
cp tools/kra_build.py ~/Library/Application\ Support/kritarunner/krita/pykrita/

# Prepare a build dir with manifest.json + raw .bgra tiles (see schema in kra_build.py),
# then:
KRA_BASE=~/my-assets/_kra KRA_OUT=~/my-assets/out.kra \
  /Applications/krita.app/Contents/MacOS/kritarunner -s kra_build
```

Raw tiles are 8-bit BGRA bytes (`w*h*4`). From PIL/numpy:
`np.array(im.convert("RGBA"))[:, :, [2, 1, 0, 3]].tobytes()`.

## How it works

- **AI Image docker** (`grok_image.py`): a `DockWidget` with a provider combo, prompt box, count spinner, and a selection-aware region readout. Each provider has a `QThread` worker that POSTs JSON with stdlib `urllib` (Grok: Bearer-auth `images/generations` with `b64_json`; Gemini: `generateContent` with inline PNG data for img2img; Imagen: `predict` with `sampleCount`). Returned bytes are decoded to `QImage`, converted to RGBA8888, and written into a new paint layer with `setPixelData` — scaled to the selection rectangle when one was used as input. If no document is open, one is created at the image's size.
- **Uploader bridge** (`docker.py`): an `Extension` runs a 500 ms `QTimer` loop that processes command files and (every 5 s) exports the open-project list. All file writes are atomic (`.tmp` then `replace`). Inserted images get an RGBA→BGRA channel swizzle before `setPixelData`, matching Krita's native byte order. A docker gives manual "Export Canvas Now" / "Insert Queued Images" buttons and live status.
- **`kra_build.py`**: runs inside `kritarunner` (Krita's headless Python host). Reads a manifest, creates the document, adds a background layer, then per object: a group containing the base shape layer and an empty `color - Inherit Alpha` paint layer above it with `setInheritAlpha(True)` — Krita's built-in clipping mask, so later painting can never spill off the shape. Full workflow docs in [`docs/agent-skill.md`](docs/agent-skill.md) and [`docs/inherit-alpha-howto.md`](docs/inherit-alpha-howto.md).

## Screenshots

No screenshots are bundled yet. After installing, look for the **AI Image** and **Krita Image Uploader** dockers on the right-hand side of Krita (both register at `DockRight`).

## Publish

```bash
gh repo create blu-inman/krita-agent --public --source . --push
```

## License

[MIT](LICENSE) — Copyright (c) 2026 Blu Inman
