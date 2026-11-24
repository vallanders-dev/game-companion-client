"""client/gui/motion.py — one motion language for the main window and the chat
(docs/chat_redesign.md, "Motion: every interaction point", 2026-11-23).

Every animated widget takes its durations and curve from here, so the whole
window moves the same way: ease-out cubic, short for small things, longer for
layout, nothing that bounces.

`enabled()` is the single switch: False when Windows' "Show animations in
Windows" is off (SPI_GETCLIENTAREAANIMATION) or OMBRI_NO_MOTION=1 is set (the
harnesses use it so a check never waits on an animation). With motion off
every helper jumps straight to the end value.

`quiet()` is the other gate: True while the window is hidden or minimized or a
recognised game is in front. Continuous effects (the thinking row, the
newest answer's glow) stop their timers then - nothing animates while the
player plays.
"""
from __future__ import annotations

import ctypes
import math
import os
import sys

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QGraphicsOpacityEffect, QProgressBar, QWidget

FAST = 120      # hover, press, icon swaps
SMALL = 180     # a row fading in, a chip toggling
LAYOUT = 300    # the window widening, the tab content, a card collapsing
STAGGER = 60    # cards / link rows appearing one after another
RISE = 6        # px a new row rises while it fades in
LOOP_THINK = 2.4    # s, the thinking row's tilt and breath
LOOP_GLOW = 6.0     # s, the newest answer's glow (slow: owner asked for it diffused and quiet)

# The glow (owner, 2026-11-23: "less conspicuous, a little more diffused").
GLOW_RGB = (226, 93, 208)   # magenta, the palette's thinking colour
GLOW_SPREAD = 26            # px the glow reaches outside a panel
GLOW_MAX_ALPHA = 0.07       # peak opacity of the glow's innermost ring
GLOW_MIN_ALPHA = 0.02

CURVE = QEasingCurve.Type.OutCubic

_cached: bool | None = None


def enabled() -> bool:
    global _cached
    if os.environ.get("OMBRI_NO_MOTION") == "1":
        return False
    if _cached is None:
        _cached = True
        if sys.platform.startswith("win"):
            try:
                value = ctypes.c_int(1)
                SPI_GETCLIENTAREAANIMATION = 0x1042
                if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0,
                                                              ctypes.byref(value), 0):
                    _cached = bool(value.value)
            except Exception:  # noqa: BLE001 - no answer = animate, like Windows' own default
                _cached = True
    return _cached


_quiet_probe = None  # set by ui_process: a zero-arg callable, True while a game is in front


def set_game_in_front_probe(probe) -> None:
    global _quiet_probe
    _quiet_probe = probe


def quiet(widget: QWidget | None = None) -> bool:
    """True when continuous animations must stop."""
    if not enabled():
        return True
    if widget is not None:
        win = widget.window()
        if not widget.isVisible() or win.isMinimized() or not win.isVisible():
            return True
    if _quiet_probe is not None:
        try:
            return bool(_quiet_probe())
        except Exception:  # noqa: BLE001
            return False
    return False


def tween(owner: QObject, start, end, ms: int, on_value, on_done=None,
          curve: QEasingCurve.Type = CURVE) -> QVariantAnimation | None:
    """Animate a value from `start` to `end`, calling `on_value(v)` each frame.
    With motion off it calls `on_value(end)` (and `on_done`) at once and
    returns None. The animation is parented to `owner`, so it dies with it."""
    if not enabled() or ms <= 0:
        on_value(end)
        if on_done:
            on_done()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(curve)
    anim.valueChanged.connect(on_value)
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def fade_in(widget: QWidget, ms: int = SMALL, rise: int = RISE, delay: int = 0) -> None:
    """A new row: opacity 0 -> 1 while it rises `rise` px (its top margin
    shrinks back to normal). New rows land at the bottom of a column, so the
    few pixels the row gives back never push anything else around. The
    opacity effect is removed at the end - an effect left on a widget costs a
    repaint pass for good."""
    if not enabled():
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    m = widget.contentsMargins()
    top, bottom = m.top(), m.bottom()

    def step(v: float) -> None:
        if widget.graphicsEffect() is effect:
            effect.setOpacity(v)
        off = round(rise * (1.0 - v))
        widget.setContentsMargins(m.left(), top + off, m.right(), bottom)

    def done() -> None:
        widget.setContentsMargins(m.left(), top, m.right(), bottom)
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    def go() -> None:
        tween(widget, 0.0, 1.0, ms, step, done)

    step(0.0)
    if delay > 0:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(delay, widget, go)
    else:
        go()


