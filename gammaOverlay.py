"""
gamma overlay

Controls (on an image):
    Left-drag            move (snaps to screen edges, hold Ctrl to disable snapping)
    Drag bottom-right    resize (keeps aspect ratio)
    Mouse wheel          resize (Shift + wheel = fine, 1% per notch)
    Alt + mouse wheel    change opacity
    Arrow keys           nudge 1px (Shift = 10px) after clicking the image
    Double-click         reset size to 100%
    Right-click          full menu (opacity, size, flip, click-through, ...)

Everything else (add images, lock all, show/hide all, start with Windows, quit)
is in the tray icon menu.

<3
"""

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QPointF, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QGuiApplication, QIcon, QImage, QPainter, QPen,
    QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QMenu,
    QSlider, QSystemTrayIcon, QWidget, QWidgetAction,
)

APP_NAME = "gammaOverlay"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
IMAGES_DIR = CONFIG_DIR / "images"
LAYOUT_FILE = CONFIG_DIR / "layout.json"

GRIP = 18          
MIN_SIDE = 16      
SNAP = 14          
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All files (*)"


class OverlayImage(QWidget):
    changed = Signal()
    removeRequested = Signal(object)
    duplicateRequested = Signal(object)

    def __init__(self, path, x=None, y=None, scale=1.0, opacity=1.0, click_through=False, on_top=True, flip=False, name=None):
        super().__init__()
        img = QImage(str(path))
        if img.isNull():
            raise ValueError(f"Could not load image: {path}")
        self.source = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self.path = str(path)
        self.display_name = name or Path(path).name

        self.img_scale = float(scale)
        self.img_opacity = float(opacity)
        self.click_through = bool(click_through)
        self.on_top = bool(on_top)
        self.flip = bool(flip)

        self._hover = False
        self._drag_offset = None
        self._resizing = False
        self._cache = None

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self._apply_flags()
        self._refresh()
        if x is not None and y is not None:
            self.move(int(x), int(y))

    # rendering
    def _base_size(self):
        
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        return self.source.width() / dpr, self.source.height() / dpr

    def _refresh(self, smooth=True, keep_center=False):
        bw, bh = self._base_size()
        w = max(MIN_SIDE, round(bw * self.img_scale))
        h = max(MIN_SIDE, round(bh * self.img_scale))
        center = self.geometry().center()
        self.resize(w, h)
        if keep_center:
            self.move(center.x() - w // 2, center.y() - h // 2)
        self._rebuild_cache(smooth)
        self.update()

    def _rebuild_cache(self, smooth=True):
        dpr = self.devicePixelRatioF()
        img = self.source.mirrored(True, False) if self.flip else self.source
        mode = Qt.SmoothTransformation if smooth else Qt.FastTransformation
        img = img.scaled(int(self.width() * dpr), int(self.height() * dpr),
                         Qt.IgnoreAspectRatio, mode)
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self._cache = pm

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        p.setOpacity(self.img_opacity)
        if self._cache:
            p.drawPixmap(0, 0, self._cache)
        if self._hover and not self.click_through:
            p.setOpacity(1.0)
            p.setPen(QPen(QColor(0, 0, 0, 200), 1))
            p.drawRect(0, 0, self.width() - 1, self.height() - 1)
            pen = QPen(QColor(255, 255, 255, 230), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawRect(0, 0, self.width() - 1, self.height() - 1)
            w, h = self.width(), self.height()
            tri = QPolygonF([QPointF(w - 1, h - GRIP), QPointF(w - 1, h - 1),
                             QPointF(w - GRIP, h - 1)])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawPolygon(tri)

    # states
    def _apply_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.on_top:
            flags |= Qt.WindowStaysOnTopHint
        if self.click_through:
            flags |= Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def set_scale(self, scale, keep_center=True):
        self.img_scale = max(0.02, min(20.0, scale))
        self._refresh(keep_center=keep_center)
        self.changed.emit()

    def set_opacity(self, value):
        self.img_opacity = max(0.05, min(1.0, value))
        self.update()
        self.changed.emit()

    def set_click_through(self, value):
        self.click_through = bool(value)
        self._hover = False
        self._apply_flags()
        self.changed.emit()

    def set_on_top(self, value):
        self.on_top = bool(value)
        self._apply_flags()
        self.changed.emit()

    def set_flip(self, value):
        self.flip = bool(value)
        self._rebuild_cache()
        self.update()
        self.changed.emit()

    def to_dict(self):
        return {
            "path": self.path, "name": self.display_name,
            "x": self.x(), "y": self.y(), "scale": self.img_scale,
            "opacity": self.img_opacity, "click_through": self.click_through,
            "on_top": self.on_top, "flip": self.flip,
        }

    # input
    def _in_grip(self, pos):
        return pos.x() >= self.width() - GRIP and pos.y() >= self.height() - GRIP

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.raise_()
        if self._in_grip(e.position().toPoint()):
            self._resizing = True
            self._start_global = e.globalPosition()
            self._start_w = self.width()
        else:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._resizing:
            dx = e.globalPosition().x() - self._start_global.x()
            new_w = max(MIN_SIDE, self._start_w + dx)
            bw, _ = self._base_size()
            self.img_scale = max(0.02, min(20.0, new_w / bw))
            self._refresh(smooth=False)
        elif self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            pos = e.globalPosition().toPoint() - self._drag_offset
            if not (e.modifiers() & Qt.ControlModifier):
                pos = self._snap(pos, e.globalPosition().toPoint())
            self.move(pos)
        else:
            self.setCursor(Qt.SizeFDiagCursor if self._in_grip(e.position().toPoint())
                           else Qt.SizeAllCursor)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._resizing:
            self._rebuild_cache(smooth=True)
            self.update()
        self._resizing = False
        self._drag_offset = None
        self.changed.emit()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.set_scale(1.0)

    def wheelEvent(self, e):
        delta = e.angleDelta().y() or e.angleDelta().x()
        if not delta:
            return
        steps = delta / 120.0
        if e.modifiers() & Qt.AltModifier:
            self.set_opacity(self.img_opacity + 0.05 * steps)
        else:
            if e.modifiers() & Qt.ShiftModifier:
                self.set_scale(self.img_scale + 0.01 * steps)   # fine: 1% per notch
            else:
                self.set_scale(self.img_scale * (1.07 ** steps))

    def keyPressEvent(self, e):
        step = 10 if e.modifiers() & Qt.ShiftModifier else 1
        moves = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0),
                 Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
        if e.key() in moves:
            dx, dy = moves[e.key()]
            self.move(self.x() + dx, self.y() + dy)
            self.changed.emit()
        else:
            super().keyPressEvent(e)

    def _snap(self, pos, cursor):
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        g = screen.geometry()
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        if abs(x - g.left()) < SNAP:
            x = g.left()
        elif abs(x + w - (g.right() + 1)) < SNAP:
            x = g.right() + 1 - w
        if abs(y - g.top()) < SNAP:
            y = g.top()
        elif abs(y + h - (g.bottom() + 1)) < SNAP:
            y = g.bottom() + 1 - h
        return QPoint(x, y)

    # context menu
    def _size_widget(self, parent):
        """Slider (1% steps) + number box (0.1% steps) for precise sizing."""
        box = QWidget(parent)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.addWidget(QLabel("Size"))

        slider = QSlider(Qt.Horizontal)
        slider.setRange(1, 400)
        slider.setMinimumWidth(150)
        slider.setValue(round(self.img_scale * 100))
        lay.addWidget(slider)

        spin = QDoubleSpinBox()
        spin.setRange(2.0, 2000.0)
        spin.setDecimals(1)
        spin.setSingleStep(0.1)
        spin.setSuffix(" %")
        spin.setValue(self.img_scale * 100)
        lay.addWidget(spin)

        def from_slider(v):
            spin.blockSignals(True)
            spin.setValue(v)
            spin.blockSignals(False)
            self.set_scale(v / 100)

        def from_spin(v):
            slider.blockSignals(True)
            slider.setValue(round(v))
            slider.blockSignals(False)
            self.set_scale(v / 100)

        slider.valueChanged.connect(from_slider)
        spin.valueChanged.connect(from_spin)
        return box

    def contextMenuEvent(self, e):
        m = QMenu(self)

        om = m.addMenu("Opacity")
        for pct in range(100, 0, -10):
            a = om.addAction(f"{pct}%")
            a.setCheckable(True)
            a.setChecked(abs(self.img_opacity * 100 - pct) < 5)
            a.triggered.connect(lambda _=False, v=pct: self.set_opacity(v / 100))

        wa = QWidgetAction(m)
        wa.setDefaultWidget(self._size_widget(m))
        m.addAction(wa)

        sm = m.addMenu("Size presets")
        for pct in (25, 50, 75, 100, 150, 200, 300):
            a = sm.addAction(f"{pct}%")
            a.triggered.connect(lambda _=False, v=pct: self.set_scale(v / 100))

        flip = m.addAction("Flip horizontally")
        flip.setCheckable(True)
        flip.setChecked(self.flip)
        flip.triggered.connect(self.set_flip)

        top = m.addAction("Always on top")
        top.setCheckable(True)
        top.setChecked(self.on_top)
        top.triggered.connect(self.set_on_top)

        m.addAction("Click-through (unlock from tray menu)").triggered.connect(
            lambda: self.set_click_through(True))

        m.addSeparator()
        m.addAction("Duplicate").triggered.connect(
            lambda: self.duplicateRequested.emit(self))
        m.addAction("Remove").triggered.connect(
            lambda: self.removeRequested.emit(self))
        m.exec(e.globalPos())


# control
def resource_path(name):
    """Find a bundled file, both when run as a script and as a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def make_icon():
   
    for name in ("icon.ico", "icon.png"):
        path = resource_path(name)
        if os.path.exists(path):
            return QIcon(path)
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#4C8BF5"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(16, 16, 32, 32, 6, 6)
    p.setBrush(QColor("#4C8BF5"))
    p.drawEllipse(36, 21, 8, 8)
    p.end()
    return QIcon(pm)


def autostart_supported():
    return sys.platform == "win32"


def _run_key(access):
    import winreg
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run", 0, access)


def autostart_enabled():
    if not autostart_supported():
        return False
    import winreg
    try:
        with _run_key(winreg.KEY_QUERY_VALUE) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled):
    if not autostart_supported():
        return
    import winreg
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        cmd = f'"{exe}" "{os.path.abspath(__file__)}"'
    with _run_key(winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


class Controller:
    def __init__(self, app):
        self.app = app
        self.items = []
        self.all_visible = True

        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        self.save_timer = QTimer()
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.save)

        self.tray = QSystemTrayIcon(make_icon())
        self.tray.setToolTip(APP_NAME)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.rebuild_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        app.aboutToQuit.connect(self.save)
        self.load()
        if not self.items:
            self.tray.showMessage(
                APP_NAME, "Right-click the tray icon and choose 'Add images...' to start.",
                QSystemTrayIcon.Information, 5000)

    # items
    def add_item(self, path, **state):
        try:
            item = OverlayImage(path, **state)
        except ValueError as err:
            self.tray.showMessage(APP_NAME, str(err), QSystemTrayIcon.Warning, 4000)
            return None
        item.changed.connect(self.save_timer.start)
        item.removeRequested.connect(self.remove_item)
        item.duplicateRequested.connect(self.duplicate_item)
        self.items.append(item)
        if state.get("x") is None:
            self._place_cascade(item)
        else:
            self._keep_on_screen(item)
        if self.all_visible:
            item.show()
        return item

    def _store_copy(self, src):
        """Copy the image into our own folder so moving the original won't break the layout."""
        dest = IMAGES_DIR / f"{uuid.uuid4().hex[:8]}_{Path(src).name}"
        shutil.copy2(src, dest)
        return str(dest)

    def add_files(self, files):
        for f in files:
            try:
                stored = self._store_copy(f)
            except OSError as err:
                self.tray.showMessage(APP_NAME, f"Could not read {f}: {err}",
                                      QSystemTrayIcon.Warning, 4000)
                continue
            item = self.add_item(stored, name=Path(f).name)
            if item is None:
                Path(stored).unlink(missing_ok=True)
        self.save()

    def remove_item(self, item):
        if item in self.items:
            self.items.remove(item)
        item.hide()
        item.deleteLater()
        try:
            if Path(item.path).parent == IMAGES_DIR:
                Path(item.path).unlink(missing_ok=True)
        except OSError:
            pass
        self.save()

    def duplicate_item(self, item):
        state = item.to_dict()
        state["x"] += 30
        state["y"] += 30
        stored = self._store_copy(item.path)
        self.add_item(stored, **{k: v for k, v in state.items() if k != "path"})
        self.save()

    def _place_cascade(self, item):
        g = QGuiApplication.primaryScreen().availableGeometry()
        n = len(self.items) - 1
        item.move(g.center().x() - item.width() // 2 + 30 * (n % 8),
                  g.center().y() - item.height() // 2 + 30 * (n % 8))

    def _keep_on_screen(self, item):
        rect = item.geometry()
        if not any(s.geometry().intersects(rect) for s in QGuiApplication.screens()):
            self._place_cascade(item)

    # persistence
    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {"items": [i.to_dict() for i in self.items]}
            LAYOUT_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def load(self):
        if not LAYOUT_FILE.exists():
            return
        try:
            data = json.loads(LAYOUT_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for entry in data.get("items", []):
            path = entry.get("path")
            if not path or not Path(path).exists():
                continue
            self.add_item(path, **{k: v for k, v in entry.items() if k != "path"})

    # tray
    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_visible()

    def toggle_visible(self):
        self.all_visible = not self.all_visible
        for i in self.items:
            i.setVisible(self.all_visible)

    def set_all_click_through(self, value):
        for i in self.items:
            i.set_click_through(value)

    def set_all_on_top(self, value):
        for i in self.items:
            i.set_on_top(value)

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(None, "Add images", "", IMAGE_FILTER)
        if files:
            self.add_files(files)

    def rebuild_menu(self):
        m = self.menu
        m.clear()
        m.addAction("Add images...").triggered.connect(self.pick_files)
        m.addSeparator()

        vis = m.addAction("Show all images")
        vis.setCheckable(True)
        vis.setChecked(self.all_visible)
        vis.triggered.connect(lambda _=False: self.toggle_visible())

        m.addAction("Lock all (click-through)").triggered.connect(
            lambda: self.set_all_click_through(True))
        m.addAction("Unlock all").triggered.connect(
            lambda: self.set_all_click_through(False))
        m.addAction("All always on top").triggered.connect(
            lambda: self.set_all_on_top(True))
        m.addAction("All normal layering").triggered.connect(
            lambda: self.set_all_on_top(False))

        if self.items:
            m.addSeparator()
            im = m.addMenu(f"Images ({len(self.items)})")
            for item in self.items:
                sub = im.addMenu(item.display_name)
                lock = sub.addAction("Unlock" if item.click_through else "Lock (click-through)")
                lock.triggered.connect(
                    lambda _=False, it=item: it.set_click_through(not it.click_through))
                sub.addAction("Bring to front").triggered.connect(
                    lambda _=False, it=item: (it.show(), it.raise_()))
                sub.addAction("Remove").triggered.connect(
                    lambda _=False, it=item: self.remove_item(it))

        m.addSeparator()
        if autostart_supported():
            auto = m.addAction("Start with Windows")
            auto.setCheckable(True)
            auto.setChecked(autostart_enabled())
            auto.triggered.connect(lambda checked: set_autostart(checked))
        m.addAction("Open data folder").triggered.connect(self._open_folder)
        m.addAction("Quit").triggered.connect(self.app.quit)

    def _open_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_DIR)))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    controller = Controller(app)  
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
