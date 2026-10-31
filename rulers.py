from __future__ import annotations
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget, QGraphicsView, QGridLayout


class RulerWidget(QWidget):
    """Linijka milimetrowa śledząca pozycję i zoom sceny QGraphicsView."""
    HORIZONTAL = 0
    VERTICAL = 1

    def __init__(self, orientation: int, view: QGraphicsView, parent: QWidget | None = None):
        super().__init__(parent)
        self.orientation = orientation
        self.view = view
        self.thickness = 24
        
        self.bg_color = QColor(24, 24, 28)
        self.major_pen = QPen(QColor(110, 110, 120), 1.0)
        self.mid_pen = QPen(QColor(80, 80, 90), 0.8)
        self.minor_pen = QPen(QColor(55, 55, 62), 0.6)
        self.text_color = QColor(170, 170, 180)
        self.font = QFont("Segoe UI", 7)

        if self.orientation == self.HORIZONTAL:
            self.setFixedHeight(self.thickness)
        else:
            self.setFixedWidth(self.thickness)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.bg_color)
        painter.setFont(self.font)

        transform = self.view.transform()
        # Skala m11 dla X oraz m22 dla Y
        scale = abs(transform.m11()) if self.orientation == self.HORIZONTAL else abs(transform.m22())
        if scale <= 1e-4:
            return

        # Dynamiczne dobieranie gęstości siatki w zależności od stopnia powiększenia
        if scale > 6.0:
            step = 1       # 1 mm
            major_step = 5
        elif scale > 2.0:
            step = 2       # 2 mm
            major_step = 10
        elif scale > 0.8:
            step = 10      # 10 mm
            major_step = 50
        elif scale > 0.3:
            step = 20      # 20 mm
            major_step = 100
        else:
            step = 50      # 50 mm
            major_step = 100

        if self.orientation == self.HORIZONTAL:
            # Lewa i prawa granica widoku w układzie współrzędnych sceny
            p_left = self.view.mapToScene(0, 0)
            p_right = self.view.mapToScene(self.width(), 0)
            min_x = min(p_left.x(), p_right.x())
            max_x = max(p_left.x(), p_right.x())

            start_val = int(min_x // step) * step
            curr = start_val
            while curr <= max_x:
                view_pt = self.view.mapFromScene(QPointF(curr, 0))
                vx = view_pt.x()

                if 0 <= vx <= self.width():
                    is_major = (int(round(curr)) % major_step == 0)
                    is_mid = (int(round(curr)) % (major_step // 2) == 0) if major_step >= 10 else False

                    if is_major:
                        painter.setPen(self.major_pen)
                        painter.drawLine(vx, self.height() - 14, vx, self.height())
                        painter.setPen(self.text_color)
                        painter.drawText(vx + 3, self.height() - 6, f"{int(curr)}")
                    elif is_mid:
                        painter.setPen(self.mid_pen)
                        painter.drawLine(vx, self.height() - 8, vx, self.height())
                    else:
                        painter.setPen(self.minor_pen)
                        painter.drawLine(vx, self.height() - 4, vx, self.height())

                curr += step

            # Dolna linia graniczna miarki
            painter.setPen(self.major_pen)
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        else:
            # Górna i dolna granica widoku w układzie sceny
            p_top = self.view.mapToScene(0, 0)
            p_bottom = self.view.mapToScene(0, self.height())
            min_y = min(p_top.y(), p_bottom.y())
            max_y = max(p_top.y(), p_bottom.y())

            start_val = int(min_y // step) * step
            curr = start_val
            while curr <= max_y:
                view_pt = self.view.mapFromScene(QPointF(0, curr))
                vy = view_pt.y()

                if 0 <= vy <= self.height():
                    is_major = (int(round(curr)) % major_step == 0)
                    is_mid = (int(round(curr)) % (major_step // 2) == 0) if major_step >= 10 else False

                    if is_major:
                        painter.setPen(self.major_pen)
                        painter.drawLine(self.width() - 14, vy, self.width(), vy)
                        painter.setPen(self.text_color)
                        painter.save()
                        painter.translate(self.width() - 6, vy - 3)
                        painter.rotate(-90)
                        painter.drawText(0, 0, f"{int(curr)}")
                        painter.restore()
                    elif is_mid:
                        painter.setPen(self.mid_pen)
                        painter.drawLine(self.width() - 8, vy, self.width(), vy)
                    else:
                        painter.setPen(self.minor_pen)
                        painter.drawLine(self.width() - 4, vy, self.width(), vy)

                curr += step

            # Prawa linia graniczna miarki
            painter.setPen(self.major_pen)
            painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


class CornerBoxWidget(QWidget):
    """Narożnik łączący miarki z etykietą jednostki [mm]."""
    def __init__(self, size: int = 24, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 24))
        painter.setPen(QPen(QColor(90, 90, 100), 1.0))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        
        painter.setPen(QColor(130, 130, 140))
        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignCenter, "mm")


class CanvasWithRulers(QWidget):
    """Złożony widżet łączący CanvasView oraz zsynchronizowane miarki X/Y."""
    def __init__(self, view: QGraphicsView, parent: QWidget | None = None):
        super().__init__(parent)
        self.view = view

        self.corner = CornerBoxWidget(size=24)
        self.ruler_x = RulerWidget(RulerWidget.HORIZONTAL, self.view)
        self.ruler_y = RulerWidget(RulerWidget.VERTICAL, self.view)

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        grid.addWidget(self.corner, 0, 0)
        grid.addWidget(self.ruler_x, 0, 1)
        grid.addWidget(self.ruler_y, 1, 0)
        grid.addWidget(self.view, 1, 1)

        # Synchronizacja odświeżania miarek przy przesuwaniu widoku (panning)
        self.view.horizontalScrollBar().valueChanged.connect(self.update_rulers)
        self.view.verticalScrollBar().valueChanged.connect(self.update_rulers)

    def update_rulers(self):
        self.ruler_x.update()
        self.ruler_y.update()