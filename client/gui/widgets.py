"""client/gui/widgets.py — what the main window's views share: the palette,
the window's style sheet, the `_label()` / `_card()` helpers and the small
stroke icons. Split out of main_window.py on 2026-11-21 so the Chat view
(client/gui/chat_view.py) and the settings panel (client/gui/settings_panel.py)
use the same look without importing the window that contains them."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel

from client.gui.fonts import BODY_FAMILY, HEADING_FAMILY

WINDOW_W, WINDOW_H = 440, 780
RADIUS = 12

BG = "#111318"
BAR = "#0C0E12"
CARD = "#1A1D24"
CONTROL = "#2A2E38"
TEXT = "#E7E9ED"
TEXT_2 = "#A3A8B3"
TEXT_3 = "#8A919E"
CHIP_TEXT = "#C9CDD4"
ACCENT = "#FF6B4A"
OK_GREEN = "#5B7CFF"    # connected: the palette's blue (2026-11-19, was green)
WARN_AMBER = "#FF6B4A"  # offline: coral (was amber)

QSS = f"""
QWidget#root {{ background: {BG}; border-radius: {RADIUS}px; }}
QWidget#titlebar {{ background: {BAR}; border-top-left-radius: {RADIUS}px; border-top-right-radius: {RADIUS}px; }}
QLabel {{ color: {TEXT}; font-family: "{BODY_FAMILY}"; background: transparent; }}
QFrame#card {{ background: {CARD}; border-radius: 10px; }}
QFrame#chip {{ background: {CARD}; border-radius: 13px; }}
QLabel#caps {{ color: {TEXT_3}; font-size: 11px; font-weight: 700; }}
QLabel#sub {{ color: {TEXT_2}; font-size: 13px; }}
QLabel#keycap {{ background: {CONTROL}; border-radius: 6px; border-bottom: 2px solid #0B0C10;
                 font-family: "{HEADING_FAMILY}"; font-weight: 600; font-size: 14px; padding: 3px 10px; }}
QPushButton#winbtn {{ background: transparent; border: none; border-radius: 6px; }}
QPushButton#winbtn:hover {{ background: #1F232B; }}
QPushButton#btn {{ background: {CONTROL}; color: {TEXT}; border: none; border-radius: 8px;
                   font-family: "{BODY_FAMILY}"; font-size: 14px; font-weight: 600; padding: 0 16px; }}
QPushButton#btn:hover {{ background: #2E3340; }}
QPushButton#small {{ background: {CONTROL}; color: {TEXT}; border: none; border-radius: 6px;
                     font-family: "{BODY_FAMILY}"; font-size: 13px; font-weight: 600; padding: 0 12px; }}
QPushButton#small:hover {{ background: #2E3340; }}
QPushButton#danger {{ background: #3A1F19; color: #FF8A6E; border: none; border-radius: 6px;
                      font-family: "{BODY_FAMILY}"; font-size: 13px; font-weight: 600; padding: 0 12px; }}
QPushButton#danger:hover {{ background: #4A261E; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {CONTROL}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QProgressBar {{ background: {CONTROL}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QWidget#nav {{ background: {BAR}; }}
QPushButton#navbtn {{ background: transparent; color: {TEXT_2}; border: none; border-radius: 8px;
                      font-family: "{BODY_FAMILY}"; font-size: 13px; font-weight: 600; padding: 0 10px; }}
QPushButton#navbtn:hover {{ color: {TEXT}; background: #15181F; }}
QPushButton#navbtn:checked {{ background: {CARD}; color: {TEXT}; }}
QPushButton#primary {{ background: {ACCENT}; color: #14161C; border: none; border-radius: 8px;
                       font-family: "{BODY_FAMILY}"; font-size: 14px; font-weight: 700; padding: 0 18px; }}
