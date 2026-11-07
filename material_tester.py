from __future__ import annotations
from typing import List, Tuple
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainterPath, QPen, QColor, QBrush, QFont, QTransform
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QComboBox, QGroupBox, QMessageBox, QDialogButtonBox, QGraphicsItem, QGraphicsPathItem
)

class MaterialTestDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generator Testu Materiałowego (Grid)")
        self.resize(500, 550)
        self.setStyleSheet("""
            QDialog { background-color: #1a1a1e; color: #dcdcdc; font-family: 'Segoe UI', sans-serif; font-size: 11px; }
            QGroupBox { font-weight: bold; border: 1px solid #3c3c44; border-radius: 4px; margin-top: 8px; padding-top: 12px; color: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
            QLabel { color: #dcdcdc; }
            QDoubleSpinBox, QSpinBox, QComboBox { background-color: #121215; color: #ffffff; border: 1px solid #3c3c44; border-radius: 4px; padding: 3px 6px; }
            QPushButton { background-color: #2d2d34; color: #ffffff; border: 1px solid #40404a; border-radius: 4px; padding: 6px 14px; }
        """)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        # 1. Wybór typu testu
        box_type = QGroupBox("Rodzaj testu materiałowego")
        l_type = QHBoxLayout(box_type)
        self.combo_test_type = QComboBox()
        self.combo_test_type.addItems(["Test Cięcia (Kwadraty)", "Test Wypełnienia (Raster)"])
        self.combo_test_type.currentIndexChanged.connect(self._on_test_type_changed)
        l_type.addWidget(QLabel("Wybierz tryb:"))
        l_type.addWidget(self.combo_test_type)
        main_layout.addWidget(box_type)

        # 2. Wymiary arkusza
        box_sheet = QGroupBox("Wymiary arkusza testowego")
        grid_sheet = QGridLayout(box_sheet)
        grid_sheet.addWidget(QLabel("Szer. x Wys. [mm]:"), 0, 0)
        
        h_sheet = QHBoxLayout()
        self.spin_sheet_w = QDoubleSpinBox()
        self.spin_sheet_w.setRange(20, 500)
        self.spin_sheet_w.setValue(120.0)
        self.spin_sheet_h = QDoubleSpinBox()
        self.spin_sheet_h.setRange(20, 500)
        self.spin_sheet_h.setValue(100.0)
        h_sheet.addWidget(self.spin_sheet_w)
        h_sheet.addWidget(QLabel("x"))
        h_sheet.addWidget(self.spin_sheet_h)
        grid_sheet.addLayout(h_sheet, 0, 1)
        main_layout.addWidget(box_sheet)

        # 3. Parametry testowe
        self.box_params = QGroupBox("Parametry i siatka (Min - Max)")
        self.grid_params = QGridLayout(self.box_params)
        
        self.grid_params.addWidget(QLabel("Moc [%]:"), 0, 0)
        h_p = QHBoxLayout()
        self.spin_pmin, self.spin_pmax = QSpinBox(), QSpinBox()
        self.spin_pmin.setRange(5, 100); self.spin_pmax.setRange(5, 100)
        self.spin_pmin.setValue(30); self.spin_pmax.setValue(80)
        h_p.addWidget(self.spin_pmin); h_p.addWidget(QLabel("-")); h_p.addWidget(self.spin_pmax)
        self.grid_params.addLayout(h_p, 0, 1)

        self.grid_params.addWidget(QLabel("Prędkość [mm/min]:"), 1, 0)
        h_s = QHBoxLayout()
        self.spin_smin, self.spin_smax = QSpinBox(), QSpinBox()
        self.spin_smin.setRange(100, 15000); self.spin_smax.setRange(100, 15000)
        self.spin_smin.setValue(600); self.spin_smax.setValue(3000)
        h_s.addWidget(self.spin_smin); h_s.addWidget(QLabel("-")); h_s.addWidget(self.spin_smax)
        self.grid_params.addLayout(h_s, 1, 1)

        self.grid_params.addWidget(QLabel("Liczba skoków siatki:"), 2, 0)
        h_steps = QHBoxLayout()
        self.spin_steps_p, self.spin_steps_s = QSpinBox(), QSpinBox()
        self.spin_steps_p.setRange(2, 20); self.spin_steps_s.setRange(2, 20)
        self.spin_steps_p.setValue(4); self.spin_steps_s.setValue(4) # Siatka 4x4
        h_steps.addWidget(QLabel("Moc (Y):")); h_steps.addWidget(self.spin_steps_p)
        h_steps.addWidget(QLabel("Prędkość (X):")); h_steps.addWidget(self.spin_steps_s)
        self.grid_params.addLayout(h_steps, 2, 1)

        self.grid_params.addWidget(QLabel("Ilość przejść:"), 3, 0)
        self.spin_passes = QSpinBox()
        self.spin_passes.setRange(1, 10)
        self.spin_passes.setValue(2)
        self.grid_params.addWidget(self.spin_passes, 3, 1)

        # Parametry kreskowania (tylko test wypełnienia)
        self.grid_params.addWidget(QLabel("Kreskowanie: odstęp [mm] / kąt [°]:"), 4, 0)
        h_hatch = QHBoxLayout()
        self.spin_hatch_interval = QDoubleSpinBox()
        self.spin_hatch_interval.setRange(0.02, 2.0)
        self.spin_hatch_interval.setSingleStep(0.02)
        self.spin_hatch_interval.setDecimals(2)
        self.spin_hatch_interval.setValue(0.1)
        self.spin_hatch_angle = QDoubleSpinBox()
        self.spin_hatch_angle.setRange(0.0, 179.0)
        self.spin_hatch_angle.setDecimals(0)
        self.spin_hatch_angle.setValue(0.0)
        h_hatch.addWidget(self.spin_hatch_interval); h_hatch.addWidget(QLabel("/")); h_hatch.addWidget(self.spin_hatch_angle)
        self.grid_params.addLayout(h_hatch, 4, 1)
        self.spin_hatch_interval.setEnabled(False)
        self.spin_hatch_angle.setEnabled(False)

        main_layout.addWidget(self.box_params)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

    def _on_test_type_changed(self, index: int):
        if index == 0:
            self.spin_smin.setValue(600); self.spin_smax.setValue(2500)
            self.spin_pmin.setValue(30); self.spin_pmax.setValue(80)
            self.spin_passes.setValue(2); self.spin_passes.setEnabled(True)
        else:
            self.spin_smin.setValue(1000); self.spin_smax.setValue(4000)
            self.spin_pmin.setValue(20); self.spin_pmax.setValue(60)
            self.spin_passes.setValue(1); self.spin_passes.setEnabled(False)
        is_fill = index == 1
        self.spin_hatch_interval.setEnabled(is_fill)
        self.spin_hatch_angle.setEnabled(is_fill)

    @staticmethod
    def _unique_cell_colors(count: int, reserved: Tuple[str, ...] = ()) -> List[QColor]:
        """
        Kolor komórki jednoznacznie wskazuje warstwę (także po zapisie projektu),
        więc każda komórka musi mieć inny kolor. Przy kolizji odcienia zmieniana jest jasność.
        """
        used = {c.lower() for c in reserved}
        colors: List[QColor] = []
        for k in range(count):
            hue = int((k / max(1, count)) * 320)
            lightness = 150
            color = QColor.fromHsl(hue, 200, lightness)
            step = 0
            while color.name() in used:
                step += 1
                lightness = 150 + (step // 2 + 1) * 8 * (1 if step % 2 else -1)
                if not 40 <= lightness <= 215:
                    hue = (hue + 1) % 360
                    step = 0
                    lightness = 150
                color = QColor.fromHsl(hue, 200, lightness)
            used.add(color.name())
            colors.append(color)
        return colors

    def generate_test_data(self, start_pos: QPointF = QPointF(10.0, 10.0)) -> Tuple[List[QGraphicsItem], List[dict]]:
        items: List[QGraphicsItem] = []
        layer_configs: List[dict] = []
        
        is_fill_test = self.combo_test_type.currentIndex() == 1
        
        # 1. Obrys arkusza
        sheet_path = QPainterPath()
        sheet_path.addRect(start_pos.x(), start_pos.y(), self.spin_sheet_w.value(), self.spin_sheet_h.value())
        sheet_item = QGraphicsPathItem(sheet_path)
        sheet_item.setPen(QPen(QColor("#808080"), 0.8, Qt.DashLine))
        sheet_item.setZValue(10)
        sheet_item.setData(0, {"type": "guide"})  # Tylko podgląd – nie jest wypalany
        items.append(sheet_item)

        pmin, pmax = self.spin_pmin.value(), self.spin_pmax.value()
        smin, smax = self.spin_smin.value(), self.spin_smax.value()
        steps_p, steps_s = self.spin_steps_p.value(), self.spin_steps_s.value()
        
        grid_x = start_pos.x() + 25.0  # Miejsce na etykiety osi Y z lewej
        grid_y = start_pos.y() + 15.0  # Miejsce na etykiety osi X na dole
        box_size, step_x, step_y = 12.0, 18.0, 18.0

        font = QFont("Segoe UI", 4)
        
        # 2. Generowanie osi X (Prędkości - na dole pod rzędem 0)
        for j in range(steps_s):
            speed_val = smin + (smax - smin) * (j / max(1, steps_s - 1))
            t_path = QPainterPath()
            t_path.addText(0, 0, font, f"S{int(speed_val)}")
            
            # Odwrócenie tekstu i pozycjonowanie pod dolnym wierszem
            tr = QTransform()
            tr.translate(grid_x + j * step_x, grid_y - 4.0)
            tr.scale(1, -1) # Rozwiązuje problem odwróconych tekstów na kanwie
            t_path = tr.map(t_path)
            
            t_item = QGraphicsPathItem(t_path)
            t_item.setPen(QPen(QColor("#00FFFF"), 0.3)); t_item.setBrush(QBrush(QColor("#00FFFF")))
            t_item.setZValue(21)
            t_item.setData(0, {"type": "test_label"}); items.append(t_item)

        # 3. Generowanie osi Y (Moce - po lewej stronie rzędu j=0)
        for i in range(steps_p):
            power_val = pmin + (pmax - pmin) * (i / max(1, steps_p - 1))
            t_path = QPainterPath()
            t_path.addText(0, 0, font, f"{int(power_val)}%")
            
            tr = QTransform()
            tr.translate(grid_x - 14.0, grid_y + i * step_y + box_size / 2.0 - 2.0)
            tr.scale(1, -1)
            t_path = tr.map(t_path)
            
            t_item = QGraphicsPathItem(t_path)
            t_item.setPen(QPen(QColor("#00FFFF"), 0.3)); t_item.setBrush(QBrush(QColor("#00FFFF")))
            t_item.setZValue(21)
            t_item.setData(0, {"type": "test_label"}); items.append(t_item)

        # 4. Generowanie komórek testowych (kwadratów) i dynamicznych warstw
        layer_counter = 0
        cell_colors = self._unique_cell_colors(steps_p * steps_s, reserved=("#00ffff", "#808080"))
        for i in range(steps_p):
            for j in range(steps_s):
                power_val = pmin + (pmax - pmin) * (i / max(1, steps_p - 1))
                speed_val = smin + (smax - smin) * (j / max(1, steps_s - 1))

                # Unikalny kolor dla każdej komórki (kolor = przypisanie do warstwy)
                color = cell_colors[layer_counter]

                layer_configs.append({
                    "name": f"P{int(power_val)} S{int(speed_val)}",
                    "color": color.name(),
                    "speed": float(speed_val),
                    "power": float(power_val),
                    "passes": int(self.spin_passes.value()),
                    "mode": "Fill" if is_fill_test else "Line",
                    "operation": "fill" if is_fill_test else "cut",
                    "hatch_interval": float(self.spin_hatch_interval.value()),
                    "hatch_angle": float(self.spin_hatch_angle.value()),
                    "output": True
                })

                path = QPainterPath()
                path.addRect(grid_x + j * step_x, grid_y + i * step_y, box_size, box_size)
                
                item = QGraphicsPathItem(path)
                item.setPen(QPen(color, 1.0))
                if is_fill_test:
                    brush = QColor(color)
                    brush.setAlpha(80)
                    item.setBrush(QBrush(brush))
                
                item.setZValue(12)
                items.append(item)
                layer_counter += 1

        return items, layer_configs