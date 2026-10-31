from __future__ import annotations
import sys
import io
import urllib.request
import urllib.parse
import math
import xml.etree.ElementTree as ET
import re
import json
from typing import List, Tuple, Optional, Dict, Any

import shapely
from shapely.geometry import Polygon, MultiPolygon, LineString, LinearRing, MultiPoint
import numpy as np

from PySide6.QtCore import (
    Qt, QPointF, QRectF, Signal, QObject, QUrl, QThread, QSize, QElapsedTimer
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QWheelEvent, QMouseEvent, QKeyEvent,
    QPolygonF, QPainterPath, QTransform, QImage, QPixmap, QFont,
    QCursor, QAction, QKeySequence, QIcon, QClipboard, QGuiApplication
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QLabel, QDoubleSpinBox, QSpinBox, QComboBox,
    QCheckBox, QSlider, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QMessageBox, QColorDialog, QSplitter, QGraphicsView,
    QGraphicsScene, QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem, QProgressBar,
    QFrame, QToolButton, QSizePolicy, QStatusBar, QLineEdit, QGraphicsSceneMouseEvent,
    QStyle
)
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from svg_engine import SvgDocumentImporter
from raster_engine import RasterImageProcessor
from cam_engine import GCodePostProcessor
from gcode_visualizer import GCodeVisualizerDialog
from jog_panel import JogPanelWidget
from rulers import CanvasWithRulers
from path_optimizer import PathOptimizer
from project_manager import ProjectSerializer
from time_estimator import CycleTimeEstimator


# ==============================================================================
# WARSTWA DANYCH I MODELU
# ==============================================================================

class LayerConfig:
    def __init__(self, name: str, color: QColor, mode: str = "Line", speed: float = 1200.0, power: float = 80.0, passes: int = 1, kerf: float = 0.15):
        self.name = name
        self.color = color
        self.mode = mode         # "Line", "Fill", "Image"
        self.speed = speed       # mm/min
        self.power = power       # %
        self.passes = passes
        self.kerf = kerf         # Szerokość rzazu w mm
        self.output = True
        self.visible = True


class MachineModel(QObject):
    """Model maszyny i wirtualna pozycja głowicy."""
    position_changed = Signal(float, float)

    def __init__(self, bed_w=300.0, bed_h=300.0):
        super().__init__()
        self.bed_w = bed_w
        self.bed_h = bed_h
        self.x = 0.0
        self.y = 0.0

    def jog_to(self, x: float, y: float):
        self.x = max(0.0, min(self.bed_w, x))
        self.y = max(0.0, min(self.bed_h, y))
        self.position_changed.emit(self.x, self.y)

    def set_zero(self):
        self.x = 0.0
        self.y = 0.0
        self.position_changed.emit(self.x, self.y)


# ==============================================================================
# ELEMENTY GRAFICZNE KANWY (CAD ENGINE)
# ==============================================================================

class LaserReticleItem(QGraphicsItem):
    """Krzyżak pozycji lasera (wskaźnik optyczny)."""
    def __init__(self, size=14):
        super().__init__()
        self.size = size
        self.setZValue(1000)

    def boundingRect(self):
        return QRectF(-self.size, -self.size, self.size * 2, self.size * 2)

    def paint(self, painter: QPainter, option, widget=None):
        pen = QPen(QColor(255, 40, 40, 220), 1.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(-self.size, 0), QPointF(self.size, 0))
        painter.drawLine(QPointF(0, -self.size), QPointF(0, self.size))
        painter.drawEllipse(QPointF(0, 0), 2.5, 2.5)


class AlignmentGuideItem(QGraphicsItem):
    """Wizualne linie pomocnicze CAD (pokazywane podczas przyciągania)."""
    def __init__(self, bed_w=300.0, bed_h=300.0):
        super().__init__()
        self.bed_w = bed_w
        self.bed_h = bed_h
        self.setZValue(850)
        self.v_lines: List[float] = []
        self.h_lines: List[float] = []

    def set_guides(self, v_lines: List[float], h_lines: List[float]):
        self.prepareGeometryChange()
        self.v_lines = v_lines
        self.h_lines = h_lines
        self.update()

    def clear(self):
        if self.v_lines or self.h_lines:
            self.prepareGeometryChange()
            self.v_lines = []
            self.h_lines = []
            self.update()

    def boundingRect(self):
        return QRectF(-50, -50, self.bed_w + 100, self.bed_h + 100)

    def paint(self, painter: QPainter, option, widget=None):
        if not self.v_lines and not self.h_lines:
            return

        pen = QPen(QColor(0, 220, 255, 200), 1.0, Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)

        for x in self.v_lines:
            painter.drawLine(QPointF(x, -20), QPointF(x, self.bed_h + 20))

        for y in self.h_lines:
            painter.drawLine(QPointF(-20, y), QPointF(self.bed_w + 20, y))


class SnapEngine:
    """Silnik kalkulacji przyciągania do siatki i krawędzi innych elementów."""
    def __init__(self, tolerance_mm: float = 2.0, grid_size_mm: float = 1.0):
        self.tolerance = tolerance_mm
        self.grid_size = grid_size_mm
        self.snap_to_grid = True
        self.snap_to_objects = True

    def calculate_snap(
        self, 
        current_rect: QRectF, 
        scene: QGraphicsScene, 
        ignore_items: List[QGraphicsItem]
    ) -> Tuple[float, float, List[float], List[float]]:
        best_dx = 0.0
        best_dy = 0.0
        min_dist_x = self.tolerance
        min_dist_y = self.tolerance
        guide_x: List[float] = []
        guide_y: List[float] = []

        my_x_targets = [current_rect.left(), current_rect.center().x(), current_rect.right()]
        my_y_targets = [current_rect.bottom(), current_rect.center().y(), current_rect.top()]

        if self.snap_to_objects:
            other_x_targets = []
            other_y_targets = []

            for item in scene.items():
                if item in ignore_items or not item.isVisible() or item.zValue() > 800 or item.zValue() < 0:
                    continue
                b = item.sceneBoundingRect()
                if b.width() <= 0 or b.height() <= 0:
                    continue
                other_x_targets.extend([b.left(), b.center().x(), b.right()])
                other_y_targets.extend([b.bottom(), b.center().y(), b.top()])

            for mx in my_x_targets:
                for ox in other_x_targets:
                    dist = ox - mx
                    if abs(dist) < abs(min_dist_x):
                        min_dist_x = dist
                        best_dx = dist
                        guide_x = [ox]

            for my in my_y_targets:
                for oy in other_y_targets:
                    dist = oy - my
                    if abs(dist) < abs(min_dist_y):
                        min_dist_y = dist
                        best_dy = dist
                        guide_y = [oy]

        if self.snap_to_grid:
            if best_dx == 0.0:
                for mx in my_x_targets:
                    grid_target = round(mx / self.grid_size) * self.grid_size
                    dist = grid_target - mx
                    if abs(dist) < abs(min_dist_x):
                        min_dist_x = dist
                        best_dx = dist
                        guide_x = [grid_target]

            if best_dy == 0.0:
                for my in my_y_targets:
                    grid_target = round(my / self.grid_size) * self.grid_size
                    dist = grid_target - my
                    if abs(dist) < abs(min_dist_y):
                        min_dist_y = dist
                        best_dy = dist
                        guide_y = [grid_target]

        return best_dx, best_dy, guide_x, guide_y