QPushButton#primary:hover {{ background: #FF8062; }}
QPushButton#primary:disabled {{ background: {CONTROL}; color: {TEXT_3}; }}
QLineEdit, QComboBox {{ background: {CONTROL}; color: {TEXT}; border: 1px solid #343946; border-radius: 8px;
                        padding: 7px 10px; font-family: "{BODY_FAMILY}"; font-size: 14px;
                        selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {CARD}; color: {TEXT}; border: 1px solid #343946;
                               selection-background-color: {ACCENT}; selection-color: #14161C; outline: 0; }}
QCheckBox {{ color: {TEXT}; spacing: 10px; font-family: "{BODY_FAMILY}"; font-size: 14px; }}
QCheckBox:disabled {{ color: {TEXT_3}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3A3F4B; background: {CONTROL}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {CONTROL}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal:disabled {{ background: #3A3F4B; }}
QSlider::handle:horizontal:disabled {{ background: #4A4F5B; }}
QFrame#bubble {{ background: {CONTROL}; border-radius: 12px; }}
QFrame#chatcard {{ background: #20242D; border-radius: 10px; border: 1px solid #2A2E38; }}
QFrame#chatcard:hover {{ background: #262B35; border: 1px solid #3A3F4B; }}
QFrame#thumb {{ background: #14161C; border-radius: 8px; }}
QWidget#chatroot {{ background: #0E0F13; border-top-right-radius: {RADIUS}px; border-bottom-right-radius: {RADIUS}px; }}
QPushButton#tabbtn {{ background: transparent; color: {TEXT_3}; border: none; border-radius: 15px;
                      font-family: "{BODY_FAMILY}"; font-size: 13px; font-weight: 600; padding: 0 14px; }}
QPushButton#tabbtn:hover {{ color: {TEXT_2}; }}
QPushButton#tabbtn[on="true"] {{ color: {TEXT}; }}
QPushButton#iconbtn {{ background: transparent; border: none; border-radius: 6px; }}
QPushButton#iconbtn:hover {{ background: #1F232B; }}
QPushButton#chatchip {{ background: #16181E; color: {CHIP_TEXT}; border: 1px solid rgba(255,255,255,0.08);
                        border-radius: 14px; font-family: "{BODY_FAMILY}"; font-size: 12px; padding: 0 11px; }}
QPushButton#chatchip:hover {{ background: #1F232B; border: 1px solid rgba(255,255,255,0.14); }}
QPushButton#chatchip:checked {{ background: {OK_GREEN}; color: #14161C; border: 1px solid {OK_GREEN}; }}
QPlainTextEdit#chatedit {{ background: transparent; color: {TEXT}; border: none; font-family: "{BODY_FAMILY}";
                           font-size: 14px; selection-background-color: {ACCENT}; }}
QLineEdit#chatsearch, QComboBox#chatcombo {{ background: #16181E; border: 1px solid rgba(255,255,255,0.08);
                                             font-size: 13px; padding: 6px 10px; }}
QLineEdit#chatsearch:focus, QComboBox#chatcombo:focus {{ border: 1px solid rgba(255,107,74,0.55); }}
QPushButton#newpill {{ background: {CONTROL}; color: {TEXT}; border: 1px solid rgba(255,255,255,0.10);
                       border-radius: 14px; font-family: "{BODY_FAMILY}"; font-size: 12px; font-weight: 600;
                       padding: 6px 14px; }}
