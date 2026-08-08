import importlib.util
import pathlib
import socket
import sys
import types

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_module(name: str, module):
    if name in sys.modules:
        return
    try:
        available = importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        # ``find_spec("package.child")`` imports/inspects the parent.  A parent
        # stub deliberately has no import spec, so treat the child as missing.
        available = False
    if not available:
        sys.modules[name] = module


# Provide lightweight stubs for optional third-party dependencies to keep tests hermetic.
fake_requests = types.ModuleType("requests")
fake_requests.get = lambda *args, **kwargs: (_ for _ in ()).throw(
    RuntimeError("requests stub")
)


class FakeDocument:
    def __init__(self, text):
        self.text = text

    def summary(self, html_partial=True):
        raise RuntimeError("readability stub")


fake_readability = types.ModuleType("readability")
fake_readability.Document = FakeDocument

fake_html = types.ModuleType("html")
fake_html.fromstring = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    RuntimeError("lxml stub")
)

fake_lxml = types.ModuleType("lxml")
fake_lxml.__path__ = []
fake_lxml.html = fake_html

_ensure_module("requests", fake_requests)
_ensure_module("readability", fake_readability)
_ensure_module("lxml", fake_lxml)
_ensure_module("lxml.html", fake_html)


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch, request):
    """Fail tests that accidentally cross a real network boundary."""
    if request.node.get_closest_marker("live_e2e"):
        return

    def blocked(*_args, **_kwargs):
        raise RuntimeError("External network access is disabled during tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
