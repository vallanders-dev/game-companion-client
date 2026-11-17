"""client/gui/speaking - the library of looks for the main window's orb while Ombri talks.

One file per design. To pick another for a release, change ACTIVE; to add one, write a
class with the two methods below and list it in DESIGNS.

    feed_level(level, t)   the voice's loudness, 0..1 (RMS of a 100 ms slice), about 10
                           times a second while audio plays. `t` is the widget's clock.
    paint(p, center, radius, t, state, draw_orb, box, dpr)
                           draws one frame. `draw_orb(painter)` draws the orb itself; call
                           it exactly once. `radius` is the widget's halo radius, `box` its
                           size in logical pixels, `dpr` the screen's pixel ratio.

The in-game overlay doesn't use this (owner, 2026-11-05: nothing extra moves on that side
of the screen while the player is playing).
"""
from __future__ import annotations

from client.gui.speaking.atom import AtomDesign
from client.gui.speaking.ripple import RippleDesign

ACTIVE = "ripple"
DESIGNS = {"atom": AtomDesign, "ripple": RippleDesign}


def make_design(name: str | None = None):
    return DESIGNS[name or ACTIVE]()
