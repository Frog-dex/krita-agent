from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita

from .grok_image import GrokImageDocker

Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("grokImageDocker", DockWidgetFactoryBase.DockRight, GrokImageDocker)
)
