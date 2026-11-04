"""
Klient mostka grbl-bridge dla LaserBurn Studio.

Jedno miejsce całej komunikacji z Raspberry Pi:
- WebSocket: telemetria (stan maszyny), komendy ręczne, sterowanie zadaniem,
- HTTP: wysłanie pliku G-code jako zadania.

Mostek v2 (pole "capabilities" w stanie) dostaje komendy JSON i sam wykonuje
bezpieczne anulowanie (feed hold -> Hold:0 -> soft-reset, czyszczenie bufora GRBL).
Dla mostka v1 klient wykonuje tę sekwencję po swojej stronie, obserwując telemetrię.
"""

from __future__ import annotations
import io
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket


RT_FEED_HOLD = "!"
RT_CYCLE_START = "~"
RT_SOFT_RESET = "\x18"
RT_JOG_CANCEL = "\x85"


def http_base_from_ws(ws_url: str) -> str:
    base = ws_url.strip().replace("ws://", "http://").replace("wss://", "https://")
    if base.endswith("/ws"):
        base = base[:-3]
    return base.rstrip("/")


class GCodeUploadWorker(QThread):
    """Wysyła plik G-code jako zadanie (POST /api/files/local, print=true)."""
    finished_signal = Signal(bool, str)

    def __init__(self, host_url: str, gcode_content: str, filename: str = "job.nc", parent=None):
        super().__init__(parent)
        self.target_url = f"{http_base_from_ws(host_url)}/api/files/local"
        self.gcode_content = gcode_content
        self.filename = filename

    def run(self):
        boundary = "----LaserBurnBoundary7MA4YWxkTrZu0gW"
        body = io.BytesIO()
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="print"\r\n\r\ntrue\r\n')
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="file"; filename="{self.filename}"\r\n'.encode())
        body.write(b"Content-Type: text/plain\r\n\r\n")
        body.write(self.gcode_content.encode("utf-8"))
        body.write(f"\r\n--{boundary}--\r\n".encode())
        payload = body.getvalue()

        req = urllib.request.Request(
            self.target_url, data=payload, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Content-Length": str(len(payload))},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                info = {}
                try:
                    info = json.loads(response.read().decode("utf-8") or "{}")
                except ValueError:
                    pass
                total = info.get("total_lines")
                suffix = f" ({total} linii)" if total else ""
                self.finished_signal.emit(True, f"Zadanie wysłane. Rozpoczęto wypalanie{suffix}.")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail", "")
            except Exception:
                pass
            if e.code == 409:
                self.finished_signal.emit(False, f"Mostek odrzucił zadanie: {detail or 'maszyna zajęta'}")
            else:
                self.finished_signal.emit(False, f"Serwer zwrócił kod {e.code} {detail}".strip())
        except Exception as e:
            self.finished_signal.emit(False, f"Błąd transmisji: {e}")


class BridgeClient(QObject):
    """Połączenie z mostkiem; emituje znormalizowany stan maszyny."""

    connected = Signal()
    disconnected = Signal()
    state_changed = Signal(dict)       # pełny stan (pola MachineState + "job_state" zawsze obecne)
    ack_received = Signal(dict)        # odpowiedzi mostka v2 na komendy JSON
    error = Signal(str)
    notice = Signal(str)               # komunikaty dla użytkownika (np. wynik anulowania)

    LEGACY_STOP_TIMEOUT_MS = 8000

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.ws = QWebSocket()
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_message)
        self.ws.errorOccurred.connect(self._on_error)

        self.url = ""
        self.state: Dict[str, Any] = {}
        self.capabilities: set = set()
        self._upload_worker: Optional[GCodeUploadWorker] = None
        self._legacy_cancelled = False   # v1: blokada wznowienia po anulowaniu

        # Bezpieczne anulowanie dla mostka v1 (sekwencja po stronie klienta)
        self._legacy_cancel_timer = QTimer(self)
        self._legacy_cancel_timer.setInterval(100)
        self._legacy_cancel_timer.timeout.connect(self._legacy_cancel_tick)
        self._legacy_cancel_elapsed = 0

    # ------------------------------------------------------------------ połączenie
    def open(self, url: str):
        self.url = url.strip()
        self.ws.open(QUrl(self.url))

    def close(self):
        self.ws.close()

    def is_connected(self) -> bool:
        return self.ws.state() == QAbstractSocket.SocketState.ConnectedState

    @property
    def supports_job_control(self) -> bool:
        return "job_control" in self.capabilities

    def _on_connected(self):
        self.state = {}
        self.capabilities = set()
        self.connected.emit()

    def _on_disconnected(self):
        self._legacy_cancel_timer.stop()
        self.state = {}
        self.disconnected.emit()

    def _on_error(self, _code):
        self.error.emit(self.ws.errorString())

    # ------------------------------------------------------------------ odbiór
    def _on_message(self, message: str):
        try:
            data = json.loads(message)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if data.get("type") == "ack":
            self.ack_received.emit(data)
            if not data.get("ok", True):
                self.error.emit(str(data.get("message", "Komenda odrzucona przez mostek")))
            elif data.get("cmd") == "cancel":
                self.notice.emit(str(data.get("message", "Zadanie anulowane.")))
            return

        self.capabilities = set(data.get("capabilities") or [])
        if "job_state" not in data:  # mostek v1 – stan zadania wyznaczany lokalnie
            data["job_state"] = self._legacy_job_state(data)
        self.state = data
        self.state_changed.emit(data)

    def _legacy_job_state(self, data: Dict[str, Any]) -> str:
        if self._legacy_cancel_timer.isActive():
            return "cancelling"
        status = str(data.get("status", ""))
        if data.get("job_running"):
            return "paused" if status.startswith(("Hold", "Door")) else "running"
        return "idle"

    # ------------------------------------------------------------------ wysyłanie
    def _send_text(self, text: str) -> bool:
        if not self.is_connected():
            self.error.emit("Brak połączenia z mostkiem.")
            return False
        self.ws.sendTextMessage(text)
        return True

    def _send_cmd(self, cmd: str, **kw) -> bool:
        return self._send_text(json.dumps({"cmd": cmd, **kw}))

    def send_gcode(self, line: str) -> bool:
        """Komenda ręczna (jog, $X, G10, framing…). Mostek v2 odrzuca ją w trakcie zadania."""
        if self.supports_job_control:
            return self._send_cmd("gcode", line=line)
        return self._send_text(line)

    def pause(self) -> bool:
        if self.supports_job_control:
            return self._send_cmd("pause")
        # v1: "!" przez WebSocket oznacza anulowanie, a nie pauzę – pauza niedostępna
        self.error.emit("Ta wersja mostka nie obsługuje pauzy – zaktualizuj grbl-bridge.")
        return False

    def resume(self) -> bool:
        job_state = self.state.get("job_state")
        if job_state in ("cancelling", "error") or self._legacy_cancelled:
            self.error.emit("Zadanie zostało anulowane – wznowienie zablokowane (bufor wyczyszczony).")
            return False
        if self.supports_job_control:
            return self._send_cmd("resume")
        return self._send_text(RT_CYCLE_START)

    def cancel(self) -> bool:
        """Anuluj zadanie i usuń z pamięci GRBL przyjęte linie (bez utraty pozycji)."""
        if self.supports_job_control:
            return self._send_cmd("cancel")
        # v1: mostek po "!" przestaje wysyłać i robi feed hold; bufor GRBL czyścimy sami resetem
        if not self._send_text(RT_FEED_HOLD):
            return False
        self._legacy_cancelled = True
        self._legacy_cancel_elapsed = 0
        self._legacy_cancel_timer.start()
        return True

    def _legacy_cancel_tick(self):
        self._legacy_cancel_elapsed += self._legacy_cancel_timer.interval()
        status = str(self.state.get("status", ""))
        stopped = status.startswith(("Hold:0", "Idle", "Alarm", "Door:0", "Door:1")) or status == "Hold"
        timed_out = self._legacy_cancel_elapsed >= self.LEGACY_STOP_TIMEOUT_MS
        if stopped or timed_out:
            self._legacy_cancel_timer.stop()
            self._send_text(RT_SOFT_RESET)
            self.notice.emit(
                "Zadanie anulowane, bufor GRBL wyczyszczony." if stopped else
                "Maszyna nie zatrzymała się w czasie – wykonano reset (sprawdź pozycję/bazowanie)."
            )

    def unlock(self) -> bool:
        self._legacy_cancelled = False
        if self.supports_job_control:
            return self._send_cmd("unlock")
        return self._send_text("$X")

    def acknowledge_error(self) -> bool:
        self._legacy_cancelled = False
        if self.supports_job_control:
            return self._send_cmd("ack_error")
        return True

    # ------------------------------------------------------------------ zadanie
    def upload_job(self, gcode_text: str, on_finished, filename: str = "laserburn_job.nc") -> bool:
        if not self.is_connected():
            self.error.emit("Brak połączenia z mostkiem.")
            return False
        if self._upload_worker is not None and self._upload_worker.isRunning():
            self.error.emit("Poprzednie wysyłanie jeszcze trwa.")
            return False
        self._legacy_cancelled = False
        self._upload_worker = GCodeUploadWorker(self.url, gcode_text, filename=filename, parent=self)
        self._upload_worker.finished_signal.connect(on_finished)
        self._upload_worker.start()
        return True
