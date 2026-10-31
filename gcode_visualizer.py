from __future__ import annotations
import re
import math
from typing import List, Tuple, Optional
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QWheelEvent,
    QMouseEvent, QPainterPath
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QLabel, QFrame, QSizePolicy
)


@dataclass
class GCodeSegment:
    cmd: str             # "G0" lub "G1"
    start: QPointF
    end: QPointF
    feed: float          # mm/min
    power: float         # S-value
    duration: float      # sekundy
    dist: float          # mm


class SimulationCanvas(QFrame):
    """Płótno CAD 2D z płynnym zoom/pan i renderingiem ścieżek lasera."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: #121214; border: 1px solid #2a2a30; border-radius: 4px;")
        
        self.segments: List[GCodeSegment] = []
        self.current_step = 0
        
        self.scale = 2.0
        self.pan_x = 40.0
        self.pan_y = 40.0
        self._last_mouse_pos = QPointF()
        self._is_panning = False
        
        # Pióra rysujące
        self.pen_rapid = QPen(QColor(240, 70, 70, 160), 1.0, Qt.DashLine)
        self.pen_rapid.setCosmetic(True)
        
        self.pen_cut = QPen(QColor(0, 230, 120), 1.5, Qt.SolidLine)
        self.pen_cut.setCosmetic(True)
        
        self.pen_future = QPen(QColor(50, 50, 58, 80), 1.0, Qt.SolidLine)
        self.pen_future.setCosmetic(True)

    def set_data(self, segments: List[GCodeSegment]):
        self.segments = segments
        self.current_step = len(segments)
        self.fit_view()
        self.update()

    def set_step(self, step: int):
        self.current_step = max(0, min(step, len(self.segments)))
        self.update()

    def fit_view(self):
        if not self.segments:
            return
        xs = [s.start.x() for s in self.segments] + [s.end.x() for s in self.segments]
        ys = [s.start.y() for s in self.segments] + [s.end.y() for s in self.segments]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        w = max(5.0, max_x - min_x)
        h = max(5.0, max_y - min_y)
        
        sx = (self.width() - 80) / w
        sy = (self.height() - 80) / h
        self.scale = min(sx, sy)
        
        # Centrowanie w oknie
        self.pan_x = (self.width() - (min_x + max_x) * self.scale) / 2.0
        self.pan_y = (self.height() + (min_y + max_y) * self.scale) / 2.0

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale *= factor
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._is_panning = True
            self._last_mouse_pos = event.position()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_panning:
            delta = event.position() - self._last_mouse_pos
            self.pan_x += delta.x()
            self.pan_y += delta.y()
            self._last_mouse_pos = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._is_panning = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        # Odwracamy Y do góry zgodnie z układem współrzędnych CNC
        painter.translate(self.pan_x, self.pan_y)
        painter.scale(self.scale, -self.scale)

        # 1. Rysowanie całego projektu w tle (szary kontur przed wypaleniem)
        if self.segments:
            path_future = QPainterPath()
            for s in self.segments[self.current_step:]:
                path_future.moveTo(s.start)
                path_future.lineTo(s.end)
            painter.setPen(self.pen_future)
            painter.drawPath(path_future)

        # 2. Wykonana część ścieżki
        head_pos = QPointF(0, 0)
        for i in range(self.current_step):
            s = self.segments[i]
            if s.cmd == "G0":
                painter.setPen(self.pen_rapid)
            else:
                painter.setPen(self.pen_cut)
            painter.drawLine(s.start, s.end)
            head_pos = s.end

        # 3. Głowica lasera
        if self.segments and self.current_step > 0:
            active_cmd = self.segments[self.current_step - 1].cmd
            painter.setPen(QPen(QColor(255, 255, 255), 1.0))
            if active_cmd == "G1":
                painter.setBrush(QBrush(QColor(255, 40, 40, 220)))  # Czerwona plamka w cięciu
            else:
                painter.setBrush(QBrush(QColor(0, 150, 255, 200))) # Niebieska w jałowym
            
            radius = 2.0 / self.scale
            painter.drawEllipse(head_pos, radius, radius)


class GCodeVisualizerDialog(QDialog):
    """Główne okno symulatora z estymatorem czasu GRBL."""
    def __init__(self, gcode_content: str, parent=None, machine_accel=1000.0):
        super().__init__(parent)
        self.setWindowTitle("Symulacja Ścieżki Narzędzia & Estymacja Czasu (G-code Preview)")
        self.resize(1050, 720)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1e;
                color: #e0e0e0;
                font-family: 'Segoe UI', sans-serif;
            }
            QPushButton {
                background-color: #2b2b32;
                border: 1px solid #40404a;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: bold;
                color: #fff;
            }
            QPushButton:hover {
                background-color: #383842;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #2b2b32;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #0078d4;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                width: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }
        """)

        self.accel = machine_accel  # Przyspieszenie osi mm/s^2 ($120/$121 w GRBL)
        self.segments: List[GCodeSegment] = []
        self.total_time_sec = 0.0
        self.total_dist_cut = 0.0
        self.total_dist_rapid = 0.0
        
        self.playback_speed = 1.0
        self.timer = QTimer(self)
        self.timer.setInterval(25)  # 40 FPS
        self.timer.timeout.connect(self._on_tick)

        self._init_ui()
        self._parse_gcode(gcode_content)
        self.canvas.set_data(self.segments)
        self._update_time_label()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        self.canvas = SimulationCanvas()
        main_layout.addWidget(self.canvas, stretch=1)

        # Suwak postępu
        h_slider = QHBoxLayout()
        self.lbl_progress = QLabel("100.0%")
        self.lbl_progress.setFixedWidth(55)
        self.lbl_progress.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(1000)
        self.slider.valueChanged.connect(self._on_slider_changed)
        
        h_slider.addWidget(self.slider)
        h_slider.addWidget(self.lbl_progress)
        main_layout.addLayout(h_slider)

        # Dolny pasek akcji
        bottom_bar = QHBoxLayout()
        
        self.btn_play = QPushButton("▶ Odtwarzaj")
        self.btn_play.clicked.connect(self._toggle_play)
        bottom_bar.addWidget(self.btn_play)

        self.btn_reset = QPushButton("⏮ Reset")
        self.btn_reset.clicked.connect(self._reset_sim)
        bottom_bar.addWidget(self.btn_reset)

        btn_fit = QPushButton("🔍 Dopasuj widok")
        btn_fit.clicked.connect(self.canvas.fit_view)
        bottom_bar.addWidget(btn_fit)

        bottom_bar.addSpacing(20)
        bottom_bar.addWidget(QLabel("Prędkość:"))
        
        for spd in [1.0, 2.0, 5.0, 10.0]:
            b = QPushButton(f"{int(spd)}x")
            b.setFixedWidth(40)
            b.clicked.connect(lambda _, s=spd: self._set_speed(s))
            bottom_bar.addWidget(b)

        bottom_bar.addStretch()

        self.lbl_stats = QLabel("Szacowany czas: 00:00:00")
        self.lbl_stats.setStyleSheet("font-size: 13px; font-weight: bold; color: #00e676;")
        bottom_bar.addWidget(self.lbl_stats)

        main_layout.addLayout(bottom_bar)

    def _parse_gcode(self, gcode_input: str | list[str]):
        """Parser kodu G-code z trapezoidalną estymacją czasów."""
        cur_x = 0.0
        cur_y = 0.0
        cur_f = 1000.0
        cur_s = 0.0
        active_motion_mode = "G0"  # Modalność GRBL (domyślnie G0)
        laser_enabled = False       # Czy włączono M3/M4

        self.segments.clear()
        self.total_time_sec = 0.0
        self.total_dist_cut = 0.0
        self.total_dist_rapid = 0.0

        rapid_feed = 6000.0  # mm/min

        # 1. Ujednolicenie wejścia (obsługa str oraz list)
        if isinstance(gcode_input, list):
            lines = gcode_input
        elif isinstance(gcode_input, str):
            lines = gcode_input.splitlines()
        else:
            lines = []

        for raw_line in lines:
            # Usunięcie komentarzy w nawiasach i po średniku
            line = re.sub(r'\(.*?\)', '', raw_line).split(';')[0].strip()
            if not line:
                continue

            # Odczyt posuwu F
            m_f = re.search(r'[Ff]([\d\.]+)', line)
            if m_f:
                cur_f = float(m_f.group(1))

            # Odczyt mocy S
            m_s = re.search(r'[Ss]([\d\.]+)', line)
            if m_s:
                cur_s = float(m_s.group(1))

            # Załączenie / wyłączenie głowicy
            if "M3" in line or "M4" in line:
                laser_enabled = True
            elif "M5" in line:
                laser_enabled = False

            # Aktualizacja trybu modalnego ruchu (G0 / G1 / G2 / G3)
            m_cmd = re.search(r'\b(G0|G1|G00|G01)\b', line, re.IGNORECASE)
            if m_cmd:
                cmd_raw = m_cmd.group(1).upper()
                active_motion_mode = "G0" if cmd_raw in ("G0", "G00") else "G1"

            # Odczyt współrzędnych docelowych
            m_x = re.search(r'[Xx]([-+]?\d*\.?\d+)', line)
            m_y = re.search(r'[Yy]([-+]?\d*\.?\d+)', line)

            if not m_x and not m_y:
                continue

            target_x = float(m_x.group(1)) if m_x else cur_x
            target_y = float(m_y.group(1)) if m_y else cur_y

            dist = math.hypot(target_x - cur_x, target_y - cur_y)

            if dist > 0.001:
                # Segment pali TYLKO jeśli: jest w trybie G1, laser jest załączony (M3/M4) i S > 0
                is_burning = (active_motion_mode == "G1") and laser_enabled and (cur_s > 0.0)
                cmd = "G1" if is_burning else "G0"

                # Prędkość dla kalkulacji czasu
                feed = cur_f if active_motion_mode == "G1" else rapid_feed
                v_target = max(1.0, feed / 60.0)  # mm/s

                # Model trapezoidalny przyspieszenia
                accel = getattr(self, "accel", 500.0)  # mm/s^2 domyślnie
                s_acc = (v_target ** 2) / accel
                if dist >= s_acc:
                    t_segment = (v_target / accel) + (dist - s_acc) / v_target
                else:
                    t_segment = 2.0 * math.sqrt(dist / accel)

                seg = GCodeSegment(
                    cmd=cmd,
                    start=QPointF(cur_x, cur_y),
                    end=QPointF(target_x, target_y),
                    feed=feed,
                    power=cur_s if is_burning else 0.0,
                    duration=t_segment,
                    dist=dist
                )
                self.segments.append(seg)
                self.total_time_sec += t_segment

                if is_burning:
                    self.total_dist_cut += dist
                else:
                    self.total_dist_rapid += dist

            cur_x = target_x
            cur_y = target_y

    def _update_time_label(self):
        hours = int(self.total_time_sec // 3600)
        minutes = int((self.total_time_sec % 3600) // 60)
        seconds = int(self.total_time_sec % 60)
        
        self.lbl_stats.setText(
            f"Czas: {hours:02d}:{minutes:02d}:{seconds:02d} | Praca (G1): {self.total_dist_cut:.1f} mm | Dojazdy (G0): {self.total_dist_rapid:.1f} mm"
        )

    def _on_slider_changed(self, val):
        if not self.segments:
            return
        ratio = val / 1000.0
        step = int(ratio * len(self.segments))
        self.canvas.set_step(step)
        self.lbl_progress.setText(f"{ratio * 100:.1f}%")

    def _toggle_play(self):
        if self.timer.isActive():
            self.timer.stop()
            self.btn_play.setText("▶ Odtwarzaj")
        else:
            if self.slider.value() >= 1000:
                self.slider.setValue(0)
            self.timer.start()
            self.btn_play.setText("⏸ Pauza")

    def _reset_sim(self):
        self.timer.stop()
        self.btn_play.setText("▶ Odtwarzaj")
        self.slider.setValue(0)

    def _set_speed(self, spd):
        self.playback_speed = spd

    def _on_tick(self):
        cur_val = self.slider.value()
        step_increment = int(5 * self.playback_speed)
        new_val = cur_val + max(1, step_increment)
        
        if new_val >= 1000:
            self.slider.setValue(1000)
            self.timer.stop()
            self.btn_play.setText("▶ Odtwarzaj")
        else:
            self.slider.setValue(new_val)