"""A small, native, liquid-glass quota monitor. No credentials enter the UI."""
from __future__ import annotations

import math
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont,
    QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QToolButton, QWidget

from .windows import enable_glass, is_startup_enabled, load_settings, save_settings, toggle_startup
from .glass import draw_liquid_glass
from .presentation import select_display_window

PALETTE = {
    "text": QColor("#f1f5f9"),
    "secondary": QColor(218, 229, 241, 205),
    "muted": QColor(190, 207, 224, 165),
    "mint": QColor("#a6dec8"),
    "track": QColor(225, 238, 250, 30),
    "progress_a": QColor("#77b8ed"),
    "progress_b": QColor("#8bd5d0"),
    "divider": QColor(226, 238, 249, 26),
}
TEXT = PALETTE["text"]
MUTED = PALETTE["muted"]
MINT = PALETTE["mint"]
TYPE = {"brand": 18, "value": 68, "percent": 27, "label": 13,
        "status": 11, "reset": 14, "meta": 10, "compact": 12}
SPACE = {"card_left": 25, "card_right": 25}
BEIJING = timezone(timedelta(hours=8))


def font(size: int, weight=QFont.Weight.Normal, family="Microsoft YaHei UI") -> QFont:
    result = QFont(family)
    result.setPixelSize(size)
    result.setWeight(weight)
    return result


def draw_text(p: QPainter, x: float, y: float, text: str, size=12, color=TEXT,
              weight=QFont.Weight.Normal, width=340, height=28, align=Qt.AlignmentFlag.AlignLeft):
    p.setFont(font(size, weight))
    p.setPen(color)
    p.drawText(QRectF(x, y, width, height), align | Qt.AlignmentFlag.AlignVCenter |
               Qt.TextFlag.TextSingleLine, text)


def rounded(p: QPainter, rect: QRectF, radius: float, fill, border=None):
    p.setBrush(fill)
    p.setPen(QPen(border, 1) if border else Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, radius, radius)


def icon(name: str, color=TEXT) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    if name == "pin":
        p.drawLine(13, 7, 21, 10)
        p.drawLine(14, 8, 12, 16)
        p.drawLine(20, 10, 18, 18)
        p.drawLine(11, 15, 20, 18)
        p.drawLine(16, 18, 12, 25)
    elif name in ("refresh", "sync"):
        p.drawArc(QRectF(7.5, 7.5, 17, 17), 38 * 16, 290 * 16)
        p.drawLine(24, 8, 24, 14)
        p.drawLine(24, 14, 18, 14)
        if name == "sync":
            p.drawLine(8, 24, 8, 18)
            p.drawLine(8, 18, 14, 18)
    elif name == "more":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        for x in (9, 16, 23):
            p.drawEllipse(QRectF(x - 1.5, 14.5, 3, 3))
    else:
        p.drawLine(9, 12, 14, 16)
        p.drawLine(14, 16, 9, 20)
        p.drawLine(18, 20, 24, 20)
    p.end()
    return QIcon(pixmap)


def app_icon() -> QIcon:
    return QIcon(brand_pixmap(128))


def brand_pixmap(size: int) -> QPixmap:
    """Use the user's original mark, cropping its wallpaper to the app tile."""
    path = Path(__file__).resolve().parent.parent / "assets" / "codex-logo.png"
    source = QPixmap(str(path))
    if source.isNull():
        return icon("terminal", MINT).pixmap(size, size)
    # The supplied artwork has a 405px canvas around a roughly 335px icon tile.
    tile = source.copy(24, 12, 350, 350)
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    p = QPainter(result)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), size * .30, size * .30)
    p.setClipPath(clip)
    p.drawPixmap(QRectF(0, 0, size, size), tile, QRectF(tile.rect()))
    p.end()
    return result


def demo_usage() -> dict:
    now = int(time.time())
    def window(used, seconds, reset):
        return {"used_percent": used, "remaining_percent": 100 - used,
                "limit_window_seconds": seconds, "reset_at": reset,
                "reset_at_beijing": datetime.fromtimestamp(reset, BEIJING).isoformat(),
                "reset_after_seconds": reset - now}
    return {"ok": True, "stale": False, "plan_type": "plus",
            "fetched_at": datetime.now(timezone.utc).isoformat(), "error": None,
            "windows": {"primary": window(22, 18000, now + 8220),
                        "secondary": window(54, 604800, now + 254100)},
            "credits": None, "raw": {}}


