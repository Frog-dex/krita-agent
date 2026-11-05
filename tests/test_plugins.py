"""Run the real Krita plugins against tests/fake_krita.py (PyQt5, offscreen)."""

import json

import pytest

from conftest import requires_qt

pytestmark = requires_qt

RED_BGRA = bytes([0, 0, 255, 255])


def solid_png(path_or_none, w, h, rgb):
    from PyQt5.QtCore import QBuffer, QIODevice
    from PyQt5.QtGui import QColor, QImage

    image = QImage(w, h, QImage.Format_RGBA8888)
    image.fill(QColor(*rgb))
    if path_or_none:
        image.save(str(path_or_none), "PNG")
        return path_or_none
    buf = QBuffer()
    buf.open(QIODevice.ReadWrite)
    image.save(buf, "PNG")
    return bytes(buf.data())


def red_document(app, w=4, h=3):
    doc = app.createDocument(w, h, "t", "RGBA", "U8", "", 300.0)
    layer = doc.createNode("paint", "paintlayer")
    doc.rootNode().addChildNode(layer, None)
    layer.setPixelData(RED_BGRA * (w * h), 0, 0, w, h)
    doc.refreshProjection()
    app.window.addView(doc)
    return doc


@pytest.fixture
def uploader(krita_app):
    import krita_image_uploader
    from krita_image_uploader import docker

    for f in docker.PATHS.commands.glob("*"):
        if f.is_file():
            f.unlink()
    return docker, krita_image_uploader


def queue(docker, name, payload):
    (docker.PATHS.commands / name).write_text(json.dumps(payload))


def test_registers_extension_and_docker(uploader):
    docker, _ = uploader
    widget = docker.KritaImageUploaderDocker()
    assert widget.windowTitle() == "Krita Image Uploader"


def test_insert_image_writes_bgra_and_creates_document(uploader, krita_app, tmp_path):
    docker, _ = uploader
    png = solid_png(tmp_path / "red.png", 6, 2, (255, 0, 0))
    queue(docker, "insert-1.json", {"action": "insert_image", "path": str(png), "name": "Red"})
    docker.SYNC.process_commands()

    done = json.loads((docker.PATHS.done / "insert-1.json").read_text())
    assert done["status"] == "succeeded", done
    doc = krita_app.activeDocument()
    assert (doc.width(), doc.height()) == (6, 2)
    layer = doc.rootNode().childNodes()[-1]
    assert layer.name() == "Red"
    assert layer.pixels[4][:4] == RED_BGRA  # red stays red (BGRA in Krita)


def test_insert_missing_file_fails_once(uploader, tmp_path):
    docker, _ = uploader
    queue(docker, "insert-2.json", {"action": "insert_image", "path": str(tmp_path / "nope.png")})
    docker.SYNC.process_commands()
    docker.SYNC.process_commands()
    err = docker.PATHS.commands / "insert-2.error.json"
    assert json.loads(err.read_text())["status"] == "failed"
    assert not (docker.PATHS.commands / "insert-2.error.error.json").exists()


def test_export_canvas(uploader, krita_app):
    from PyQt5.QtGui import QColor, QImage

    docker, _ = uploader
    queue(docker, "export-0.json", {"action": "export_canvas"})
    docker.SYNC.process_commands()
    assert "No active document" in (docker.PATHS.commands / "export-0.error.json").read_text()

    red_document(krita_app, 40, 20)
    queue(docker, "export-1.json", {"action": "export_canvas", "max_side": 10})
    docker.SYNC.process_commands()
    assert json.loads((docker.PATHS.done / "export-1.json").read_text())["status"] == "succeeded"
    info = json.loads(docker.PATHS.latest_info.read_text())
    assert (info["width"], info["preview_width"], info["preview_height"]) == (40, 10, 5)
    image = QImage(str(docker.PATHS.latest_image))
    assert QColor(image.pixel(2, 2)).getRgb()[:3] == (255, 0, 0)
    projects = json.loads(docker.PATHS.projects.read_text())["projects"]
    assert projects[0]["active"] is True