class TransformBoxItem(QGraphicsItem):
    """Manipulator CAD z obsługą wielokrotnego zaznaczenia, skalowania i rotacji."""
    HANDLE_SIZE = 7.0
    ROT_HANDLE_OFFSET = 24.0

    NONE = 0
    SCALE_TL = 1
    SCALE_TR = 2
    SCALE_BR = 3
    SCALE_BL = 4
    ROTATE = 5
    DRAG_MOVE = 6

    def __init__(self, snap_engine: SnapEngine, guides: AlignmentGuideItem, parent=None):
        super().__init__(parent)
        self.setZValue(950)
        self.targets: List[QGraphicsItem] = []
        self.snap_engine = snap_engine
        self.guides = guides

        self._active_handle = self.NONE
        self._drag_start_scene = QPointF()
        self._initial_target_transforms: dict[QGraphicsItem, QTransform] = {}
        self._initial_target_positions: dict[QGraphicsItem, QPointF] = {}
        self._initial_bbox_scene = QRectF()
        self._pivot_scene = QPointF()

    def set_targets(self, items: List[QGraphicsItem]):
        self.targets = [it for it in items if it.isVisible()]
        if self.targets:
            self.setVisible(True)
            self.update_geometry()
        else:
            self.setVisible(False)
            self.guides.clear()

    def set_target(self, item: Optional[QGraphicsItem]):
        self.set_targets([item] if item else [])

    @property
    def target(self) -> QGraphicsItem | None:
        return self.targets[0] if self.targets else None

    def get_combined_scene_rect(self) -> QRectF:
        if not self.targets:
            return QRectF()
        rect = self.targets[0].sceneBoundingRect()
        for it in self.targets[1:]:
            rect = rect.united(it.sceneBoundingRect())
        return rect

    def update_geometry(self):
        if not self.targets:
            return
        self.prepareGeometryChange()
        rect = self.get_combined_scene_rect()
        self.setPos(rect.topLeft())
        self.update()

    def boundingRect(self):
        if not self.targets:
            return QRectF()
        r = self.get_combined_scene_rect()
        pad = self.HANDLE_SIZE + self.ROT_HANDLE_OFFSET + 6.0
        return QRectF(-pad, -pad - self.ROT_HANDLE_OFFSET, r.width() + 2 * pad, r.height() + 2 * pad + self.ROT_HANDLE_OFFSET)

    def _get_local_rect(self) -> QRectF:
        r = self.get_combined_scene_rect()
        return QRectF(0, 0, r.width(), r.height())

    def _get_local_handle_rects(self) -> Dict[int, QRectF]:
        r = self._get_local_rect()
        s = self.HANDLE_SIZE
        return {
            self.SCALE_TL: QRectF(r.left() - s/2, r.top() - s/2, s, s),
            self.SCALE_TR: QRectF(r.right() - s/2, r.top() - s/2, s, s),
            self.SCALE_BR: QRectF(r.right() - s/2, r.bottom() - s/2, s, s),
            self.SCALE_BL: QRectF(r.left() - s/2, r.bottom() - s/2, s, s),
            self.ROTATE:   QRectF(r.center().x() - s/2, r.top() - self.ROT_HANDLE_OFFSET - s/2, s, s)
        }

    def paint(self, painter: QPainter, option, widget=None):
        if not self.targets or not any(it.isSelected() for it in self.targets):
            return

        painter.setRenderHint(QPainter.Antialiasing, True)
        r = self._get_local_rect()

        pen_box = QPen(QColor(0, 150, 255, 220), 1.0, Qt.DashLine)
        pen_box.setCosmetic(True)
        painter.setPen(pen_box)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)

        pen_stem = QPen(QColor(0, 150, 255, 160), 1.0)
        pen_stem.setCosmetic(True)
        painter.setPen(pen_stem)
        painter.drawLine(QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.top() - self.ROT_HANDLE_OFFSET))

        h_pen = QPen(QColor(0, 120, 220), 1.2)
        h_pen.setCosmetic(True)
        painter.setPen(h_pen)
        painter.setBrush(QBrush(QColor(255, 255, 255)))

        rects = self._get_local_handle_rects()
        for h_id in (self.SCALE_TL, self.SCALE_TR, self.SCALE_BR, self.SCALE_BL):
            painter.drawRect(rects[h_id])

        painter.setPen(QPen(QColor(40, 180, 80), 1.2))
        painter.setBrush(QBrush(QColor(200, 255, 210)))
        painter.drawEllipse(rects[self.ROTATE])

    def _hit_test(self, local_pos: QPointF) -> int:
        if not self.targets:
            return self.NONE
        rects = self._get_local_handle_rects()
        for h_id, rect in rects.items():
            if rect.adjusted(-3, -3, 3, 3).contains(local_pos):
                return h_id
        if self._get_local_rect().contains(local_pos):
            return self.DRAG_MOVE
        return self.NONE

    def hoverMoveEvent(self, event):
        h = self._hit_test(event.pos())
        if h in (self.SCALE_TL, self.SCALE_BR):
            self.setCursor(QCursor(Qt.SizeFDiagCursor))
        elif h in (self.SCALE_TR, self.SCALE_BL):
            self.setCursor(QCursor(Qt.SizeBDiagCursor))
        elif h == self.ROTATE:
            self.setCursor(QCursor(Qt.CrossCursor))
        elif h == self.DRAG_MOVE:
            self.setCursor(QCursor(Qt.SizeAllCursor))
        else:
            self.setCursor(QCursor(Qt.ArrowCursor))
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() != Qt.LeftButton or not self.targets:
            super().mousePressEvent(event)
            return

        h = self._hit_test(event.pos())
        if h != self.NONE:
            self._active_handle = h
            self._drag_start_scene = event.scenePos()
            self._initial_bbox_scene = self.get_combined_scene_rect()

            self._initial_target_transforms = {it: QTransform(it.transform()) for it in self.targets}
            self._initial_target_positions = {it: it.pos() for it in self.targets}

            r = self._initial_bbox_scene
            if h == self.SCALE_BR:
                self._pivot_scene = r.topLeft()
            elif h == self.SCALE_BL:
                self._pivot_scene = r.topRight()
            elif h == self.SCALE_TR:
                self._pivot_scene = r.bottomLeft()
            elif h == self.SCALE_TL:
                self._pivot_scene = r.bottomRight()
            else:
                self._pivot_scene = r.center()

            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
        if self._active_handle == self.NONE or not self.targets:
            super().mouseMoveEvent(event)
            return

        curr_scene_pos = event.scenePos()

        # 1. Przesuwanie
        if self._active_handle == self.DRAG_MOVE:
            raw_delta = curr_scene_pos - self._drag_start_scene
            tentative_rect = self._initial_bbox_scene.translated(raw_delta.x(), raw_delta.y())

            dx, dy, gx, gy = self.snap_engine.calculate_snap(
                tentative_rect, self.scene(), ignore_items=[self, self.guides] + self.targets
            )

            effective_delta = raw_delta + QPointF(dx, dy)
            for it in self.targets:
                it.setPos(self._initial_target_positions[it] + effective_delta)

            self.guides.set_guides(gx, gy)
            self.update_geometry()
            event.accept()
            return

        # 2. Skalowanie
        if self._active_handle in (self.SCALE_TL, self.SCALE_TR, self.SCALE_BR, self.SCALE_BL):
            v_init = self._drag_start_scene - self._pivot_scene
            v_curr = curr_scene_pos - self._pivot_scene
            len_init = math.hypot(v_init.x(), v_init.y())
            len_curr = math.hypot(v_curr.x(), v_curr.y())

            if len_init > 1e-4:
                dot = v_curr.x() * v_init.x() + v_curr.y() * v_init.y()
                sign = 1.0 if dot >= 0 else -1.0
                scale_factor = (len_curr / len_init) * sign
                scale_factor = max(0.02, scale_factor)

                t_global = QTransform()
                t_global.translate(self._pivot_scene.x(), self._pivot_scene.y())
                t_global.scale(scale_factor, scale_factor)
                t_global.translate(-self._pivot_scene.x(), -self._pivot_scene.y())

                for it in self.targets:
                    init_pos = self._initial_target_positions[it]
                    new_pos = t_global.map(init_pos)
                    it.setPos(new_pos)
                    
                    t_scale = QTransform()
                    t_scale.scale(scale_factor, scale_factor)
                    it.setTransform(self._initial_target_transforms[it] * t_scale)

                self.update_geometry()
                event.accept()
                return

        # 3. Rotacja
        if self._active_handle == self.ROTATE:
            v_start = self._drag_start_scene - self._pivot_scene
            v_curr = curr_scene_pos - self._pivot_scene

            a_start = math.atan2(v_start.y(), v_start.x())
            a_curr = math.atan2(v_curr.y(), v_curr.x())
            delta_deg = math.degrees(a_curr - a_start)

            if event.modifiers() & Qt.ShiftModifier:
                delta_deg = round(delta_deg / 15.0) * 15.0

            t_rot = QTransform()
            t_rot.translate(self._pivot_scene.x(), self._pivot_scene.y())
            t_rot.rotate(delta_deg)
            t_rot.translate(-self._pivot_scene.x(), -self._pivot_scene.y())

            for it in self.targets:
                init_pos = self._initial_target_positions[it]
                it.setPos(t_rot.map(init_pos))
                
                t_local = QTransform()
                t_local.rotate(delta_deg)
                it.setTransform(self._initial_target_transforms[it] * t_local)

            self.guides.clear()
            self.update_geometry()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if self._active_handle != self.NONE:
            self._active_handle = self.NONE
            self.guides.clear()
            self.setCursor(QCursor(Qt.ArrowCursor))
            self.update_geometry()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CanvasView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor(28, 28, 30)))
        self.setDragMode(QGraphicsView.RubberBandDrag)

        self._panning = False
        self._pan_start = QPointF()
        self._space_pressed = False

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            main_window = self.window()
            if hasattr(main_window, "delete_selected_items"):
                main_window.delete_selected_items()
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            self.setDragMode(QGraphicsView.RubberBandDrag)
            if not self._panning:
                self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)
        parent = self.parentWidget()
        if hasattr(parent, "update_rulers"):
            parent.update_rulers()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        parent = self.parentWidget()
        if hasattr(parent, "update_rulers"):
            parent.update_rulers()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self._space_pressed):
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return

        super().mouseMoveEvent(event)
        win = self.window()
        if hasattr(win, "transform_box") and win.transform_box and win.transform_box.isVisible():
            win.transform_box.update_geometry()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor if self._space_pressed else Qt.ArrowCursor)
            if not self._space_pressed:
                self.setDragMode(QGraphicsView.RubberBandDrag)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CollapsibleBox(QWidget):
    def __init__(self, title="", parent=None, collapsed=False):
        super().__init__(parent)
        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(not collapsed)
        self.toggle_button.setStyleSheet("""
            QToolButton {
                font-weight: bold;
                font-size: 11px;
                color: #e0e0e0;
                background-color: #2b2b30;
                border: 1px solid #3c3c44;
                border-radius: 4px;
                padding: 6px 10px;
                text-align: left;
            }
            QToolButton:hover {
                background-color: #383840;
                color: #ffffff;
            }
            QToolButton:checked {
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                background-color: #323238;
            }
        """)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.DownArrow if not collapsed else Qt.RightArrow)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle_button.clicked.connect(self._on_toggle)

        self.content_area = QFrame()
        self.content_area.setStyleSheet("""
            QFrame {
                background-color: #202024;
                border: 1px solid #3c3c44;
                border-top: none;
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
            }
        """)
        
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 4)
        layout.setSpacing(0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content_area)

        if collapsed:
            self.content_area.setVisible(False)

    def _on_toggle(self, checked):
        self.toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.content_area.setVisible(checked)

    def addLayout(self, layout):
        self.content_layout.addLayout(layout)

    def setContentLayout(self, layout):
        self.content_layout.addLayout(layout)

    def addWidget(self, widget):
        self.content_layout.addWidget(widget)


class LaserCrosshairItem(QGraphicsItem):
    """Celownik głowicy lasera o stałym rozmiarze na ekranie niezależnie od zoomu."""
    def __init__(self, radius: float = 8.0, parent=None):
        super().__init__(parent)
        self.radius = float(radius)
        self.setZValue(9999)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)

        self.pen_ring = QPen(QColor(255, 40, 40, 230), 1.8)
        self.pen_ring.setCosmetic(True)

        self.pen_cross = QPen(QColor(255, 60, 60, 210), 1.2)
        self.pen_cross.setCosmetic(True)

        self.brush_center = QBrush(QColor(255, 0, 0, 255))

    def boundingRect(self) -> QRectF:
        r = getattr(self, "radius", 8.0) + 15.0
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(self.pen_ring)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

        painter.setPen(self.pen_cross)
        arm = self.radius + 6.0
        painter.drawLine(QPointF(-arm, 0), QPointF(arm, 0))
        painter.drawLine(QPointF(0, -arm), QPointF(0, arm))

        painter.setBrush(self.brush_center)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(0, 0), 2.0, 2.0)


class GCodeUploadWorker(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, host_url: str, gcode_content: str, filename: str = "job.nc"):
        super().__init__()
        clean_host = host_url.replace("ws://", "http://").replace("wss://", "https://")
        if clean_host.endswith("/ws"):
            clean_host = clean_host[:-3]
        self.target_url = f"{clean_host}/api/files/local"
        self.gcode_content = gcode_content
        self.filename = filename

    def run(self):
        try:
            boundary = "----LaserCAMBoundaryWebKitFormBoundary"
            body = io.BytesIO()

            body.write(f"--{boundary}\r\n".encode("utf-8"))
            body.write(b'Content-Disposition: form-data; name="print"\r\n\r\n')
            body.write(b"true\r\n")

            body.write(f"--{boundary}\r\n".encode("utf-8"))
            body.write(
                f'Content-Disposition: form-data; name="file"; filename="{self.filename}"\r\n'.encode("utf-8")
            )
            body.write(b"Content-Type: text/plain\r\n\r\n")
            body.write(self.gcode_content.encode("utf-8"))
            body.write(b"\r\n")
            body.write(f"--{boundary}--\r\n".encode("utf-8"))

            payload = body.getvalue()

            req = urllib.request.Request(
                self.target_url,
                data=payload,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(payload)),
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in (200, 201):
                    self.finished_signal.emit(True, "Zadanie wysłane. Rozpoczęto wypalanie!")
                else:
                    self.finished_signal.emit(False, f"Serwer zwrócił kod {response.status}")
        except Exception as e:
            self.finished_signal.emit(False, f"Błąd transmisji: {e}")


