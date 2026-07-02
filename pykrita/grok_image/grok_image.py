"""
AI Image — Krita docker that funnels multiple image providers into the canvas.

Providers:
  - Grok        — xAI grok-2-image, text-to-image only
  - Nano Banana — Gemini 2.5 Flash Image, text-to-image AND image-to-image
  - Imagen 4    — Google Imagen 4, text-to-image, higher quality
  - Local       — defers to the existing krita-ai-diffusion docker

Keys are read (in order):
  1. ~/.config/grok-image/config.json
     {"XAI_API_KEY": "...", "GOOGLE_API_KEY": "..."}
  2. environment XAI_API_KEY / GOOGLE_API_KEY

No key is ever stored in this file.
"""

import base64
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

from krita import DockWidget, Krita
from PyQt5.QtCore import Qt, QByteArray, QThread, pyqtSignal, QBuffer, QIODevice
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton,
    QLabel, QComboBox, QSpinBox, QCheckBox, QMessageBox
)


GROK_URL = "https://api.x.ai/v1/images/generations"
GROK_MODEL = "grok-imagine-image"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
NANO_BANANA_MODEL = "gemini-2.5-flash-image"
IMAGEN_MODEL = "imagen-4.0-generate-001"


def load_keys():
    keys = {"XAI_API_KEY": "", "GOOGLE_API_KEY": ""}

    cfg = Path.home() / ".config" / "grok-image" / "config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            for name in keys:
                if data.get(name):
                    keys[name] = str(data[name]).strip()
        except Exception:
            pass

    for name in keys:
        if not keys[name]:
            env_value = os.environ.get(name, "").strip()
            if env_value:
                keys[name] = env_value

    return keys


def qimage_to_png_bytes(qimage):
    buffer = QBuffer()
    buffer.open(QIODevice.ReadWrite)
    qimage.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def active_selection_bounds():
    """Return (x, y, w, h) of the active selection, or None if no selection.
    Falls back to the full document size."""
    document = Krita.instance().activeDocument()
    if document is None:
        return None
    selection = document.selection()
    if selection is not None and selection.width() > 0 and selection.height() > 0:
        return (selection.x(), selection.y(), selection.width(), selection.height())
    return (0, 0, document.width(), document.height())


def active_canvas_region_as_png(bounds):
    """Render only the given region of the active document as PNG bytes.

    Tries Document.pixelData first (raw bytes of the merged projection in the
    document's native format). Falls back to Document.thumbnail + crop if that
    fails or returns an unexpected layout (non-RGBA U8 documents)."""
    document = Krita.instance().activeDocument()
    if document is None:
        return None
    document.refreshProjection()

    x, y, w, h = bounds

    try:
        pixel_bytes = document.pixelData(x, y, w, h)
    except Exception:
        pixel_bytes = None

    if pixel_bytes:
        raw = bytes(pixel_bytes)
        if len(raw) == w * h * 4:
            qimage = QImage(raw, w, h, QImage.Format_RGBA8888).copy()
            if not qimage.isNull():
                return qimage_to_png_bytes(qimage)

    # Fallback: thumbnail then crop
    full_qimage = document.thumbnail(document.width(), document.height())
    if full_qimage is None or full_qimage.isNull():
        return None
    cropped = full_qimage.copy(x, y, w, h)
    if cropped.isNull():
        return None
    return qimage_to_png_bytes(cropped)


# --- Provider workers ---------------------------------------------------------

class ProviderWorker(QThread):
    finished_with_result = pyqtSignal(object, object)

    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    def post_json(self, url, headers, body):
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            method="POST", headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))


class GrokWorker(ProviderWorker):
    def run(self):
        try:
            data = self.post_json(
                GROK_URL,
                {"Authorization": "Bearer " + self.payload["api_key"]},
                {
                    "model": GROK_MODEL,
                    "prompt": self.payload["prompt"],
                    "n": self.payload["count"],
                    "response_format": "b64_json",
                },
            )
            images = []
            for item in data.get("data", []):
                if "b64_json" in item:
                    images.append(base64.b64decode(item["b64_json"]))
                elif "url" in item:
                    with urllib.request.urlopen(item["url"], timeout=60) as inner:
                        images.append(inner.read())
            self.finished_with_result.emit(images, None)
        except Exception as error:
            self.finished_with_result.emit(None, str(error))