class ResultBridge(QObject):
    result = Signal(object)


class QuotaWidget(QWidget):
    def __init__(self, service=None, *, demo=False, api_port=18765, api_error=None, preview=False):
        super().__init__()
        self.service = service
        self.demo = demo
        self.preview = preview
        self.api_port = api_port
        self.api_error = api_error
        self.settings = {} if preview else load_settings()
        self.pinned = bool(self.settings.get("pinned", False))
        self.compact = bool(self.settings.get("compact", False))
        self.interval = self.settings.get("interval", 60)
        if self.interval not in (60, 120, 300):
            self.interval = 60
        self.data = demo_usage() if demo else {}
        self.loading = False
        self._quit = False
        self._last_manual = 0.0
        self._drag_position = None
        self._progress = 0.0
        self._progress_from = 0.0
        self._progress_target = 0.0
        self._progress_started = 0.0
        initial_window = select_display_window(self.data)[1]
        initial_percent = self.percent(initial_window)
        self._progress = self._progress_target = initial_percent / 100 if initial_percent is not None else 0.0
        self._bridge = ResultBridge(self)
        self._bridge.result.connect(self.receive_usage)
        self.setWindowTitle("Codex 实时额度")
        self.setWindowIcon(app_icon())
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if self.pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedSize(320, 320)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.show_menu(self.mapToGlobal(pos)))

        self.pin_button = self.tool_button("pin", "置顶窗口", QRectF(250, 24, 26, 26), self.toggle_pin)
        self.menu_button = self.tool_button("more", "显示模式与更多选项", QRectF(282, 24, 26, 26),
                                            lambda: self.show_menu(self.mapToGlobal(QPoint(180, 64))))
        self.refresh_button = QToolButton(self)
        self.refresh_button.setText("  刷新额度")
        self.refresh_button.setIcon(icon("refresh", QColor("#172f32")))
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.refresh_button.setGeometry(32, 248, 256, 38)
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setFont(font(13, QFont.Weight.DemiBold))
        self.refresh_button.setStyleSheet("""
            QToolButton { color: #eaf2f8; background: rgba(190,215,236,22);
                border: 1px solid rgba(220,238,250,35); border-radius: 12px; padding: 4px; }
            QToolButton:hover { background: rgba(205,228,246,38); }
            QToolButton:pressed { background: rgba(170,205,231,45); }
            QToolButton:disabled { color: #9aaebe; background: rgba(155,180,200,18); }
        """)
        self.refresh_button.clicked.connect(self.manual_refresh)
        self.refresh_button.hide()
        self.apply_mode()
        self.tray = None
        if not preview and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("Codex 实时额度")
            self.tray.activated.connect(self.tray_activated)
            self.tray.setContextMenu(self.make_menu())
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.setInterval(self.interval * 1000)
        self.timer.timeout.connect(self.refresh)
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.update)
        self.tick_timer.start(1000)
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(16)
        self.progress_timer.timeout.connect(self.animate_progress)
        if not preview:
            self.restore_position()
            if not demo:
                self.timer.start()
                QTimer.singleShot(200, self.refresh)
        self.update_pin_button()

    def tool_button(self, name, tooltip, rect, callback):
        b = QToolButton(self)
        b.setGeometry(rect.toRect())
        b.setIcon(icon(name))
        b.setToolTip(tooltip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet("QToolButton { background: transparent; border: none; border-radius: 9px; } QToolButton:hover { background: rgba(214,230,244,30); } QToolButton:pressed { background: rgba(214,230,244,42); }")
        b.clicked.connect(callback)
        return b

    def restore_position(self):
        point = self.settings.get("position")
        if isinstance(point, list) and len(point) == 2 and all(isinstance(n, int) for n in point):
            visible = any(s.availableGeometry().intersects(QRectF(point[0], point[1], 100, 100).toRect())
                          for s in QApplication.screens())
            if visible:
                self.move(*point)
                return
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 34, screen.top() + 80)

    def persist(self):
        if not self.preview:
            self.settings.update(position=[self.x(), self.y()], pinned=self.pinned, interval=self.interval, compact=self.compact)
            try:
                save_settings(self.settings)
            except OSError:
                pass  # Read-only profiles should not prevent using or closing the widget.

    def showEvent(self, event):
        super().showEvent(event)
        if not self.preview:
            QTimer.singleShot(0, lambda: enable_glass(int(self.winId())))

    def update_pin_button(self):
        self.pin_button.setIcon(icon("pin", MINT if self.pinned else QColor(222, 235, 247, 165)))
        self.pin_button.setToolTip("取消置顶" if self.pinned else "置顶窗口")

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pinned)
        self.show()
        self.update_pin_button()
        self.persist()
        self.refresh_tray_menu()

    def apply_mode(self):
        self.setFixedSize(200, 78) if self.compact else self.setFixedSize(320, 320)
        self.pin_button.setVisible(not self.compact)
        self.menu_button.setVisible(not self.compact)
        if not self.compact:
            self.pin_button.setGeometry(250, 24, 26, 26)
            self.menu_button.setGeometry(282, 24, 26, 26)
        self.refresh_button.hide()
        self.update()

    def set_compact(self, compact):
        self.compact = bool(compact)
        self.apply_mode()
        if self.isVisible():
            screen = self.screen().availableGeometry()
            self.move(max(screen.left(), min(self.x(), screen.right() - self.width() + 1)),
                      max(screen.top(), min(self.y(), screen.bottom() - self.height() + 1)))
            if not self.preview:
                enable_glass(int(self.winId()))
        self.persist()
        self.refresh_tray_menu()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = None
            self.set_compact(not self.compact)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def refresh_tray_menu(self):
        if self.tray:
            previous = self.tray.contextMenu()
            self.tray.setContextMenu(self.make_menu())
            if previous:
                previous.deleteLater()

    def make_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background: #20303a; color: #e5f4f3; border: 1px solid #4b626b; border-radius: 10px; padding: 6px; font: 12px 'Microsoft YaHei UI'; } QMenu::item { padding: 8px 22px; border-radius: 5px; } QMenu::item:selected { background: #3b575b; } QMenu::separator { height: 1px; background: #395059; margin: 5px; }")
        menu.addAction("显示组件", self.reveal)
        menu.addAction("刷新额度", self.manual_refresh)
        modes = menu.addMenu("显示模式")
        mode_group = QActionGroup(modes)
        mode_group.setExclusive(True)
        for compact, title in ((False, "大卡片"), (True, "小组件")):
            action = QAction(title, modes)
            action.setCheckable(True)
            action.setChecked(self.compact == compact)
            action.triggered.connect(lambda checked=False, value=compact: self.set_compact(value))
            mode_group.addAction(action)
            modes.addAction(action)
        pin = menu.addAction("窗口置顶")
        pin.setCheckable(True)
        pin.setChecked(self.pinned)
        pin.triggered.connect(self.toggle_pin)
        submenu = menu.addMenu("自动刷新")
        group = QActionGroup(submenu)
        group.setExclusive(True)
        for seconds, title in ((60, "每 1 分钟"), (120, "每 2 分钟"), (300, "每 5 分钟")):
            action = QAction(title, submenu)
            action.setCheckable(True)
            action.setChecked(self.interval == seconds)
            action.triggered.connect(lambda checked=False, value=seconds: self.set_interval(value))
            group.addAction(action)
            submenu.addAction(action)
        if sys.platform == "win32" and not self.demo:
            startup = menu.addAction("开机启动")
            startup.setCheckable(True)
            startup.setChecked(is_startup_enabled())
            startup.triggered.connect(self.set_startup)
        menu.addSeparator()
        if self.api_port and not self.demo:
            api = menu.addAction("打开本地额度 API", lambda: QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{self.api_port}/api/usage")))
            api.setEnabled(not self.api_error)
        menu.addAction("打开 Codex 用量页面", lambda: QDesktopServices.openUrl(QUrl("https://chatgpt.com/codex/settings/usage")))
        menu.addAction("隐藏到托盘" if self.tray else "最小化", self.hide if self.tray else self.showMinimized)
        menu.addSeparator()
        menu.addAction("退出", self.quit_app)
        return menu

    def show_menu(self, global_position):
        menu = self.make_menu()
        menu.exec(global_position)
        menu.deleteLater()

    def set_interval(self, seconds):
        self.interval = seconds
        self.timer.setInterval(seconds * 1000)
        self.persist()
        self.refresh_tray_menu()
        self.update()

    def set_startup(self, enabled):
        try:
            if not toggle_startup(enabled):
                self.notify("无法修改开机启动", "请检查 Windows 当前用户的权限。")
        except (OSError, ValueError):
            self.notify("无法修改开机启动", "请检查 Windows 当前用户的权限。")
        self.refresh_tray_menu()

    def notify(self, title, message):
        if self.tray:
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 6000)

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.reveal()

    def manual_refresh(self):
        if self.demo:
            self.data = demo_usage()
            self.begin_progress_animation()
            self.update()
            return
        if time.monotonic() - self._last_manual < 10 or self.loading:
            return
        self._last_manual = time.monotonic()
        self.refresh(force=True)

    def refresh(self, force=False):
        if self.loading or not self.service:
            return
        self.loading = True
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("  正在同步…")
        self.update()
        service, bridge = self.service, self._bridge
        def run():
            try:
                result = service.get_usage(force=force)
            except Exception:
                result = {"ok": False, "error": {"code": "unexpected", "message": "读取失败，请稍后重试。"}}
            try:
                bridge.result.emit(result)
            except RuntimeError:
                pass  # The application may have been closed during the HTTP request.
        threading.Thread(target=run, name="quota-refresh", daemon=True).start()

    def receive_usage(self, data):
        # Retain the last usable result on unexpected local worker failures as well.
        if not data.get("windows") and self.data.get("windows"):
            self.data = {**self.data, "ok": False, "stale": True, "error": data.get("error")}
        else:
            self.data = data
        self.begin_progress_animation()
        self.loading = False
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("  重新连接" if not data.get("ok") else "  刷新额度")
        if self.tray:
            kind, window = select_display_window(self.data)
            remaining = self.percent(window)
            title = "5 小时额度" if kind == "five_hour" else "本周额度"
            self.tray.setToolTip(f"Codex · {title}剩余 {remaining:g}%" if remaining is not None else "Codex 实时额度 · 等待数据")
        self.update()

    def begin_progress_animation(self):
        _, window = select_display_window(self.data)
        target = self.percent(window)
        self._progress_from = self._progress
        self._progress_target = (target / 100.0) if target is not None else 0.0
        self._progress_started = time.monotonic()
        if self.preview:
            self._progress = self._progress_target
            return
        self.progress_timer.start()

    def animate_progress(self):
        elapsed = time.monotonic() - self._progress_started
        t = min(1.0, elapsed / .24)
        eased = t * t * (3.0 - 2.0 * t)
        self._progress = self._progress_from + (self._progress_target - self._progress_from) * eased
        self.update(QRectF(20, 170, 280, 18).toRect())
        if t >= 1.0:
            self._progress = self._progress_target
            self.progress_timer.stop()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if self.windowHandle():
                self.windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_position = None
        self.persist()
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        self.persist()
        if self.tray and not self._quit:
            self.hide()
            event.ignore()
        else:
            event.accept()

    def quit_app(self):
        self._quit = True
        self.persist()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    @staticmethod
    def percent(window):
        v = (window or {}).get("remaining_percent")
        return max(0.0, min(100.0, float(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None

    @staticmethod
    def percent_label(value):
        if value is None:
            return "—"
        return f"{value:.0f}" if value == int(value) else f"{value:.1f}"

    @staticmethod
    def window_title(window, fallback):
        seconds = (window or {}).get("limit_window_seconds")
        if not isinstance(seconds, (float, int)) or seconds <= 0:
            return fallback, "—"
        if seconds == 604800:
            return "本周额度", "7D"
        if seconds % 86400 == 0:
            days = int(seconds // 86400)
            return f"{days} 天额度", f"{days}D"
        if seconds % 3600 == 0:
            hours = int(seconds // 3600)
            return f"{hours} 小时额度", f"{hours}H"
        minutes = max(1, int(seconds // 60))
        return f"{minutes} 分钟额度", f"{minutes}M"

    def reset_labels(self, window):
        reset = (window or {}).get("reset_at")
        if not isinstance(reset, (float, int)) or not math.isfinite(reset):
            return "等待重置时间", "北京时间 · 暂无数据"
        try:
            dt = datetime.fromtimestamp(reset, BEIJING)
        except (OSError, OverflowError, ValueError):
            return "等待重置时间", "北京时间 · 暂无数据"
        seconds = max(0, math.ceil(reset - time.time()))
        if not seconds:
            countdown = "窗口已到期 · 等待同步"
        elif seconds >= 86400:
            countdown = f"{seconds // 86400} 天 {(seconds % 86400) // 3600} 小时后重置"
        elif seconds >= 3600:
            countdown = f"{seconds // 3600} 小时 {(seconds % 3600) // 60:02d} 分后重置"
        else:
            countdown = f"{max(1, seconds // 60)} 分钟后重置"
        return countdown, f"北京时间 · {dt.month:02d}/{dt.day:02d} {dt:%H:%M}"

    def selected_usage(self):
        kind, window = select_display_window(self.data)
        title = {"five_hour": "5 小时额度", "weekly": "本周额度"}.get(kind, "额度")
        return kind, window, title, self.percent(window)

    def status(self):
        if self.demo:
            return "示例数据", QColor("#b9cbe0")
        if self.loading:
            return "正在同步", QColor("#a9c9dc")
        if self.data.get("stale"):
            return "缓存数据", QColor("#e0c898")
        if self.data.get("ok"):
            return "实时同步", MINT
        return "等待连接", QColor("#afbdca")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        margin = 4 if self.compact else 7
        rect = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)
        draw_liquid_glass(p, rect, min(rect.height() / 2, 31) if self.compact else 49)
        kind, window, title, remaining = self.selected_usage()
        state, color = self.status()
        reset, date = self.reset_labels(window)
        error = (self.data.get("error") or {}).get("message")
        hint = f"{title} · 剩余 {self.percent_label(remaining)}%" if remaining is not None else "尚未获取到额度数据"
        if kind == "weekly":
            hint += "\n未提供可用的 5 小时额度，显示周额度。"
        if error:
            hint += "\n" + error
        hint += f"\n{state} · {reset}\n{date}\n双击切换大小 · 右键打开菜单"
        if self.api_error:
            hint += f"\n本地 API 端口 {self.api_port} 已占用"
        self.setToolTip(hint)
        if self.compact:
            self.paint_compact(p, title, remaining, state, color)
        else:
            self.paint_card(p, kind, title, remaining, reset, date, state, color)
        p.end()

    def paint_compact(self, p, title, remaining, state, color):
        # A centered horizontal lockup sized specifically for the 200×78 mode.
        p.drawPixmap(QRectF(16, 25, 28, 28), brand_pixmap(84), QRectF(0, 0, 84, 84))
        draw_text(p, 55, 21, title, TYPE["compact"], TEXT, QFont.Weight.Medium, 110, 18)
        if remaining is None:
            line = "正在同步…" if self.loading else "等待额度数据"
        else:
            line = f"剩余 {self.percent_label(remaining)}%"
            if self.data.get("stale"):
                line = f"缓存 · {self.percent_label(remaining)}%"
        draw_text(p, 55, 41, line, TYPE["compact"], PALETTE["secondary"], width=110, height=18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(179, 37, 4, 4))

    def paint_card(self, p, kind, title, remaining, reset, date, state, color):
        self.draw_header(p, state, color)
        self.draw_quota_value(p, remaining)
        draw_text(p, 26, 143, title + " · 剩余" if remaining is not None else "额度信息暂不可用",
                  TYPE["label"], PALETTE["secondary"], width=265, height=21)
        self.draw_progress_bar(p, remaining)
        self.draw_status_reset(p, reset, state, color)
        self.draw_footer(p, kind, date)

    def draw_header(self, p, state, color):
        p.drawPixmap(QRectF(26, 25, 28, 28), brand_pixmap(84), QRectF(0, 0, 84, 84))
        draw_text(p, 62, 24, "Codex", TYPE["brand"], TEXT, QFont.Weight.DemiBold, 94, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(173, 35, 5, 5))
        status_short = {"实时同步": "实时", "正在同步": "同步中", "缓存数据": "缓存",
                        "等待连接": "离线", "示例数据": "示例"}.get(state, "")
        draw_text(p, 181, 25, status_short, TYPE["meta"], color, width=58, height=25)

    def draw_quota_value(self, p, remaining):
        value = self.percent_label(remaining)
        size = TYPE["value"] if len(value) <= 4 else 58
        p.setFont(font(size, QFont.Weight.Light, "Segoe UI"))
        p.setPen(TEXT if remaining is not None else PALETTE["secondary"])
        p.drawText(QRectF(25, 52, 260, 82), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)
        if remaining is not None:
            number_width = p.fontMetrics().horizontalAdvance(value)
            percent_size = TYPE["percent"]
            percent_font = font(percent_size, QFont.Weight.Light, "Segoe UI")
            p.setFont(percent_font)
            p.setPen(PALETTE["secondary"])
            # Align the percent sign to the numeral's optical midline.
            x = min(25 + number_width + 5, 259)
            p.drawText(QRectF(x, 85, 36, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "%")

    def draw_progress_bar(self, p, remaining):
        track = QRectF(26, 172, 268, 7)
        rounded(p, track, 3.5, PALETTE["track"])
        if remaining is None:
            return
        width = track.width() * self._progress
        if width <= 0:
            return
        fill = QRectF(track.left(), track.top(), width, track.height())
        gradient = QLinearGradient(fill.topLeft(), fill.topRight())
        gradient.setColorAt(0, PALETTE["progress_a"])
        gradient.setColorAt(1, PALETTE["progress_b"])
        rounded(p, fill, min(3.5, width / 2), gradient)

    def draw_status_reset(self, p, reset, state, color):
        panel = QRectF(20, 197, 280, 54)
        panel_path = QPainterPath()
        panel_path.addRoundedRect(panel, 18, 18)
        p.fillPath(panel_path, QColor(221, 235, 248, 12))
        p.setPen(QPen(PALETTE["divider"], .7))
        p.drawLine(QPoint(139, 210), QPoint(139, 238))
        self.draw_clock_icon(p, QRectF(29, 213, 20, 20), QColor("#91bfe9"))
        draw_text(p, 57, 210, state, TYPE["status"], color, width=74, height=24)
        p.setPen(QPen(PALETTE["secondary"], 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawArc(QRectF(148, 214, 15, 15), 35 * 16, 286 * 16)
        p.drawLine(161, 212, 165, 212)
        p.drawLine(165, 212, 164, 216)
        draw_text(p, 169, 205, reset, TYPE["reset"], TEXT, QFont.Weight.Medium, 125, 38)

    @staticmethod
    def draw_clock_icon(p, rect, color):
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawEllipse(rect.adjusted(1, 1, -1, -1))
        p.drawLine(QPoint(int(rect.center().x()), int(rect.top() + 5)),
                   QPoint(int(rect.center().x()), int(rect.center().y())))
        p.drawLine(QPoint(int(rect.center().x()), int(rect.center().y())),
                   QPoint(int(rect.right() - 5), int(rect.center().y() + 3)))
        p.restore()

    def draw_footer(self, p, kind, date):
        draw_text(p, 26, 265, date.replace("北京时间 · ", "北京时间  ·  "),
                  TYPE["meta"], PALETTE["muted"], width=145, height=25)
        if kind == "five_hour":
            weekly = None
            for candidate in (self.data.get("windows") or {}).values():
                if isinstance(candidate, dict) and candidate.get("limit_window_seconds") == 604800:
                    weekly = self.percent(candidate)
                    if weekly is not None:
                        break
            footer = f"本周剩余 {self.percent_label(weekly)}%" if weekly is not None else "右键查看更多选项"
        elif kind == "weekly":
            footer = "5h 缺失 · 周额度回退"
        else:
            footer = "右键查看更多选项"
        if self.data.get("stale"):
            footer = "缓存数据 · " + footer
        draw_text(p, 170, 265, footer, TYPE["meta"], PALETTE["secondary"], width=124, height=25,
                  align=Qt.AlignmentFlag.AlignRight)


def render_preview(path: str):
    """Render real widgets for usage, fallback, sync, empty, cache and compact states."""
    samples: list[tuple[str, QuotaWidget]] = []

    def render_over(canvas: QImage, widget: QuotaWidget, offset: QPoint, scale=1.0):
        layer = QImage(round(widget.width() * scale), round(widget.height() * scale),
                       QImage.Format.Format_ARGB32_Premultiplied)
        layer.setDevicePixelRatio(scale)
        layer.fill(QColor(0, 0, 0, 0))
        widget.render(layer)
        painter = QPainter(canvas)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawImage(offset, layer)
        painter.end()

    def sample(label: str, *, compact=False, state="live", fallback=False):
        widget = QuotaWidget(demo=False, preview=True)
        widget.data = demo_usage()
        widget.data["windows"]["primary"]["remaining_percent"] = 84
        widget.data["windows"]["primary"]["used_percent"] = 16
        widget.data["windows"]["secondary"]["remaining_percent"] = 64
        widget.data["windows"]["secondary"]["used_percent"] = 36
        if state == "syncing":
            widget.loading = True
        elif state == "empty":
            widget.data = {"ok": False, "stale": False, "windows": {}, "error": None}
        elif state == "cached":
            widget.data["ok"] = False
            widget.data["stale"] = True
        if fallback:
            widget.data["windows"]["primary"] = None
        _, selected = select_display_window(widget.data)
        percent = widget.percent(selected)
        widget._progress = widget._progress_target = percent / 100 if percent is not None else 0.0
        if compact:
            widget.set_compact(True)
        widget.ensurePolished()
        samples.append((label, widget))
        return widget

    sample("实时 · 5 小时", state="live")
    sample("回退 · 周额度", state="live", fallback=True)
    sample("缓存数据", state="cached")
    sample("正在同步", state="syncing")
    sample("无额度数据", state="empty")
    sample("迷你组件 · 200 × 78", compact=True, state="live")

    def render_canvas(background_name: str, light: bool, output: Path):
        width, height = 1080, 800
        canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.setDevicePixelRatio(1.0)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if light:
            background = QLinearGradient(0, 0, width, height)
            background.setColorAt(0, QColor("#d5e0eb"))
            background.setColorAt(1, QColor("#edf1f5"))
            heading, sub = QColor("#243347"), QColor("#53677b")
        else:
            background = QLinearGradient(0, 0, width, height)
            background.setColorAt(0, QColor("#172332"))
            background.setColorAt(.58, QColor("#25364a"))
            background.setColorAt(1, QColor("#1a2938"))
            heading, sub = QColor("#e8eef4"), QColor("#aab9c8")
        p.fillRect(canvas.rect(), background)
        draw_text(p, 28, 16, "Codex Quota  /  {}".format(background_name), 16, heading,
                  QFont.Weight.DemiBold, 600, 30)
        draw_text(p, 28, 42, "320 × 320 主卡片    ·    200 × 78 迷你组件    ·    全部为真实 PySide6 组件渲染",
                  10, sub, width=850, height=20)
        positions = [(28, 82), (380, 82), (732, 82),
                     (28, 438), (380, 438), (732, 438)]
        for (label, widget), (x, y) in zip(samples, positions):
            draw_text(p, x, y - 24, label, 11, heading, QFont.Weight.Medium, 320, 18)
        p.end()
        for (_, widget), (x, y) in zip(samples, positions):
            if widget.compact:
                render_over(canvas, widget, QPoint(x + 60, y + 112))
            else:
                render_over(canvas, widget, QPoint(x, y))
        p = QPainter(canvas)
        draw_text(p, 28, 768, "真实组件 · 状态预览", 10, sub, width=400, height=18)
        draw_text(p, 820, 768, "配额进度表示剩余比例", 10, sub, width=230, height=18,
                  align=Qt.AlignmentFlag.AlignRight)
        p.end()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not canvas.save(str(output)):
            raise OSError("无法保存预览图片")

    destination = Path(path).resolve()
    render_canvas("深色桌面", False, destination)
    render_canvas("浅色桌面", True, destination.with_name(destination.stem + "-light" + destination.suffix))

    edge_samples = []
    for label, value in (("满额 · 100%", 100.0), ("小数 · 9.5%", 9.5), ("耗尽 · 0%", 0.0)):
        widget = QuotaWidget(demo=False, preview=True)
        widget.data = demo_usage()
        widget.data["windows"]["primary"]["remaining_percent"] = value
        widget.data["windows"]["primary"]["used_percent"] = 100 - value
        widget._progress = widget._progress_target = value / 100
        widget.ensurePolished()
        edge_samples.append((label, widget))
    edge_path = destination.with_name(destination.stem + "-edge-values" + destination.suffix)
    edge_canvas = QImage(1080, 390, QImage.Format.Format_ARGB32_Premultiplied)
    edge_canvas.fill(QColor("#202f40"))
    p = QPainter(edge_canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    for i, (label, _) in enumerate(edge_samples):
        x = 28 + i * 352
        draw_text(p, x, 18, label, 12, TEXT, QFont.Weight.Medium, 320, 24)
    p.end()
    for i, (_, widget) in enumerate(edge_samples):
        render_over(edge_canvas, widget, QPoint(28 + i * 352, 54))
    edge_canvas.save(str(edge_path))

    dpi_path = destination.with_name(destination.stem + "-dpi" + destination.suffix)
    for scale, suffix in ((1.0, "100"), (1.25, "125"), (1.5, "150")):
        logical_width, logical_height = 600, 405
        dpi_canvas = QImage(round(logical_width * scale), round(logical_height * scale),
                            QImage.Format.Format_ARGB32_Premultiplied)
        dpi_canvas.setDevicePixelRatio(scale)
        dpi_canvas.fill(QColor("#202f40"))
        p = QPainter(dpi_canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        draw_text(p, 22, 16, "320 × 320 主卡片    /    {}% 缩放".format(suffix),
                  12, TEXT, QFont.Weight.Medium, 500, 24)
        p.end()
        render_over(dpi_canvas, samples[0][1], QPoint(22, 54), scale)
        render_over(dpi_canvas, samples[-1][1], QPoint(370, 210), scale)
        dpi_canvas.save(str(dpi_path.with_name(dpi_path.stem + "-" + suffix + dpi_path.suffix)))

    for _, widget in edge_samples:
        widget.close()
    for _, widget in samples:
        widget.close()

def run_widget(args) -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("CodexQuota")
    app.setOrganizationName("LocalTools")
    app.setFont(font(12))
    app.setWindowIcon(app_icon())
    if args.render_preview:
        render_preview(args.render_preview)
        return 0
    # A second launch reveals the existing widget instead of opening another API.
    lock = None
    if not args.demo:
        socket = QLocalSocket()
        socket.connectToServer("CodexQuota.Widget.v1")
        if socket.waitForConnected(350):
            socket.write(b"show")
            socket.waitForBytesWritten(350)
            socket.disconnectFromServer()
            return 0
        lock = QLocalServer()
        QLocalServer.removeServer("CodexQuota.Widget.v1")
        lock.listen("CodexQuota.Widget.v1")
    server = None
    api_error = None
    service = None
    if not args.demo:
        from .backend import UsageService, start_api
        service = UsageService(auth_path=args.auth_file)
        if not args.no_api:
            try:
                server, _ = start_api(service, port=args.port)
            except OSError:
                api_error = "port_unavailable"
    widget = QuotaWidget(service, demo=args.demo,
                         api_port=None if args.no_api else args.port, api_error=api_error)
    if lock:
        def reveal():
            while lock.hasPendingConnections():
                client = lock.nextPendingConnection()
                client.close()
                client.deleteLater()
            widget.reveal()
        lock.newConnection.connect(reveal)
    app.setQuitOnLastWindowClosed(widget.tray is None)
    widget.show()
    result = app.exec()
    widget.persist()
    if server:
        server.shutdown()
        server.server_close()
    if lock:
        lock.close()
    return result
