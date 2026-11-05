"""A tiny in-memory stand-in for Krita's ``krita`` Python module.

Just enough of the scripting API (Krita / Document / Node / DockWidget ...) for
the plugins and kra_build.py to run under plain Python + PyQt5 in tests.
Pixels are stored the way Krita stores RGBA/U8: BGRA bytes.
"""

from PyQt5.QtCore import QObject
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QDockWidget


class Node:
    def __init__(self, document, name, node_type):
        self.document = document
        self._name = name
        self._type = node_type
        self.children = []
        self.parent = None
        self.inherit_alpha = False
        self.pixels = None  # (x, y, w, h, bytes)

    def name(self):
        return self._name

    def type(self):
        return self._type.lower()

    def childNodes(self):
        return list(self.children)

    def addChildNode(self, child, above):
        child.parent = self
        if above is None:
            self.children.append(child)
        else:
            self.children.insert(self.children.index(above) + 1, child)
        return True

    def setPixelData(self, data, x, y, w, h):
        data = bytes(data)
        assert len(data) == w * h * 4, "setPixelData size mismatch"
        self.pixels = (x, y, w, h, data)

    def setInheritAlpha(self, value):
        self.inherit_alpha = bool(value)

    def inheritAlpha(self):
        return self.inherit_alpha


class Document:
    def __init__(self, width, height, name="Untitled", model="RGBA", depth="U8", filename=""):
        self._w, self._h = width, height
        self._name = name
        self._model, self._depth = model, depth
        self._filename = filename
        self.root = Node(self, "root", "grouplayer")
        self.projection = bytearray(width * height * 4)
        self.saved_to = None
        self.closed = False
        self.selection_bounds = None

    # --- metadata
    def width(self):
        return self._w

    def height(self):
        return self._h

    def name(self):
        return self._name

    def fileName(self):
        return self._filename

    def colorModel(self):
        return self._model

    def colorDepth(self):
        return self._depth

    def setBatchmode(self, value):
        pass

    def rootNode(self):
        return self.root

    def selection(self):
        if self.selection_bounds is None:
            return None
        return _Selection(*self.selection_bounds)

    def createNode(self, name, node_type):
        return Node(self, name, node_type)

    # --- pixels
    def _layers(self, node=None):
        node = node or self.root
        for child in node.children:
            if child.children:
                yield from self._layers(child)
            elif child.pixels is not None:
                yield child

    def refreshProjection(self):
        """Flatten paint layers bottom-to-top, replacing pixels whose alpha > 0."""
        out = bytearray(self._w * self._h * 4)
        for layer in self._layers():
            x0, y0, w, h, data = layer.pixels
            for row in range(h):
                y = y0 + row
                if not 0 <= y < self._h:
                    continue
                for col in range(w):
                    x = x0 + col
                    if not 0 <= x < self._w:
                        continue
                    src = (row * w + col) * 4
                    if data[src + 3]:
                        dst = (y * self._w + x) * 4
                        out[dst:dst + 4] = data[src:src + 4]
        self.projection = out

    def pixelData(self, x, y, w, h):
        rows = []
        for row in range(y, y + h):
            start = (row * self._w + x) * 4
            rows.append(bytes(self.projection[start:start + w * 4]))
        return b"".join(rows)

    def thumbnail(self, w, h):
        image = QImage(bytes(self.projection), self._w, self._h, self._w * 4, QImage.Format_ARGB32).copy()
        return image.scaled(w, h)

    def saveAs(self, path):
        self.saved_to = path
        with open(path, "wb") as handle:
            handle.write(b"fake kra")
        return True

    def close(self):
        self.closed = True


class _Selection:
    def __init__(self, x, y, w, h):
        self._b = (x, y, w, h)

    def x(self):
        return self._b[0]

    def y(self):
        return self._b[1]

    def width(self):
        return self._b[2]

    def height(self):
        return self._b[3]


class View:
    def __init__(self, document):
        self._doc = document

    def document(self):
        return self._doc


class Window:
    def __init__(self, app):
        self.app = app
        self._views = []

    def activate(self):
        pass

    def views(self):
        return list(self._views)

    def addView(self, document):
        view = View(document)
        self._views.append(view)
        self.app.active = document
        return view

    def showView(self, view):
        self.app.active = view.document()


class Krita:
    _instance = None

    def __init__(self):
        self.docs = []
        self.active = None
        self.window = Window(self)
        self.extensions = []
        self.dock_factories = []
        self.files = {}  # path -> Document, for openDocument

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = cls()
        return cls._instance

    def version(self):
        return "5.2.fake"

    def setBatchmode(self, value):
        pass

    def activeDocument(self):
        return self.active

    def setActiveDocument(self, document):
        self.active = document

    def documents(self):
        return list(self.docs)

    def activeWindow(self):
        return self.window

    def createDocument(self, w, h, name, model, depth, profile, resolution):
        doc = Document(w, h, name, model, depth)
        self.docs.append(doc)
        return doc

    def openDocument(self, path):
        doc = self.files.get(path) or Document(8, 8, filename=path)
        doc._filename = path
        self.docs.append(doc)
        return doc

    def addExtension(self, extension):
        self.extensions.append(extension)

    def addDockWidgetFactory(self, factory):
        self.dock_factories.append(factory)


class Extension(QObject):
    def __init__(self, parent=None):
        super().__init__()


class DockWidget(QDockWidget):
    def canvasChanged(self, canvas):
        pass


class DockWidgetFactoryBase:
    DockRight = 2


class DockWidgetFactory:
    def __init__(self, name, area, cls):
        self.name, self.area, self.cls = name, area, cls
