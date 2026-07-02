from krita import Krita, DockWidgetFactory, DockWidgetFactoryBase
from .grok_image import GrokImageDocker

Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("grokImageDocker", DockWidgetFactoryBase.DockRight, GrokImageDocker)
)
