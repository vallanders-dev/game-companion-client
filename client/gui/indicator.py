"""client/gui/indicator.py - which look shows Ombri's state.

    "shadow"  Ombri as a little shadow creature with a neon rim (client/gui/shadow.py,
              owner, 2026-11-19).
    "eyes"    two coral eyes (client/gui/eyes.py, 2026-11-18).
    "orb"     the Qt sphere (client/gui/orb.py), the look shipped up to 0.1.22. Its
              code is untouched; "orb" brings it back exactly, in the main window and
              in game.

The owner asked to keep every earlier look one line away, so never delete one. The
tray icon, the window/taskbar icon and the small marks follow "shadow" (owner,
2026-11-19); with "eyes" or "orb" they are the sphere.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

STYLE = "shadow"


def custom() -> bool:
    """True when a drawn character (eyes or shadow) replaces the sphere."""
    return STYLE != "orb"


def eyes() -> bool:  # kept for older callers
    return STYLE == "eyes"


def draw(painter: QPainter, center: QPointF, box: float, state: str, t: float, *, level: float = 0.0,
         calm: bool = False, backing: QColor | None = None, ring: QColor | None = None,
         look: tuple[float, float] | None = None) -> None:
    """Draw the picked character (not the sphere) in a `box` square at `center`.
    `look` (-1..1 each way): where the cursor is; the shadow's eyes follow it."""
    if STYLE == "shadow":
        from client.gui.shadow import draw_shadow
        draw_shadow(painter, center, box, state, t, level=level, calm=calm, ring=ring, look=look)
    else:
        from client.gui.eyes import draw_eyes
        draw_eyes(painter, center, box, state, t, level=level, calm=calm, backing=backing, ring=ring)


def icon_image(size: int) -> QImage:
    """Ombri's mark as a still picture (window icon, the small marks in the
    window and notices): the shadow head when it's the picked look, else the
    sphere (owner, 2026-11-19: the icon changes everywhere with the look)."""
    if STYLE == "shadow":
        from client.gui.shadow import shadow_image
        return shadow_image(size)
    from client.gui.orb import sphere_image
    return sphere_image(size)


def app_icon() -> QIcon:
    """The window / taskbar icon: the head (the tray has the disc)."""
    if STYLE == "shadow":
        from client.gui.shadow import shadow_icon
        return shadow_icon()
    return tray_icon()


def tray_icon() -> QIcon:
    """The tray's icon: with the shadow, the in-game disc (dark, with the neon
    rim; owner, 2026-11-19), else the sphere."""
    if STYLE == "shadow":
        from client.gui.shadow import disc_icon
        return disc_icon()
    from client.gui.orb import sphere_image
    return QIcon(QPixmap.fromImage(sphere_image(64)))