QPushButton#newpill:hover {{ background: #343946; }}
"""


def _label(text: str = "", name: str | None = None, size: int | None = None, weight: int | None = None,
           color: str | None = None, heading: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    style = []
    if heading:
        style.append(f'font-family: "{HEADING_FAMILY}"')
    if size:
        style.append(f"font-size: {size}px")
    if weight:
        style.append(f"font-weight: {weight}")
    if color:
        style.append(f"color: {color}")
    if style:
        lab.setStyleSheet("; ".join(style) + ";")
    if name == "caps":
        f = lab.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
        lab.setFont(f)
    return lab


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def line_icon(kind: str, color: str = TEXT, size: int = 16) -> QIcon:
    """Small stroke icons (title bar, tips, tray menu) - the mockup's inline
    SVGs, redrawn with QPainter on a 16-unit grid."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 16, size / 16)
    pen = QPen(QColor(color), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "minus":
        p.drawLine(QPointF(3, 8), QPointF(13, 8))
    elif kind == "close":
        p.drawLine(QPointF(4, 4), QPointF(12, 12))
        p.drawLine(QPointF(12, 4), QPointF(4, 12))
    elif kind == "window":
        p.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
        p.drawLine(QPointF(2, 6), QPointF(14, 6))
    elif kind == "pause":
        p.drawLine(QPointF(5.5, 3), QPointF(5.5, 13))
        p.drawLine(QPointF(10.5, 3), QPointF(10.5, 13))
    elif kind == "play":
        path = QPainterPath(QPointF(5, 3))
        path.lineTo(13, 8)
        path.lineTo(5, 13)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "gear":
        p.drawEllipse(QPointF(8, 8), 2.2, 2.2)
        for a, b in (((8, 1.8), (8, 3.8)), ((8, 12.2), (8, 14.2)), ((1.8, 8), (3.8, 8)), ((12.2, 8), (14.2, 8)),
                     ((3.6, 3.6), (5, 5)), ((11, 11), (12.4, 12.4)), ((3.6, 12.4), (5, 11)), ((11, 5), (12.4, 3.6))):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif kind == "power":
        p.drawLine(QPointF(8, 2), QPointF(8, 8))
        p.drawArc(QRectF(3, 3.4, 10, 10), 125 * 16, 290 * 16)
    elif kind == "screen":
        p.drawRoundedRect(QRectF(2, 3.2, 12, 8.8), 1.2, 1.2)
        p.drawLine(QPointF(5.6, 14), QPointF(10.4, 14))
    elif kind == "shield":
        path = QPainterPath(QPointF(8, 2))
        path.lineTo(12.8, 4)
        path.lineTo(12.8, 7.6)
        path.cubicTo(12.8, 10.8, 10.7, 13, 8, 14)
        path.cubicTo(5.3, 13, 3.2, 10.8, 3.2, 7.6)
        path.lineTo(3.2, 4)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "mic":
        p.drawRoundedRect(QRectF(5.6, 2, 4.8, 8), 2.4, 2.4)
        p.drawArc(QRectF(3.6, 4, 8.8, 8.8), 180 * 16, 180 * 16)
        p.drawLine(QPointF(8, 12.4), QPointF(8, 14.4))
    elif kind == "note":  # a sheet with three lines ("Minhas notas")
        p.drawRoundedRect(QRectF(3, 2, 10, 12), 1.5, 1.5)
        for y in (5.5, 8, 10.5):
            p.drawLine(QPointF(5.5, y), QPointF(10.5 if y != 10.5 else 8.5, y))
    elif kind == "chat":  # a speech bubble (the Chat view)
        path = QPainterPath(QPointF(2.5, 3.5))
        path.lineTo(13.5, 3.5)
        path.lineTo(13.5, 10.5)
        path.lineTo(7, 10.5)
        path.lineTo(4, 13.2)
        path.lineTo(4, 10.5)
        path.lineTo(2.5, 10.5)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "link":  # an arrow leaving a box (a card that opens in the browser)
        p.drawLine(QPointF(7, 3), QPointF(3, 3))
        p.drawLine(QPointF(3, 3), QPointF(3, 13))
        p.drawLine(QPointF(3, 13), QPointF(13, 13))
        p.drawLine(QPointF(13, 13), QPointF(13, 9))
        p.drawLine(QPointF(8, 8), QPointF(13.5, 2.5))
        p.drawLine(QPointF(9.5, 2.5), QPointF(13.5, 2.5))
        p.drawLine(QPointF(13.5, 2.5), QPointF(13.5, 6.5))
    elif kind == "image":  # a picture: a frame, a sun, a hill
        p.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
        p.drawEllipse(QPointF(5.5, 6.5), 1.2, 1.2)
        path = QPainterPath(QPointF(2.5, 12))
        path.lineTo(6.5, 8.5)
        path.lineTo(9, 10.5)
        path.lineTo(11, 8.5)
        path.lineTo(13.5, 11.5)
        p.drawPath(path)
    elif kind == "copy":  # two sheets (the chat's "Copiar")
        p.drawRoundedRect(QRectF(5.5, 5.5, 8, 8.5), 1.5, 1.5)
        p.drawLine(QPointF(10.5, 3.5), QPointF(10.5, 3))
        path = QPainterPath(QPointF(10.5, 3.2))
        path.lineTo(10.5, 2.5)
        path.lineTo(3, 2.5)
        path.lineTo(3, 11)
        path.lineTo(3.8, 11)
        p.drawPath(path)
    elif kind == "check":  # a tick ("Copiado", "Lembrar" on)
        path = QPainterPath(QPointF(3.2, 8.4))
        path.lineTo(6.6, 11.6)
        path.lineTo(12.8, 4.6)
        p.drawPath(path)
    elif kind in ("chevron_left", "chevron_right"):  # the Build pane's back / next
        x0, x1 = (10, 6) if kind == "chevron_left" else (6, 10)
        p.drawLine(QPointF(x0, 4), QPointF(x1, 8))
        p.drawLine(QPointF(x1, 8), QPointF(x0, 12))
    elif kind == "cube":  # a block (the "Construir" button, the Build pane)
        path = QPainterPath(QPointF(8, 2.2))
        path.lineTo(13.4, 5.1)
        path.lineTo(13.4, 10.9)
        path.lineTo(8, 13.8)
        path.lineTo(2.6, 10.9)
        path.lineTo(2.6, 5.1)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(2.6, 5.1), QPointF(8, 8))
        p.drawLine(QPointF(13.4, 5.1), QPointF(8, 8))
        p.drawLine(QPointF(8, 8), QPointF(8, 13.8))
    p.end()
    return QIcon(pm)
