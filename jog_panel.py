from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QDoubleSpinBox, QButtonGroup
)
from PySide6.QtWebSockets import QWebSocket


class JogPanelWidget(QWidget):
    """
    Panel ręcznego sterowania osiami (JOG) kompatybilny z GRBL 1.1 ($J=...).
    Odwzorowuje funkcjonalność mobilnego WebUI.
    """
    # Sygnały dla głównego okna (jeśli potrzebna dodatkowa reakcja UI)
    command_sent = Signal(str)

    def __init__(self, ws_client: Optional[QWebSocket] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ws_client = ws_client
        self.current_step: float = 10.0
        self.laser_focus_active: bool = False

        self._setup_ui()

    def set_ws_client(self, ws: QWebSocket):
        """Umożliwia podpięcie lub zmianę klienta WebSocket."""
        self.ws_client = ws

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(8)

        # -------------------------------------------------------------
        # 1. WYBÓR KROKU (0.1 mm, 1 mm, 10 mm, 50 mm)
        # -------------------------------------------------------------
        step_label = QLabel("Krok przesuwu:")
        step_label.setStyleSheet("color: #aaa; font-weight: 500;")
        main_layout.addWidget(step_label)

        h_step_layout = QHBoxLayout()
        h_step_layout.setSpacing(4)
        self.btn_group_step = QButtonGroup(self)
        self.btn_group_step.setExclusive(True)

        steps = [0.1, 1.0, 10.0, 50.0]
        for val in steps:
            txt = f"{val:g} mm"
            btn = QPushButton(txt)
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2b2b30;
                    color: #dcdcdc;
                    border: 1px solid #3c3c44;
                    border-radius: 4px;
                    padding: 5px;
                    font-weight: bold;
                }
                QPushButton:checked {
                    background-color: #d83b01;
                    border-color: #d83b01;
                    color: white;
                }
                QPushButton:hover:!checked {
                    background-color: #383840;
                }
            """)
            if val == self.current_step:
                btn.setChecked(True)

            btn.clicked.connect(lambda checked=False, v=val: self._set_step(v))
            self.btn_group_step.addButton(btn)
            h_step_layout.addWidget(btn)

        main_layout.addLayout(h_step_layout)

        # -------------------------------------------------------------
        # 2. MANIPULATOR OSI XY ORAZ Z
        # -------------------------------------------------------------
        h_jog_axes = QHBoxLayout()
        h_jog_axes.setSpacing(10)

        # Siatka XY (3x3)
        grid_xy = QGridLayout()
        grid_xy.setSpacing(4)

        btn_style_jog = """
            QPushButton {
                background-color: #2e323e;
                color: #ffffff;
                border: 1px solid #444a5a;
                border-radius: 6px;
                padding: 10px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #3b4050;
            }
            QPushButton:pressed {
                background-color: #0078d4;
                color: #ffffff;
            }
        """

        btn_y_plus = QPushButton("▲ Y+")
        btn_y_minus = QPushButton("▼ Y-")
        btn_x_minus = QPushButton("◀ X-")
        btn_x_plus = QPushButton("▶ X+")
        btn_home_xy = QPushButton("XY 0")
        btn_home_xy.setStyleSheet("""
            QPushButton {
                background-color: #1f222a;
                color: #00a4ef;
                border: 1px solid #3c3c44;
                border-radius: 6px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #2b2b32; }
            QPushButton:pressed { background-color: #0078d4; color: white; }
        """)

        for b in [btn_y_plus, btn_y_minus, btn_x_minus, btn_x_plus]:
            b.setStyleSheet(btn_style_jog)

        btn_y_plus.clicked.connect(lambda: self._send_jog(0, self.current_step, 0))
        btn_y_minus.clicked.connect(lambda: self._send_jog(0, -self.current_step, 0))
        btn_x_minus.clicked.connect(lambda: self._send_jog(-self.current_step, 0, 0))
        btn_x_plus.clicked.connect(lambda: self._send_jog(self.current_step, 0, 0))
        btn_home_xy.clicked.connect(lambda: self.send_gcode("G90 G0 X0 Y0 F4000"))

        grid_xy.addWidget(btn_y_plus, 0, 1)
        grid_xy.addWidget(btn_x_minus, 1, 0)
        grid_xy.addWidget(btn_home_xy, 1, 1)
        grid_xy.addWidget(btn_x_plus, 1, 2)
        grid_xy.addWidget(btn_y_minus, 2, 1)

        h_jog_axes.addLayout(grid_xy, stretch=3)

        # Kolumna osi Z
        v_jog_z = QVBoxLayout()
        v_jog_z.setSpacing(4)

        btn_z_plus = QPushButton("▲ Z+")
        btn_z_minus = QPushButton("▼ Z-")
        btn_z_park = QPushButton("Z +5")

        for b in [btn_z_plus, btn_z_minus]:
            b.setStyleSheet(btn_style_jog)

        btn_z_park.setStyleSheet(btn_home_xy.styleSheet())

        btn_z_plus.clicked.connect(lambda: self._send_jog(0, 0, self.current_step))
        btn_z_minus.clicked.connect(lambda: self._send_jog(0, 0, -self.current_step))
        btn_z_park.clicked.connect(lambda: self.send_gcode("G90 G0 Z5 F1500"))

        v_jog_z.addWidget(btn_z_plus)
        v_jog_z.addWidget(btn_z_park)
        v_jog_z.addWidget(btn_z_minus)

        h_jog_axes.addLayout(v_jog_z, stretch=1)
        main_layout.addLayout(h_jog_axes)

        # -------------------------------------------------------------
        # 3. ZEROWANIE WSPÓŁRZĘDNYCH ROBOCZYCH (G10 L20)
        # -------------------------------------------------------------
        zero_label = QLabel("Układ roboczy (Work Offset):")
        zero_label.setStyleSheet("color: #aaa; font-weight: 500;")
        main_layout.addWidget(zero_label)

        grid_zero = QGridLayout()
        grid_zero.setSpacing(4)

        btn_zero_style = """
            QPushButton {
                background-color: #0e4f7a;
                color: #ffffff;
                border: 1px solid #1a6294;
                border-radius: 4px;
                padding: 6px;
                font-weight: 600;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #116296; }
            QPushButton:pressed { background-color: #093756; }
        """

        btn_zero_xy = QPushButton("Zeruj XY")
        btn_zero_y = QPushButton("Zeruj Y")
        btn_zero_z = QPushButton("Zeruj Z")
        btn_zero_all = QPushButton("Zeruj XYZ")

        for b in [btn_zero_xy, btn_zero_y, btn_zero_z, btn_zero_all]:
            b.setStyleSheet(btn_zero_style)

        btn_zero_xy.clicked.connect(lambda: self.send_gcode("G10 L20 P1 X0 Y0"))
        btn_zero_y.clicked.connect(lambda: self.send_gcode("G10 L20 P1 Y0"))
        btn_zero_z.clicked.connect(lambda: self.send_gcode("G10 L20 P1 Z0"))
        btn_zero_all.clicked.connect(lambda: self.send_gcode("G10 L20 P1 X0 Y0 Z0"))

        grid_zero.addWidget(btn_zero_xy, 0, 0)
        grid_zero.addWidget(btn_zero_y, 0, 1)
        grid_zero.addWidget(btn_zero_z, 1, 0)
        grid_zero.addWidget(btn_zero_all, 1, 1)
        main_layout.addLayout(grid_zero)

        # -------------------------------------------------------------
        # 4. SZYBKIE AKCJE (Plamka celująca, Odblokuj, Baza $H)
        # -------------------------------------------------------------
        h_actions = QHBoxLayout()
        h_actions.setSpacing(4)

        self.btn_focus = QPushButton("💡 Plamka 0.5%")
        self.btn_focus.setCheckable(True)
        self.btn_focus.setStyleSheet("""
            QPushButton {
                background-color: #2b2b30;
                color: #e0e0e0;
                border: 1px solid #3c3c44;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
            }
            QPushButton:checked {
                background-color: #107c41;
                border-color: #107c41;
                color: white;
            }
        """)
        self.btn_focus.clicked.connect(self._toggle_laser_focus)

        btn_unlock = QPushButton("🔓 Odblokuj ($X)")
        btn_unlock.setStyleSheet("""
            QPushButton {
                background-color: #3a3a42;
                color: #f0f0f0;
                border: 1px solid #4e4e58;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #484852; }
        """)
        btn_unlock.clicked.connect(lambda: self.send_gcode("$X"))

        btn_home = QPushButton("🏠 Baza ($H)")
        btn_home.setStyleSheet(btn_unlock.styleSheet())
        btn_home.clicked.connect(lambda: self.send_gcode("$H"))

        h_actions.addWidget(self.btn_focus)
        h_actions.addWidget(btn_unlock)
        h_actions.addWidget(btn_home)
        main_layout.addLayout(h_actions)
        # Duży pasek STOP wewnątrz modułu Jog
        self.btn_jog_stop = QPushButton("🛑 ZATRZYMAJ RUCH (HOLD)")
        self.btn_jog_stop.setStyleSheet("""
            QPushButton {
                background-color: #8b0000;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                border: 1px solid #d13438;
            }
            QPushButton:hover { background-color: #b30000; }
            QPushButton:pressed { background-color: #500000; }
        """)
        self.btn_jog_stop.clicked.connect(self._emergency_hold)
        main_layout.addWidget(self.btn_jog_stop)

    def _emergency_hold(self):
        """Wysyła natychmiastowe przerwanie ruchu i zgaszenie plamki."""
        if self.ws_client and self.ws_client.isValid():
            self.ws_client.sendTextMessage("!")
            self.ws_client.sendTextMessage("M5")
            self.ws_client.sendTextMessage("\x85")
        if self.btn_focus.isChecked():
            self.btn_focus.setChecked(False)
    
    def _set_step(self, val: float):
        self.current_step = val

    def _send_jog(self, dx: float, dy: float, dz: float):
        """Wysyła polecenie płynnego ruchu GRBL Jog ($J=...)."""
        feed = 800 if dz != 0 else (1000 if self.current_step <= 1.0 else 3000)
        parts = ["$J=G91"]
        if dx != 0:
            parts.append(f"X{dx:.3f}")
        if dy != 0:
            parts.append(f"Y{dy:.3f}")
        if dz != 0:
            parts.append(f"Z{dz:.3f}")
        parts.append(f"F{feed}")

        cmd = " ".join(parts)
        self.send_gcode(cmd)

    def _toggle_laser_focus(self):
        self.laser_focus_active = self.btn_focus.isChecked()
        if self.laser_focus_active:
            # Włączenie wiązki 0.5% (S5 przy $30=1000)
            self.send_gcode("M3 S5")
        else:
            self.send_gcode("M5")

    def send_gcode(self, cmd: str):
        """Kieruje polecenie do WebSocketu lub emituje sygnał."""
        if self.ws_client and self.ws_client.isValid():
            self.ws_client.sendTextMessage(cmd)
        self.command_sent.emit(cmd)