# -- Liquid motion (owner, 2026-11-23: "liquid/water-like motion") -----------------------
# Springs instead of fixed curves where something travels: it carries momentum, can be
# redirected mid-way without a jerk, and settles on its own (Apple's "fluid interfaces"
# idea). `frequency` (Hz) is how quick it answers, `damping` 1.0 = no overshoot (hover,
# the window), lower = a soft overshoot (the tab pill's drop).
SPRING_LAYOUT = (2.6, 1.0)   # the window widening: momentum, no overshoot
SPRING_DROP = (3.4, 0.62)    # the tab pill: travels like a drop and settles
SPRING_LEVEL = (1.4, 0.9)    # the water level in "Perguntas hoje"


class Spring(QObject):
    """A damped spring driving one number. `to(target)` starts or redirects it
    (the velocity is kept); `on_value(x)` each frame. Motion off: it jumps."""

    def __init__(self, owner: QObject, value: float, on_value, frequency: float = 2.6,
                 damping: float = 1.0, on_done=None) -> None:
        super().__init__(owner)
        from PySide6.QtCore import QElapsedTimer, QTimer
        self.x = float(value)
        self.v = 0.0
        self.target = float(value)
        self.on_value = on_value
        self.on_done = on_done
        self.omega = 2 * math.pi * frequency
        self.zeta = damping
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()

    def to(self, target: float) -> None:
        self.target = float(target)
        if not enabled():
            self.jump(target)
            return
        if not self._timer.isActive():
            self._clock.start()
            self._timer.start()

    def jump(self, value: float) -> None:
        self._timer.stop()
        self.x = self.target = float(value)
        self.v = 0.0
        self.on_value(self.x)

    def moving(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        dt = min(self._clock.restart() / 1000.0, 0.05)
        steps = max(1, int(dt / 0.004))  # small steps: stable at any frame rate
        h = dt / steps
        for _ in range(steps):
            a = -self.omega * self.omega * (self.x - self.target) - 2 * self.zeta * self.omega * self.v
            self.v += a * h
            self.x += self.v * h
        if abs(self.x - self.target) < 0.25 and abs(self.v) < 2.0:
            self._timer.stop()
            self.x, self.v = self.target, 0.0
            self.on_value(self.x)
            if self.on_done:
                self.on_done()
            return
        self.on_value(self.x)


def paint_drop(p: QPainter, sx: Spring, sw: Spring, tail: Spring, y0: float, h0: float) -> None:
    """The chat's tab pill this frame, as a drop (pen and brush already set): its centre
    and width ride springs, it stretches the way it travels and thins a little, and a
    smaller tail follows joined by a neck, so it pulls apart and merges like water."""
    x, w, v = sx.x, sw.x, sx.v
    stretch = min(abs(v) * 0.045, 22.0)
    h = h0 - min(stretch * 0.18, 4.0)
    y = y0 + (h0 - h) / 2
    left = x - w / 2 - (stretch if v > 0 else 0)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(left, y, w + stretch, h).adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
    gap = abs(tail.x - x)
    if gap > w * 0.35:
        th = h * 0.62
        tw = min(w * 0.55, gap)
        t = QPainterPath()
        t.addRoundedRect(QRectF(tail.x - tw / 2, y + (h - th) / 2, tw, th), th / 2, th / 2)
        nh = th * 0.55
        a, c = sorted((tail.x, x))
        neck = QPainterPath()
        neck.addRect(QRectF(a, y + (h - nh) / 2, c - a, nh))
        shape = shape.united(t).united(neck)
    p.drawPath(shape)


class WaterBar(QProgressBar):
    """A progress bar that fills like water: the level flows to a new value (a
    spring), its leading edge is a gently moving wave, and a soft light drifts
    through it. The wave only runs while the window is in front and no game is
    (quiet()); otherwise it's a still level. value() is the target, as before."""

    def __init__(self, fill: str, track: str) -> None:
        super().__init__()
        from PySide6.QtCore import QElapsedTimer, QTimer
        self._fill = QColor(fill)
        self._track = QColor(track)
        self._level = 0.0  # 0..1, what's drawn
        self._spring = Spring(self, 0.0, self._set_level, *SPRING_LEVEL)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._wave = QTimer(self)
        self._wave.setInterval(33)
        self._wave.timeout.connect(self._wave_tick)
        self.setTextVisible(False)

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt's name
        super().setValue(value)
        span = max(1, self.maximum() - self.minimum())
        target = (self.value() - self.minimum()) / span
        self._spring.to(target * 1000.0)

    def _set_level(self, x: float) -> None:
        self._level = max(0.0, min(1.0, x / 1000.0))
        self.update()

    def _wave_tick(self) -> None:
        # While a game is in front (or the window is away) it only checks once a second.
        if quiet(self):
            self._wave.setInterval(1000)
            return
        self._wave.setInterval(33)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if enabled():
            self._wave.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._wave.stop()

    def enterEvent(self, event) -> None:  # noqa: N802 - back in front: the wave wakes
        super().enterEvent(event)
        if enabled() and not self._wave.isActive():
            self._wave.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        clip = QPainterPath()
        clip.addRoundedRect(r, rad, rad)
        p.setClipPath(clip)
        p.fillRect(r, self._track)
        w = r.width() * self._level
        if w > 0.5:
            live = self._wave.isActive() and self._wave.interval() < 100
            t = self._clock.elapsed() / 1000.0 if live else 0.0
            amp = min(3.0, w * 0.08)  # the leading edge sways a few px
            path = QPainterPath()
            path.moveTo(0, 0)
            n = 8
            for i in range(n + 1):
                y = r.height() * i / n
                x = w + amp * math.sin(t * 2.2 + i * 0.9) * (0.6 + 0.4 * math.sin(t * 0.7))
                path.lineTo(min(x, r.width()), y)
            path.lineTo(0, r.height())
            path.closeSubpath()
            p.fillPath(path, self._fill)
            # a soft light drifting through the water
            if live:
                cx = (t * 0.18 % 1.4 - 0.2) * w
                g = QLinearGradient(cx - 40, 0, cx + 40, 0)
                light = QColor(255, 255, 255, 0)
                g.setColorAt(0.0, light)
                light.setAlpha(46)
                g.setColorAt(0.5, light)
                light.setAlpha(0)
                g.setColorAt(1.0, light)
                p.setClipPath(path)
                p.fillRect(r, g)
        p.end()


class _Ripple(QWidget):
    """A ring spreading from where a button was pressed, like a drop on water."""

    def __init__(self, button: QWidget, at) -> None:
        super().__init__(button)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setGeometry(button.rect())
        self._at = at
        self._t = 0.0
        r = button.rect()
        self._reach = math.hypot(max(at.x(), r.width() - at.x()), max(at.y(), r.height() - at.y()))
        self.show()
        self.raise_()
        tween(self, 0.0, 1.0, RIPPLE_MS, self._step, self.deleteLater, QEasingCurve.Type.OutQuad)

    def _step(self, v: float) -> None:
        self._t = v
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 6, 6)
        p.setClipPath(clip)
        rad = 4 + self._reach * self._t
        fade = 1.0 - self._t
        fill = QColor(255, 255, 255, int(34 * fade))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(QPointF(self._at), rad, rad)
        ring = QColor(255, 255, 255, int(70 * fade))
        p.setPen(QPen(ring, 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(self._at), rad, rad)
        p.end()


RIPPLE_MS = 520


class _RippleFilter(QObject):
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (event.type() == QEvent.Type.MouseButtonPress and isinstance(obj, QAbstractButton)
                and obj.isEnabled() and enabled() and obj.width() > 24):
            try:
                _Ripple(obj, event.position().toPoint())
            except Exception:  # noqa: BLE001 - a ripple never breaks a click
                pass
        return False


_ripples: _RippleFilter | None = None


def install_ripples() -> None:
    """Every button in this process ripples when pressed (once per process)."""
    global _ripples
    app = QApplication.instance()
    if _ripples is not None or app is None:
        return
    _ripples = _RippleFilter(app)
    app.installEventFilter(_ripples)
