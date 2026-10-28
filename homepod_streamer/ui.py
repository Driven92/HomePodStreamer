# -*- coding: utf-8 -*-
"""图形界面（PyQt6）—— 深色卡片式投送面板。

设计取向
--------
信息结构参考 TuneBlade：一屏之内看到「设备列表 + 音量 + 延迟预设 + 连接状态」，
不做多页签、不藏设置。视觉参考 iOS/macOS 的「隔空播放」面板与现代桌面音频 App
（Sonos / Spotify Connect）：深色底、单一强调渐变、圆角卡片、状态点。

实现上刻意不引入任何图片资源与第三方主题包：
- 滑块、开关、分段控件、频谱条、图标全部用 QPainter 现画，高 DPI 自适应、不增加体积；
- 只用一个集中调色板（见下方常量），换肤只需改这几个颜色。
"""
import sys
import threading

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from homepod_streamer import autostart, config
from homepod_streamer.streamer import LATENCY_PRESETS, configure_latency, current_latency
from homepod_streamer.version import VERSION_NOTE, version_string

# ---------------------------------------------------------------- 调色板
BG_TOP = "#131722"
BG_BOTTOM = "#0B0E14"
CARD = "#161B26"
CARD_HOVER = "#1B2130"
CARD_ACTIVE = "#1B2436"
BORDER = "#232A38"
BORDER_ACTIVE = "#3B5BDB"
TEXT = "#EEF2F9"
SUB = "#8792A8"
SUB_DIM = "#5F6A7E"
ACCENT = "#5B8CFF"
ACCENT_2 = "#A66BFF"
LIVE = "#3DDC97"
DANGER = "#FF6B6B"

FONT_STACK = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]


# ---------------------------------------------------------------- 图标绘制
def _draw_speaker(p: QPainter, r: QRectF, color: QColor, with_waves=True) -> None:
    """在给定矩形内画一个扬声器图标（箱体 + 喇叭口 + 声波）。"""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    body = QRectF(r.left() + r.width() * 0.06,
                  r.top() + r.height() * 0.30,
                  r.width() * 0.20,
                  r.height() * 0.40)
    p.drawRoundedRect(body, 1.2, 1.2)
    cone = QPainterPath()
    cone.moveTo(body.right(), body.top())
    cone.lineTo(r.left() + r.width() * 0.48, r.top() + r.height() * 0.16)
    cone.lineTo(r.left() + r.width() * 0.48, r.top() + r.height() * 0.84)
    cone.lineTo(body.right(), body.bottom())
    cone.closeSubpath()
    p.drawPath(cone)
    if with_waves:
        pen = QPen(color)
        pen.setWidthF(max(1.1, r.width() * 0.055))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx = r.left() + r.width() * 0.46
        cy = r.center().y()
        for frac in (0.17, 0.30):
            rr = QRectF(cx - r.width() * frac, cy - r.height() * frac,
                        r.width() * frac * 2, r.height() * frac * 2)
            p.drawArc(rr, -52 * 16, 104 * 16)
    p.restore()


