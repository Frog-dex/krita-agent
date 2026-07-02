# PROMPT.md — Recreate `krita-agent` from scratch

> Feed this prompt to an AI coding agent to rebuild this project. It is self-contained: purpose, UI, behaviors, file structure, and the implementation tricks that make Krita's Python API cooperate.

---

Build **krita-agent**: a set of Krita Python plugins and headless tools that turn Krita into an AI-native canvas. The headline feature is an **"AI Image" docker that funnels multiple cloud image providers (xAI Grok, Gemini Nano Banana, Imagen 4) straight into Krita layers — with selection-aware image-to-image**: the artist lassoes a region, the plugin sends exactly those pixels to the model, and the generated result is scaled back into that exact rectangle as a new paint layer. No plugins-of-plugins, no pip installs, no blocking the UI.

## Constraints

- Target Krita 5.x scripting API (`from krita import ...`) with the bundled Python — **PyQt5 + stdlib only** (`urllib.request` for HTTP, `json`, `base64`, `pathlib`). No requests, no pip.
- Never store API keys in source. Load them at generate-time (so edits to the config take effect without restart) from, in order: `~/.config/grok-image/config.json` (`{"XAI_API_KEY": "...", "GOOGLE_API_KEY": "..."}`), then the same-named environment variables.
- All network calls run on `QThread` subclasses emitting a `pyqtSignal(object, object)` → `(images, error)`; the docker's Generate button disables while a worker runs.

## File structure

```
krita-agent/
├── pykrita/
│   ├── grok_image.desktop              # Krita plugin manifest (Type=Service, ServiceTypes=Krita/PythonPlugin, X-KDE-Library=grok_image)
│   ├── grok_image/
│   │   ├── __init__.py                 # registers DockWidgetFactory("grokImageDocker", DockRight, GrokImageDocker)
│   │   └── grok_image.py               # the AI Image docker (~450 lines)
│   ├── krita_image_uploader.desktop    # manifest (X-KDE-Library=krita_image_uploader)
│   └── krita_image_uploader/
│       ├── __init__.py                 # registers the Extension AND a DockWidgetFactory("kritaImageUploaderDocker", ...)
│       └── docker.py                   # file-based agent bridge (~370 lines)
├── tools/
│   └── kra_build.py                    # headless kritarunner .kra builder
├── docs/
│   ├── inherit-alpha-howto.md          # human/agent how-to for clipping-mask painting
│   └── agent-skill.md                  # condensed agent reference (layer ops, gotchas, kritarunner)
├── README.md  ·  LICENSE (MIT)  ·  .gitignore
```

## Component 1 — AI Image docker (`pykrita/grok_image/grok_image.py`)

**UI (QVBoxLayout in a DockWidget titled "AI Image"):** provider `QComboBox` — `["Nano Banana (img2img)", "Grok 2 Image", "Imagen 4", "Local — krita-ai-diffusion"]`; a `QPlainTextEdit` prompt (min height 80, placeholder "Describe the image…"); a `QCheckBox` "Use canvas / selection as input image"; a small gray region label; a Count `QSpinBox` (1–4); a Generate `QPushButton`; a word-wrapping status `QLabel`.

**Control logic (`refresh_controls`):** the input-image checkbox is only enabled for Nano Banana (the sole img2img provider); the count spinner is disabled for Local and Nano Banana; when Local is selected the button relabels to "Open krita-ai-diffusion docker" and pressing it just prints instructions to the status label (defer local Stable Diffusion to the existing krita-ai-diffusion plugin — do not reimplement it).

**Selection-aware region:** `active_selection_bounds()` returns `(x, y, w, h)` of `document.selection()` if it has positive size, else the full document, else `None` when no document. `canvasChanged` and provider switches update the region label ("Selection: WxH at (x, y)." vs "No selection — using full canvas (WxH).").

**Canvas capture trick:** to send pixels to the model, call `document.refreshProjection()` then try `document.pixelData(x, y, w, h)`; if the returned length equals `w*h*4`, wrap it in `QImage(raw, w, h, QImage.Format_RGBA8888).copy()` (the `.copy()` detaches from the buffer — required). **Fallback** for non-RGBA/U8 documents: `document.thumbnail(full_w, full_h)` then `.copy(x, y, w, h)` to crop. Encode to PNG via `QBuffer` + `qimage.save(buffer, "PNG")`.

**Providers (each a ProviderWorker subclass with a shared `post_json(url, headers, body)` using `urllib.request`, 180 s timeout):**
- **Grok**: POST `https://api.x.ai/v1/images/generations`, header `Authorization: Bearer <XAI_API_KEY>`, body `{model: "grok-imagine-image", prompt, n: count, response_format: "b64_json"}`. Read `data[].b64_json` (base64-decode) or fetch `data[].url`.
- **Nano Banana**: POST `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key=<GOOGLE_API_KEY>` with `{"contents":[{"parts": parts}]}` where `parts` is `[{"text": prompt}]` plus, for img2img, `{"inline_data": {"mime_type": "image/png", "data": <base64 PNG>}}`. Collect images from `candidates[].content.parts[].inline_data.data` — **check both `inline_data` and `inlineData` spellings**.
- **Imagen 4**: POST `.../imagen-4.0-generate-001:predict?key=...` with `{"instances":[{"prompt": prompt}], "parameters":{"sampleCount": count}}`; read `predictions[].bytesBase64Encoded` (fall back to `imageBytes`).

