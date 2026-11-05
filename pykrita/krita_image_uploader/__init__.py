from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita

from .docker import KritaImageUploaderDocker, KritaImageUploaderExtension

Krita.instance().addExtension(KritaImageUploaderExtension(Krita.instance()))
Krita.instance().addDockWidgetFactory(
    DockWidgetFactory(
        "kritaImageUploaderDocker",
        DockWidgetFactoryBase.DockRight,
        KritaImageUploaderDocker,
    )
)