def _round_icon(size: int) -> QPixmap:
    """应用图标：深色圆角方块 + 强调渐变扬声器。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(ACCENT))
    grad.setColorAt(1.0, QColor(ACCENT_2))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.26, size * 0.26)
    _draw_speaker(p, QRectF(size * 0.24, size * 0.24, size * 0.52, size * 0.52),
                  QColor("#FFFFFF"))
    p.end()
    return pm


class GradientAvatar(QWidget):
    """圆角渐变方块 + 居中扬声器，用于“正在投送”卡片的左侧图标。"""

    def __init__(self, size=54, radius=16, parent=None):
        super().__init__(parent)
        self._size = size
        self._radius = radius
        self._dim = True
        self.setFixedSize(size, size)

    def set_dim(self, dim: bool):
        if self._dim != dim:
            self._dim = dim
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, self._size, self._size)
        grad = QLinearGradient(0, 0, self._size, self._size)
        if self._dim:
            grad.setColorAt(0.0, QColor("#2A3244"))
            grad.setColorAt(1.0, QColor("#202736"))
        else:
            grad.setColorAt(0.0, QColor(ACCENT))
            grad.setColorAt(1.0, QColor(ACCENT_2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(r, self._radius, self._radius)
        ink = QColor("#FFFFFF") if not self._dim else QColor("#7C879C")
        _draw_speaker(p, QRectF(self._size * 0.26, self._size * 0.26,
                                self._size * 0.48, self._size * 0.48), ink)


class EqBars(QWidget):
    """跳动的频谱条：投送中用动画表示“有声音在走”，空闲时压平成静态。"""

    def __init__(self, bars=5, parent=None):
        super().__init__(parent)
        self._n = bars
        self._levels = [0.18] * bars
        self._t = 0.0
        self._live = False
        self.setFixedSize(bars * 7, 26)
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._tick)

    def set_live(self, live: bool):
        self._live = live
        if live:
            self._timer.start()
        else:
            self._timer.stop()
            self._levels = [0.18] * self._n
            self.update()

    def _tick(self):
        import math

        self._t += 0.22
        for i in range(self._n):
            base = 0.35 + 0.35 * math.sin(self._t * (1.0 + i * 0.23) + i * 1.1)
            wob = 0.30 * math.sin(self._t * 2.7 + i * 2.3)
            self._levels[i] = max(0.18, min(1.0, base + wob))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = 3.0
        gap = (self.width() - self._n * w) / max(1, self._n - 1)
        h = self.height()
        for i, lv in enumerate(self._levels):
            bh = max(4.0, h * lv)
            x = i * (w + gap)
            r = QRectF(x, (h - bh) / 2, w, bh)
            if self._live:
                grad = QLinearGradient(0, r.top(), 0, r.bottom())
                grad.setColorAt(0.0, QColor(ACCENT_2))
                grad.setColorAt(1.0, QColor(ACCENT))
                col = QBrush(grad)
            else:
                col = QBrush(QColor("#3A4459"))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, w / 2, w / 2)


class IconButton(QPushButton):
    """自绘图标按钮（刷新 / 最小化 / 关闭 / 隐藏），悬停时高亮。"""

    def __init__(self, kind: str, size=28, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        tips = {
            "refresh": "重新扫描设备",
            "min": "最小化到任务栏",
            "close": "关闭窗口（最小化到托盘）",
            "hide": "隐藏到托盘",
        }
        if kind in tips:
            self.setToolTip(tips[kind])

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 26)))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)
        c = QColor(TEXT) if self._hover else QColor(SUB)
        pen = QPen(c)
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = r.center().x(), r.center().y()
        if self._kind == "close":
            d = 4.6
            p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
            p.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))
        elif self._kind == "min":
            p.drawLine(QPointF(cx - 5.0, cy + 3.0), QPointF(cx + 5.0, cy + 3.0))
        elif self._kind == "refresh":
            arc = QRectF(cx - 5.4, cy - 5.4, 10.8, 10.8)
            p.drawArc(arc, 40 * 16, 280 * 16)
            tip = QPointF(cx + 4.6, cy - 4.4)
            p.drawLine(tip, QPointF(tip.x() - 4.2, tip.y() - 0.4))
            p.drawLine(tip, QPointF(tip.x() + 0.2, tip.y() + 4.2))
        elif self._kind == "hide":
            p.drawRoundedRect(QRectF(cx - 5.2, cy - 2.6, 10.4, 7.2), 2, 2)
            p.drawLine(QPointF(cx - 3.0, cy + 4.6), QPointF(cx + 3.0, cy + 4.6))


class ToggleSwitch(QWidget):
    """开关：圆角轨道 + 滑动圆点，切带有 140ms 缓动。"""

    toggled = pyqtSignal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @pyqtProperty(float)
    def pos(self):
        return self._pos

    @pos.setter
    def pos(self, v):
        self._pos = v
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool, emit: bool = False):
        on = bool(on)
        if on == self._checked:
            return
        self._checked = on
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        if emit:
            self.toggled.emit(on)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked, emit=True)
            e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        if self._checked:
            grad = QLinearGradient(0, 0, r.width(), 0)
            grad.setColorAt(0.0, QColor(ACCENT))
            grad.setColorAt(1.0, QColor(ACCENT_2))
            p.setBrush(QBrush(grad))
        else:
            p.setBrush(QBrush(QColor("#2B3242")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = 3 + self._pos * (r.width() - d - 6)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawEllipse(QRectF(x, 3, d, d))


class VolumeSlider(QSlider):
    """自绘音量条：圆角轨道 + 渐变已填充段 + 带光晕的圆点。"""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 100)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background:transparent;border:none;")

    def _track(self) -> QRectF:
        return QRectF(10, self.height() / 2 - 3, self.width() - 20, 6)

    def _value_from_x(self, x: float) -> int:
        t = self._track()
        frac = (x - t.left()) / max(1.0, t.width())
        frac = min(1.0, max(0.0, frac))
        return int(round(self.minimum() + frac * (self.maximum() - self.minimum())))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._track()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#262E3E")))
        p.drawRoundedRect(t, 3, 3)

        span = max(1, self.maximum() - self.minimum())
        frac = (self.value() - self.minimum()) / span
        fill = QRectF(t.left(), t.top(), max(0.0, t.width() * frac), t.height())
        if fill.width() > 1:
            grad = QLinearGradient(t.left(), 0, t.right(), 0)
            grad.setColorAt(0.0, QColor(ACCENT))
            grad.setColorAt(1.0, QColor(ACCENT_2))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(fill, 3, 3)

        cx = t.left() + t.width() * frac
        cy = t.center().y()
        halo = QRadialGradient(QPointF(cx, cy), 13)
        halo.setColorAt(0.0, QColor(91, 140, 255, 110))
        halo.setColorAt(1.0, QColor(91, 140, 255, 0))
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(cx, cy), 13, 13)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawEllipse(QPointF(cx, cy), 7.0, 7.0)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_from_x(e.position().x()))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_from_x(e.position().x()))
            e.accept()
        else:
            super().mouseMoveEvent(e)


class Segmented(QWidget):
    """分段选择器（延迟预设）。选中项为强调色胶囊，宽度均分。"""

    changed = pyqtSignal(object)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName("Seg")
        # 自定义 QWidget 子类要用样式表画背景，必须打开 WA_StyledBackground
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._opts = list(options)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = []
        for i, (label, _val) in enumerate(self._opts):
            b = QPushButton(label, self)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(28)
            b.clicked.connect(lambda _c=False, idx=i: self._on_click(idx))
            self._group.addButton(b, i)
            lay.addWidget(b, 1)
            self._buttons.append(b)
        self.setStyleSheet(
            f"""
            QWidget#Seg{{background:#11151E;border:1px solid {BORDER};border-radius:10px;}}
            QPushButton{{background:transparent;border:none;border-radius:8px;
                        color:{SUB};font-size:11px;}}
            QPushButton:hover{{color:{TEXT};background:rgba(255,255,255,0.04);}}
            QPushButton:checked{{color:#FFFFFF;font-weight:600;
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {ACCENT}, stop:1 {ACCENT_2});}}
            """
        )

    def _on_click(self, idx: int):
        self.changed.emit(self._opts[idx][1])

    def set_value(self, sec: float):
        best = min(range(len(self._opts)),
                   key=lambda i: abs(float(self._opts[i][1]) - float(sec)))
        self._buttons[best].setChecked(True)


class DeviceDelegate(QStyledItemDelegate):
    """设备行：图标 + 名称/IP + 右侧圆形操作按钮（▶ 连接 / ■ 停止）。"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, p: QPainter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole) or {}
        active = bool(data.get("active"))
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        if active:
            p.setBrush(QBrush(QColor(CARD_ACTIVE)))
            p.drawRoundedRect(r, 12, 12)
            p.setPen(QPen(QColor(59, 91, 219, 150), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        elif hovered or selected:
            p.setBrush(QBrush(QColor(CARD_HOVER)))
            p.drawRoundedRect(r, 12, 12)
        else:
            p.setBrush(QBrush(QColor("#141925")))
            p.drawRoundedRect(r, 12, 12)

        # 左侧：圆形扬声器图标
        icon_r = QRectF(r.left() + 12, r.center().y() - 15, 30, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(91, 140, 255, 40) if active else QColor(255, 255, 255, 14)))
        p.drawEllipse(icon_r)
        _draw_speaker(p, icon_r.adjusted(8, 8, -8, -8),
                      QColor(ACCENT) if active else QColor("#7C879C"), with_waves=False)

        # 文本
        name = str(data.get("name") or "")
        addr = str(data.get("address") or "")
        f_name = QFont(option.font)
        f_name.setPointSizeF(10.0)
        f_name.setWeight(QFont.Weight.DemiBold)
        p.setFont(f_name)
        p.setPen(QPen(QColor(TEXT)))
        p.drawText(QRectF(icon_r.right() + 12, r.top() + 10, r.width() - 130, 18),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), name)
        f_sub = QFont(option.font)
        f_sub.setPointSizeF(8.4)
        p.setFont(f_sub)
        p.setPen(QPen(QColor(LIVE if active else SUB_DIM)))
        p.drawText(QRectF(icon_r.right() + 12, r.top() + 29, r.width() - 130, 16),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "正在投送" if active else addr)

        # 右侧：圆形操作按钮
        btn_r = QRectF(r.right() - 46, r.center().y() - 15, 30, 30)
        if active:
            p.setBrush(QBrush(QColor(61, 220, 151, 30)))
            p.setPen(QPen(QColor(61, 220, 151, 120), 1))
            p.drawEllipse(btn_r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(LIVE)))
            p.drawRoundedRect(QRectF(btn_r.center().x() - 4.5, btn_r.center().y() - 4.5,
                                     9, 9), 2, 2)
        else:
            p.setBrush(QBrush(QColor(255, 255, 255, 16)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(btn_r)
            tri = QPainterPath()
            cx, cy = btn_r.center().x() + 1.5, btn_r.center().y()
            tri.moveTo(cx - 4.0, cy - 5.5)
            tri.lineTo(cx + 6.0, cy)
            tri.lineTo(cx - 4.0, cy + 5.5)
            tri.closeSubpath()
            p.setBrush(QBrush(QColor(ACCENT)))
            p.drawPath(tri)
        p.restore()


class DeviceList(QListWidget):
    """点右侧圆形按钮即连接/断开；点其它位置只做选中。"""

    actionTriggered = pyqtSignal(int)

    ACTION_ZONE = 62

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSpacing(2)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet(
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{border:none;}"
        )

    def mouseReleaseEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if (idx.isValid() and e.button() == Qt.MouseButton.LeftButton
                and e.position().x() >= self.viewport().width() - self.ACTION_ZONE):
            self.setCurrentIndex(idx)
            self.actionTriggered.emit(idx.row())
            e.accept()
            return
        super().mouseReleaseEvent(e)
        self.viewport().update()

    def mouseDoubleClickEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid():
            self.actionTriggered.emit(idx.row())
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


# ---------------------------------------------------------------- 主窗口
class MainWindow(QWidget):
    devices_ready = pyqtSignal(list)
    status_update = pyqtSignal(str)
    tooltip_update = pyqtSignal(str)
    # 推流是异步的：连接成功/失败都由守护事件循环回调过来，再经信号回到 GUI 线程
    stream_connected = pyqtSignal()
    stream_failed = pyqtSignal(str)
    # 托盘里"连接上次设备"要先扫描（会阻塞几秒），放到线程里做完再回 GUI 线程连接
    connect_request = pyqtSignal(object)
    restart_request = pyqtSignal()

    TEST_SECONDS = 8   # 有声测试播放时长

    def __init__(self, minimized=False):
        super().__init__()
        from homepod_streamer import HomePodStreamer

        self.s = HomePodStreamer()
        self.s.set_callbacks(on_error=self.stream_failed.emit,
                             on_connected=self.stream_connected.emit)
        self.devices = []
        self.active_name = None
        self._pending_name = None      # 正在连接的设备（握手未完成）
        self._capture_label = ""       # 采集来源的名字，排障时能一眼看出抓错设备
        self._testing = False
        self.tray = None
        self.tray_auto_act = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(f"HomePod Streamer  {version_string()}")
        self.setWindowIcon(QIcon(_round_icon(64)))
        self._apply_font()

        self._build_ui()
        self._build_tray()

        self.devices_ready.connect(self._on_devices)
        self.status_update.connect(self._on_status)
        self.tooltip_update.connect(self._on_tooltip)
        self.stream_connected.connect(self._on_stream_connected)
        self.stream_failed.connect(self._on_stream_failed)
        self.connect_request.connect(self._start_stream)
        self.restart_request.connect(self._on_restart_request)

        self._test_timer = QTimer(self)
        self._test_timer.setSingleShot(True)
        self._test_timer.timeout.connect(self._end_test)

        self.chk_auto.setChecked(autostart.is_enabled())
        self.slider.blockSignals(True)
        self.slider.setValue(int(config.load_config().get("volume", 35)))
        self.slider.blockSignals(False)
        self._update_vol_label(self.slider.value())
        self.lat_seg.set_value(current_latency())

        self.setFixedWidth(500)
        self._fit_window()

        self._do_scan()
        if minimized:
            self.hide()
            saved = config.load_config().get("last_device")
            if saved:
                QTimer.singleShot(800, lambda: self._connect_to(saved))

    # ---------- 基础 ----------
    def _apply_font(self):
        f = QFont()
        f.setFamilies(FONT_STACK)
        f.setPointSizeF(9.5)
        QApplication.instance().setFont(f)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.setClipPath(path)

        bg = QLinearGradient(0, 0, 0, r.height())
        bg.setColorAt(0.0, QColor(BG_TOP))
        bg.setColorAt(1.0, QColor(BG_BOTTOM))
        p.fillPath(path, QBrush(bg))

        # 右上角淡淡的强调光晕，避免整块纯色显得死板
        glow = QRadialGradient(QPointF(r.width() * 0.86, -r.height() * 0.06),
                               r.width() * 0.75)
        glow.setColorAt(0.0, QColor(91, 140, 255, 46))
        glow.setColorAt(1.0, QColor(91, 140, 255, 0))
        p.fillPath(path, QBrush(glow))
        glow2 = QRadialGradient(QPointF(r.width() * 0.05, r.height() * 1.02),
                                r.width() * 0.6)
        glow2.setColorAt(0.0, QColor(166, 107, 255, 30))
        glow2.setColorAt(1.0, QColor(166, 107, 255, 0))
        p.fillPath(path, QBrush(glow2))

        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)

    # ---------- 布局自适应 ----------
    @staticmethod
    def _list_height(n: int) -> int:
        """设备列表高度：0 个走空状态提示，1~3 个按行高撑开，更多则滚动。"""
        if n <= 0:
            return 104
        return min(3, n) * 60 + 6

    def _fit_window(self):
        """按设备数量调整窗口高度（无边框窗口不会自动长高，需要手动设固定高度）。"""
        self.stack.setFixedHeight(self._list_height(len(self.devices)))
        self.adjustSize()
        self.setFixedSize(500, self.height())

    # ---------- 拖拽（无边框窗口） ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 56:
            wh = self.windowHandle()
            if wh is not None:
                wh.startSystemMove()
                e.accept()
                return
        super().mousePressEvent(e)

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(12)

        # 标题栏
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(_round_icon(24))
        logo.setFixedSize(24, 24)
        title_row.addWidget(logo)
        name = QLabel("HomePod Streamer")
        name.setStyleSheet(f"color:{TEXT};font-size:12.5px;font-weight:600;")
        title_row.addWidget(name)
        ver = QLabel(version_string())
        ver.setStyleSheet(
            f"QLabel{{color:{SUB};background:rgba(255,255,255,0.06);"
            f"border-radius:8px;padding:1px 7px;font-size:10px;font-weight:600;}}"
        )
        title_row.addWidget(ver)
        title_row.addStretch(1)
        b_min = IconButton("min")
        b_close = IconButton("close")
        b_min.clicked.connect(self.showMinimized)
        b_close.clicked.connect(self.close)
        title_row.addWidget(b_min)
        title_row.addWidget(b_close)
        root.addLayout(title_row)

        # 正在投送卡片
        self.hero = QFrame()
        self.hero.setObjectName("Hero")
        self.hero.setStyleSheet(
            f"QFrame#Hero{{background:{CARD};border:1px solid {BORDER};border-radius:14px;}}"
        )
        hero_lay = QHBoxLayout(self.hero)
        hero_lay.setContentsMargins(14, 14, 14, 14)
        hero_lay.setSpacing(12)
        self.avatar = GradientAvatar(48, 14)
        hero_lay.addWidget(self.avatar)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.hero_title = QLabel("待连接")
        self.hero_title.setStyleSheet(f"color:{TEXT};font-size:13px;font-weight:600;")
        self.hero_sub = QLabel("选择一个设备开始投送")
        self.hero_sub.setStyleSheet(f"color:{SUB};font-size:11px;")
        text_col.addWidget(self.hero_title)
        text_col.addWidget(self.hero_sub)
        hero_lay.addLayout(text_col, 1)
        self.eq = EqBars()
        hero_lay.addWidget(self.eq, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.hero)

        # 设备区
        dev_head = QHBoxLayout()
        dev_head.setSpacing(6)
        lbl = QLabel("可用设备")
        lbl.setStyleSheet(f"color:{SUB};font-size:10.5px;font-weight:600;")
        dev_head.addWidget(lbl)
        self.count_lbl = QLabel("0")
        self.count_lbl.setStyleSheet(
            f"QLabel{{color:{SUB};background:rgba(255,255,255,0.07);"
            f"border-radius:7px;padding:0px 6px;font-size:10px;}}"
        )
        dev_head.addWidget(self.count_lbl)
        dev_head.addStretch(1)
        self.b_refresh = IconButton("refresh")
        self.b_refresh.clicked.connect(self._do_scan)
        dev_head.addWidget(self.b_refresh)
        root.addLayout(dev_head)

        self.list = DeviceList()
        self.list.setItemDelegate(DeviceDelegate(self.list))
        self.list.actionTriggered.connect(self._on_row_action)
        self.empty = QLabel("未发现设备\n确认与 HomePod 在同一 Wi-Fi，然后点右上角刷新")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color:{SUB_DIM};font-size:11px;")
        self.stack = QWidget()
        self.stack_lay = QStackedLayout(self.stack)
        self.stack_lay.setContentsMargins(0, 0, 0, 0)
        self.stack_lay.addWidget(self.list)
        self.stack_lay.addWidget(self.empty)
        self.stack.setFixedHeight(self._list_height(0))
        self.stack_lay.setCurrentWidget(self.list)
        root.addWidget(self.stack)

        # 设置卡：音量 + 延迟
        card = QFrame()
        card.setObjectName("Card")
        card.setStyleSheet(
            f"QFrame#Card{{background:{CARD};border:1px solid {BORDER};border-radius:14px;}}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        vol_lbl = QLabel("音量")
        vol_lbl.setStyleSheet(f"color:{TEXT};font-size:11.5px;font-weight:600;")
        vol_row.addWidget(vol_lbl)
        vol_row.addStretch(1)
        self.vol_val = QLabel("35%")
        self.vol_val.setStyleSheet(f"color:{SUB};font-size:11px;")
        self.vol_val.setFixedWidth(36)
        self.vol_val.setAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
        vol_row.addWidget(self.vol_val)
        cl.addLayout(vol_row)

        self.slider = VolumeSlider()
        self._vol_timer = QTimer(self)
        self._vol_timer.setSingleShot(True)
        self._vol_timer.setInterval(120)
        self._vol_timer.timeout.connect(self._apply_vol)
        self.slider.valueChanged.connect(self._on_vol)
        cl.addWidget(self.slider)

        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{BORDER};")
        cl.addWidget(div)

        lat_row = QHBoxLayout()
        lat_row.setSpacing(6)
        lat_lbl = QLabel("延迟缓冲")
        lat_lbl.setStyleSheet(f"color:{TEXT};font-size:11.5px;font-weight:600;")
        lat_row.addWidget(lat_lbl)
        self.lat_hint = QLabel("越小延迟越低")
        self.lat_hint.setStyleSheet(f"color:{SUB_DIM};font-size:10px;")
        lat_row.addWidget(self.lat_hint)
        lat_row.addStretch(1)
        cl.addLayout(lat_row)

        self.lat_seg = Segmented([(f"{sec:g}s", sec) for _label, sec in LATENCY_PRESETS])
        self.lat_seg.setFixedHeight(34)
        self.lat_seg.changed.connect(self._on_latency_changed)
        cl.addWidget(self.lat_seg)
        root.addWidget(card)

        # 底部：开机自启 + 辅助操作
        bot = QHBoxLayout()
        bot.setSpacing(8)
        self.chk_auto = ToggleSwitch(autostart.is_enabled())
        self.chk_auto.toggled.connect(self._on_auto_toggle)
        bot.addWidget(self.chk_auto)
        auto_lbl = QLabel("开机自动启动")
        auto_lbl.setStyleSheet(f"color:{SUB};font-size:11px;")
        bot.addWidget(auto_lbl)
        bot.addStretch(1)
        self.b_test = QPushButton("有声测试")
        self.b_test.setToolTip(
            f"向选中的音箱播放 {self.TEST_SECONDS} 秒提示音。\n"
            "它不经过系统声音采集——能听到就说明投送链路正常，"
            "听不到则是连接或音箱本身的问题。"
        )
        self.b_hide = QPushButton("隐藏到托盘")
        for b in (self.b_test, self.b_hide):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;outline:none;"
                f"color:{SUB};font-size:10.5px;padding:2px 4px;}}"
                f"QPushButton:hover{{color:{TEXT};}}"
            )
            bot.addWidget(b)
        self.b_test.clicked.connect(self._do_test)
        self.b_hide.clicked.connect(self.hide)
        root.addLayout(bot)

        # 状态行
        st = QHBoxLayout()
        st.setSpacing(0)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(f"color:{SUB_DIM};font-size:10px;")
        st.addWidget(self.status_label, 1)
        self.ver_footer = QLabel(
            f"{version_string()} · "
            + ("打包版" if getattr(sys, "frozen", False) else "源码版")
        )
        self.ver_footer.setStyleSheet(f"color:{SUB_DIM};font-size:10px;")
        self.ver_footer.setToolTip(f"HomePod Streamer {version_string()}\n{VERSION_NOTE}")
        st.addWidget(self.ver_footer)
        root.addLayout(st)

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(QIcon(_round_icon(64)), self)
        tray.setToolTip(f"HomePod Streamer {version_string()}")

        menu = QMenu()
        show_act = menu.addAction("显示窗口")
        show_act.triggered.connect(self._show_window)
        conn_act = menu.addAction("连接上次设备")
        conn_act.triggered.connect(
            lambda: self._connect_to(config.load_config().get("last_device"))
        )
        disc_act = menu.addAction("断开连接")
        disc_act.triggered.connect(self._do_disconnect)
        menu.addSeparator()
        auto_act = menu.addAction("开机自动启动")
        auto_act.setCheckable(True)
        auto_act.setChecked(autostart.is_enabled())
        auto_act.toggled.connect(self._on_auto_toggle)
        self.tray_auto_act = auto_act
        menu.addSeparator()
        about_act = menu.addAction(f"关于（{version_string()}）")
        about_act.triggered.connect(self._show_about)
        quit_act = menu.addAction("退出")
        quit_act.triggered.connect(self._quit)

        tray.activated.connect(self._on_tray_activated)
        tray.setContextMenu(menu)
        tray.show()
        self.tray = tray

    # ---------- 交互 ----------
    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_about(self):
        exe = sys.executable if getattr(sys, "frozen", False) else "（源码运行）"
        QMessageBox.about(
            self, "关于 HomePod Streamer",
            f"<b>HomePod Streamer {version_string()}</b><br>{VERSION_NOTE}<br><br>"
            f"当前延迟缓冲：{current_latency():g} 秒<br>程序位置：{exe}",
        )

    # ---------- 扫描 / 连接 ----------
    def _do_scan(self):
        self.status_update.emit("扫描中…")
        self.tooltip_update.emit("扫描中…")

        def work():
            try:
                found = self.s.scan(timeout=5)
            except Exception as e:  # noqa: BLE001
                self.status_update.emit(f"扫描失败：{e}")
                return
            self.devices_ready.emit(found)
            self.status_update.emit(f"发现 {len(found)} 个设备")
            self.tooltip_update.emit(f"发现 {len(found)} 个设备")

        threading.Thread(target=work, daemon=True).start()

    def _on_devices(self, devs):
        self.devices = devs
        self.count_lbl.setText(str(len(devs)))
        self.list.clear()
        for d in devs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, {
                "name": d["name"], "address": d["address"],
                "active": self.active_name is not None
                and self.active_name in d["name"],
            })
            item.setSizeHint(QSize(0, 58))
            self.list.addItem(item)
        self.stack_lay.setCurrentWidget(self.list if devs else self.empty)
        if devs and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._sync_hero()
        self._fit_window()

    def _on_row_action(self, row: int):
        if not (0 <= row < len(self.devices)):
            return
        if self._testing:
            self._end_test("已停止测试音")
        target = self.devices[row]
        if self.active_name and self.active_name in target["name"]:
            self._do_disconnect()
        else:
            if self.active_name:            # 换设备：先断旧的，否则会撞"已有推流"
                self._do_disconnect()
            self._start_stream(target)

    def _connect_to(self, name):
        """扫描并连接指定名字的设备（扫描放线程里，避免窗口假死）。"""
        self.status_update.emit("正在查找设备…")

        def work():
            try:
                found = self.s.scan(timeout=5)
            except Exception as e:  # noqa: BLE001
                self.status_update.emit(f"扫描失败：{e}")
                return
            target = next((d for d in found if name and name in d["name"]), None)
            if target is None:
                target = found[0] if found else None
            if target is None:
                self.status_update.emit("未找到可连接设备")
                return
            if found:                    # 顺便把设备列表刷出来
                self.devices_ready.emit(found)
            self.connect_request.emit(target)

        threading.Thread(target=work, daemon=True).start()

    def _start_stream(self, target, quiet=False):
        from homepod_streamer import WasapiLoopbackCapture

        name = target["name"]
        try:
            capture = WasapiLoopbackCapture()
        except Exception as e:  # noqa: BLE001
            msg = f"打不开系统音频采集：{e}"
            self.status_update.emit(msg)
            if not quiet:
                QMessageBox.critical(self, "采集失败", msg)
            return
        try:
            self.s.set_volume(int(self.slider.value()))
            # 必须**先**记下待连接设备再启动：连接有时几十毫秒就完成，
            # 回调回到 GUI 线程时若 _pending_name 还是空的，就会漏掉"已连接"状态。
            self._pending_name = name
            self._testing = False
            self._capture_label = capture.source_name
            self.s.start_stream(target["conf"], capture.generator(), capture=capture)
        except Exception as e:  # noqa: BLE001
            self._pending_name = None
            try:
                capture.stop()
            except Exception:  # noqa: BLE001
                pass
            self.status_update.emit(f"连接失败：{e}")
            self._sync_hero()
            if not quiet:
                QMessageBox.critical(self, "连接失败", str(e))
            return
        self._capture_label = capture.source_name
        if self._pending_name:              # 一般此时还在握手；极快完成的情况已被回调处理过
            self.status_update.emit(f"正在连接 {name}…")
            self.tooltip_update.emit(f"正在连接 {name}…")
        config.update(last_device=name)
        self._sync_hero()

    def _on_stream_connected(self):
        if self._testing:
            # 测试音不占用 active_name，避免把设备行标成"正在投送"
            if self._pending_name:
                self.status_update.emit(
                    f"正在向 {self._pending_name} 播放测试音（{self.TEST_SECONDS} 秒）"
                )
            self._sync_hero()
            return
        if self._pending_name:
            self.active_name, self._pending_name = self._pending_name, None
        if self.active_name:
            msg = f"正在投送到 {self.active_name}"
            if self._capture_label:
                msg += f" · 采集：{self._capture_label}"
            self.status_update.emit(msg)
            self.tooltip_update.emit(f"正在投送到 {self.active_name}")
        self._sync_hero()

    def _on_stream_failed(self, msg: str):
        """连接/推流出错：必须把界面从"正在投送"拽回空闲，并把原因显示出来。"""
        self._pending_name = None
        self.active_name = None
        self._testing = False
        self._test_timer.stop()
        self.status_update.emit(msg)
        self.tooltip_update.emit(f"投送失败：{msg}")
        self._sync_hero()
        visible = self.isVisible() and not self.isMinimized()
        if visible:
            QMessageBox.warning(self, "投送失败", msg)
        elif self.tray is not None:
            self.tray.showMessage("投送失败", msg,
                                  QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _do_disconnect(self):
        self.s.stop_stream()
        self.active_name = None
        self._pending_name = None
        self._testing = False
        self._test_timer.stop()
        self.status_update.emit("已断开")
        self.tooltip_update.emit("已断开")
        self._sync_hero()

    def _do_test(self):
        """有声测试：绕开系统采集，直接向音箱播一段提示音。"""
        if self._testing:
            self._end_test("已停止测试音")
            return
        row = self.list.currentRow()
        if not (0 <= row < len(self.devices)):
            self.status_update.emit("请先在列表中选择一个设备")
            return
        from homepod_streamer import tone_generator

        target = self.devices[row]
        try:
            self.s.stop_stream()
        except Exception:  # noqa: BLE001
            pass
        self.active_name = None
        try:
            self.s.set_volume(int(self.slider.value()))
            # 同 _start_stream：回调可能比这行还快，先记状态再启动
            self._pending_name = target["name"]
            self._testing = True
            self.s.start_stream(target["conf"], tone_generator())
        except Exception as e:  # noqa: BLE001
            self._pending_name = None
            self._testing = False
            self.status_update.emit(f"测试失败：{e}")
            self._sync_hero()
            return
        self.status_update.emit(
            f"正在向 {target['name']} 播放测试音（{self.TEST_SECONDS} 秒）"
        )
        self.tooltip_update.emit(f"播放测试音：{target['name']}")
        self._test_timer.start(int(self.TEST_SECONDS * 1000))
        self._sync_hero()

    def _end_test(self, msg: str = "测试音播放完毕"):
        self._test_timer.stop()
        if not self._testing:
            return
        self._testing = False
        self._pending_name = None
        try:
            self.s.stop_stream()
        except Exception:  # noqa: BLE001
            pass
        self.active_name = None
        self.status_update.emit(msg)
        self.tooltip_update.emit("就绪")
        self._sync_hero()

    def _sync_hero(self):
        """把“正在投送”卡片、设备行高亮、频谱动画同步到当前状态。"""
        name = self.active_name
        if self._testing:
            target = self._pending_name or name or ""
            self.hero_title.setText("正在播放测试音"
                                    + (f" → {target}" if target else ""))
            self.hero_sub.setText("能听到提示音说明投送链路正常")
            self.avatar.set_dim(False)
            self.eq.set_live(True)
        elif self._pending_name:
            self.hero_title.setText(f"正在连接 {self._pending_name}…")
            self.hero_sub.setText("正在与音箱握手，约 1~2 秒")
            self.avatar.set_dim(False)
            self.eq.set_live(False)
        elif name:
            target = next((d for d in self.devices if name in d["name"]), None)
            addr = target["address"] if target else ""
            self.hero_title.setText(f"正在投送到 {name}")
            self.hero_sub.setText(addr or "已建立 AirPlay 会话")
            self.avatar.set_dim(False)
            self.eq.set_live(True)
        else:
            self.hero_title.setText("待连接")
            self.hero_sub.setText("点设备右侧 ▶ 开始投送" if self.devices
                                  else "选择一个设备开始投送")
            self.avatar.set_dim(True)
            self.eq.set_live(False)
        # 行内的“是否正在投送”标记必须跟着状态刷新，否则高亮会一直停在扫描时的值
        for i in range(self.list.count()):
            item = self.list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            data["active"] = bool(name and name in str(data.get("name", "")))
            item.setData(Qt.ItemDataRole.UserRole, data)
        self.list.viewport().update()

    # ---------- 音量 / 延迟 / 自启 ----------
    def _on_vol(self, v):
        self._update_vol_label(v)
        self._vol_timer.start()

    def _update_vol_label(self, v):
        self.vol_val.setText(f"{int(v)}%")

    def _apply_vol(self):
        v = int(self.slider.value())
        self.s.set_volume(v)
        config.update(volume=v)

    def _on_latency_changed(self, sec):
        sec = float(sec)
        configure_latency(sec)
        config.update(latency=sec)
        self.lat_seg.set_value(sec)      # 程序内改值（如 --latency）时同步选中态
        if self.active_name:
            self.status_update.emit(f"延迟缓冲 {sec:g}s，正在重连生效…")
            threading.Thread(target=self._restart_stream, daemon=True).start()
        else:
            self.status_update.emit(f"延迟缓冲已设为 {sec:g}s")

    def _restart_stream(self):
        """在工作线程里断开（要等 1~3 秒），再回 GUI 线程重连，避免界面卡住。"""
        try:
            self.s.stop_stream()
        except Exception as e:  # noqa: BLE001
            self.status_update.emit(f"应用延迟设置失败：{e}")
            return
        self.restart_request.emit()

    def _on_restart_request(self):
        target = next((d for d in self.devices
                       if self.active_name and self.active_name in d["name"]), None)
        if target is None:
            self._sync_hero()
            return
        self._start_stream(target, quiet=True)

    def _on_auto_toggle(self, want):
        want = bool(want)
        try:
            autostart.set_enabled(want)
            config.update(autostart=want)
            self.status_update.emit(f"开机自启已{'启用' if want else '禁用'}")
        except Exception as e:  # noqa: BLE001
            self.status_update.emit(f"设置开机自启失败：{e}")
        self._sync_autostart_ui(want)

    def _sync_autostart_ui(self, want):
        self.chk_auto.setChecked(want)
        if self.tray_auto_act is not None:
            self.tray_auto_act.blockSignals(True)
            self.tray_auto_act.setChecked(want)
            self.tray_auto_act.blockSignals(False)

    # 状态行里出现这些词就标红，避免"没声音也没提示"的静默失败
    ERROR_WORDS = ("失败", "错误", "中断", "无法", "打不开")

    def _on_status(self, msg):
        self.status_label.setText(msg)
        err = any(w in msg for w in self.ERROR_WORDS)
        self.status_label.setStyleSheet(
            f"color:{DANGER if err else SUB_DIM};font-size:10px;"
        )

    def _on_tooltip(self, msg):
        if self.tray is not None:
            self.tray.setToolTip(msg)

    def _quit(self):
        self.s.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.tray is not None:
            self.hide()
            event.ignore()
        else:
            self.s.close()
            event.accept()


def run_gui(minimized=False):
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow(minimized=minimized)
    if not minimized:
        w.show()
    sys.exit(app.exec())
