from __future__ import annotations
import math
import re
from typing import Tuple


class CycleTimeEstimator:
    """
    Szacuje czas wykonania programu G-code z uwzględnieniem
    prędkości posuwów (G0/G1) oraz przyspieszenia osi GRBL ($120, $121).
    """

    def __init__(self, default_rapid_f: float = 6000.0, accel_mm_s2: float = 500.0):
        # Domyślny posuw G0 [mm/min] oraz przyspieszenie w mm/s^2 (np. parametr $120/$121 w GRBL)
        self.default_rapid_f = default_rapid_f
        self.accel = accel_mm_s2

    def estimate(self, gcode_text: str) -> Tuple[float, float, float]:
        """
        Zwraca:
            (total_seconds, rapid_distance_mm, cut_distance_mm)
        """
        curr_x, curr_y = 0.0, 0.0
        curr_feed = 1000.0  # mm/min
        is_rapid = False

        total_time_s = 0.0
        rapid_dist = 0.0
        cut_dist = 0.0

        for line in gcode_text.splitlines():
            line = line.strip()
            # Odrzucamy komentarze i puste linie
            if not line or line.startswith(";") or line.startswith("("):
                continue

            # Usunięcie komentarzy w linii
            clean_line = re.sub(r";.*|\(.*\)", "", line).strip()
            if not clean_line:
                continue

            parts = clean_line.split()
            target_x = curr_x
            target_y = curr_y
            has_motion = False

            for part in parts:
                cmd = part[0].upper()
                val_str = part[1:]
                try:
                    if cmd == "G":
                        code = int(float(val_str))
                        if code == 0:
                            is_rapid = True
                        elif code == 1:
                            is_rapid = False
                    elif cmd == "X":
                        target_x = float(val_str)
                        has_motion = True
                    elif cmd == "Y":
                        target_y = float(val_str)
                        has_motion = True
                    elif cmd == "F":
                        curr_feed = float(val_str)
                except ValueError:
                    continue

            if has_motion:
                dx = target_x - curr_x
                dy = target_y - curr_y
                dist = math.hypot(dx, dy)

                if dist > 1e-4:
                    feed_mm_s = (self.default_rapid_f if is_rapid else curr_feed) / 60.0
                    
                    # Model trapezowy przyspieszenia:
                    # Droga potrzebna na rozpędzenie i wyhamowanie do V_max
                    d_accel = (feed_mm_s ** 2) / self.accel
                    
                    if dist < d_accel:
                        # Profil trójkątny (brak osiągnięcia pełnej prędkości)
                        t_seg = 2.0 * math.sqrt(dist / self.accel)
                    else:
                        # Profil trapezowy (rozpędzanie + stała prędkość + hamowanie)
                        t_accel = 2.0 * (feed_mm_s / self.accel)
                        d_const = dist - d_accel
                        t_const = d_const / feed_mm_s
                        t_seg = t_accel + t_const

                    total_time_s += t_seg

                    if is_rapid:
                        rapid_dist += dist
                    else:
                        cut_dist += dist

                    curr_x, target_y = target_x, target_y

        return total_time_s, rapid_dist, cut_dist

    @staticmethod
    def format_time(seconds: float) -> str:
        """Formatuje sekundy do postaci HH:MM:SS lub MM:SS."""
        s = int(round(seconds))
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"