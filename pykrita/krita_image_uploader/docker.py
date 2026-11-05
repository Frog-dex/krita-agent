"""Krita Image Uploader — a file-based bridge so an external agent can drive Krita.

Krita polls ``~/.krita-image-uploader/commands/`` every 500 ms. The on-disk
protocol (queue, done/, .error.json, atomic writes) lives in ``bridge_core.py``;
this module only supplies the Krita-side action handlers and the docker UI.
"""

import os
import time
from pathlib import Path

from krita import DockWidget, Extension, Krita
from PyQt5.QtCore import QByteArray, Qt, QTimer
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from . import bridge_core as core

PATHS = core.BridgePaths()


def clear_preview_locks():
    """macOS Finder/quick-look can leave immutable flags that wedge the bridge."""
    for path in (PATHS.krita, PATHS.latest_image, PATHS.latest_info):
        if not path.exists():
            continue
        try:
            if hasattr(os, "chflags"):
                os.chflags(path, 0)
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            pass


def now_text():
    return core.timestamp()[-8:]


class BridgeSync:
    def __init__(self):
        PATHS.ensure()
        self.last_status = "Idle"
        self._last_project_export = 0.0
        self.handlers = {
            "insert_image": self.handle_insert_image,
            "export_canvas": self.handle_export_canvas,
            "open_project": self.handle_open_project,
            "ping": self.handle_ping,
        }

    # --- timers -------------------------------------------------------------

    def tick_commands(self):
        self.export_open_projects_if_needed()
        self.process_commands()

    def tick_canvas(self):
        if not core.read_state(PATHS, "live_canvas_preview", False):
            return
        try:
            self.export_latest_canvas()
        except Exception as exc:  # noqa: BLE001 - never let a timer raise into Krita
            self.last_status = "Live export error: " + str(exc)

    def process_commands(self):
        for name, status, message in core.process_commands(PATHS, self.handlers):
            if status == "failed":
                self.last_status = f"Command error ({name}): {message}"
            else:
                self.last_status = f"{message} at {now_text()}"

    # --- handlers -----------------------------------------------------------

    def handle_insert_image(self, command):
        path = Path(command.get("path", "")).expanduser()
        name = command.get("name") or "AI result"
        self.insert_image(path, name, core.clamp_scale(command.get("scale", 1.0)))
        return "Inserted image"

    def handle_export_canvas(self, command):
        info = self.export_latest_canvas(command.get("max_side", 1024))
        self.export_open_projects()
        return "Captured canvas: " + info["preview_path"]

    def handle_open_project(self, command):
        self.open_project(Path(command.get("path", "")).expanduser())
        self.export_open_projects()
        try:
            self.export_latest_canvas(command.get("max_side", 1024))
        except RuntimeError:
            pass  # the project opened; a snapshot is a bonus
        return "Opened project"

    def handle_ping(self, command):
        document = Krita.instance().activeDocument()
        title = (document.fileName() or "Untitled") if document is not None else "none"
        return f"pong (protocol {core.PROTOCOL_VERSION}, Krita {Krita.instance().version()}, active: {title})"

    # --- project list -------------------------------------------------------

    def export_open_projects_if_needed(self):
        now = time.monotonic()
        if now - self._last_project_export < 5.0:
            return
        self._last_project_export = now
        try:
            self.export_open_projects()
        except Exception as exc:  # noqa: BLE001
            self.last_status = "Project list error: " + str(exc)

    def export_open_projects(self):
        app = Krita.instance()
        active = app.activeDocument()
        projects = []
        for document in app.documents():
            filename = document.fileName() or ""
            projects.append({
                "document": filename,
                "title": Path(filename).name if filename else "Untitled",
                "width": int(document.width()),
                "height": int(document.height()),
                "active": document == active,
            })
        core.atomic_write_json(PATHS.projects, {"updated_at": core.timestamp(), "projects": projects})

    # --- canvas export ------------------------------------------------------

    def export_latest_canvas(self, max_side=1024):
        """Save a PNG snapshot of the active canvas; raises RuntimeError on failure."""
        document = Krita.instance().activeDocument()
        if document is None:
            raise RuntimeError("No active document")

        document.refreshProjection()
        width, height = int(document.width()), int(document.height())
        thumb_width, thumb_height = core.fit_within(width, height, max_side)

        image = None
        if document.colorModel() == "RGBA" and document.colorDepth() == "U8":
            raw = bytes(document.pixelData(0, 0, width, height))
            if len(raw) == width * height * 4:
                # Native BGRA bytes == QImage ARGB32 on little-endian machines.
                image = QImage(raw, width, height, width * 4, QImage.Format_ARGB32).copy()
        if image is None or image.isNull():
            image = document.thumbnail(width, height)
        if image is None or image.isNull():
            raise RuntimeError("Canvas pixel read failed")
        if (thumb_width, thumb_height) != (width, height):
            image = image.scaled(thumb_width, thumb_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        snapshot = PATHS.snapshots / core.snapshot_name()
        tmp = snapshot.with_name("." + snapshot.name + ".part")
        if not image.save(str(tmp), "PNG"):
            raise RuntimeError("Could not write snapshot PNG")
        tmp.replace(snapshot)
        core.atomic_write_bytes(PATHS.latest_image, snapshot.read_bytes())
        core.prune_snapshots(PATHS)

        info = {
            "document": document.fileName() or "Untitled",
            "preview_path": str(snapshot),
            "latest_path": str(PATHS.latest_image),
            "width": width,
            "height": height,
            "preview_width": image.width(),
            "preview_height": image.height(),
            "updated_at": core.timestamp(),
        }
        core.atomic_write_json(PATHS.latest_info, info)
        self.last_status = "Exported canvas at " + now_text()
        return info

    # --- insert / open ------------------------------------------------------

    def insert_image(self, image_path, layer_name, scale=1.0):
        if not image_path.is_file():
            raise FileNotFoundError(str(image_path))

        image = QImage(str(image_path))
        if image.isNull():
            raise RuntimeError("Could not decode image: " + str(image_path))
        if scale < 1.0:
            image = image.scaled(
                max(1, int(image.width() * scale)),
                max(1, int(image.height() * scale)),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        image = image.convertToFormat(QImage.Format_RGBA8888)
        width, height = image.width(), image.height()

        app = Krita.instance()
        document = app.activeDocument()
        if document is None:
            document = app.createDocument(width, height, "AI Result", "RGBA", "U8", "", 300.0)
            window = app.activeWindow()
            if window is not None:
                window.addView(document)

        layer = document.createNode(layer_name, "paintLayer")
        document.rootNode().addChildNode(layer, None)

        ptr = image.constBits()
        ptr.setsize(image.bytesPerLine() * height)
        raw = bytes(ptr)
        stride, row = image.bytesPerLine(), width * 4
        if stride != row:
            raw = b"".join(raw[y * stride:y * stride + row] for y in range(height))
        # Krita's native byte order is BGRA; skipping this swaps red and blue.
        layer.setPixelData(QByteArray(core.rgba_to_bgra(raw)), 0, 0, width, height)
        document.refreshProjection()

    def open_project(self, project_path):
        if not project_path.exists():
            raise FileNotFoundError(str(project_path))

        requested = project_path.resolve()
        app = Krita.instance()
        document = None
        for open_document in app.documents():
            filename = open_document.fileName() or ""
            if filename and Path(filename).expanduser().resolve() == requested:
                document = open_document
                break

        if document is None:
            document = app.openDocument(str(project_path))
        if document is None:
            raise RuntimeError("Could not open project: " + str(project_path))

        try:
            self.activate_document(document)
        except Exception as exc:  # noqa: BLE001 - view APIs vary across Krita versions
            self.last_status = "Opened, but could not focus the view: " + str(exc)
        document.refreshProjection()

    def activate_document(self, document):
        app = Krita.instance()
        window = app.activeWindow()
        if window is not None:
            window.activate()
            view = next((v for v in window.views() if v.document() == document), None)
            if view is not None:
                window.showView(view)
            else:
                window.addView(document)
        app.setActiveDocument(document)


SYNC = BridgeSync()


class KritaImageUploaderExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.command_timer = QTimer()
        self.command_timer.timeout.connect(SYNC.tick_commands)
        # Live canvas streaming: a no-op unless state.json has
        # {"live_canvas_preview": true}.
        self.canvas_timer = QTimer()
        self.canvas_timer.timeout.connect(SYNC.tick_canvas)

    def setup(self):
        PATHS.ensure()
        clear_preview_locks()
        self.command_timer.start(500)
        self.canvas_timer.start(2000)
        SYNC.last_status = "Bridge ready — watching " + str(PATHS.commands)

    def createActions(self, window):
        pass


class KritaImageUploaderDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Krita Image Uploader")
        clear_preview_locks()

        root = QWidget()
        layout = QVBoxLayout(root)
        self.status_label = QLabel(SYNC.last_status)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        export_button = QPushButton("Export Canvas Now")
        export_button.clicked.connect(self.export_now)
        layout.addWidget(export_button)

        insert_button = QPushButton("Process Queued Commands")
        insert_button.clicked.connect(self.insert_now)
        layout.addWidget(insert_button)

        layout.addStretch()
        self.setWidget(root)

        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self.refresh_status)
        self.ui_timer.start(2000)

    def canvasChanged(self, canvas):
        self.refresh_status()

    def export_now(self):
        try:
            SYNC.export_latest_canvas()
        except Exception as exc:  # noqa: BLE001
            SYNC.last_status = "Export error: " + str(exc)
        self.refresh_status()

    def insert_now(self):
        SYNC.process_commands()
        self.refresh_status()

    def refresh_status(self):
        self.status_label.setText(SYNC.last_status)
