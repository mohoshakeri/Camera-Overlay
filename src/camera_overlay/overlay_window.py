import math
from typing import Optional

from PIL import Image
from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PyQt5.QtWidgets import QApplication, QWidget

from camera_overlay.capture import CameraWorker
from camera_overlay.config import OverlayConfig
from camera_overlay.constants import (
    APP_DISPLAY_NAME,
    APP_WM_CLASS,
    CLOSE_BUTTON_MARGIN,
    CLOSE_BUTTON_MIN,
    DEFAULT_SIZE,
    FRAME_INTERVAL_MS,
    MAX_SIZE,
    MIN_SIZE,
    RESIZE_EDGE,
    ZOOM_MAX,
    ZOOM_MIN,
    ZOOM_STEP,
)


class CameraOverlay(QWidget):
    """Circular Always-On-Top Camera Overlay Window."""

    closed_by_user = pyqtSignal()

    def __init__(self, config: OverlayConfig) -> None:
        """Build Overlay Window And Start Camera."""
        super().__init__()
        self._config = config
        self._size = DEFAULT_SIZE
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, config.initial_zoom))
        self._frame: Optional[Image.Image] = None
        self._drag_offset: Optional[QPoint] = None
        self._resizing = False
        self._resize_start_pos: Optional[QPoint] = None
        self._resize_start_size = DEFAULT_SIZE
        self._error: Optional[str] = None
        self._pixmap_cache: Optional[QPixmap] = None
        self._pixmap_key: Optional[tuple] = None
        self._pointer_inside = False

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowRole(APP_WM_CLASS)
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self._apply_geometry(self._size)
        self._center_on_screen()

        self._worker = CameraWorker(config)
        self._worker.frame_ready.connect(self._on_frame, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_camera_failed, Qt.QueuedConnection)
        self._worker.start()

        self._repaint_timer = QTimer(self)
        self._repaint_timer.timeout.connect(self.update)
        self._repaint_timer.start(FRAME_INTERVAL_MS)

    def _center_on_screen(self) -> None:
        """Place Overlay Near The Top-Right Of The Primary Screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        margin = 24
        x = area.right() - self.width() - margin
        y = area.top() + margin
        self.move(x, y)

    def _apply_geometry(self, diameter: int) -> None:
        """Resize Window Around Its Current Center."""
        diameter = max(MIN_SIZE, min(MAX_SIZE, int(diameter)))
        old_center = self.geometry().center()
        self._size = diameter
        self.setFixedSize(diameter, diameter)
        new_geo = self.geometry()
        new_geo.moveCenter(old_center)
        self.setGeometry(new_geo)
        self._pixmap_cache = None
        self._pixmap_key = None
        self._update_mask()

    def _update_mask(self) -> None:
        """Clip Window To A Circle."""
        self.setMask(QRegion(0, 0, self.width(), self.height(), QRegion.Ellipse))

    def _close_button_size(self) -> int:
        """Scale Close Button Slightly With Overlay Size."""
        return max(CLOSE_BUTTON_MIN, int(self.width() * 0.09))

    def _close_button_rect(self) -> QRect:
        """Return Geometry Of The Floating Close Button Inside The Circle."""
        size = self._close_button_size()
        radius = self.width() / 2.0
        center = self.width() / 2.0
        half_diag = (size / 2.0) * math.sqrt(2.0)
        ring = max(12.0, radius - CLOSE_BUTTON_MARGIN - half_diag)
        angle = -math.pi / 4
        cx = center + ring * math.cos(angle)
        cy = center + ring * math.sin(angle)
        return QRect(int(cx - size / 2), int(cy - size / 2), size, size)

    def _on_frame(self, frame: object) -> None:
        """Store Latest Camera Frame And Invalidate Cache."""
        if isinstance(frame, Image.Image):
            self._frame = frame
            self._error = None
            self._pixmap_cache = None
            self._pixmap_key = None

    def _on_camera_failed(self, message: str) -> None:
        """Show Camera Failure State."""
        self._error = message
        self.update()

    def _distance_from_center(self, pos: QPoint) -> float:
        """Return Distance From Window Center To Point."""
        center = QPoint(self.width() // 2, self.height() // 2)
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        return (dx * dx + dy * dy) ** 0.5

    def _is_on_resize_edge(self, pos: QPoint) -> bool:
        """Detect Whether Cursor Is Near The Circular Border."""
        if self._close_button_rect().contains(pos):
            return False
        radius = self.width() / 2.0
        distance = self._distance_from_center(pos)
        return radius - RESIZE_EDGE <= distance <= radius + 2

    def _zoomed_frame(self, frame: Image.Image) -> Image.Image:
        """Crop Frame Center According To Current Zoom."""
        width, height = frame.size
        side = min(width, height) / self._zoom
        left = (width - side) / 2
        top = (height - side) / 2
        return frame.crop((left, top, left + side, top + side))

    def _frame_to_pixmap(self, frame: Image.Image) -> QPixmap:
        """Convert Pillow Image To Circular Pixmap With Cache."""
        key = (id(frame), self._size, round(self._zoom, 3))
        if self._pixmap_cache is not None and self._pixmap_key == key:
            return self._pixmap_cache
        cropped = self._zoomed_frame(frame)
        resized = cropped.resize((self._size, self._size), Image.Resampling.BILINEAR)
        data = resized.tobytes("raw", "RGB")
        image = QImage(
            data,
            resized.width,
            resized.height,
            resized.width * 3,
            QImage.Format_RGB888,
        ).copy()
        pixmap = QPixmap.fromImage(image)
        self._pixmap_cache = pixmap
        self._pixmap_key = key
        return pixmap

    def _paint_close_button(self, painter: QPainter) -> None:
        """Draw Close Cross Only While The Pointer Is Over The Overlay."""
        if not self._pointer_inside:
            return
        rect = QRectF(self._close_button_rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 24, 200))
        painter.drawEllipse(rect)
        pen = QPen(QColor(255, 255, 255, 240), max(2.2, rect.width() * 0.08))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        inset = rect.width() * 0.28
        painter.drawLine(
            QPoint(int(rect.left() + inset), int(rect.top() + inset)),
            QPoint(int(rect.right() - inset), int(rect.bottom() - inset)),
        )
        painter.drawLine(
            QPoint(int(rect.right() - inset), int(rect.top() + inset)),
            QPoint(int(rect.left() + inset), int(rect.bottom() - inset)),
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        """Paint Circular Camera Feed, Border, And Close Button."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRect(0, 0, self.width(), self.height())
        clip = QPainterPath()
        clip.addEllipse(QRectF(rect))
        painter.setClipPath(clip)

        if self._frame is not None:
            painter.drawPixmap(rect, self._frame_to_pixmap(self._frame))
        else:
            painter.fillRect(rect, QColor(28, 28, 32, 230))
            painter.setPen(QColor(220, 220, 220))
            painter.drawText(
                rect,
                Qt.AlignCenter | Qt.TextWordWrap,
                self._error or "Starting camera...",
            )

        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 210), 3))
        painter.setBrush(Qt.NoBrush)
        inset = 1.5
        painter.drawEllipse(
            QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2)
        )
        self._paint_close_button(painter)
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        """Show Close Control While The Pointer Is Over The Overlay."""
        self._pointer_inside = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        """Hide Close Control When The Pointer Leaves The Overlay."""
        self._pointer_inside = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """Start Drag, Resize, Or Close Interaction."""
        if event.button() != Qt.LeftButton:
            return
        if self._pointer_inside and self._close_button_rect().contains(event.pos()):
            self.close()
            return
        if self._is_on_resize_edge(event.pos()):
            self._resizing = True
            self._resize_start_pos = event.globalPos()
            self._resize_start_size = self._size
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """Move Window Or Change Diameter."""
        if not self._pointer_inside:
            self._pointer_inside = True
            self.update()
        if self._resizing and self._resize_start_pos is not None:
            delta = event.globalPos() - self._resize_start_pos
            growth = int((delta.x() + delta.y()) / 2)
            self._apply_geometry(self._resize_start_size + growth)
            return
        if self._drag_offset is not None:
            self.move(event.globalPos() - self._drag_offset)
            return
        if self._pointer_inside and self._close_button_rect().contains(event.pos()):
            self.setCursor(Qt.PointingHandCursor)
        elif self._is_on_resize_edge(event.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        """End Drag Or Resize Interaction."""
        if event.button() != Qt.LeftButton:
            return
        self._resizing = False
        self._drag_offset = None
        self._resize_start_pos = None
        if self._pointer_inside and self._close_button_rect().contains(event.pos()):
            self.setCursor(Qt.PointingHandCursor)
        elif self._is_on_resize_edge(event.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Zoom Camera Content With Mouse Wheel."""
        delta = event.angleDelta().y()
        if delta == 0:
            return
        direction = 1 if delta > 0 else -1
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom + direction * ZOOM_STEP))
        self._pixmap_cache = None
        self._pixmap_key = None
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Close Overlay On Escape."""
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        """Stop Camera Worker And Notify Listeners."""
        self._repaint_timer.stop()
        self._worker.stop()
        self._worker.wait(2000)
        self.closed_by_user.emit()
        super().closeEvent(event)
