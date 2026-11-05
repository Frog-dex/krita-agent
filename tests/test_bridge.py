import json
import threading
import time

import pytest

from conftest import load_module

core = load_module("bridge_core", "pykrita/krita_image_uploader/bridge_core.py")
import krita_bridge  # noqa: E402  (tools/ is on sys.path via conftest)


@pytest.fixture
def paths(tmp_path):
    p = core.BridgePaths(tmp_path / "bridge")
    p.ensure()
    return p


def queue(paths, name, payload):
    path = paths.commands / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def test_success_unknown_and_failure_paths(paths):
    queue(paths, "a.json", {"action": "ping"})
    queue(paths, "b.json", {"action": "teleport"})
    queue(paths, "c.json", {"action": "boom"})
    queue(paths, "d.json", "{broken")

    def boom(command):
        raise FileNotFoundError("/nope.png")

    results = core.process_commands(paths, {"ping": lambda c: "pong", "boom": boom})
    assert [r[:2] for r in results] == [
        ("a.json", "succeeded"), ("b.json", "ignored"), ("c.json", "failed"), ("d.json", "failed"),
    ]
    done_a = json.loads((paths.done / "a.json").read_text())
    assert done_a["status"] == "succeeded" and done_a["message"] == "pong" and done_a["completed_at"]
    assert json.loads((paths.done / "b.json").read_text())["status"] == "ignored"
    err = json.loads((paths.commands / "c.error.json").read_text())
    assert err["status"] == "failed" and err["action"] == "boom" and "/nope.png" in err["error"]
    assert (paths.commands / "d.error.json").exists()
    assert core.pending_commands(paths) == []


def test_error_reports_are_not_requeued(paths):
    """Regression: <name>.error.json matched *.json and was re-run every tick."""
    queue(paths, "x.json", {"action": "boom"})
    calls = []

    def boom(command):
        calls.append(command)
        raise RuntimeError("nope")

    for _ in range(3):
        core.process_commands(paths, {"boom": boom})
    assert len(calls) == 1
    assert sorted(p.name for p in paths.commands.glob("*.json")) == ["x.error.json"]


def test_temp_and_hidden_files_are_skipped(paths):
    for name in ("cmd.tmp.json", ".cmd.json.part", ".hidden.json", "real.json"):
        (paths.commands / name).write_text("{}")
    assert [p.name for p in core.pending_commands(paths)] == ["real.json"]


def test_atomic_write_leaves_no_temp(paths):
    core.atomic_write_json(paths.projects, {"projects": []})
    assert json.loads(paths.projects.read_text()) == {"projects": []}
    assert [p.name for p in paths.krita.iterdir() if p.is_file()] == ["projects.json"]


def test_read_state(paths):
    assert core.read_state(paths, "live_canvas_preview", False) is False
    paths.state.write_text('{"live_canvas_preview": true}')
    assert core.read_state(paths, "live_canvas_preview", False) is True
    paths.state.write_text("garbage")
    assert core.read_state(paths, "live_canvas_preview", "d") == "d"


def test_helpers():
    assert core.clamp_scale(None) == 1.0
    assert core.clamp_scale("0.5") == 0.5
    assert core.clamp_scale(9) == 1.0
    assert core.clamp_scale(0) == 0.05
    with pytest.raises(ValueError):
        core.clamp_scale("big")
    assert core.fit_within(4000, 2000, 1024) == (1024, 512)
    assert core.fit_within(100, 50, 1024) == (100, 50)
    with pytest.raises(ValueError):
        core.fit_within(10, 10, 0)
    assert core.rgba_to_bgra(bytes([1, 2, 3, 4])) == bytes([3, 2, 1, 4])
    assert core.error_path_for("/q/insert-1.json").name == "insert-1.error.json"


def test_prune_snapshots(paths):
    for i in range(7):
        (paths.snapshots / f"2026010{i}-000000-000000-canvas.png").write_bytes(b"x")
    assert core.prune_snapshots(paths, keep=3) == 4
    assert sorted(p.name[:9] for p in paths.snapshots.iterdir()) == ["20260104-", "20260105-", "20260106-"]


# --- tools/krita_bridge.py client against a simulated Krita -------------------

def run_fake_krita(paths, handlers, stop):
    while not stop.is_set():
        core.process_commands(paths, handlers)
        time.sleep(0.02)


@pytest.fixture
def live_bridge(paths):
    stop = threading.Event()
    seen = []

    def insert(command):
        seen.append(command)
        return "Inserted image"

    def broken(command):
        raise RuntimeError("no document")

    handlers = {"ping": lambda c: "pong", "insert_image": insert, "export_canvas": broken}
    thread = threading.Thread(target=run_fake_krita, args=(paths, handlers, stop), daemon=True)
    thread.start()
    yield paths, seen
    stop.set()
    thread.join(2)


def test_client_roundtrip(live_bridge, tmp_path, capsys):
    paths, seen = live_bridge
    root = str(paths.root)
    assert krita_bridge.main(["--root", root, "ping"]) == 0
    assert json.loads(capsys.readouterr().out)["message"] == "pong"

    image = tmp_path / "result.png"
    image.write_bytes(b"png")
    assert krita_bridge.main(["--root", root, "insert", str(image), "--name", "Frog", "--scale", "0.5"]) == 0
    capsys.readouterr()
    assert seen == [{"action": "insert_image", "path": str(image.resolve()), "scale": 0.5, "name": "Frog"}]

    assert krita_bridge.main(["--root", root, "export"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and out["error"] == "no document"


def test_client_timeout_and_no_wait(tmp_path, capsys):
    root = str(tmp_path / "idle")
    assert krita_bridge.main(["--root", root, "--timeout", "0.2", "ping"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "timeout"
    assert krita_bridge.main(["--root", root, "--no-wait", "ping"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "queued"


def test_client_live_projects_latest(tmp_path, capsys):
    root = tmp_path / "b"
    assert krita_bridge.main(["--root", str(root), "projects"]) == 1
    capsys.readouterr()
    assert krita_bridge.main(["--root", str(root), "live", "on"]) == 0
    assert json.loads((root / "state.json").read_text()) == {"live_canvas_preview": True}
    (root / "krita").mkdir()
    (root / "krita" / "latest.json").write_text('{"width": 3}')
    capsys.readouterr()
    assert krita_bridge.main(["--root", str(root), "latest"]) == 0
    assert json.loads(capsys.readouterr().out) == {"width": 3}


def test_client_rejects_missing_file(tmp_path):
    with pytest.raises(SystemExit):
        krita_bridge.main(["--root", str(tmp_path), "insert", str(tmp_path / "missing.png")])
