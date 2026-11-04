from __future__ import annotations
from typing import Optional, Dict, Any, List

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel, QButtonGroup
)

from bridge_client import BridgeClient


# ==============================================================================
# STYLE (jedno miejsce)
# ==============================================================================
_BASE = "QPushButton {{ {body} }} QPushButton:hover:enabled {{ {hover} }} " \
        "QPushButton:pressed {{ {pressed} }} QPushButton:disabled {{ background-color: #24242a; color: #5a5a62; border-color: #303036; }}"


def _style(bg: str, fg: str = "#ffffff", border: str = "#3c3c44", hover: str = "", pressed: str = "#0078d4",
           extra: str = "", checked: str = "") -> str:
    body = f"background-color: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 5px; padding: 6px; font-weight: bold; {extra}"
    css = _BASE.format(body=body, hover=f"background-color: {hover or bg};", pressed=f"background-color: {pressed};")
    if checked:
        css += f" QPushButton:checked {{ {checked} }}"
    return css


STYLE_STEP = _style("#2b2b30", "#dcdcdc", hover="#383840",
                    checked="background-color: #d83b01; border-color: #d83b01; color: white;")
STYLE_JOG = _style("#2e323e", border="#444a5a", hover="#3b4050", extra="padding: 10px; font-size: 13px;")
STYLE_HOME = _style("#1f222a", "#00a4ef", hover="#2b2b32", extra="font-size: 11px;")
STYLE_ZERO = _style("#0e4f7a", border="#1a6294", hover="#116296", pressed="#093756", extra="font-size: 11px;")
STYLE_ACTION = _style("#3a3a42", "#f0f0f0", border="#4e4e58", hover="#484852", extra="font-weight: normal;")
STYLE_FOCUS = _style("#2b2b30", "#e0e0e0", hover="#383840",
                     checked="background-color: #107c41; border-color: #107c41; color: white;")
STYLE_PAUSE = _style("#8a5a00", border="#b07700", hover="#a86e00", pressed="#5c3c00")
STYLE_RESUME = _style("#0e7a3d", border="#13a052", hover="#109148", pressed="#095228")
STYLE_STOP = _style("#8b0000", border="#d13438", hover="#b30000", pressed="#500000", extra="padding: 9px;")

_STATUS_COLORS = {
    "Idle": "#0e7a3d", "Run": "#d97706", "Jog": "#d97706", "Hold": "#b91c1c",
    "Door": "#b91c1c", "Alarm": "#7f1d1d", "Home": "#1d4ed8",
}