# ==============================================================================
# GŁÓWNE OKNO APLIKACJI — LASERBURN STUDIO
# ==============================================================================

class LaserBurnStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LaserBurn Studio — Stół roboczy")
        self.resize(1440, 900)

        self.bed_w = 300.0
        self.bed_h = 300.0

        # Wewnętrzny schowek CAD
        self._internal_clipboard: List[Dict[str, Any]] = []

        self.layers: List[LayerConfig] = [
            LayerConfig("00 - Czarny (Grawer)", QColor("#000000"), mode="Line", speed=3000.0, power=25.0, passes=1, kerf=0.00),
            LayerConfig("01 - Niebieski (Fill)", QColor("#0000FF"), mode="Fill", speed=2000.0, power=45.0, passes=1, kerf=0.00),
            LayerConfig("02 - Czerwony (Cięcie)", QColor("#FF0000"), mode="Line", speed=450.0,  power=90.0, passes=2, kerf=0.15),
            LayerConfig("03 - Zielony (Raster)", QColor("#00C850"), mode="Image", speed=3500.0, power=60.0, passes=1, kerf=0.00),
        ]

        self.machine = MachineModel(self.bed_w, self.bed_h)
        self.boundary_points: List[Tuple[float, float]] = []
        self.boundary_item: Optional[QGraphicsItem] = None
        self.boundary_markers: List[QGraphicsItem] = []
        self.shapely_material_poly: Optional[Polygon] = None
        self.centroid_item = None

        self.lbl_job_time = QLabel("Czas: --:--")
        self.progress_bar = QProgressBar()
        self.lbl_telemetry = QLabel(" GRBL: Rozłączony ")

        self._setup_ui()
        self._setup_canvas()
        self._connect_signals()

        self.crosshair = LaserCrosshairItem(radius=8.0)
        self.crosshair.setVisible(False)
        self.scene.addItem(self.crosshair)

        self.ws_client = QWebSocket()
        if hasattr(self, "jog_widget") and self.jog_widget:
            self.jog_widget.set_ws_client(self.ws_client)

        self.ws_client.connected.connect(self._on_ws_connected)
        self.ws_client.disconnected.connect(self._on_ws_disconnected)
        self.ws_client.textMessageReceived.connect(self._on_ws_message)
        self.ws_client.errorOccurred.connect(self._on_ws_error)

        self.estimator = CycleTimeEstimator(default_rapid_f=6000.0, accel_mm_s2=500.0)
        self.estimated_total_time_s = 0.0
        self.job_timer = QElapsedTimer()

        self.view.scale(2.2, -2.2)
        self.view.centerOn(self.bed_w / 2, self.bed_h / 2)

    # =========================================================================
    # DEFINICJE AKCJI, MENU I PASKÓW NARZĘDZIOWYCH
    # =========================================================================

    def _setup_actions(self):
        """Inicjalizacja wszystkich akcji współdzielonych między menu i paskami narzędzi."""

        # =====================================================================
        # 1. PROJEKT (Czyste ikony tekstowe / Unicode)
        # =====================================================================
        self.act_new = QAction("📄 Nowy projekt", self)
        self.act_new.setShortcut(QKeySequence.New)
        self.act_new.setToolTip("Utwórz nowy pusty projekt (Ctrl+N)")
        self.act_new.triggered.connect(self.new_project)

        self.act_open = QAction("📂 Otwórz projekt...", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.setToolTip("Wczytaj projekt LaserBurn (.lcam) (Ctrl+O)")
        self.act_open.triggered.connect(self.open_project)

        self.act_save = QAction("💾 Zapisz projekt", self)
        self.act_save.setShortcut(QKeySequence.Save)
        self.act_save.setToolTip("Zapisz bieżący projekt do pliku (Ctrl+S)")
        self.act_save.triggered.connect(self.save_project)

        self.act_import_svg = QAction("📐 Importuj SVG...", self)
        self.act_import_svg.setShortcut(QKeySequence("Ctrl+I"))
        self.act_import_svg.setToolTip("Importuj geometrię wektorową SVG (Ctrl+I)")
        self.act_import_svg.triggered.connect(self._import_svg)

        self.act_import_raster = QAction("🖼️ Importuj grafikę rastrową...", self)
        self.act_import_raster.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self.act_import_raster.setToolTip("Wczytaj grafikę rastrową pod dithering (Ctrl+Shift+I)")
        self.act_import_raster.triggered.connect(self._import_raster)

        self.act_export_gcode = QAction("⚙️ Eksportuj plik G-code...", self)
        self.act_export_gcode.setShortcut(QKeySequence("Ctrl+E"))
        self.act_export_gcode.setToolTip("Kompiluj projekt do pliku .nc (Ctrl+E)")
        self.act_export_gcode.triggered.connect(self._export_project_gcode)

        # =====================================================================
        # 2. EDYCJA I SCHOWEK
        # =====================================================================
        self.act_copy = QAction("📋 Kopiuj", self)
        self.act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        self.act_copy.setToolTip("Kopiuj zaznaczone obiekty do schowka (Ctrl+C)")
        self.act_copy.triggered.connect(self.copy_selected)

        self.act_paste = QAction("📥 Wklej", self)
        self.act_paste.setShortcut(QKeySequence.StandardKey.Paste)
        self.act_paste.setToolTip("Wklej obiekty ze schowka (Ctrl+V)")
        self.act_paste.triggered.connect(self.paste_from_clipboard)

        self.act_duplicate = QAction("📑 Duplikuj", self)
        self.act_duplicate.setShortcut(QKeySequence("Ctrl+D"))
        self.act_duplicate.setToolTip("Klonuj zaznaczone elementy obok (Ctrl+D)")
        self.act_duplicate.triggered.connect(self.duplicate_selected)

        self.act_delete = QAction("🗑️ Usuń", self)
        self.act_delete.setShortcut(QKeySequence.Delete)
        self.act_delete.setToolTip("Usuń zaznaczone obiekty (Delete / Backspace)")
        self.act_delete.triggered.connect(self.delete_selected_items)

        # =====================================================================
        # 3. POZIOMY Z (Z-ORDER)
        # =====================================================================
        self.act_bring_front = QAction("⏫ Na wierzch", self)
        self.act_bring_front.setShortcut(QKeySequence("Ctrl+]"))
        self.act_bring_front.setToolTip("Przenieś zaznaczony obiekt na sam wierzch (Ctrl+])")
        self.act_bring_front.triggered.connect(lambda: self.adjust_z_order('front'))

        self.act_send_back = QAction("⏬ Na spód", self)
        self.act_send_back.setShortcut(QKeySequence("Ctrl+["))
        self.act_send_back.setToolTip("Przenieś zaznaczony obiekt na spód nad materiał (Ctrl+[)")
        self.act_send_back.triggered.connect(lambda: self.adjust_z_order('back'))

        self.act_layer_up = QAction("🔼 Poziom wyżej", self)
        self.act_layer_up.setShortcut(QKeySequence("Ctrl+Up"))
        self.act_layer_up.setToolTip("Przesuń o 1 poziom do góry (Ctrl+Góra)")
        self.act_layer_up.triggered.connect(lambda: self.adjust_z_order('up'))

        self.act_layer_down = QAction("🔽 Poziom niżej", self)
        self.act_layer_down.setShortcut(QKeySequence("Ctrl+Down"))
        self.act_layer_down.setToolTip("Przesuń o 1 poziom w dół (Ctrl+Dół)")
        self.act_layer_down.triggered.connect(lambda: self.adjust_z_order('down'))

        # =====================================================================
        # 4. MASZYNA I STEROWANIE
        # =====================================================================
        self.act_connect = QAction("⚡ Połącz / Rozłącz z maszyną", self)
        self.act_connect.setShortcut(QKeySequence("F2"))
        self.act_connect.triggered.connect(self._toggle_ws_connection)

        self.act_frame = QAction("⛶ Ramka obrysu (Framing 0.5%)", self)
        self.act_frame.setShortcut(QKeySequence("F6"))
        self.act_frame.triggered.connect(self.frame_project)

        self.act_start = QAction("▶ Wypal / Rozpocznij zadanie", self)
        self.act_start.setShortcut(QKeySequence("F5"))
        self.act_start.triggered.connect(self.start_job_on_device)

        self.act_stop = QAction("🛑 Awaryjne Zatrzymanie (E-STOP)", self)
        self.act_stop.setShortcut(QKeySequence("F12"))
        self.act_stop.triggered.connect(self.emergency_stop)

        self.act_sim = QAction("🔍 Podgląd ścieżek (Symulacja)...", self)
        self.act_sim.setShortcut(QKeySequence("F7"))
        self.act_sim.triggered.connect(self._open_gcode_simulation)

    def _setup_menus(self):
        menubar = self.menuBar()

        menu_project = menubar.addMenu("&Projekt")
        menu_project.addAction(self.act_new)
        menu_project.addAction(self.act_open)
        menu_project.addAction(self.act_save)
        menu_project.addSeparator()
        menu_project.addAction(self.act_import_svg)
        menu_project.addAction(self.act_import_raster)
        menu_project.addSeparator()
        menu_project.addAction(self.act_export_gcode)

        menu_edit = menubar.addMenu("&Edycja")
        menu_edit.addAction(self.act_copy)
        menu_edit.addAction(self.act_paste)
        menu_edit.addAction(self.act_duplicate)
        menu_edit.addAction(self.act_delete)
        menu_edit.addSeparator()
        menu_edit.addAction(self.act_bring_front)
        menu_edit.addAction(self.act_layer_up)
        menu_edit.addAction(self.act_layer_down)
        menu_edit.addAction(self.act_send_back)

        menu_machine = menubar.addMenu("&Maszyna")
        menu_machine.addAction(self.act_connect)
        menu_machine.addSeparator()
        menu_machine.addAction(self.act_frame)
        menu_machine.addAction(self.act_start)
        menu_machine.addAction(self.act_stop)
        menu_machine.addSeparator()
        menu_machine.addAction(self.act_sim)

    def _setup_project_toolbar(self):
        self.proj_toolbar = QToolBar("Pasek Maszyny")
        self.proj_toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.proj_toolbar)

        self.proj_toolbar.addAction(self.act_new)
        self.proj_toolbar.addAction(self.act_open)
        self.proj_toolbar.addAction(self.act_save)
        self.proj_toolbar.addSeparator()

        self.btn_tb_import = QToolButton()
        self.btn_tb_import.setText("📂")
        self.btn_tb_import.setToolTip("Importuj wektory SVG (Ctrl+I)")
        self.btn_tb_import.setStyleSheet("""
            QToolButton {
                font-size: 15px;
                padding: 4px 8px;
                border: 1px solid #3c3c44;
                border-radius: 4px;
                background-color: #2b2b30;
            }
            QToolButton:hover { background-color: #383840; }
        """)
        self.btn_tb_import.clicked.connect(self._import_svg)
        self.proj_toolbar.addWidget(self.btn_tb_import)

        self.input_ws_url = QLineEdit("ws://malina.local:8000/ws")
        self.input_ws_url.setFixedWidth(160)
        self.input_ws_url.setToolTip("Adres mostka GRBL (ws://malina.local:8000/ws)")
        self.proj_toolbar.addWidget(self.input_ws_url)

        self.btn_tb_connect = QToolButton()
        self.btn_tb_connect.setText("⚡")
        self.btn_tb_connect.setToolTip("Połącz / Rozłącz z Raspberry Pi (F2)")
        self.btn_tb_connect.setStyleSheet("""
            QToolButton {
                font-size: 15px;
                padding: 4px 8px;
                border: 1px solid #3c3c44;
                border-radius: 4px;
                background-color: #2b2b30;
                color: #ffaa00;
            }
            QToolButton:hover { background-color: #383840; }
        """)
        self.btn_tb_connect.clicked.connect(self._toggle_ws_connection)
        self.proj_toolbar.addWidget(self.btn_tb_connect)

        self.btn_tb_frame = QToolButton()
        self.btn_tb_frame.setText("⛶")
        self.btn_tb_frame.setToolTip("Obrysuj materiał (Framing 0.5% - F6)")
        self.btn_tb_frame.setStyleSheet("""
            QToolButton {
                font-size: 15px;
                padding: 4px 8px;
                border: 1px solid #107c41;
                border-radius: 4px;
                background-color: #107c41;
                color: white;
            }
            QToolButton:hover { background-color: #13914d; }
        """)
        self.btn_tb_frame.clicked.connect(self.frame_project)
        self.proj_toolbar.addWidget(self.btn_tb_frame)

        self.proj_toolbar.addSeparator()

        self.btn_tb_start = QToolButton()
        self.btn_tb_start.setText("▶")
        self.btn_tb_start.setToolTip("Kompiluj i Wypal na maszynie (F5)")
        self.btn_tb_start.setStyleSheet("""
            QToolButton {
                font-size: 15px;
                padding: 4px 12px;
                border: 1px solid #107c41;
                border-radius: 4px;
                background-color: #107c41;
                color: #ffffff;
                font-weight: bold;
            }
            QToolButton:hover { background-color: #13914d; }
            QToolButton:pressed { background-color: #0c572e; }
        """)
        self.btn_tb_start.clicked.connect(self.start_job_on_device)
        self.proj_toolbar.addWidget(self.btn_tb_start)

        self.btn_tb_stop = QToolButton()
        self.btn_tb_stop.setText("🛑")
        self.btn_tb_stop.setToolTip("ZATRZYMANIE AWARYJNE (F12)")
        self.btn_tb_stop.setStyleSheet("""
            QToolButton {
                font-size: 15px;
                padding: 4px 12px;
                border: 1px solid #d13438;
                border-radius: 4px;
                background-color: #a80000;
                color: #ffffff;
            }
            QToolButton:hover { background-color: #c90000; }
            QToolButton:pressed { background-color: #600000; }
        """)
        self.btn_tb_stop.clicked.connect(self.emergency_stop)
        self.proj_toolbar.addWidget(self.btn_tb_stop)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.proj_toolbar.addWidget(spacer)

        self.lbl_job_time = QLabel("Czas: --:--")
        self.lbl_job_time.setStyleSheet("""
            QLabel {
                color: #b0b0b8;
                font-family: monospace;
                font-size: 11px;
                font-weight: bold;
                padding: 0 4px;
            }
        """)
        self.proj_toolbar.addWidget(self.lbl_job_time)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(120)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #18181b;
                border: 1px solid #3c3c44;
                border-radius: 3px;
                text-align: center;
                font-size: 9px;
                font-weight: bold;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #04d361;
                border-radius: 2px;
            }
        """)
        self.proj_toolbar.addWidget(self.progress_bar)
        self.proj_toolbar.addSeparator()

        self.lbl_telemetry = QLabel(" GRBL: Rozłączony ")
        self.lbl_telemetry.setFixedHeight(22)
        self.lbl_telemetry.setStyleSheet("""
            QLabel {
                background-color: #2b2b32;
                color: #888888;
                padding: 2px 10px;
                border-radius: 4px;
                font-family: monospace;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        self.proj_toolbar.addWidget(self.lbl_telemetry)

    def _setup_edit_toolbar(self):
        """Pasek narzędzi 2: Narzędzia Edycji Graficznej (Schowek, Usuwanie, Z-Order)."""
        self.edit_toolbar = QToolBar("Pasek Edycji")
        self.edit_toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.edit_toolbar)
        self.insertToolBarBreak(self.edit_toolbar)

        # Operacje Schowka
        self.edit_toolbar.addAction(self.act_copy)
        self.edit_toolbar.addAction(self.act_paste)
        self.edit_toolbar.addAction(self.act_duplicate)
        self.edit_toolbar.addAction(self.act_delete)
        self.edit_toolbar.addSeparator()

        # Poziomy Z
        self.edit_toolbar.addAction(self.act_bring_front)
        self.edit_toolbar.addAction(self.act_layer_up)
        self.edit_toolbar.addAction(self.act_layer_down)
        self.edit_toolbar.addAction(self.act_send_back)

    def _setup_ui(self):
        self._setup_actions()
        self._setup_menus()
        self._setup_project_toolbar()
        self._setup_edit_toolbar()
        
        main_splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(main_splitter)

        self.scene = QGraphicsScene()
        self.view = CanvasView(self.scene)
        self.canvas_container = CanvasWithRulers(self.view)
        main_splitter.addWidget(self.canvas_container)

        # Panel boczny
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)
        right_panel.setFixedWidth(520)

        # 1. Warstwy
        self.box_layers = CollapsibleBox("Warstwy", collapsed=False)
        layers_layout = QVBoxLayout()
        layers_layout.setContentsMargins(6, 6, 6, 6)
        layers_layout.setSpacing(6)

        self.layer_table = QTableWidget()
        self.layer_table.setColumnCount(7)
        self.layer_table.setHorizontalHeaderLabels([
            "Warstwa", "Tryb", "F [mm/min]", "Moc [%]", "Pętla", "Kerf [mm]", "Wł."
        ])
        self.layer_table.verticalHeader().setVisible(False)
        self.layer_table.verticalHeader().setDefaultSectionSize(28)
        self.layer_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.layer_table.setSelectionMode(QTableWidget.SingleSelection)
        self.layer_table.setMinimumHeight(160)

        header = self.layer_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        self.layer_table.setColumnWidth(0, 115)
        self.layer_table.setColumnWidth(1, 70)
        self.layer_table.setColumnWidth(2, 80)
        self.layer_table.setColumnWidth(3, 65)
        self.layer_table.setColumnWidth(4, 50)
        self.layer_table.setColumnWidth(5, 65)
        self.layer_table.setColumnWidth(6, 35)

        layers_layout.addWidget(self.layer_table)

        h_layer_actions = QHBoxLayout()
        self.btn_add_layer = QPushButton("➕ Dodaj warstwę")
        self.btn_add_layer.setStyleSheet("background-color: #1e5a2c; font-weight: bold;")
        self.btn_add_layer.clicked.connect(self._create_new_layer)
        h_layer_actions.addWidget(self.btn_add_layer)

        self.btn_color_layer = QPushButton("🎨 Zmień kolor")
        self.btn_color_layer.clicked.connect(self._change_selected_layer_color)
        h_layer_actions.addWidget(self.btn_color_layer)

        self.btn_del_layer = QPushButton("🗑️ Usuń")
        self.btn_del_layer.setStyleSheet("background-color: #6a2525;")
        self.btn_del_layer.clicked.connect(self._delete_selected_layer)
        h_layer_actions.addWidget(self.btn_del_layer)
        layers_layout.addLayout(h_layer_actions)

        palette_layout = QHBoxLayout()
        palette_layout.setSpacing(4)
        preset_colors = [
            ("#000000", "C00", "#fff"), ("#0000FF", "C01", "#fff"), 
            ("#FF0000", "C02", "#fff"), ("#00C850", "C03", "#fff"),
            ("#E6C200", "C04", "#000"), ("#D800E6", "C05", "#fff"), 
            ("#00E5D8", "C06", "#000"), ("#808080", "C07", "#fff")
        ]
        for hex_code, code_name, text_col in preset_colors:
            btn_swatch = QPushButton(code_name)
            btn_swatch.setFixedSize(38, 22)
            btn_swatch.setStyleSheet(f"""
                QPushButton {{
                    background-color: {hex_code};
                    color: {text_col};
                    font-weight: bold;
                    font-size: 10px;
                    border: 1px solid #444;
                    border-radius: 3px;
                }}
                QPushButton:hover {{
                    border: 2px solid #ffffff;
                }}
            """)
            btn_swatch.clicked.connect(lambda checked=False, c=hex_code: self._apply_color_to_selected(c))
            palette_layout.addWidget(btn_swatch)
        layers_layout.addLayout(palette_layout)

        self.box_layers.setContentLayout(layers_layout)
        right_layout.addWidget(self.box_layers)

        # 2. Jog
        self.box_jog = CollapsibleBox("Sterowanie osiami (Jog)", collapsed=False)
        self.jog_widget = JogPanelWidget()
        self.box_jog.addWidget(self.jog_widget)
        right_layout.addWidget(self.box_jog)

        # 3. Plaster drewna
        self.box_material = CollapsibleBox("Plaster drewna", collapsed=True)
        mat_layout = QVBoxLayout()
        mat_layout.setContentsMargins(6, 6, 6, 6)
        mat_layout.setSpacing(6)

        mat_layout.addWidget(QLabel("1. Ustaw plamkę lasera na krawędzi plastra."))
        self.btn_sample_pt = QPushButton("📍 Zapisz punkt krawędzi")
        self.btn_sample_pt.setStyleSheet("background-color: #154c79; font-weight: bold; padding: 6px;")
        mat_layout.addWidget(self.btn_sample_pt)

        self.lbl_trace_status = QLabel("Zarejestrowane punkty: 0")
        mat_layout.addWidget(self.lbl_trace_status)

        self.chk_spline = QCheckBox("Wygładzaj krawędź (Splajn Catmull-Rom)")
        self.chk_spline.setChecked(True)
        mat_layout.addWidget(self.chk_spline)

        h_trace_btns = QHBoxLayout()
        self.btn_build_contour = QPushButton("Zamknij obrys")
        self.btn_build_contour.setEnabled(False)
        self.btn_clear_contour = QPushButton("Wyczyść")
        h_trace_btns.addWidget(self.btn_build_contour)
        h_trace_btns.addWidget(self.btn_clear_contour)
        mat_layout.addLayout(h_trace_btns)

        mat_layout.addSpacing(6)
        mat_layout.addWidget(QLabel("<b>Automatyczne dopasowanie (Auto-Fit):</b>"))

        fit_margin_layout = QHBoxLayout()
        fit_margin_layout.addWidget(QLabel("Margines [mm]:"))
        self.spin_fit_margin = QDoubleSpinBox()
        self.spin_fit_margin.setRange(0.0, 50.0)
        self.spin_fit_margin.setValue(10.0)
        fit_margin_layout.addWidget(self.spin_fit_margin)
        mat_layout.addLayout(fit_margin_layout)

        self.chk_allow_rot = QCheckBox("Zezwalaj na obrót (najlepszy kąt)")
        self.chk_allow_rot.setChecked(True)
        mat_layout.addWidget(self.chk_allow_rot)

        self.btn_auto_fit = QPushButton("🎯 Wpisz projekt w plaster")
        self.btn_auto_fit.setStyleSheet("background-color: #6c3483; font-weight: bold; padding: 7px;")
        self.btn_auto_fit.setEnabled(False)
        self.btn_auto_fit.clicked.connect(self._auto_fit_selected_to_material)
        mat_layout.addWidget(self.btn_auto_fit)

        self.box_material.setContentLayout(mat_layout)
        right_layout.addWidget(self.box_material)

        right_layout.addStretch()
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 1)

        self._populate_layer_table()

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Gotowy. Pole robocze: 300x300 mm")

    # =========================================================================
    # LOGIKA EDYCJI GRAFICZNEJ: SCHOWEK, Z-ORDER, USUWANIE
    # =========================================================================

    def copy_selected(self):
        """Kopiuje zaznaczone elementy projektu do bufora CAD."""
        self._internal_clipboard.clear()
        selected = self._get_user_selected_items()
        
        for item in selected:
            if isinstance(item, QGraphicsPathItem):
                self._internal_clipboard.append({
                    'type': 'path',
                    'path': QPainterPath(item.path()),
                    'pos': QPointF(item.pos()),
                    'transform': QTransform(item.transform()),
                    'pen': QPen(item.pen()),
                    'brush': QBrush(item.brush()),
                    'z': item.zValue(),
                    'data': item.data(0)
                })
            elif isinstance(item, QGraphicsPixmapItem):
                self._internal_clipboard.append({
                    'type': 'pixmap',
                    'pixmap': QPixmap(item.pixmap()),
                    'pos': QPointF(item.pos()),
                    'transform': QTransform(item.transform()),
                    'z': item.zValue(),
                    'data': item.data(0)
                })

        if hasattr(self, "status") and self.status:
            self.status.showMessage(f"Skopiowano do schowka: {len(self._internal_clipboard)} obiekt(ów)", 2500)

    def paste_from_clipboard(self):
        """Wkleja elementy ze schowka z przesunięciem (+5, -5 mm)."""
        if not self._internal_clipboard:
            return

        self.scene.clearSelection()
        pasted_items = []
        offset = QPointF(5.0, -5.0)

        for entry in self._internal_clipboard:
            new_item: Optional[QGraphicsItem] = None
            if entry['type'] == 'path':
                p_item = QGraphicsPathItem(entry['path'])
                p_item.setPen(entry['pen'])
                p_item.setBrush(entry['brush'])
                new_item = p_item
            elif entry['type'] == 'pixmap':
                new_item = QGraphicsPixmapItem(entry['pixmap'])

            if new_item is not None:
                new_pos = entry['pos'] + offset
                new_item.setPos(new_pos)
                new_item.setTransform(entry['transform'])
                new_item.setZValue(entry['z'] + 0.1)
                new_item.setData(0, entry['data'])

                new_item.setFlags(
                    QGraphicsItem.ItemIsSelectable |
                    QGraphicsItem.ItemIsMovable |
                    QGraphicsItem.ItemSendsGeometryChanges
                )

                self.scene.addItem(new_item)
                new_item.setSelected(True)
                pasted_items.append(new_item)

                entry['pos'] = new_pos

        if hasattr(self, "status") and self.status:
            self.status.showMessage(f"Wklejono: {len(pasted_items)} obiekt(ów)", 2500)

    def duplicate_selected(self):
        """Klonuje zaznaczone elementy obok w jednym kroku (Ctrl+D)."""
        if self._get_user_selected_items():
            self.copy_selected()
            self.paste_from_clipboard()

    def delete_selected_items(self):
        """Usuwa zaznaczone obiekty projektowe z zachowaniem elementów systemowych."""
        selected = self.scene.selectedItems()
        if not selected:
            return

        system_items = {
            getattr(self, attr, None) 
            for attr in ("crosshair", "laser_reticle", "boundary_item", "centroid_item", "transform_box", "guides")
        }
        if hasattr(self, "boundary_markers"):
            system_items.update(self.boundary_markers)

        deleted_count = 0
        for item in selected:
            if item in system_items or getattr(item, "is_system_item", False):
                continue
            self.scene.removeItem(item)
            deleted_count += 1

        if deleted_count > 0:
            if hasattr(self, "transform_box") and self.transform_box:
                self.transform_box.set_targets([])
                self.transform_box.setVisible(False)
            self.scene.update()
            if hasattr(self, "status") and self.status:
                self.status.showMessage(f"Usunięto {deleted_count} obiekt(ów).", 3000)

    def adjust_z_order(self, action: str):
        """Zarządza kolejnością warstw na scenie (Z-Index)."""
        selected = self._get_user_selected_items()
        if not selected:
            return

        user_items = [
            it for it in self.scene.items()
            if it not in (self.transform_box, self.laser_reticle, self.crosshair, self.boundary_item, self.guides)
            and 10 <= it.zValue() < 900
        ]
        if not user_items:
            return

        user_z = [it.zValue() for it in user_items]
        min_z = min(user_z)
        max_z = max(user_z)

        if action == 'front':
            for i, it in enumerate(selected):
                it.setZValue(max_z + 1.0 + (i * 0.1))
        elif action == 'back':
            new_floor = max(10.0, min_z - 1.0)
            for i, it in enumerate(selected):
                it.setZValue(new_floor + (i * 0.1))
        elif action == 'up':
            for it in selected:
                it.setZValue(min(890.0, it.zValue() + 1.0))
        elif action == 'down':
            for it in selected:
                it.setZValue(max(10.0, it.zValue() - 1.0))

        self.scene.update()
        if hasattr(self, "status") and self.status:
            self.status.showMessage(f"Zaktualizowano poziom Z obiektów ({action})", 2000)

    def _get_user_selected_items(self) -> List[QGraphicsItem]:
        return [
            item for item in self.scene.selectedItems()
            if item not in (self.transform_box, self.laser_reticle, self.crosshair, self.boundary_item, self.guides)
            and item.zValue() >= 10
        ]

    # =========================================================================
    # DEKOMPOZYCJA ŚCIEŻEK WEKTOROWYCH I GENEROWANIE G-CODE
    # =========================================================================

    def _path_item_to_polylines(self, item: QGraphicsPathItem) -> list[list[QPointF]]:
        """Dekomponuje obiekt wektorowy na listę polilinii w układzie sceny CAD."""
        local_path = item.path()
        t = item.sceneTransform()
        scene_path = t.map(local_path)

        flat_path = scene_path.toSubpathPolygons()
        polylines: list[list[QPointF]] = []

        for qpoly in flat_path:
            pts: list[QPointF] = [qpoly.at(i) for i in range(qpoly.count())]
            if len(pts) >= 2:
                polylines.append(pts)

        return polylines

    def _find_layer_for_item(self, item: QGraphicsPathItem) -> LayerConfig:
        """Dopasowuje obiekt do technologicznej warstwy lasera na podstawie koloru pióra."""
        item_color = item.pen().color().name().upper()
        for layer in self.layers:
            if layer.color.name().upper() == item_color:
                return layer
        return self.layers[0]

    def _generate_gcode(self) -> list[str]:
        """Generuje pełny program G-code pod GRBL z podziałem na warstwy i tryby M4/M3."""
        gcode_lines: list[str] = [
            "; ==========================================",
            "; LaserBurn Studio — Wygenerowano program G-code",
            "; ==========================================",
            "G21         ; Jednostki metryczne (mm)",
            "G90         ; Pozycjonowanie absolutne",
            "$32=1       ; Aktywacja trybu lasera w GRBL",
            "M5 S0       ; Wyłączenie wiązki",
            "G0 F6000    ; Szybki posuw przelotowy",
        ]

        # 1. RASTERY (M4 - Dynamic Laser Power)
        raster_items = [
            it for it in self.scene.items() 
            if isinstance(it, QGraphicsPixmapItem) and it.data(0) and it.data(0).get("type") == "raster"
            and it.isVisible()
        ]

        if raster_items:
            feedrate_raster = 3500
            power_pct_raster = 60.0
            for layer in self.layers:
                if layer.mode.lower() == "image" and layer.output:
                    feedrate_raster = int(layer.speed)
                    power_pct_raster = float(layer.power)
                    break

            s_target_raster = int(round((power_pct_raster / 100.0) * 1000))

            for item in raster_items:
                data = item.data(0)
                binary_grid = data.get("grid")
                if binary_grid is None:
                    continue

                bbox = item.sceneBoundingRect()
                origin_x = bbox.left()
                origin_y = bbox.top()
                h_px, w_px = binary_grid.shape
                eff_interval_y = bbox.height() / max(1, h_px)

                gcode_lines.append(f"\n; --- RASTER: {bbox.width():.1f}x{bbox.height():.1f} mm ---")
                raster_code = RasterImageProcessor.generate_raster_gcode(
                    binary_grid=binary_grid,
                    interval_mm=eff_interval_y,
                    origin_x=origin_x,
                    origin_y=origin_y,
                    feedrate=feedrate_raster,
                    s_max=s_target_raster,
                    overscan_mm=2.5
                )
                gcode_lines.extend(raster_code)

        # 2. WEKTORY (M3 - Constant Laser Power / Cut & Line)
        vector_items = [
            it for it in self.scene.items()
            if isinstance(it, QGraphicsPathItem) 
            and it not in (self.boundary_item, self.centroid_item, self.transform_box, self.guides)
            and it.zValue() >= 10
            and it.isVisible()
        ]

        layer_groups: dict[LayerConfig, list[QGraphicsPathItem]] = {l: [] for l in self.layers}
        for item in vector_items:
            layer = self._find_layer_for_item(item)
            if layer in layer_groups:
                layer_groups[layer].append(item)

        for layer, items in layer_groups.items():
            if not items or not layer.output or layer.mode.lower() == "image":
                continue

            s_power = int(round((layer.power / 100.0) * 1000))
            feed = int(layer.speed)
            passes = max(1, int(layer.passes))

            gcode_lines.append(f"\n; --- WARSTWA: {layer.name} ---")
            gcode_lines.append(f"; Tryb: {layer.mode}, F: {feed} mm/min, Moc: {s_power} ({layer.power}%), Powtórzenia: {passes}")

            for pass_num in range(1, passes + 1):
                if passes > 1:
                    gcode_lines.append(f"; Powtórzenie {pass_num} z {passes}")

                for item in items:
                    polylines = self._path_item_to_polylines(item)
                    for poly in polylines:
                        if not poly:
                            continue

                        first_pt = poly[0]
                        gcode_lines.append(f"G0 X{first_pt.x():.3f} Y{first_pt.y():.3f}")
                        gcode_lines.append(f"M3 S{s_power}")
                        gcode_lines.append(f"G1 F{feed}")

                        for pt in poly[1:]:
                            gcode_lines.append(f"G1 X{pt.x():.3f} Y{pt.y():.3f}")

                        gcode_lines.append("M5 S0")

        gcode_lines.extend([
            "\n; --- ZAKOŃCZENIE CYKLU ---",
            "M5 S0       ; Wyłączenie wiązki",
            "G0 X0 Y0    ; Powrót do bazy (WPos 0,0)",
            "M2          ; Koniec programu",
            "; =========================================="
        ])
        return gcode_lines

    def _export_project_gcode(self):
        lines = self._generate_gcode()
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz G-code", "projekt.nc", "Pliki G-Code (*.nc *.gcode *.tap)")
        if not path:
            return

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        if hasattr(self, "status"):
            self.status.showMessage(f"Wyeksportowano G-code ({len(lines)} linii) do {path}", 4000)

    def _open_gcode_simulation(self):
        gcode_data = self._generate_gcode()
        gcode_text = "\n".join(gcode_data) if isinstance(gcode_data, list) else gcode_data
        sim_dialog = GCodeVisualizerDialog(gcode_text, self)
        sim_dialog.exec()

    # =========================================================================
    # OBSŁUGA PLASTRA DREWNA, WARSTW, MASZYNY I PLIKÓW
    # =========================================================================

    def emergency_stop(self):
        if self.ws_client and self.ws_client.isValid():
            self.ws_client.sendTextMessage("!")
            self.ws_client.sendTextMessage("M5")
            self.ws_client.sendTextMessage("\x85")
        self.status.showMessage("⚠️ AWARYJNE ZATRZYMANIE (FEED HOLD + LASER OFF)!", 8000)

    def _create_new_layer(self):
        palette_fallbacks = [
            "#E6C200", "#D800E6", "#00E5D8", "#FF7800", 
            "#808080", "#8E44AD", "#16A085", "#D35400"
        ]
        idx = len(self.layers)
        default_hex = palette_fallbacks[idx % len(palette_fallbacks)]
        
        dialog = QColorDialog(QColor(default_hex), self)
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)
        dialog.setWindowTitle("Wybierz kolor nowej warstwy")
        
        selected_color = QColor(default_hex)
        if dialog.exec():
            c = dialog.selectedColor()
            if c.isValid():
                selected_color = c

        layer_name = f"{idx:02d} - Warstwa"
        new_layer = LayerConfig(
            name=layer_name,
            color=selected_color,
            mode="Line",
            speed=1200.0,
            power=50.0,
            passes=1,
            kerf=0.00
        )
        self.layers.append(new_layer)
        self._populate_layer_table()
        self.layer_table.selectRow(idx)
        self.status.showMessage(f"Dodano nową warstwę: {layer_name} ({selected_color.name()})")

    def _change_selected_layer_color(self):
        selected_rows = self.layer_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "Warstwy", "Zaznacz wiersz warstwy, której kolor chcesz zmienić.")
            return

        r = selected_rows[0].row()
        current_color = self.layers[r].color
        color = QColorDialog.getColor(current_color, self, "Zmień kolor warstwy")
        if color.isValid():
            self.layers[r].color = color
            self._populate_layer_table()
            self.layer_table.selectRow(r)

    def _delete_selected_layer(self):
        selected_rows = self.layer_table.selectionModel().selectedRows()
        if not selected_rows:
            return

        if len(self.layers) <= 1:
            QMessageBox.warning(self, "Warstwy", "Projekt musi posiadać co najmniej jedną aktywną warstwę.")
            return

        r = selected_rows[0].row()
        del self.layers[r]
        self._populate_layer_table()

    def _apply_color_to_selected(self, hex_color: str):
        selected_items = [
            it for it in self.scene.selectedItems() 
            if isinstance(it, QGraphicsPathItem)
        ]
        if not selected_items:
            self.status.showMessage("Najpierw zaznacz wektor na kanwie, aby zmienić jego warstwę/kolor.")
            return

        target_color = QColor(hex_color)
        matching_layer = next((l for l in self.layers if l.color.name() == target_color.name()), None)
        if not matching_layer:
            idx = len(self.layers)
            matching_layer = LayerConfig(
                name=f"{idx:02d} - Kolor",
                color=target_color,
                mode="Line",
                speed=1500.0,
                power=70.0,
                passes=1,
                kerf=0.15
            )
            self.layers.append(matching_layer)
            self._populate_layer_table()

        new_pen = QPen(target_color, 1.0)
        new_pen.setCosmetic(True)
        for it in selected_items:
            it.setPen(new_pen)

        self.status.showMessage(f"Przypisano wektor do warstwy: {matching_layer.name} ({hex_color})")

    def _populate_layer_table(self):
        self.layer_table.setRowCount(0)
        self.layer_table.setRowCount(len(self.layers))
        self.layer_table.verticalHeader().setDefaultSectionSize(28)

        for r, layer in enumerate(self.layers):
            self.layer_table.setRowHeight(r, 28)

            for c in range(7):
                self.layer_table.setItem(r, c, QTableWidgetItem())

            name_item = self.layer_table.item(r, 0)
            name_item.setText(f"■ {layer.name}")
            if layer.color.name().upper() in ("#000000", "#111111"):
                name_item.setForeground(QColor("#FFFFFF"))
            else:
                name_item.setForeground(layer.color)
            name_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

            combo_mode = QComboBox()
            combo_mode.addItems(["Line", "Fill", "Image"])
            combo_mode.setCurrentText(layer.mode)
            combo_mode.currentTextChanged.connect(lambda val, l=layer: setattr(l, 'mode', val))
            self.layer_table.setCellWidget(r, 1, combo_mode)

            spin_spd = QDoubleSpinBox()
            spin_spd.setRange(10, 20000)
            spin_spd.setDecimals(0)
            spin_spd.setValue(layer.speed)
            spin_spd.valueChanged.connect(lambda val, l=layer: setattr(l, 'speed', val))
            self.layer_table.setCellWidget(r, 2, spin_spd)

            spin_pwr = QDoubleSpinBox()
            spin_pwr.setRange(0, 100)
            spin_pwr.setDecimals(1)
            spin_pwr.setValue(layer.power)
            spin_pwr.valueChanged.connect(lambda val, l=layer: setattr(l, 'power', val))
            self.layer_table.setCellWidget(r, 3, spin_pwr)

            spin_pass = QSpinBox()
            spin_pass.setRange(1, 50)
            spin_pass.setValue(layer.passes)
            spin_pass.valueChanged.connect(lambda val, l=layer: setattr(l, 'passes', val))
            self.layer_table.setCellWidget(r, 4, spin_pass)

            spin_kerf = QDoubleSpinBox()
            spin_kerf.setRange(0.00, 2.00)
            spin_kerf.setSingleStep(0.02)
            spin_kerf.setDecimals(2)
            spin_kerf.setValue(layer.kerf)
            spin_kerf.valueChanged.connect(lambda val, l=layer: setattr(l, 'kerf', val))
            self.layer_table.setCellWidget(r, 5, spin_kerf)

            chk_container = QWidget()
            chk_layout = QHBoxLayout(chk_container)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_out = QCheckBox()
            chk_out.setChecked(layer.output)
            chk_out.toggled.connect(lambda val, l=layer: setattr(l, 'output', val))
            chk_layout.addWidget(chk_out)
            self.layer_table.setCellWidget(r, 6, chk_container)

        self.layer_table.viewport().update()

    def _setup_canvas(self):
        self.scene.setSceneRect(-30, -30, self.bed_w + 60, self.bed_h + 60)

        bed = self.scene.addRect(0, 0, self.bed_w, self.bed_h, QPen(QColor(70, 70, 75), 1.2), QBrush(QColor(38, 38, 42)))
        bed.setZValue(-10)

        fine_pen = QPen(QColor(50, 50, 55), 0.5)
        fine_pen.setCosmetic(True)
        main_pen = QPen(QColor(65, 65, 75), 1.0)
        main_pen.setCosmetic(True)

        for x in range(0, int(self.bed_w) + 1, 10):
            p = main_pen if x % 50 == 0 else fine_pen
            line = self.scene.addLine(x, 0, x, self.bed_h, p)
            line.setZValue(-9)

        for y in range(0, int(self.bed_h) + 1, 10):
            p = main_pen if y % 50 == 0 else fine_pen
            line = self.scene.addLine(0, y, self.bed_w, y, p)
            line.setZValue(-9)

        self.snap_engine = SnapEngine(tolerance_mm=2.5, grid_size_mm=1.0)
        self.guides = AlignmentGuideItem(self.bed_w, self.bed_h)
        self.scene.addItem(self.guides)

        self.transform_box = TransformBoxItem(self.snap_engine, self.guides)
        self.transform_box.setAcceptHoverEvents(True)
        self.transform_box.setVisible(False)
        self.scene.addItem(self.transform_box)

        self.scene.selectionChanged.connect(self._on_selection_changed)

        self.laser_reticle = LaserReticleItem()
        self.scene.addItem(self.laser_reticle)
        self.laser_reticle.setPos(0, 0)

    def _on_selection_changed(self):
        selected = self._get_user_selected_items()
        if selected:
            self.transform_box.set_targets(selected)
            rect = self.transform_box.get_combined_scene_rect()
            self.status.showMessage(f"Zaznaczono obiektów: {len(selected)} (Wymiary: {rect.width():.1f} x {rect.height():.1f} mm)")
        else:
            self.transform_box.set_targets([])

    def _connect_signals(self):
        if hasattr(self, "btn_tb_import"):
            self.btn_tb_import.clicked.connect(self._import_svg)
        if hasattr(self, "btn_tb_connect"):
            self.btn_tb_connect.clicked.connect(self._toggle_ws_connection)
        if hasattr(self, "btn_tb_frame"):
            self.btn_tb_frame.clicked.connect(self.frame_project)
        if hasattr(self, "btn_tb_start"):
            self.btn_tb_start.clicked.connect(self.start_job_on_device)
        if hasattr(self, "btn_tb_stop"):
            self.btn_tb_stop.clicked.connect(self.emergency_stop)

        if hasattr(self, "btn_sample_pt"):
            self.btn_sample_pt.clicked.connect(self._on_record_boundary_point)
        if hasattr(self, "btn_build_contour"):
            self.btn_build_contour.clicked.connect(self._generate_contour)
        if hasattr(self, "btn_clear_contour"):
            self.btn_clear_contour.clicked.connect(self._clear_boundary)
        if hasattr(self, "btn_auto_fit"):
            self.btn_auto_fit.clicked.connect(self._auto_fit_selected_to_material)

        if hasattr(self, "btn_add_layer"):
            self.btn_add_layer.clicked.connect(self._create_new_layer)
        if hasattr(self, "btn_color_layer"):
            self.btn_color_layer.clicked.connect(self._change_selected_layer_color)
        if hasattr(self, "btn_del_layer"):
            self.btn_del_layer.clicked.connect(self._delete_selected_layer)

    def _machine_pos_updated(self, x: float, y: float):
        item = getattr(self, "laser_reticle", None) or getattr(self, "crosshair", None)
        if item is not None:
            item.setPos(x, y)
            self.crosshair = item
            self.laser_reticle = item

        if hasattr(self, "status") and self.status:
            self.status.showMessage(f"Pozycja maszyny WPos: X={x:.2f} mm | Y={y:.2f} mm", 2000)

    def _toggle_ws_connection(self):
        if self.ws_client.state() == QAbstractSocket.SocketState.ConnectedState:
            print("[WS] Rozłączanie...")
            self.ws_client.close()
        else:
            ws_url = "ws://malina.local:8000/ws"
            if hasattr(self, "input_ws_url") and self.input_ws_url.text().strip():
                ws_url = self.input_ws_url.text().strip()

            print(f"[WS] Łączenie z: {ws_url}")
            self.ws_client.open(QUrl(ws_url))

    def _on_ws_connected(self):
        print("[WS] Połączono z mostkiem GRBL.")
        if hasattr(self, "status"):
            self.status.showMessage("Połączono z mostkiem GRBL", 4000)
        if hasattr(self, "act_connect"):
            self.act_connect.setText("Rozłącz")
        if hasattr(self, "btn_tb_connect"):
            self.btn_tb_connect.setText("🔌")

    def _on_ws_disconnected(self):
        if hasattr(self, "btn_tb_connect"):
            self.btn_tb_connect.setText("⚡")
            self.btn_tb_connect.setToolTip("Połącz z maszyną (F2)")
        if hasattr(self, "crosshair") and self.crosshair:
            self.crosshair.setVisible(False)
        self.lbl_telemetry.setText(" GRBL: Rozłączony ")
        self.lbl_telemetry.setStyleSheet("""
            QLabel {
                background-color: #2b2b32;
                color: #888888;
                padding: 3px 8px;
                border-radius: 4px;
                font-family: monospace;
                font-weight: bold;
            }
        """)
        self.status.showMessage("Rozłączono z GRBL Bridge", 4000)

    def _on_ws_message(self, message: str):
        try:
            data = json.loads(message)
        except Exception:
            return

        wx = float(data.get("wpos_x", 0.0))
        wy = float(data.get("wpos_y", 0.0))
        status_str = data.get("status", "Brak")

        self._machine_pos_updated(wx, wy)

        if hasattr(self, "lbl_telemetry") and self.lbl_telemetry:
            self.lbl_telemetry.setText(f" GRBL: {status_str} | X: {wx:.2f} Y: {wy:.2f} ")
            if status_str == "Idle":
                self.lbl_telemetry.setStyleSheet("background-color: #0e7a3d; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-family: monospace;")
            elif status_str in ("Run", "Jog"):
                self.lbl_telemetry.setStyleSheet("background-color: #d97706; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-family: monospace;")
            elif "Hold" in status_str:
                self.lbl_telemetry.setStyleSheet("background-color: #b91c1c; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-family: monospace;")
            else:
                self.lbl_telemetry.setStyleSheet("background-color: #374151; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-family: monospace;")

        is_running = bool(data.get("job_running", False))
        progress = float(data.get("job_progress", 0.0))
        if hasattr(self, "progress_bar") and self.progress_bar:
            if is_running:
                self.progress_bar.setValue(int(progress))
                if hasattr(self, "estimated_total_time_s") and self.estimated_total_time_s > 0:
                    rem_s = max(0.0, self.estimated_total_time_s * (1.0 - progress / 100.0))
                    if hasattr(self, "lbl_job_time") and self.lbl_job_time:
                        self.lbl_job_time.setText(f"Pozostało: {CycleTimeEstimator.format_time(rem_s)}")
            elif progress == 100.0:
                self.progress_bar.setValue(100)

    def _on_ws_error(self, error_code):
        err_str = self.ws_client.errorString()
        print(f"[WS ERROR] Kod: {error_code}, Treść: {err_str}")
        self.status.showMessage(f"Błąd WS: {err_str}", 5000)

    def _on_record_boundary_point(self):
        if hasattr(self, "crosshair") and self.crosshair:
            pos = self.crosshair.pos()
            pt = (round(pos.x(), 2), round(pos.y(), 2))
            self.boundary_points.append(pt)

            if len(self.boundary_points) >= 3 and hasattr(self, "btn_build_contour"):
                self.btn_build_contour.setEnabled(True)
            if hasattr(self, "lbl_trace_status"):
                self.lbl_trace_status.setText(f"Zarejestrowane punkty: {len(self.boundary_points)}")
            if hasattr(self, "status"):
                self.status.showMessage(f"Zapisano punkt #{len(self.boundary_points)}: {pt}", 3000)

    def _generate_contour(self):
        if not hasattr(self, "boundary_points") or len(self.boundary_points) < 3:
            if hasattr(self, "status"):
                self.status.showMessage("Błąd: Zapisz minimum 3 punkty krawędzi plastra!", 4000)
            return

        try:
            mp = MultiPoint(self.boundary_points)
            poly = mp.convex_hull

            if poly.geom_type != "Polygon" or poly.area <= 1.0:
                if hasattr(self, "status"):
                    self.status.showMessage("Błąd: Punkty leżą na jednej linii! Zbierz punkty wokół plastra.", 4000)
                return

            c_x = float(poly.centroid.x)
            c_y = float(poly.centroid.y)
            self.shapely_material_poly = poly

            if hasattr(self, "status"):
                self.status.showMessage(f"Plaster: {poly.area:.0f} mm² | Środek: X={c_x:.1f} Y={c_y:.1f}", 4000)

            self._render_boundary_polygon(poly, (c_x, c_y))

            if hasattr(self, "btn_auto_fit"):
                self.btn_auto_fit.setEnabled(True)

        except Exception as e:
            print(f"[PLASTER BŁĄD] {e}")
            if hasattr(self, "status"):
                self.status.showMessage(f"Błąd geometrii: {e}", 4000)

    def _clear_boundary(self):
        if hasattr(self, "boundary_item") and self.boundary_item:
            self.scene.removeItem(self.boundary_item)
            self.boundary_item = None

        if hasattr(self, "centroid_item") and self.centroid_item:
            self.scene.removeItem(self.centroid_item)
            self.centroid_item = None

        if hasattr(self, "boundary_markers"):
            for marker in self.boundary_markers:
                if marker.scene() == self.scene:
                    self.scene.removeItem(marker)
            self.boundary_markers.clear()

        self.boundary_points.clear()
        self.shapely_material_poly = None

        if hasattr(self, "btn_build_contour"):
            self.btn_build_contour.setEnabled(False)
        if hasattr(self, "btn_auto_fit"):
            self.btn_auto_fit.setEnabled(False)
        if hasattr(self, "lbl_trace_status"):
            self.lbl_trace_status.setText("Zarejestrowane punkty: 0")
        self.scene.update()

    def _render_boundary_polygon(self, poly, centroid: Tuple[float, float]):
        if hasattr(self, "boundary_item") and self.boundary_item:
            self.scene.removeItem(self.boundary_item)
            self.boundary_item = None

        if hasattr(self, "centroid_item") and self.centroid_item:
            self.scene.removeItem(self.centroid_item)
            self.centroid_item = None

        coords = list(poly.exterior.coords)
        qpoints = [QPointF(x, y) for x, y in coords]
        qpoly = QPolygonF(qpoints)

        border_pen = QPen(QColor("#d97706"), 1.5)
        border_pen.setCosmetic(True)
        fill_brush = QBrush(QColor(217, 119, 6, 40))

        self.boundary_item = self.scene.addPolygon(qpoly, border_pen, fill_brush)
        self.boundary_item.setZValue(5) 
        
        c_x, c_y = centroid
        r = 3.0
        c_pen = QPen(QColor("#00ffcc"), 1.5)
        c_pen.setCosmetic(True)
        c_brush = QBrush(QColor("#00ffcc"))

        self.centroid_item = self.scene.addEllipse(c_x - r, c_y - r, r * 2, r * 2, c_pen, c_brush)
        self.centroid_item.setZValue(30)

        self.scene.update()

    def _auto_fit_selected_to_material(self, margin_mm: float = 5.0):
        if not hasattr(self, "shapely_material_poly") or self.shapely_material_poly is None:
            if hasattr(self, "status"):
                self.status.showMessage("Najpierw wyznacz obrys plastra drewna!", 4000)
            return

        selected_items = self._get_user_selected_items()
        target_items = selected_items if selected_items else [
            it for it in self.scene.items()
            if it not in (self.boundary_item, self.centroid_item, self.crosshair, self.transform_box, self.guides) 
            and it.zValue() >= 10
        ]

        if not target_items:
            if hasattr(self, "status"):
                self.status.showMessage("Brak elementów graficznych do dopasowania!", 3000)
            return

        group_rect = QRectF()
        for it in target_items:
            group_rect = group_rect.united(it.sceneBoundingRect())

        cur_w = group_rect.width()
        cur_h = group_rect.height()

        if cur_w <= 0.01 or cur_h <= 0.01:
            return

        safe_poly = self.shapely_material_poly.buffer(-margin_mm)
        if safe_poly.is_empty:
            safe_poly = self.shapely_material_poly

        min_x, min_y, max_x, max_y = safe_poly.bounds
        safe_w = max_x - min_x
        safe_h = max_y - min_y

        scale_factor = min(safe_w / cur_w, safe_h / cur_h, 1.0) if (cur_w > safe_w or cur_h > safe_h) else 1.0

        c = safe_poly.centroid if not safe_poly.is_empty else self.shapely_material_poly.centroid
        target_cx, target_cy = float(c.x), float(c.y)

        orig_cx = group_rect.center().x()
        orig_cy = group_rect.center().y()

        for it in target_items:
            it.moveBy(target_cx - orig_cx, target_cy - orig_cy)
            if scale_factor < 0.999:
                t = QTransform()
                t.scale(scale_factor, scale_factor)
                it.setTransform(it.transform() * t)

        if hasattr(self, "status"):
            msg = f"Dopasowano do plastra: margines {margin_mm:.1f} mm, skala: {scale_factor * 100:.1f}%"
            self.status.showMessage(msg, 4000)

    def _import_svg(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik SVG", "", "Pliki wektorowe (*.svg)")
        if not path:
            return

        pos = QPointF(10.0, 10.0)
        if hasattr(self, "shapely_material_poly") and self.shapely_material_poly:
            c = self.shapely_material_poly.centroid
            pos = QPointF(float(c.x), float(c.y))

        importer = SvgDocumentImporter(default_pen=QPen(self.layers[0].color, 1.0))
        items = importer.load_svg(path, default_pos=pos)

        self.scene.clearSelection()
        for item in items:
            self.scene.addItem(item)
            item.setSelected(True)

        self.status.showMessage(f"Zaimportowano SVG: {path} (obiektów: {len(items)})")

    def _import_raster(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 
            "Wybierz grafikę rastrową", 
            "", 
            "Obrazy (*.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return

        try:
            processor = RasterImageProcessor(default_interval_mm=0.1)
            pos = QPointF(50.0, 50.0)
            if hasattr(self, "shapely_material_poly") and self.shapely_material_poly:
                c = self.shapely_material_poly.centroid
                pos = QPointF(float(c.x), float(c.y))

            item, dithered_pil, grid = processor.load_raster(path, initial_pos=pos, apply_dither=True)
            self.scene.addItem(item)
            
            self.scene.clearSelection()
            item.setSelected(True)

            w_mm = item.data(0)["w_mm"]
            h_mm = item.data(0)["h_mm"]
            if hasattr(self, "status"):
                self.status.showMessage(f"Raster: {w_mm:.1f}x{h_mm:.1f} mm (Dither Floyd-Steinberg)", 4000)

        except Exception as e:
            print(f"[RASTER IMPORT BŁĄD] {e}")
            if hasattr(self, "status"):
                self.status.showMessage(f"Błąd importu: {e}", 4000)

    def frame_project(self):
        if not self.ws_client.isValid():
            self.status.showMessage("Brak połączenia z maszyną! Wciśnij F2 lub kliknij ⚡.", 4000)
            return

        items_to_frame = [
            item for item in self.scene.items()
            if item not in (self.transform_box, self.laser_reticle, self.crosshair, self.guides)
            and item.zValue() >= 10 and item.isVisible()
        ]

        if not items_to_frame:
            self.status.showMessage("Kanwa jest pusta – brak wektorów do obrysowania!", 4000)
            return

        rect = items_to_frame[0].sceneBoundingRect()
        for item in items_to_frame[1:]:
            rect = rect.united(item.sceneBoundingRect())

        min_x = round(min(rect.left(), rect.right()), 3)
        max_x = round(max(rect.left(), rect.right()), 3)
        min_y = round(min(rect.top(), rect.bottom()), 3)
        max_y = round(max(rect.top(), rect.bottom()), 3)

        width_mm = max_x - min_x
        height_mm = max_y - min_y

        feed_rate = 2500
        laser_power = 5

        framing_gcode = [
            "G90",
            f"G0 X{min_x:.3f} Y{min_y:.3f} F3000",
            f"M3 S{laser_power}",
            f"G1 X{max_x:.3f} Y{min_y:.3f} F{feed_rate}",
            f"G1 X{max_x:.3f} Y{max_y:.3f} F{feed_rate}",
            f"G1 X{min_x:.3f} Y{max_y:.3f} F{feed_rate}",
            f"G1 X{min_x:.3f} Y{min_y:.3f} F{feed_rate}",
            "M5"
        ]

        self.ws_client.sendTextMessage("\n".join(framing_gcode))
        self.status.showMessage(
            f"⛶ Framing: X[{min_x:.1f}..{max_x:.1f}] Y[{min_y:.1f}..{max_y:.1f}] | Wymiary: {width_mm:.1f} x {height_mm:.1f} mm",
            6000
        )

    def start_job_on_device(self):
        if not self.ws_client.isValid():
            self.status.showMessage("Najpierw połącz się z Raspberry Pi (F2)!", 4000)
            return

        gcode_lines = self._generate_gcode()
        gcode_text = "\n".join(gcode_lines)
        if not gcode_text.strip():
            self.status.showMessage("Brak aktywnych wektorów na kanwie do wypalenia!", 4000)
            return

        t_sec, rapid_mm, cut_mm = self.estimator.estimate(gcode_text)
        self.estimated_total_time_s = t_sec
        formatted_est = CycleTimeEstimator.format_time(t_sec)

        self.lbl_job_time.setText(f"Szacowany: {formatted_est}")
        self.status.showMessage(
            f"Kompilacja CAM: Cięcie: {cut_mm:.1f} mm | Przejazdy: {rapid_mm:.1f} mm | Szacowany czas: {formatted_est}",
            6000
        )

        if hasattr(self, "btn_tb_start"):
            self.btn_tb_start.setEnabled(False)
            self.btn_tb_start.setText("⏳")

        self.progress_bar.setValue(0)
        self.job_timer.restart()

        host = self.input_ws_url.text().strip()
        self.upload_worker = GCodeUploadWorker(host, gcode_text, filename="lasercam_job.nc")
        self.upload_worker.finished_signal.connect(self._on_upload_finished)
        self.upload_worker.start()

    def _on_upload_finished(self, success: bool, message: str):
        if hasattr(self, "btn_tb_start"):
            self.btn_tb_start.setEnabled(True)
            self.btn_tb_start.setText("▶")
        self.status.showMessage(message, 6000)

    def new_project(self):
        reply = QMessageBox.question(
            self, "Nowy projekt", "Czy na pewno chcesz utworzyć nowy projekt? Niezapisane zmiany zostaną utracone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            items_to_remove = [
                it for it in self.scene.items()
                if it not in (self.transform_box, self.laser_reticle, self.crosshair, self.guides)
                and it.zValue() >= 10
            ]
            for it in items_to_remove:
                self.scene.removeItem(it)
            if self.boundary_item:
                self._clear_boundary()
            self.status.showMessage("Utworzono nowy projekt", 4000)

    def save_project(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Zapisz projekt LaserBurn", "projekt.lcam", "LaserBurn Project (*.lcam)"
        )
        if not file_path:
            return

        try:
            data = ProjectSerializer.serialize(self)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.status.showMessage(f"Projekt pomyślnie zapisany: {file_path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie udało się zapisać projektu:\n{e}")

    def open_project(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Otwórz projekt LaserBurn", "", "LaserBurn Project (*.lcam);;Wszystkie pliki (*.*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("format") not in ("LaserCAM_Project", "LaserBurn_Project"):
                QMessageBox.warning(self, "Błąd formatu", "Wybrany plik nie jest poprawnym projektem LaserBurn Studio.")
                return

            ProjectSerializer.deserialize(self, data)
            self.status.showMessage(f"Projekt wczytany: {file_path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Błąd odczytu", f"Nie udało się wczytać projektu:\n{e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    app.setStyleSheet("""
        QMainWindow, QWidget {
            background-color: #1a1a1e;
            color: #dcdcdc;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11px;
        }
        QToolBar {
            background-color: #222226;
            border-bottom: 1px solid #323238;
            padding: 4px;
            spacing: 6px;
        }
        QPushButton {
            background-color: #2d2d34;
            color: #ffffff;
            border: 1px solid #40404a;
            border-radius: 4px;
            padding: 5px 12px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #3b3b45;
            border-color: #555562;
        }
        QPushButton:pressed {
            background-color: #202026;
        }
        QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit {
            background-color: #121215;
            color: #ffffff;
            border: 1px solid #3c3c44;
            border-radius: 4px;
            padding: 3px 6px;
        }
        QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
            border: 1px solid #0078d4;
        }
        QSlider::groove:horizontal {
            height: 4px;
            background: #33333a;
            border-radius: 2px;
        }
        QSlider::sub-page:horizontal {
            background: #0078d4;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #ffffff;
            width: 14px;
            margin-top: -5px;
            margin-bottom: -5px;
            border-radius: 7px;
        }
        QTableWidget {
            background-color: #151518;
            border: 1px solid #323238;
            border-radius: 4px;
            gridline-color: #26262c;
        }
        QHeaderView::section {
            background-color: #25252a;
            color: #aaaaaa;
            padding: 5px 3px;
            font-weight: 600;
            border: none;
            border-right: 1px solid #323238;
            border-bottom: 1px solid #323238;
        }
        QCheckBox {
            spacing: 6px;
        }
        QCheckBox::indicator {
            width: 14px;
            height: 14px;
            border-radius: 3px;
            border: 1px solid #4a4a55;
            background-color: #151518;
        }
        QCheckBox::indicator:checked {
            background-color: #0078d4;
            border-color: #0078d4;
        }
    """)

    window = LaserBurnStudio()
    window.show()
    sys.exit(app.exec())