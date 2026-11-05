#!/usr/bin/env python3
"""krita_bridge — command-line client for the Krita Image Uploader bridge.

Queues a JSON command for the plugin running inside Krita and (by default)
waits for the result, so a shell script or AI agent gets a clean exit code
instead of polling folders by hand. Stdlib only.

Examples:
  python3 tools/krita_bridge.py ping
  python3 tools/krita_bridge.py insert ~/Pictures/result.png --name "AI result" --scale 0.5
  python3 tools/krita_bridge.py export --max-side 1024     # prints the snapshot path
  python3 tools/krita_bridge.py open ~/art/mycard.kra
  python3 tools/krita_bridge.py projects                   # open documents (JSON)
  python3 tools/krita_bridge.py latest                     # latest snapshot metadata
  python3 tools/krita_bridge.py live on|off                # toggle live canvas export

Exit codes: 0 succeeded, 1 failed/ignored, 2 timed out (is Krita running?).
Set KRITA_BRIDGE_ROOT (or --root) to use a folder other than ~/.krita-image-uploader.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".krita-image-uploader"


def bridge_root(explicit=None):
    return Path(explicit or os.environ.get("KRITA_BRIDGE_ROOT") or DEFAULT_ROOT).expanduser()


def write_json_atomic(path, payload):
    path = Path(path)
    tmp = path.with_name("." + path.name + ".part")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


def queue_command(root, command, prefix=None):
    """Drop a command file into the queue and return its filename."""
    commands = Path(root) / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    name = "{}-{}-{}.json".format(
        prefix or command.get("action", "cmd"), time.strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:8]
    )
    write_json_atomic(commands / name, command)
    return name


def wait_for_result(root, name, timeout=15.0, poll=0.1):
    """Block until the plugin moves the command to done/ or writes an .error.json.

    Returns the stamped command dict, or None on timeout."""
    commands = Path(root) / "commands"
    done = commands / "done" / name
    error = commands / (Path(name).stem + ".error.json")
    deadline = time.monotonic() + timeout
    while True:
        for path in (done, error):
            if path.exists():
                try:
                    return json.loads(path.read_text())
                except ValueError:
                    pass  # mid-write on a non-atomic filesystem; retry
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def send(root, command, wait=True, timeout=15.0):
    name = queue_command(root, command)
    if not wait:
        return {"status": "queued", "file": str(Path(root) / "commands" / name)}
    result = wait_for_result(root, name, timeout=timeout)
    if result is None:
        return {
            "status": "timeout",
            "file": name,
            "error": f"No response within {timeout:g}s — is Krita running with the "
                     "Krita Image Uploader plugin enabled?",
        }
    return result


def build_command(args):
    if args.cmd == "ping":
        return {"action": "ping"}
    if args.cmd == "insert":
        path = Path(args.path).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"krita_bridge: no such file: {path}")
        command = {"action": "insert_image", "path": str(path), "scale": args.scale}
        if args.name:
            command["name"] = args.name
        return command
    if args.cmd == "export":
        return {"action": "export_canvas", "max_side": args.max_side}
    if args.cmd == "open":
        return {"action": "open_project", "path": str(Path(args.path).expanduser().resolve())}
    raise SystemExit(f"krita_bridge: unknown command {args.cmd}")


def set_live(root, enabled):
    state_path = Path(root) / "state.json"
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    state["live_canvas_preview"] = bool(enabled)
    Path(root).mkdir(parents=True, exist_ok=True)
    write_json_atomic(state_path, state)
    return state


def read_json_file(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="krita_bridge", description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", help="bridge folder (default: $KRITA_BRIDGE_ROOT or ~/.krita-image-uploader)")
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for Krita (default 15)")
    parser.add_argument("--no-wait", action="store_true", help="queue the command and exit immediately")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="check that Krita is listening")
    p = sub.add_parser("insert", help="insert an image as a new paint layer")
    p.add_argument("path")
    p.add_argument("--name", help="layer name (default: 'AI result')")
    p.add_argument("--scale", type=float, default=1.0, help="downscale factor 0.05-1.0")
    p = sub.add_parser("export", help="snapshot the active canvas to PNG")
    p.add_argument("--max-side", type=int, default=1024)
    p = sub.add_parser("open", help="open (or focus) a .kra / image file")
    p.add_argument("path")
    sub.add_parser("projects", help="print the open-document list Krita publishes")
    sub.add_parser("latest", help="print metadata for the newest canvas snapshot")
    p = sub.add_parser("live", help="turn live canvas export on or off")
    p.add_argument("state", choices=["on", "off"])

    args = parser.parse_args(argv)
    root = bridge_root(args.root)

    if args.cmd in ("projects", "latest"):
        name = "projects.json" if args.cmd == "projects" else "latest.json"
        data = read_json_file(root / "krita" / name)
        if data is None:
            print(f"krita_bridge: {root / 'krita' / name} not found yet", file=sys.stderr)
            return 1
        print(json.dumps(data, indent=2))
        return 0
    if args.cmd == "live":
        print(json.dumps(set_live(root, args.state == "on"), indent=2))
        return 0

    result = send(root, build_command(args), wait=not args.no_wait, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    status = result.get("status")
    if status in ("succeeded", "queued"):
        return 0
    return 2 if status == "timeout" else 1


if __name__ == "__main__":
    sys.exit(main())
