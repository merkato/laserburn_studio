"""Okno „Wstaw kształt”: rodzaj, wymiary obrysu, parametry, położenie środka i warstwa."""
from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen, QColor, QTransform
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QWidget, QLabel, QGroupBox
)

from shapes import SHAPES, SHAPE_LABELS, SHAPE_ICONS, ShapeSpec, build_path

# Ostatnio użyte ustawienia (w obrębie sesji programu)
_LAST_SPEC: dict = {}


class _ShapePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(170, 170)
        self._path = None
        self._color = QColor("#FF0000")

    def set_shape(self, path, color: QColor):
        self._path, self._color = path, QColor(color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(28, 28, 30))
        if self._path is None or self._path.isEmpty():
            return
        br = self._path.boundingRect()
        m = 14.0
        avail_w, avail_h = self.width() - 2 * m, self.height() - 2 * m
        k = min(avail_w / max(br.width(), 1e-6), avail_h / max(br.height(), 1e-6))
        if br.width() < 1e-6 or br.height() < 1e-6:  # linia pozioma/pionowa
            k = min(avail_w, avail_h) / max(br.width(), br.height(), 1e-6)
        # Oś Y sceny skierowana w górę – odwracamy ją jak widok kanwy
        t = QTransform()
        t.translate(self.width() / 2.0, self.height() / 2.0)
        t.scale(k, -k)
        t.translate(-br.center().x(), -br.center().y())
        pen = QPen(self._color if self._color.name() != "#000000" else QColor("#dddddd"), 1.6)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawPath(t.map(self._path))