class NanoBananaWorker(ProviderWorker):
    def run(self):
        try:
            parts = [{"text": self.payload["prompt"]}]
            input_image = self.payload.get("input_image_bytes")
            if input_image:
                parts.append({
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(input_image).decode("ascii"),
                    }
                })

            url = "%s/%s:generateContent?key=%s" % (
                GEMINI_BASE, NANO_BANANA_MODEL, self.payload["api_key"]
            )
            data = self.post_json(url, {}, {
                "contents": [{"parts": parts}],
            })

            images = []
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    inline = part.get("inline_data") or part.get("inlineData")
                    if inline and "data" in inline:
                        images.append(base64.b64decode(inline["data"]))
            self.finished_with_result.emit(images, None)
        except Exception as error:
            self.finished_with_result.emit(None, str(error))


class ImagenWorker(ProviderWorker):
    def run(self):
        try:
            url = "%s/%s:predict?key=%s" % (
                GEMINI_BASE, IMAGEN_MODEL, self.payload["api_key"]
            )
            data = self.post_json(url, {}, {
                "instances": [{"prompt": self.payload["prompt"]}],
                "parameters": {"sampleCount": self.payload["count"]},
            })

            images = []
            for prediction in data.get("predictions", []):
                b64 = prediction.get("bytesBase64Encoded") or prediction.get("imageBytes")
                if b64:
                    images.append(base64.b64decode(b64))
            self.finished_with_result.emit(images, None)
        except Exception as error:
            self.finished_with_result.emit(None, str(error))


# --- Docker -------------------------------------------------------------------

PROVIDERS = [
    "Nano Banana (img2img)",
    "Grok 2 Image",
    "Imagen 4",
    "Local — krita-ai-diffusion",
]


class GrokImageDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Image")

        root = QWidget()
        layout = QVBoxLayout(root)

        self.provider_picker = QComboBox()
        self.provider_picker.addItems(PROVIDERS)
        self.provider_picker.currentIndexChanged.connect(self.refresh_controls)
        layout.addWidget(QLabel("Provider:"))
        layout.addWidget(self.provider_picker)

        self.prompt_field = QPlainTextEdit()
        self.prompt_field.setPlaceholderText("Describe the image…")
        self.prompt_field.setMinimumHeight(80)
        layout.addWidget(self.prompt_field)

        self.use_canvas_checkbox = QCheckBox("Use canvas / selection as input image")
        layout.addWidget(self.use_canvas_checkbox)

        self.region_label = QLabel("")
        self.region_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.region_label)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Count:"))
        self.count_spinner = QSpinBox()
        self.count_spinner.setRange(1, 4)
        self.count_spinner.setValue(1)
        controls.addWidget(self.count_spinner)
        controls.addStretch()
        layout.addLayout(controls)

        self.generate_button = QPushButton("Generate")
        self.generate_button.clicked.connect(self.handle_generate)
        layout.addWidget(self.generate_button)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch()
        self.setWidget(root)

        self.worker_thread = None
        self.refresh_controls()

    def canvasChanged(self, canvas):
        self.refresh_region_label()

    def refresh_region_label(self):
        bounds = active_selection_bounds()
        if bounds is None:
            self.region_label.setText("No active document.")
            return
        document = Krita.instance().activeDocument()
        x, y, w, h = bounds
        full = (w == document.width() and h == document.height() and x == 0 and y == 0)
        if full:
            self.region_label.setText("No selection — using full canvas (%d×%d)." % (w, h))
        else:
            self.region_label.setText("Selection: %d×%d at (%d, %d)." % (w, h, x, y))

    def refresh_controls(self):
        provider = self.provider_picker.currentText()
        is_img2img_capable = provider.startswith("Nano Banana")
        is_local = provider.startswith("Local")

        self.use_canvas_checkbox.setEnabled(is_img2img_capable)
        if not is_img2img_capable:
            self.use_canvas_checkbox.setChecked(False)

        self.count_spinner.setEnabled(not is_local and not provider.startswith("Nano"))
        if is_local:
            self.generate_button.setText("Open krita-ai-diffusion docker")
        else:
            self.generate_button.setText("Generate")
        self.refresh_region_label()

    def handle_generate(self):
        provider = self.provider_picker.currentText()

        if provider.startswith("Local"):
            self.status_label.setText(
                "Open Settings → Dockers → AI Image Generation. "
                "krita-ai-diffusion handles local SD with full inpaint/img2img/Live mode."
            )
            return

        prompt = self.prompt_field.toPlainText().strip()
        if not prompt:
            self.status_label.setText("Empty prompt.")
            return

        keys = load_keys()

        input_image_bytes = None
        target_bounds = None
        if self.use_canvas_checkbox.isChecked():
            target_bounds = active_selection_bounds()
            if target_bounds is None:
                self.status_label.setText("No active document to use as input.")
                return
            input_image_bytes = active_canvas_region_as_png(target_bounds)
            if input_image_bytes is None:
                self.status_label.setText("Could not read pixels from canvas/selection.")
                return
        self._target_bounds = target_bounds

        if provider.startswith("Grok"):
            api_key = keys.get("XAI_API_KEY", "")
            if not api_key:
                self.warn_missing_key("XAI_API_KEY")
                return
            self.start_worker(GrokWorker({
                "api_key": api_key,
                "prompt": prompt,
                "count": self.count_spinner.value(),
            }))
            return

        if provider.startswith("Nano Banana"):
            api_key = keys.get("GOOGLE_API_KEY", "")
            if not api_key:
                self.warn_missing_key("GOOGLE_API_KEY")
                return
            self.start_worker(NanoBananaWorker({
                "api_key": api_key,
                "prompt": prompt,
                "input_image_bytes": input_image_bytes,
            }))
            return

        if provider.startswith("Imagen"):
            api_key = keys.get("GOOGLE_API_KEY", "")
            if not api_key:
                self.warn_missing_key("GOOGLE_API_KEY")
                return
            self.start_worker(ImagenWorker({
                "api_key": api_key,
                "prompt": prompt,
                "count": self.count_spinner.value(),
            }))
            return

    def start_worker(self, worker):
        self.generate_button.setEnabled(False)
        self.status_label.setText("Generating…")
        self.worker_thread = worker
        worker.finished_with_result.connect(self.handle_result)
        worker.start()

    def warn_missing_key(self, key_name):
        QMessageBox.warning(
            self, "AI Image",
            "No %s found. Add it to:\n"
            "  ~/.config/grok-image/config.json\n"
            "  or the %s environment variable" % (key_name, key_name),
        )

    def handle_result(self, image_bytes_list, error):
        self.generate_button.setEnabled(True)

        if error:
            self.status_label.setText("Error: " + error)
            return
        if not image_bytes_list:
            self.status_label.setText("No images returned.")
            return

        active_document = Krita.instance().activeDocument()
        if active_document is None:
            active_document = self.create_document_from_first(image_bytes_list[0])
            image_bytes_list = image_bytes_list[1:]

        for image_bytes in image_bytes_list:
            self.add_image_layer(active_document, image_bytes)

        self.status_label.setText("Added %d layer(s)." % len(image_bytes_list))

    def create_document_from_first(self, image_bytes):
        qimage = QImage.fromData(QByteArray(image_bytes))
        document = Krita.instance().createDocument(
            qimage.width(), qimage.height(),
            "AI Image", "RGBA", "U8", "", 300.0,
        )
        Krita.instance().activeWindow().addView(document)
        self.add_image_layer(document, image_bytes)
        return document

    def add_image_layer(self, document, image_bytes):
        qimage = QImage.fromData(QByteArray(image_bytes))
        if qimage.isNull():
            self.status_label.setText("Decoded image was empty.")
            return
        if qimage.format() != QImage.Format_RGBA8888:
            qimage = qimage.convertToFormat(QImage.Format_RGBA8888)

        target_bounds = getattr(self, "_target_bounds", None)
        if target_bounds is not None:
            x, y, w, h = target_bounds
            if qimage.width() != w or qimage.height() != h:
                qimage = qimage.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        else:
            x, y = 0, 0
            w, h = qimage.width(), qimage.height()

        layer_name = self.provider_picker.currentText().split(" ")[0]
        layer = document.createNode(layer_name + " image", "paintLayer")
        document.rootNode().addChildNode(layer, None)

        ptr = qimage.bits()
        ptr.setsize(qimage.byteCount())
        layer.setPixelData(
            QByteArray(bytes(ptr)),
            x, y, w, h,
        )
        document.refreshProjection()
