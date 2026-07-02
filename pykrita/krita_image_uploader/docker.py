import datetime
import json
import os
import stat
import time
from pathlib import Path

from krita import DockWidget, Extension, Krita
from PyQt5.QtCore import QByteArray, QTimer, Qt
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


STATE_DIR = Path.home() / ".krita-image-uploader"
KRITA_DIR = STATE_DIR / "krita"
COMMAND_DIR = STATE_DIR / "commands"
DONE_DIR = COMMAND_DIR / "done"
LATEST_IMAGE = KRITA_DIR / "latest.png"
LATEST_INFO = KRITA_DIR / "latest.json"
OPEN_PROJECTS = KRITA_DIR / "projects.json"
SNAPSHOT_DIR = KRITA_DIR / "snapshots"
STATE_FILE = STATE_DIR / "state.json"


def ensure_dirs():
    for folder in (STATE_DIR, KRITA_DIR, SNAPSHOT_DIR, COMMAND_DIR, DONE_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def clear_preview_locks():
    for path in (KRITA_DIR, LATEST_IMAGE, LATEST_INFO, LATEST_IMAGE.with_suffix(".tmp.png")):
        try:
            if hasattr(os, "chflags") and path.exists():
                os.chflags(path, 0)
        except Exception:
            pass
        try:
            if path.is_dir():
                path.chmod(0o755)
            elif path.exists():
                path.chmod(0o644)
        except Exception:
            pass


def now_text():
    return datetime.datetime.now().strftime("%H:%M:%S")


def state_value(key, default=None):
    try:
        if not STATE_FILE.exists():
            return default
        return json.loads(STATE_FILE.read_text()).get(key, default)
    except Exception:
        return default


class BridgeSync:
    def __init__(self):
        ensure_dirs()
        self.last_status = "Idle"
        self._last_project_export = 0.0

    def tick_commands(self):
        self.export_open_projects_if_needed()
        self.process_commands()

    def tick_canvas(self):
        if not state_value("live_canvas_preview", False):
            return
        self.export_latest_canvas()

    def export_open_projects_if_needed(self):
        now = time.monotonic()
        if now - self._last_project_export < 5.0:
            return
        self._last_project_export = now
        self.export_open_projects()

    def export_open_projects(self):
        try:
            active = Krita.instance().activeDocument()
            projects = []
            for document in Krita.instance().documents():
                filename = document.fileName() or ""
                title = Path(filename).name if filename else "Untitled"
                projects.append({
                    "document": filename,
                    "title": title,
                    "width": int(document.width()),
                    "height": int(document.height()),
                    "active": document == active,
                })
            tmp = OPEN_PROJECTS.with_suffix(".tmp.json")
            tmp.write_text(json.dumps({
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "projects": projects,
            }, indent=2, sort_keys=True))
            tmp.replace(OPEN_PROJECTS)
        except Exception as exc:
            self.last_status = "Project list error: " + str(exc)

    def export_latest_canvas(self, max_side=1024):
        document = Krita.instance().activeDocument()
        if document is None:
            self.last_status = "No active document"
            return

        try:
            width = int(document.width())
            height = int(document.height())
            max_side = int(max_side)
            scale = min(1.0, float(max_side) / float(max(width, height)))
            thumb_width = max(1, int(width * scale))
            thumb_height = max(1, int(height * scale))
            raw = bytes(document.pixelData(0, 0, width, height))
            if len(raw) != width * height * 4:
                self.last_status = "Canvas pixel read failed"
                return
            image = QImage(raw, width, height, QImage.Format_ARGB32).copy()
            if scale < 1.0:
                image = image.scaled(
                    thumb_width,
                    thumb_height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            if image is None or image.isNull():
                self.last_status = "Canvas image failed"
                return

            snapshot = SNAPSHOT_DIR / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-canvas.png")
            tmp = snapshot.with_suffix(".tmp.png")
            image.save(str(tmp), "PNG")
            tmp.replace(snapshot)

            info = {
                "document": document.fileName() or "Untitled",
                "preview_path": str(snapshot),
                "width": width,
                "height": height,
                "preview_width": thumb_width,
                "preview_height": thumb_height,
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            tmp_info = LATEST_INFO.with_suffix(".tmp.json")
            tmp_info.write_text(json.dumps(info, indent=2, sort_keys=True))
            tmp_info.replace(LATEST_INFO)
            self.last_status = "Streamed canvas at " + now_text()
        except Exception as exc:
            self.last_status = "Export error: " + str(exc)

    def process_commands(self):
        ensure_dirs()
        for command_path in sorted(COMMAND_DIR.glob("*.json")):
            command = {}
            try:
                command = json.loads(command_path.read_text())
                action = command.get("action")
                if action == "insert_image":
                    scale = float(command.get("scale", 1.0) or 1.0)
                    self.insert_image(
                        Path(command.get("path", "")).expanduser(),
                        command.get("name") or "AI result",
                        scale,
                    )
                    self.finish_command(command_path, command, "succeeded", "Inserted image")
                    self.last_status = "Inserted image at " + now_text()
                elif action == "export_canvas":
                    self.export_latest_canvas(command.get("max_side", 1024))
                    self.export_open_projects()
                    self.finish_command(command_path, command, "succeeded", "Captured canvas")
                    self.last_status = "Captured canvas at " + now_text()
                elif action == "open_project":
                    self.open_project(Path(command.get("path", "")).expanduser())
                    self.export_latest_canvas(command.get("max_side", 1024))
                    self.export_open_projects()
                    self.finish_command(command_path, command, "succeeded", "Opened project")
                    self.last_status = "Opened project at " + now_text()
                else:
                    self.finish_command(command_path, command, "ignored", "Unknown action")
            except Exception as exc:
                command["status"] = "failed"
                command["error"] = str(exc)
                command["completed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                error_path = command_path.with_suffix(".error.json")
                error_path.write_text(json.dumps(command, indent=2, sort_keys=True))
                try:
                    command_path.unlink()
                except Exception:
                    pass
                self.last_status = "Command error: " + str(exc)

    def finish_command(self, command_path, command, status, message):
        command["status"] = status
        command["message"] = message
        command["completed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        done_path = DONE_DIR / command_path.name
        done_path.write_text(json.dumps(command, indent=2, sort_keys=True))
        command_path.unlink()

    def insert_image(self, image_path, layer_name, scale=1.0):
        if not image_path.exists():
            raise FileNotFoundError(str(image_path))

        image = QImage(str(image_path))
        if image.isNull():
            raise RuntimeError("Could not decode image: " + str(image_path))
        scale = max(0.05, min(float(scale), 1.0))
        if scale < 1.0:
            image = image.scaled(
                max(1, int(image.width() * scale)),
                max(1, int(image.height() * scale)),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        if image.format() != QImage.Format_RGBA8888:
            image = image.convertToFormat(QImage.Format_RGBA8888)

        document = Krita.instance().activeDocument()
        if document is None:
            document = Krita.instance().createDocument(
                image.width(),
                image.height(),
                "AI Result",
                "RGBA",
                "U8",
                "",
                300.0,
            )
            Krita.instance().activeWindow().addView(document)

        layer = document.createNode(layer_name, "paintLayer")
        document.rootNode().addChildNode(layer, None)

        ptr = image.bits()
        ptr.setsize(image.byteCount())
        rgba = bytes(ptr)
        bgra = bytearray(rgba)
        bgra[0::4] = rgba[2::4]
        bgra[2::4] = rgba[0::4]
        layer.setPixelData(QByteArray(bytes(bgra)), 0, 0, image.width(), image.height())
        document.refreshProjection()

    def open_project(self, project_path):
        if not project_path.exists():
            raise FileNotFoundError(str(project_path))

        requested = str(project_path.resolve())
        document = None
        for open_document in Krita.instance().documents():
            filename = open_document.fileName() or ""
            if not filename:
                continue
            try:
                if str(Path(filename).expanduser().resolve()) == requested:
                    document = open_document
                    break
            except Exception:
                if filename == str(project_path):
                    document = open_document
                    break

        if document is None:
            document = Krita.instance().openDocument(str(project_path))
        if document is None:
            raise RuntimeError("Could not open project: " + str(project_path))

        self.activate_document(document)
        document.refreshProjection()

    def activate_document(self, document):
        app = Krita.instance()
        window = app.activeWindow()
        if window is not None:
            try:
                window.activate()
            except Exception:
                pass

            visible = False
            try:
                for view in window.views():
                    if view.document() == document:
                        try:
                            view.setVisible()
                        except Exception:
                            pass
                        try:
                            window.showView(view)
                        except Exception:
                            pass
                        visible = True
                        break
            except Exception:
                pass

            if not visible:
                try:
                    window.addView(document)
                except Exception:
                    pass

        try:
            app.setActiveDocument(document)
        except Exception:
            pass


SYNC = BridgeSync()


class KritaImageUploaderExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.command_timer = QTimer()
        self.command_timer.timeout.connect(SYNC.tick_commands)

    def setup(self):
        ensure_dirs()
        clear_preview_locks()
        self.command_timer.start(500)
        SYNC.last_status = "Auto import ready. Live canvas export is off."

    def createActions(self, window):
        pass


class KritaImageUploaderDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Krita Image Uploader")
        clear_preview_locks()

        root = QWidget()
        layout = QVBoxLayout(root)
        self.status_label = QLabel("Background sync disabled. Use buttons here when needed.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        export_button = QPushButton("Export Canvas Now")
        export_button.clicked.connect(self.export_now)
        layout.addWidget(export_button)

        insert_button = QPushButton("Insert Queued Images")
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
        SYNC.export_latest_canvas()
        self.refresh_status()

    def insert_now(self):
        SYNC.process_commands()
        self.refresh_status()

    def refresh_status(self):
        self.status_label.setText(SYNC.last_status)
