"""
AI Image — Krita docker that funnels multiple image providers into the canvas.

Providers:
  - Nano Banana — Gemini 2.5 Flash Image, text-to-image AND image-to-image
  - Grok        — xAI grok-imagine-image, text-to-image
  - Imagen 4    — Google Imagen 4, text-to-image, higher quality
  - Local       — defers to the existing krita-ai-diffusion docker

Keys are read at generate-time (in order):
  1. ~/.config/grok-image/config.json
     {"XAI_API_KEY": "...", "GOOGLE_API_KEY": "..."}
  2. environment XAI_API_KEY / GOOGLE_API_KEY

No key is ever stored in this file. Request building / response parsing lives
in ``providers.py`` (plain stdlib, unit-tested outside Krita).
"""

from krita import DockWidget, Krita
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import providers


def qimage_to_png_bytes(qimage):
    buffer = QBuffer()
    buffer.open(QIODevice.ReadWrite)
    qimage.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def qimage_rgba_bytes(qimage):
    """Tightly packed RGBA8888 bytes of a QImage (handles any bytesPerLine)."""
    if qimage.format() != QImage.Format_RGBA8888:
        qimage = qimage.convertToFormat(QImage.Format_RGBA8888)
    width, height = qimage.width(), qimage.height()
    ptr = qimage.constBits()
    ptr.setsize(qimage.bytesPerLine() * height)
    raw = bytes(ptr)
    stride = qimage.bytesPerLine()
    row = width * 4
    if stride == row:
        return raw
    return b"".join(raw[y * stride:y * stride + row] for y in range(height))


def active_selection_bounds():
    """Return (x, y, w, h) of the active selection, the full document when there
    is no selection, or None when no document is open."""
    document = Krita.instance().activeDocument()
    if document is None:
        return None
    selection = document.selection()
    if selection is not None and selection.width() > 0 and selection.height() > 0:
        return (selection.x(), selection.y(), selection.width(), selection.height())
    return (0, 0, document.width(), document.height())


def active_canvas_region_as_png(bounds):
    """Render only the given region of the active document as PNG bytes.

    Uses Document.pixelData (merged projection, native byte order — BGRA for an
    RGBA/U8 document) and falls back to Document.thumbnail + crop for other
    colour depths/models."""
    document = Krita.instance().activeDocument()
    if document is None:
        return None
    document.refreshProjection()

    x, y, w, h = bounds
    is_rgba8 = document.colorModel() == "RGBA" and document.colorDepth() == "U8"

    if is_rgba8:
        try:
            raw = bytes(document.pixelData(x, y, w, h))
        except Exception:  # noqa: BLE001 - fall through to the thumbnail path
            raw = b""
        if len(raw) == w * h * 4:
            rgba = providers.swap_red_blue(raw)
            qimage = QImage(rgba, w, h, w * 4, QImage.Format_RGBA8888).copy()
            if not qimage.isNull():
                return qimage_to_png_bytes(qimage)

    full_qimage = document.thumbnail(document.width(), document.height())
    if full_qimage is None or full_qimage.isNull():
        return None
    cropped = full_qimage.copy(x, y, w, h)
    if cropped.isNull():
        return None
    return qimage_to_png_bytes(cropped)


# --- Worker -------------------------------------------------------------------

class ProviderWorker(QThread):
    """Runs providers.generate() off the UI thread; emits (images, error)."""

    finished_with_result = pyqtSignal(object, object)

    def __init__(self, provider_key, api_key, prompt, count=1, input_png=None):
        super().__init__()
        self.provider_key = provider_key
        self.api_key = api_key
        self.prompt = prompt
        self.count = count
        self.input_png = input_png

    def run(self):
        try:
            images = providers.generate(
                self.provider_key, self.api_key, self.prompt, self.count, self.input_png
            )
            self.finished_with_result.emit(images, None)
        except Exception as error:  # noqa: BLE001 - surface every failure in the docker
            self.finished_with_result.emit(None, str(error) or error.__class__.__name__)


# --- Docker -------------------------------------------------------------------

class GrokImageDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Image")

        root = QWidget()
        layout = QVBoxLayout(root)

        self.provider_picker = QComboBox()
        self.provider_picker.addItems([p.label for p in providers.PROVIDERS])
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
        # Captured when a job starts so switching provider/selection mid-run
        # can't change where or under what name the result lands.
        self._job_bounds = None
        self._job_layer_prefix = "AI"
        self.refresh_controls()

    def current_provider(self):
        return providers.PROVIDERS[max(0, self.provider_picker.currentIndex())]

    def canvasChanged(self, canvas):
        self.refresh_region_label()

    def refresh_region_label(self):
        bounds = active_selection_bounds()
        document = Krita.instance().activeDocument()
        size = (document.width(), document.height()) if document is not None else (0, 0)
        self.region_label.setText(providers.fit_region_label(bounds, size))

    def refresh_controls(self):
        provider = self.current_provider()

        self.use_canvas_checkbox.setEnabled(provider.img2img)
        if not provider.img2img:
            self.use_canvas_checkbox.setChecked(False)

        self.count_spinner.setEnabled(provider.max_count > 0)
        if provider.max_count:
            self.count_spinner.setMaximum(provider.max_count)
        self.generate_button.setText(
            "Open krita-ai-diffusion docker" if provider.is_local else "Generate"
        )
        self.refresh_region_label()

    def handle_generate(self):
        provider = self.current_provider()

        if provider.is_local:
            self.status_label.setText(
                "Open Settings → Dockers → AI Image Generation. "
                "krita-ai-diffusion handles local SD with full inpaint/img2img/Live mode."
            )
            return

        if self.worker_thread is not None and self.worker_thread.isRunning():
            self.status_label.setText("Still generating — please wait.")
            return

        prompt = self.prompt_field.toPlainText().strip()
        if not prompt:
            self.status_label.setText("Empty prompt.")
            return

        api_key = providers.load_keys().get(provider.api_key_name, "")
        if not api_key:
            QMessageBox.warning(self, "AI Image", providers.missing_key_message(provider.api_key_name))
            return

        input_png = None
        target_bounds = None
        if provider.img2img and self.use_canvas_checkbox.isChecked():
            target_bounds = active_selection_bounds()
            if target_bounds is None:
                self.status_label.setText("No active document to use as input.")
                return
            input_png = active_canvas_region_as_png(target_bounds)
            if input_png is None:
                self.status_label.setText("Could not read pixels from canvas/selection.")
                return

        self._job_bounds = target_bounds
        self._job_layer_prefix = provider.layer_prefix
        count = self.count_spinner.value() if provider.max_count else 1
        self.start_worker(ProviderWorker(provider.key, api_key, prompt, count, input_png))

    def start_worker(self, worker):
        self.generate_button.setEnabled(False)
        self.status_label.setText("Generating…")
        self.worker_thread = worker
        worker.finished_with_result.connect(self.handle_result)
        worker.start()

    def handle_result(self, image_bytes_list, error):
        self.generate_button.setEnabled(True)

        if error:
            self.status_label.setText("Error: " + error)
            return
        if not image_bytes_list:
            self.status_label.setText("No images returned.")
            return

        document = Krita.instance().activeDocument()
        if document is None:
            document = self.create_document_for(image_bytes_list[0])
            if document is None:
                self.status_label.setText("Could not decode the returned image.")
                return

        added = 0
        for index, image_bytes in enumerate(image_bytes_list, start=1):
            suffix = f" {index}" if len(image_bytes_list) > 1 else ""
            if self.add_image_layer(document, image_bytes, self._job_layer_prefix + " image" + suffix):
                added += 1
        document.refreshProjection()

        skipped = len(image_bytes_list) - added
        message = f"Added {added} layer(s)."
        if skipped:
            message += f" {skipped} image(s) could not be decoded."
        self.status_label.setText(message)

    def create_document_for(self, image_bytes):
        qimage = QImage.fromData(QByteArray(image_bytes))
        if qimage.isNull():
            return None
        document = Krita.instance().createDocument(
            qimage.width(), qimage.height(), "AI Image", "RGBA", "U8", "", 300.0,
        )
        Krita.instance().activeWindow().addView(document)
        # A brand-new document has no selection to fit into.
        self._job_bounds = None
        return document

    def add_image_layer(self, document, image_bytes, layer_name):
        qimage = QImage.fromData(QByteArray(image_bytes))
        if qimage.isNull():
            return False

        if self._job_bounds is not None:
            x, y, w, h = self._job_bounds
            if qimage.width() != w or qimage.height() != h:
                qimage = qimage.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        else:
            x, y = 0, 0
            w, h = qimage.width(), qimage.height()

        layer = document.createNode(layer_name, "paintLayer")
        document.rootNode().addChildNode(layer, None)
        # Krita's RGBA/U8 pixels are BGRA in memory — swap or red/blue invert.
        bgra = providers.swap_red_blue(qimage_rgba_bytes(qimage))
        layer.setPixelData(QByteArray(bgra), x, y, w, h)
        return True