class ShapeDialog(QDialog):
    """
    layers: lista (nazwa, QColor) warstw; wynik: spec(), center(), layer_index().
    """

    def __init__(self, kind: str, layers: List[Tuple[str, QColor]], layer_index: int,
                 center: Tuple[float, float], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wstaw kształt")
        self._layers = layers
        self._updating = False
        self._aspect = 1.0

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        form = QFormLayout()

        self.combo_kind = QComboBox()
        for k in SHAPES:
            self.combo_kind.addItem(f"{SHAPE_ICONS[k]}  {SHAPE_LABELS[k]}", k)
        form.addRow("Kształt:", self.combo_kind)

        def dspin(lo, hi, val, dec=2, suffix=" mm", step=1.0):
            s = QDoubleSpinBox(); s.setRange(lo, hi); s.setDecimals(dec); s.setSingleStep(step)
            s.setSuffix(suffix); s.setValue(val); s.setMinimumWidth(110)
            return s

        self.sp_w = dspin(0.1, 2000, 40.0)
        self.sp_h = dspin(0.1, 2000, 40.0)
        self.chk_lock = QCheckBox("Zachowaj proporcje")
        self.chk_lock.setChecked(True)
        self.lbl_w, self.lbl_h = QLabel("Szerokość:"), QLabel("Wysokość:")
        form.addRow(self.lbl_w, self.sp_w)
        form.addRow(self.lbl_h, self.sp_h)
        form.addRow("", self.chk_lock)

        self.sp_radius = dspin(0.0, 1000, 0.0)
        self.lbl_radius = QLabel("Promień narożnika:")
        form.addRow(self.lbl_radius, self.sp_radius)

        self.sp_sides = QSpinBox(); self.sp_sides.setRange(3, 64); self.sp_sides.setValue(6)
        self.lbl_sides = QLabel("Liczba boków:")
        form.addRow(self.lbl_sides, self.sp_sides)

        self.sp_points = QSpinBox(); self.sp_points.setRange(3, 64); self.sp_points.setValue(5)
        self.lbl_points = QLabel("Liczba ramion:")
        form.addRow(self.lbl_points, self.sp_points)

        self.sp_inner = dspin(5, 95, 50, dec=0, suffix=" %", step=5)
        self.lbl_inner = QLabel("Promień wewnętrzny:")
        form.addRow(self.lbl_inner, self.sp_inner)

        self.sp_length = dspin(0.1, 2000, 50.0)
        self.lbl_length = QLabel("Długość:")
        form.addRow(self.lbl_length, self.sp_length)

        self.sp_angle = dspin(-360, 360, 0.0, dec=1, suffix=" °", step=15)
        self.lbl_angle = QLabel("Kąt:")
        form.addRow(self.lbl_angle, self.sp_angle)
        left.addLayout(form)

        pos_box = QGroupBox("Położenie środka i warstwa")
        pform = QFormLayout(pos_box)
        self.sp_x = dspin(-2000, 2000, center[0])
        self.sp_y = dspin(-2000, 2000, center[1])
        pform.addRow("X:", self.sp_x)
        pform.addRow("Y:", self.sp_y)
        self.combo_layer = QComboBox()
        for name, color in layers:
            self.combo_layer.addItem(f"■ {name}", QColor(color))
            self.combo_layer.setItemData(self.combo_layer.count() - 1, QColor(color), Qt.ForegroundRole)
        if layers:
            self.combo_layer.setCurrentIndex(max(0, min(layer_index, len(layers) - 1)))
        pform.addRow("Warstwa:", self.combo_layer)
        left.addWidget(pos_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Wstaw")
        buttons.button(QDialogButtonBox.Cancel).setText("Anuluj")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        left.addWidget(buttons)
        root.addLayout(left)

        self.preview = _ShapePreview()
        root.addWidget(self.preview, 1)

        self._restore_last()
        idx = self.combo_kind.findData(kind)
        self.combo_kind.setCurrentIndex(max(0, idx))

        self.combo_kind.currentIndexChanged.connect(self._on_kind)
        self.sp_w.valueChanged.connect(lambda v: self._on_size(True))
        self.sp_h.valueChanged.connect(lambda v: self._on_size(False))
        self.chk_lock.toggled.connect(self._remember_aspect)
        for w in (self.sp_radius, self.sp_sides, self.sp_points, self.sp_inner, self.sp_length, self.sp_angle):
            w.valueChanged.connect(self._refresh)
        self.combo_layer.currentIndexChanged.connect(self._refresh)
        self._remember_aspect()
        self._on_kind()

    # ------------------------------------------------------------------ logika
    def _restore_last(self):
        for name, widget in (("width", self.sp_w), ("height", self.sp_h), ("corner_radius", self.sp_radius),
                             ("sides", self.sp_sides), ("points", self.sp_points), ("length", self.sp_length)):
            if name in _LAST_SPEC:
                widget.setValue(_LAST_SPEC[name])
        if "inner_ratio" in _LAST_SPEC:
            self.sp_inner.setValue(_LAST_SPEC["inner_ratio"] * 100.0)

    def _remember_aspect(self, *_):
        self._aspect = self.sp_w.value() / max(self.sp_h.value(), 1e-6)

    def _on_size(self, width_changed: bool):
        if self._updating:
            return
        if self.chk_lock.isChecked():
            self._updating = True
            if width_changed:
                self.sp_h.setValue(self.sp_w.value() / max(self._aspect, 1e-6))
            else:
                self.sp_w.setValue(self.sp_h.value() * self._aspect)
            self._updating = False
        else:
            self._remember_aspect()
        self._refresh()

    def _on_kind(self, *_):
        kind = self.combo_kind.currentData()
        is_line = kind == "line"
        rows = {
            (self.lbl_w, self.sp_w): not is_line,
            (self.lbl_h, self.sp_h): not is_line,
            (self.chk_lock,): not is_line,
            (self.lbl_radius, self.sp_radius): kind == "rect",
            (self.lbl_sides, self.sp_sides): kind == "polygon",
            (self.lbl_points, self.sp_points): kind == "star",
            (self.lbl_inner, self.sp_inner): kind == "star",
            (self.lbl_length, self.sp_length): is_line,
            (self.lbl_angle, self.sp_angle): kind in ("line", "polygon", "star"),
        }
        for widgets, visible in rows.items():
            for w in widgets:
                w.setVisible(visible)
        self.lbl_angle.setText("Kąt:" if is_line else "Obrót:")
        self.adjustSize()
        self._refresh()

    def _refresh(self, *_):
        color = self.combo_layer.currentData() if self.combo_layer.count() else QColor("#FF0000")
        try:
            self.preview.set_shape(build_path(self.spec()), color)
        except ValueError:
            self.preview.set_shape(None, color)

    # ------------------------------------------------------------------ wynik
    def spec(self) -> ShapeSpec:
        return ShapeSpec(
            kind=self.combo_kind.currentData(),
            width=self.sp_w.value(), height=self.sp_h.value(),
            corner_radius=self.sp_radius.value(),
            sides=self.sp_sides.value(), points=self.sp_points.value(),
            inner_ratio=self.sp_inner.value() / 100.0,
            length=self.sp_length.value(), angle=self.sp_angle.value(),
        )

    def center(self) -> Tuple[float, float]:
        return self.sp_x.value(), self.sp_y.value()

    def layer_index(self) -> int:
        return self.combo_layer.currentIndex()

    def accept(self):
        s = self.spec()
        _LAST_SPEC.update(width=s.width, height=s.height, corner_radius=s.corner_radius,
                          sides=s.sides, points=s.points, inner_ratio=s.inner_ratio, length=s.length)
        super().accept()