**Result handling:** if a key is missing, show a `QMessageBox.warning` telling the user exactly where to put it. On success: if no document is open, create one sized to the first image (`createDocument(w, h, "AI Image", "RGBA", "U8", "", 300.0)` + `activeWindow().addView(doc)`). For each image: decode with `QImage.fromData(QByteArray(bytes))`, convert to `Format_RGBA8888`; if the request used a selection, `scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)` so the result drops precisely into the selection rect; create a paint layer named after the provider, `rootNode().addChildNode(layer, None)`, then `layer.setPixelData(QByteArray(bytes(ptr)), x, y, w, h)` where `ptr = qimage.bits(); ptr.setsize(qimage.byteCount())`. Finish with `document.refreshProjection()` and report "Added N layer(s)."

## Component 2 — Krita Image Uploader bridge (`pykrita/krita_image_uploader/docker.py`)

Purpose: let an **external agent** command a live Krita through the filesystem — no sockets, no server. State root `~/.krita-image-uploader/` with `commands/`, `commands/done/`, `krita/` (holding `latest.png`, `latest.json`, `projects.json`, `snapshots/`), and `state.json` for flags.

- A `BridgeSync` singleton + an `Extension` whose `setup()` starts a 500 ms `QTimer` calling `tick_commands()`.
- **Command loop:** iterate `sorted(COMMAND_DIR.glob("*.json"))`; parse; dispatch on `action`:
  - `insert_image {path, name, scale}` — load QImage, clamp scale to [0.05, 1.0] and downscale with KeepAspectRatio, convert to RGBA8888, then **swizzle to BGRA before `setPixelData`** (`bgra[0::4]=rgba[2::4]; bgra[2::4]=rgba[0::4]`) — Krita's native byte order is BGRA; skipping this swaps red/blue. Create the document if none is open.
  - `export_canvas {max_side}` — read the full canvas via `pixelData`, wrap as `QImage(..., Format_ARGB32).copy()`, downscale to `max_side`, save a timestamped PNG into `snapshots/`, and write `latest.json` metadata (document, path, dimensions, updated_at).
  - `open_project {path}` — if a document with the same resolved path is already open, activate its view (`window.showView(view)` / `addView`, then `app.setActiveDocument`); else `openDocument`.
- **Completion protocol:** every processed command gets `status`/`message`/`completed_at` stamped and is moved to `done/` (same filename); failures write `<name>.error.json` next to the queue and remove the original. The agent polls `done/` for results.
- **Atomic writes everywhere:** write `.tmp` siblings then `Path.replace()` — the agent never reads a half-written JSON/PNG.
- **Project inventory:** every 5 s (rate-limited with `time.monotonic()`), dump all open documents (`filename, title, width, height, active`) to `projects.json`.
- Optional live canvas streaming gated on `state_value("live_canvas_preview", False)` from `state.json` (off by default).
- On startup, `clear_preview_locks()` clears `chflags`/permissions on the preview files (macOS Finder locks can wedge the bridge).
- A small docker shows `last_status` (refreshed every 2 s) plus two buttons: "Export Canvas Now" and "Insert Queued Images".

## Component 3 — headless `.kra` builder (`tools/kra_build.py`)

Do **not** hand-write `.kra` (custom binary tile format). Use `kritarunner`, Krita's headless script host:

- Config via env (kritarunner inherits it): `KRA_BASE` (dir with `manifest.json` + raw `.bgra` pixel files, default `~/kra-build`), `KRA_OUT` (default `<KRA_BASE>/output.kra`).
- Manifest schema: `{"name": ..., "canvas": [W,H], "resolution": 300, "bg": {"file","w","h"}, "cards": [{"name","file","x","y","w","h"}, ...]}`.
- Build: `app.setBatchmode(True)`; create doc; add a `BACKGROUND` paint layer from raw bytes; then per card a **group layer** containing the base layer (`setPixelData` at its x/y) and, above it, an empty paint layer named `color - Inherit Alpha` with `setInheritAlpha(True)` — Krita's built-in clipping mask, so any later painting on it clips to the base silhouette. `doc.refreshProjection(); doc.saveAs(OUT); doc.close()`.
- **kritarunner gotchas (bake these into the docs):** `-s` imports a module *by name* from `~/Library/Application Support/kritarunner/krita/pykrita/` only — `PYTHONPATH` and absolute paths are ignored, so the script must be copied there; the entry point must be a function literally named `__main__(*args)`; `print()` output is swallowed — verify by checking the saved file; `setPixelData` expects **8-bit BGRA** (from PIL/numpy: `np.array(im.convert("RGBA"))[:, :, [2, 1, 0, 3]].tobytes()`) — precompute tiles in regular python3, keep the kritarunner script dependency-free; in the saved `maindoc.xml`, Inherit Alpha appears as `channelflags="1110"`.

## Component 4 — docs

Write two markdown docs: `docs/inherit-alpha-howto.md` — a step-by-step human/agent tutorial on the Inherit-Alpha pattern (`GROUP → [paint layer, Inherit Alpha ON] over [base shape]`), the Layers docker, blend modes (Normal covers / Color-Overlay tint / Multiply shadows / Add highlights), a worked glass-marble example, and the gotchas: *pure white paints gray when the brush is textured or Flow < 100%* (use a hard round at Flow 100, or `Edit ▸ Fill with Foreground Color` which is always 100%); *color spilling* means Inherit Alpha is off or the layer is outside the group; *unerasable lines* are baked into the base layer. And `docs/agent-skill.md` — the condensed agent reference of the same material plus the kritarunner section, ending with the golden rule: **an agent never edits the artist's live session/file — always render to new files for side-by-side sign-off.**

## Ship checklist

MIT LICENSE; `.gitignore` with `.DS_Store`, `__pycache__/`, `*.pyc`, `*.kra-autosave`, `_*.png`; README with install (copy `pykrita/*` into Krita's resource folder → enable both plugins in the Python Plugin Manager → restart), key setup, bridge command examples, and the kritarunner invocation. Verify by installing into a real Krita, generating with each provider, and round-tripping one bridge command from a shell.
