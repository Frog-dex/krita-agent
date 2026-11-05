"""File-protocol core of the Krita Image Uploader bridge — no Krita or Qt imports.

The plugin (``docker.py``) supplies Krita-specific action handlers; this module
owns the on-disk protocol so it can be unit-tested and shared with the
``tools/krita_bridge.py`` client:

  <root>/commands/<name>.json         queued command  {"action": ..., ...}
  <root>/commands/done/<name>.json    finished command, stamped with
                                       status / message / completed_at
  <root>/commands/<name>.error.json   failed command, stamped with error
  <root>/krita/latest.png|latest.json newest canvas snapshot + metadata
  <root>/krita/projects.json          open documents (refreshed every 5 s)
  <root>/krita/snapshots/             timestamped canvas snapshots
  <root>/state.json                   flags, e.g. {"live_canvas_preview": true}
"""

import datetime
import json
import os
from pathlib import Path

PROTOCOL_VERSION = 2
DEFAULT_ROOT = Path.home() / ".krita-image-uploader"
SNAPSHOT_KEEP = 50
ACTIONS = ("insert_image", "export_canvas", "open_project", "ping")


class BridgePaths:
    def __init__(self, root=None):
        env_root = os.environ.get("KRITA_BRIDGE_ROOT")
        self.root = Path(root or env_root or DEFAULT_ROOT).expanduser()
        self.krita = self.root / "krita"
        self.commands = self.root / "commands"
        self.done = self.commands / "done"
        self.snapshots = self.krita / "snapshots"
        self.latest_image = self.krita / "latest.png"
        self.latest_info = self.krita / "latest.json"
        self.projects = self.krita / "projects.json"
        self.state = self.root / "state.json"

    def ensure(self):
        for folder in (self.root, self.krita, self.snapshots, self.commands, self.done):
            folder.mkdir(parents=True, exist_ok=True)


def timestamp():
    return datetime.datetime.now().isoformat(timespec="seconds")


def atomic_write_bytes(path, data):
    """Write to a hidden sibling then rename, so readers never see half a file.

    The temp name starts with "." and ends in ".part" so it never matches the
    command queue's ``*.json`` glob."""
    path = Path(path)
    tmp = path.with_name("." + path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def atomic_write_json(path, payload):
    atomic_write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def read_state(paths, key, default=None):
    try:
        data = json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return default
    return data.get(key, default) if isinstance(data, dict) else default


def is_command_file(path):
    """True for queued commands; skips error reports, temp and hidden files."""
    name = Path(path).name
    return (
        name.endswith(".json")
        and not name.endswith(".error.json")
        and not name.endswith(".tmp.json")
        and not name.startswith(".")
    )


def pending_commands(paths):
    if not paths.commands.is_dir():
        return []
    return sorted(p for p in paths.commands.glob("*.json") if is_command_file(p))


def error_path_for(command_path):
    command_path = Path(command_path)
    return command_path.with_name(command_path.stem + ".error.json")


def finish_command(paths, command_path, command, status, message):
    command = dict(command)
    command.update(status=status, message=message, completed_at=timestamp())
    atomic_write_json(paths.done / Path(command_path).name, command)
    Path(command_path).unlink(missing_ok=True)
    return command


def fail_command(command_path, command, error):
    command = dict(command) if isinstance(command, dict) else {"raw": command}
    command.update(status="failed", error=str(error), completed_at=timestamp())
    atomic_write_json(error_path_for(command_path), command)
    Path(command_path).unlink(missing_ok=True)
    return command


def process_commands(paths, handlers):
    """Run every queued command through ``handlers[action](command) -> message``.

    Returns a list of (filename, status, message) tuples. A handler raising
    marks the command failed; unknown actions are moved to done/ as "ignored"."""
    paths.ensure()
    results = []
    for command_path in pending_commands(paths):
        command = {}
        try:
            command = json.loads(command_path.read_text())
            if not isinstance(command, dict):
                raise ValueError("command must be a JSON object")
            action = command.get("action")
            handler = handlers.get(action)
            if handler is None:
                finish_command(paths, command_path, command, "ignored", f"Unknown action: {action!r}")
                results.append((command_path.name, "ignored", f"Unknown action: {action!r}"))
                continue
            message = handler(command) or "OK"
            finish_command(paths, command_path, command, "succeeded", message)
            results.append((command_path.name, "succeeded", message))
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the agent
            fail_command(command_path, command, exc)
            results.append((command_path.name, "failed", str(exc)))
    return results


def clamp_scale(value):
    try:
        scale = float(value if value not in (None, "") else 1.0)
    except (TypeError, ValueError):
        raise ValueError(f"scale must be a number, got {value!r}") from None
    return max(0.05, min(scale, 1.0))


def fit_within(width, height, max_side):
    """Size (w, h) scaled down (never up) so the long side is <= max_side."""
    max_side = int(max_side)
    if max_side <= 0:
        raise ValueError("max_side must be positive")
    scale = min(1.0, float(max_side) / float(max(width, height)))
    return max(1, int(width * scale)), max(1, int(height * scale))


def rgba_to_bgra(pixels):
    """Swap channels 0 and 2 of 8-bit 4-channel pixels (RGBA <-> BGRA)."""
    src = bytes(pixels)
    if len(src) % 4:
        raise ValueError("pixel buffer length must be a multiple of 4")
    out = bytearray(src)
    out[0::4] = src[2::4]
    out[2::4] = src[0::4]
    return bytes(out)


def snapshot_name(now=None):
    now = now or datetime.datetime.now()
    return now.strftime("%Y%m%d-%H%M%S-%f") + "-canvas.png"


def prune_snapshots(paths, keep=SNAPSHOT_KEEP):
    """Delete all but the newest ``keep`` snapshots; returns how many were removed."""
    snaps = sorted(p for p in paths.snapshots.glob("*-canvas.png"))
    stale = snaps[:-keep] if keep > 0 else snaps
    for path in stale:
        path.unlink(missing_ok=True)
    return len(stale)
