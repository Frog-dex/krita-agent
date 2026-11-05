"""Shared test setup: fake `krita` module, offscreen Qt, isolated bridge folder."""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pykrita"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Must be set before the uploader plugin is imported (it resolves paths at import).
_BRIDGE_ROOT = tempfile.mkdtemp(prefix="krita-bridge-test-")
os.environ["KRITA_BRIDGE_ROOT"] = _BRIDGE_ROOT

HAVE_QT = importlib.util.find_spec("PyQt5") is not None
if HAVE_QT:
    import fake_krita

    sys.modules.setdefault("krita", fake_krita)

requires_qt = pytest.mark.skipif(not HAVE_QT, reason="PyQt5 not installed")


def load_module(name, path):
    """Import a single .py file by path, bypassing its package __init__."""
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def qapp():
    if not HAVE_QT:
        pytest.skip("PyQt5 not installed")
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def krita_app(qapp):
    import fake_krita

    return fake_krita.Krita.reset()


@pytest.fixture
def bridge_root():
    return Path(_BRIDGE_ROOT)
