import json

import pytest

Image = pytest.importorskip("PIL.Image")

import kra_prepare  # noqa: E402
from conftest import requires_qt  # noqa: E402


def make_png(path, size, rgba):
    Image.new("RGBA", size, rgba).save(path)
    return path


def test_bgra_conversion():
    im = Image.new("RGBA", (1, 1), (10, 20, 30, 40))
    assert kra_prepare.to_bgra_bytes(im) == bytes([30, 20, 10, 40])


def test_parse_helpers():
    assert kra_prepare.parse_size("2480x3508") == (2480, 3508)
    with pytest.raises(Exception):
        kra_prepare.parse_size("big")
    path, pos = kra_prepare.parse_card_spec("art/card one.png@12,-3")
    assert (path.name, pos) == ("card one.png", (12, -3))
    assert kra_prepare.parse_card_spec("x.png")[1] is None


def test_auto_layout_wraps_without_overlap():
    sizes = [(100, 150)] * 5
    positions, (w, h) = kra_prepare.auto_layout(sizes, gap=10, margin=20, max_row_width=320)
    assert positions[:3] == [(20, 20), (130, 20), (240, 20)]
    assert positions[3] == (20, 180)
    assert (w, h) == (360, 350)


def test_prepare_writes_manifest_tiles_and_preview(tmp_path):
    a = make_png(tmp_path / "frog.png", (30, 40), (0, 200, 0, 255))
    b = make_png(tmp_path / "frog2.png", (20, 20), (200, 0, 0, 128))
    out = tmp_path / "job"
    manifest = kra_prepare.prepare(
        out, [(a, None), (b, (100, 5))], bg_color="#102030", gap=10, margin=10, preview=True
    )
    assert manifest["canvas"] == [130, 60]
    assert [c["name"] for c in manifest["cards"]] == ["frog", "frog2"]
    assert manifest["cards"][0] | {"file": None} == {"name": "frog", "file": None, "x": 10, "y": 10, "w": 30, "h": 40}
    assert json.loads((out / "manifest.json").read_text()) == manifest
    tile = (out / "card01.bgra").read_bytes()
    assert len(tile) == 30 * 40 * 4 and tile[:4] == bytes([0, 200, 0, 255])
    assert (out / "bg.bgra").read_bytes()[:4] == bytes([0x30, 0x20, 0x10, 255])
    preview = Image.open(out / "preview.png")
    assert preview.size == (130, 60)
    assert preview.getpixel((15, 15)) == (0, 200, 0, 255)
    assert preview.getpixel((0, 0)) == (0x10, 0x20, 0x30, 255)


def test_prepare_duplicate_names_and_missing_file(tmp_path):
    a = make_png(tmp_path / "card.png", (4, 4), (1, 2, 3, 255))
    m = kra_prepare.prepare(tmp_path / "j", [(a, None), (a, None)])
    assert [c["name"] for c in m["cards"]] == ["card", "card 2"]
    assert kra_prepare.main([str(tmp_path / "j2"), str(tmp_path / "missing.png")]) == 1


@requires_qt
def test_kra_build_end_to_end(tmp_path, krita_app, monkeypatch):
    import kra_build

    a = make_png(tmp_path / "marble.png", (8, 8), (0, 0, 255, 255))
    job = tmp_path / "job"
    kra_prepare.prepare(job, [(a, (2, 3))], canvas=(16, 16), bg_color="#000000")
    out = tmp_path / "out.kra"
    monkeypatch.setenv("KRA_BASE", str(job))
    monkeypatch.setenv("KRA_OUT", str(out))

    payload = kra_build.__main__()
    assert payload == {"ok": True, "out": str(out), "layers": 4}
    assert json.loads((tmp_path / "out.kra.result.json").read_text())["ok"] is True

    doc = krita_app.documents()[-1]
    assert doc.closed and doc.saved_to == str(out)
    bg, group = doc.rootNode().childNodes()
    assert bg.name() == "BACKGROUND"
    assert (group.name(), group.type()) == ("marble", "grouplayer")
    border, paint = group.childNodes()
    assert border.pixels[:4] == (2, 3, 8, 8) and border.pixels[4][:4] == bytes([255, 0, 0, 255])
    assert paint.name() == kra_build.INHERIT_ALPHA_LAYER and paint.inheritAlpha()


@requires_qt
def test_kra_build_reports_bad_tile(tmp_path, krita_app, monkeypatch):
    import kra_build

    job = tmp_path / "job"
    job.mkdir()
    (job / "manifest.json").write_text(json.dumps({
        "canvas": [4, 4], "cards": [{"file": "c.bgra", "x": 0, "y": 0, "w": 2, "h": 2}],
    }))
    (job / "c.bgra").write_bytes(b"\0" * 7)
    monkeypatch.setenv("KRA_BASE", str(job))
    monkeypatch.setenv("KRA_OUT", str(tmp_path / "o.kra"))
    payload = kra_build.__main__()
    assert payload["ok"] is False and "expected 16" in payload["error"]