class JogPanelWidget(QWidget):
    """
    Ręczne sterowanie GRBL 1.1 ($J=...) oraz sterowanie zadaniem na mostku:
    Pauza (feed hold), Wznów (cycle start), STOP = anuluj i wyczyść bufor GRBL.
    Przyciski są aktywne tylko w stanach, w których dana akcja jest bezpieczna.
    """
    command_sent = Signal(str)

    STEPS = (0.1, 1.0, 10.0, 50.0)

    def __init__(self, bridge: Optional[BridgeClient] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.bridge: Optional[BridgeClient] = None
        self.current_step: float = 10.0
        self._motion_buttons: List[QPushButton] = []   # wymagają maszyny w spoczynku
        self._setup_ui()
        self.set_bridge(bridge)

    # ------------------------------------------------------------------ podłączenie
    def set_bridge(self, bridge: Optional[BridgeClient]):
        if self.bridge is bridge:
            return
        if self.bridge is not None:
            for sig, slot in ((self.bridge.state_changed, self.update_state),
                              (self.bridge.disconnected, self._on_disconnected)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.bridge = bridge
        if bridge is not None:
            bridge.state_changed.connect(self.update_state)
            bridge.disconnected.connect(self._on_disconnected)
        self.update_state(bridge.state if bridge and bridge.is_connected() else None)

    # ------------------------------------------------------------------ UI
    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #aaa; font-weight: 500;")
        return lbl

    def _button(self, text: str, style: str, slot=None, motion: bool = False, tooltip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(style)
        if tooltip:
            btn.setToolTip(tooltip)
        if slot is not None:
            btn.clicked.connect(slot)
        if motion:
            self._motion_buttons.append(btn)
        return btn

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(4, 4, 4, 4)
        main.setSpacing(8)

        # Stan maszyny i zadania
        self.lbl_state = QLabel("Rozłączony")
        self.lbl_state.setWordWrap(True)
        main.addWidget(self.lbl_state)

        # 1. Krok
        main.addWidget(self._section_label("Krok przesuwu:"))
        h_step = QHBoxLayout()
        h_step.setSpacing(4)
        self.btn_group_step = QButtonGroup(self)
        self.btn_group_step.setExclusive(True)
        for val in self.STEPS:
            btn = QPushButton(f"{val:g} mm")
            btn.setCheckable(True)
            btn.setStyleSheet(STYLE_STEP)
            btn.setChecked(val == self.current_step)
            btn.clicked.connect(lambda _=False, v=val: self._set_step(v))
            self.btn_group_step.addButton(btn)
            h_step.addWidget(btn)
        main.addLayout(h_step)

        # 2. XY i Z
        h_axes = QHBoxLayout()
        h_axes.setSpacing(10)
        grid_xy = QGridLayout()
        grid_xy.setSpacing(4)
        grid_xy.addWidget(self._button("▲ Y+", STYLE_JOG, lambda: self._send_jog(0, self.current_step, 0), True), 0, 1)
        grid_xy.addWidget(self._button("◀ X-", STYLE_JOG, lambda: self._send_jog(-self.current_step, 0, 0), True), 1, 0)
        grid_xy.addWidget(self._button("XY 0", STYLE_HOME, lambda: self.send_gcode("G90 G0 X0 Y0 F4000"), True,
                                       "Szybki przejazd do X0 Y0 układu roboczego"), 1, 1)
        grid_xy.addWidget(self._button("▶ X+", STYLE_JOG, lambda: self._send_jog(self.current_step, 0, 0), True), 1, 2)
        grid_xy.addWidget(self._button("▼ Y-", STYLE_JOG, lambda: self._send_jog(0, -self.current_step, 0), True), 2, 1)
        h_axes.addLayout(grid_xy, stretch=3)

        v_z = QVBoxLayout()
        v_z.setSpacing(4)
        v_z.addWidget(self._button("▲ Z+", STYLE_JOG, lambda: self._send_jog(0, 0, self.current_step), True))
        v_z.addWidget(self._button("Z +5", STYLE_HOME, lambda: self.send_gcode("G90 G0 Z5 F1500"), True))
        v_z.addWidget(self._button("▼ Z-", STYLE_JOG, lambda: self._send_jog(0, 0, -self.current_step), True))
        h_axes.addLayout(v_z, stretch=1)
        main.addLayout(h_axes)

        # 3. Układ roboczy
        main.addWidget(self._section_label("Układ roboczy (Work Offset):"))
        grid_zero = QGridLayout()
        grid_zero.setSpacing(4)
        for i, (txt, cmd) in enumerate((("Zeruj XY", "G10 L20 P1 X0 Y0"), ("Zeruj Y", "G10 L20 P1 Y0"),
                                        ("Zeruj Z", "G10 L20 P1 Z0"), ("Zeruj XYZ", "G10 L20 P1 X0 Y0 Z0"))):
            grid_zero.addWidget(self._button(txt, STYLE_ZERO, lambda _=False, c=cmd: self.send_gcode(c), True), i // 2, i % 2)
        main.addLayout(grid_zero)

        # 4. Szybkie akcje
        h_actions = QHBoxLayout()
        h_actions.setSpacing(4)
        self.btn_focus = self._button("💡 Plamka 0.5%", STYLE_FOCUS, self._toggle_laser_focus, True)
        self.btn_focus.setCheckable(True)
        self.btn_unlock = self._button("🔓 Odblokuj ($X)", STYLE_ACTION, self._unlock,
                                       tooltip="Kasuje alarm GRBL lub potwierdza błąd zadania")
        self.btn_home = self._button("🏠 Baza ($H)", STYLE_ACTION, lambda: self.send_gcode("$H"))
        h_actions.addWidget(self.btn_focus)
        h_actions.addWidget(self.btn_unlock)
        h_actions.addWidget(self.btn_home)
        main.addLayout(h_actions)

        # 5. Sterowanie zadaniem
        main.addWidget(self._section_label("Zadanie:"))
        h_job = QHBoxLayout()
        h_job.setSpacing(4)
        self.btn_pause = self._button("⏸ Pauza", STYLE_PAUSE, self._pause,
                                      tooltip="Feed hold – zadanie zostaje w pamięci i można je wznowić")
        self.btn_resume = self._button("▶ Wznów", STYLE_RESUME, self._resume,
                                       tooltip="Wyjście z Hold/Door i kontynuacja zadania (zablokowane po anulowaniu)")
        h_job.addWidget(self.btn_pause)
        h_job.addWidget(self.btn_resume)
        main.addLayout(h_job)

        self.btn_jog_stop = self._button(
            "🛑 STOP – ANULUJ I WYCZYŚĆ", STYLE_STOP, self.emergency_stop,
            tooltip="Feed hold, a po zatrzymaniu soft-reset: usuwa z pamięci GRBL niewykonane linie.\n"
                    "Wyprowadza maszynę z Hold także po przycisku bezpieczeństwa."
        )
        main.addWidget(self.btn_jog_stop)

    # ------------------------------------------------------------------ stan
    def _on_disconnected(self):
        self.update_state(None)

    def update_state(self, state: Optional[Dict[str, Any]]):
        """Włącza tylko akcje bezpieczne w bieżącym stanie maszyny i zadania."""
        connected = bool(self.bridge and self.bridge.is_connected() and state)
        status = str((state or {}).get("status", ""))
        job_state = str((state or {}).get("job_state", "idle"))
        base_status = status.split(":")[0]

        idle = connected and base_status in ("Idle", "Jog") and job_state == "idle"
        alarm = connected and base_status == "Alarm"
        held = connected and base_status in ("Hold", "Door")
        job_active = connected and job_state in ("running", "paused", "cancelling")
        door_open = status in ("Door:1", "Door:2", "Door:3")

        for btn in self._motion_buttons:
            btn.setEnabled(idle)
        self.btn_home.setEnabled(connected and (idle or alarm))
        self.btn_unlock.setEnabled(connected and not job_active and (alarm or job_state == "error" or idle))
        self.btn_pause.setEnabled(connected and job_state == "running" and not held
                                  and bool(self.bridge and self.bridge.supports_job_control))
        self.btn_resume.setEnabled(connected and (held or job_state == "paused") and not door_open
                                   and status not in ("Hold:1",) and job_state not in ("cancelling", "error"))
        # STOP zawsze dostępny przy połączeniu – telemetria może być opóźniona
        self.btn_jog_stop.setEnabled(connected)

        if not connected:
            self.btn_focus.setChecked(False)
            self.lbl_state.setText("Rozłączony")
            self.lbl_state.setStyleSheet("color: #888; font-weight: bold;")
            return

        names = {"idle": "brak zadania", "running": "w toku", "paused": "wstrzymane",
                 "cancelling": "anulowanie…", "error": "przerwane błędem"}
        text = f"GRBL: {status or '?'} | zadanie: {names.get(job_state, job_state)}"
        if (state or {}).get("job_running"):
            text += f" ({float(state.get('job_progress', 0.0)):.0f}%)"
        if door_open:
            text += "\nDrzwi/obwód bezpieczeństwa otwarty – zamknij, aby wznowić."
        elif status == "Hold:1":
            text += "\nHamowanie…"
        if alarm:
            text += f"\nAlarm {state.get('alarm') or ''} – Odblokuj ($X); po utracie pozycji wykonaj bazowanie."
        if job_state == "error" and state.get("last_error"):
            text += f"\n{state['last_error']}"
        color = _STATUS_COLORS.get(base_status, "#374151")
        self.lbl_state.setText(text)
        self.lbl_state.setStyleSheet(
            f"background-color: {color}; color: white; padding: 4px 6px; border-radius: 4px; font-weight: bold;")

    # ------------------------------------------------------------------ akcje
    def _set_step(self, val: float):
        self.current_step = val

    def _send_jog(self, dx: float, dy: float, dz: float):
        """Płynny ruch GRBL Jog ($J=...) w układzie przyrostowym."""
        feed = 800 if dz else (1000 if self.current_step <= 1.0 else 3000)
        parts = ["$J=G91"]
        for axis, val in (("X", dx), ("Y", dy), ("Z", dz)):
            if val:
                parts.append(f"{axis}{val:.3f}")
        parts.append(f"F{feed}")
        self.send_gcode(" ".join(parts))

    def _toggle_laser_focus(self):
        # Plamka celownicza 0.5% (S5 przy $30=1000)
        self.send_gcode("M3 S5" if self.btn_focus.isChecked() else "M5")

    def _pause(self):
        if self.bridge:
            self.bridge.pause()

    def _resume(self):
        if self.bridge:
            self.bridge.resume()

    def _unlock(self):
        if not self.bridge:
            return
        state = self.bridge.state or {}
        if state.get("job_state") == "error":
            self.bridge.acknowledge_error()
        if str(state.get("status", "")).startswith("Alarm") or state.get("job_state") != "error":
            self.bridge.unlock()

    def emergency_stop(self):
        """Anuluj zadanie i wyczyść bufor GRBL (bezpieczne wyjście z Hold)."""
        if self.btn_focus.isChecked():
            self.btn_focus.setChecked(False)
        if self.bridge:
            self.bridge.cancel()
        self.command_sent.emit("STOP")

    def send_gcode(self, cmd: str):
        if self.bridge and self.bridge.send_gcode(cmd):
            self.command_sent.emit(cmd)