def test_open_project_reuses_open_document(uploader, krita_app, tmp_path):
    docker, _ = uploader
    kra = tmp_path / "card.kra"
    kra.write_bytes(b"kra")
    for i in range(2):
        queue(docker, f"open-{i}.json", {"action": "open_project", "path": str(kra)})
        docker.SYNC.process_commands()
        assert json.loads((docker.PATHS.done / f"open-{i}.json").read_text())["status"] == "succeeded"
    assert len(krita_app.documents()) == 1
    assert krita_app.activeDocument().fileName() == str(kra)


def test_ping_and_live_gate(uploader, krita_app):
    docker, _ = uploader
    queue(docker, "ping.json", {"action": "ping"})
    docker.SYNC.process_commands()
    assert "pong" in json.loads((docker.PATHS.done / "ping.json").read_text())["message"]

    red_document(krita_app)
    docker.PATHS.latest_info.unlink(missing_ok=True)
    docker.SYNC.tick_canvas()
    assert not docker.PATHS.latest_info.exists()
    docker.PATHS.state.write_text('{"live_canvas_preview": true}')
    try:
        docker.SYNC.tick_canvas()
        assert docker.PATHS.latest_info.exists()
    finally:
        docker.PATHS.state.unlink()


# --- AI Image docker ----------------------------------------------------------

@pytest.fixture
def ai_docker(krita_app):
    import grok_image  # noqa: F401 - registers the factory
    from grok_image.grok_image import GrokImageDocker

    return GrokImageDocker()


def test_controls_follow_provider(ai_docker):
    ai_docker.provider_picker.setCurrentIndex(0)  # Nano Banana
    assert ai_docker.use_canvas_checkbox.isEnabled()
    assert not ai_docker.count_spinner.isEnabled()
    ai_docker.provider_picker.setCurrentIndex(1)  # Grok
    assert not ai_docker.use_canvas_checkbox.isEnabled()
    assert ai_docker.count_spinner.isEnabled()
    ai_docker.provider_picker.setCurrentIndex(3)  # Local
    assert ai_docker.generate_button.text() == "Open krita-ai-diffusion docker"


def test_result_creates_document_and_counts_all_layers(ai_docker, krita_app):
    """Regression: the first image went into the new document but wasn't counted."""
    ai_docker._job_layer_prefix = "Imagen"
    png = solid_png(None, 5, 5, (255, 0, 0))
    ai_docker.handle_result([png, png], None)
    assert ai_docker.status_label.text() == "Added 2 layer(s)."
    doc = krita_app.activeDocument()
    layers = doc.rootNode().childNodes()
    assert [n.name() for n in layers] == ["Imagen image 1", "Imagen image 2"]
    assert layers[0].pixels[4][:4] == RED_BGRA  # regression: red/blue used to be swapped


def test_result_lands_in_selection(ai_docker, krita_app):
    doc = red_document(krita_app, 20, 20)
    ai_docker._job_bounds = (4, 6, 8, 3)
    ai_docker.handle_result([solid_png(None, 64, 64, (0, 0, 255))], None)
    layer = doc.rootNode().childNodes()[-1]
    x, y, w, h, data = layer.pixels
    assert (x, y, w, h) == (4, 6, 8, 3)
    assert data[:4] == bytes([255, 0, 0, 255])  # blue in BGRA


def test_bad_bytes_and_errors(ai_docker, krita_app):
    ai_docker.handle_result(None, "HTTP 401 Unauthorized")
    assert ai_docker.status_label.text() == "Error: HTTP 401 Unauthorized"
    red_document(krita_app)
    ai_docker.handle_result([b"not an image"], None)
    assert "could not be decoded" in ai_docker.status_label.text()


def test_canvas_region_png_is_true_color(krita_app, qapp):
    from PyQt5.QtCore import QByteArray
    from PyQt5.QtGui import QColor, QImage

    from grok_image.grok_image import active_canvas_region_as_png

    red_document(krita_app, 10, 10)
    png = active_canvas_region_as_png((2, 2, 4, 4))
    image = QImage.fromData(QByteArray(png))
    assert (image.width(), image.height()) == (4, 4)
    assert QColor(image.pixel(0, 0)).getRgb() == (255, 0, 0, 255)
