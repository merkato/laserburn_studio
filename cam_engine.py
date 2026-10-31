import math
import numpy as np
from typing import List, Tuple, Optional
from shapely.geometry import Polygon, MultiPolygon, LineString, MultiLineString, box
from shapely import affinity
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QTransform
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsItem
from PIL import Image
from path_optimizer import PathOptimizer
from kerf_compensator import KerfCompensator


class GCodePostProcessor:
    """
    Generator i postprocesor G-code kompatybilny ze standardem GRBL 1.1f+ dla laserów CNC.
    Obsługuje komendy modalne, dynamiczną moc lasera M4 oraz kompresję RLE dla rastrów.
    """
    def __init__(self, laser_mode: str = "M4", s_max: int = 1000):
        self.laser_mode = laser_mode
        self.s_max = s_max

    def _header(self) -> List[str]:
        return [
            "; --- GENEROWANE PRZEZ LASERBURN STUDIO ---",
            "G21         ; Jednostki metryczne (mm)",
            "G90         ; Pozycjonowanie absolutne",
            "M4 S0       ; Aktywacja dynamicznej mocy lasera (M4)",
            "G0 Z0       ; Pozycja robocza Z",
            ""
        ]

    def _footer(self) -> List[str]:
        return [
            "",
            "M5          ; Wyłączenie lasera",
            "G0 X0 Y0    ; Powrót do bazy WPos",
            "M2          ; Koniec programu"
        ]

    # =========================================================================
    # 1. GENEROWANIE ŚCIEŻEK CIĘCIA KONTURU (LINE)
    # =========================================================================
    def generate_line_paths(
        self, 
        paths: List[List[Tuple[float, float]]], 
        feed: float, 
        power_pct: float, 
        passes: int = 1
    ) -> List[str]:
        """
        Generuje kod dla graweru/cięcia wektorowego po obrysie (Line).
        paths: Lista ciągów punktów [(x, y), ...] w milimetrach stołu.
        """
        lines = []
        s_val = int(round((power_pct / 100.0) * self.s_max))

        for p_idx in range(1, passes + 1):
            lines.append(f"; --- Przejście {p_idx}/{passes} ---")
            for subpath in paths:
                if len(subpath) < 2:
                    continue

                start_pt = subpath[0]
                # Ruch jałowy z wyłączoną wiązką
                lines.append(f"G0 X{start_pt[0]:.3f} Y{start_pt[1]:.3f}")
                
                # Pierwszy ruch roboczy ustala posuw F i moc S
                p1 = subpath[1]
                lines.append(f"G1 X{p1[0]:.3f} Y{p1[1]:.3f} F{feed:.1f} S{s_val}")

                # Kolejne punkty (modalne G1, F i S)
                for pt in subpath[2:]:
                    lines.append(f"G1 X{pt[0]:.3f} Y{pt[1]:.3f}")

            lines.append("M5 S0 ; Wyłączenie na nawrocie")

        return lines

    # =========================================================================
    # 2. ALGORYTM KRESKOWANIA SHAPELY (FILL HATCHING)
    # =========================================================================
    def generate_fill_hatch(
        self, 
        polygons: List[Polygon], 
        interval_mm: float, 
        angle_deg: float, 
        feed: float, 
        power_pct: float
    ) -> List[str]:
        """
        Kreskowanie wnętrza wektorów zadanym kątem i gęstością (Fill/Hatch).
        Generuje ciągły zygzak (meander) redukujący puste przejazdy głowicy.
        """
        lines = []
        s_val = int(round((power_pct / 100.0) * self.s_max))
        hatch_segments: List[List[Tuple[float, float]]] = []

        for poly in polygons:
            if poly.is_empty or not poly.is_valid:
                continue

            # Rotacja do poziomu w celu prostego przecięcia siatką poziomą Y
            rotated_poly = affinity.rotate(poly, -angle_deg, origin=(0, 0))
            minx, miny, maxx, maxy = rotated_poly.bounds

            current_y = miny + interval_mm / 2.0
            sweep_lines = []
            while current_y <= maxy:
                sweep_lines.append(LineString([(minx - 10, current_y), (maxx + 10, current_y)]))
                current_y += interval_mm

            if not sweep_lines:
                continue

            multi_grid = MultiLineString(sweep_lines)
            intersection = rotated_poly.intersection(multi_grid)

            # Ekstrakcja wyciętych odcinków
            extracted_lines = []
            if intersection.geom_type == 'LineString':
                extracted_lines.append(intersection)
            elif intersection.geom_type == 'MultiLineString':
                extracted_lines.extend(list(intersection.geoms))

            # Obrót powrotny do pierwotnej orientacji
            for seg in extracted_lines:
                orig_seg = affinity.rotate(seg, angle_deg, origin=(0, 0))
                hatch_segments.append(list(orig_seg.coords))

        if not hatch_segments:
            return ["; Brak geometrii do kreskowania"]

        # Sortowanie zygzakiem (Meander)
        lines.append(f"; --- Wypełnienie Hatch (Krok: {interval_mm:.2f}mm, Kąt: {angle_deg}°) ---")
        reverse = False
        for seg in hatch_segments:
            pts = seg[::-1] if reverse else seg
            lines.append(f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
            lines.append(f"G1 X{pts[1][0]:.3f} Y{pts[1][1]:.3f} F{feed:.1f} S{s_val}")
            lines.append("M5 S0")
            reverse = not reverse

        return lines

    def generate_line_paths(
        self, 
        paths: List[List[Tuple[float, float]]], 
        feed: float, 
        power_pct: float, 
        passes: int = 1,
        optimize_order: bool = True
    ) -> List[str]:
        lines = []
        s_val = int(round((power_pct / 100.0) * self.s_max))

        # Optymalizacja kolejności cięcia: Inside-Out + redukcja G0
        if optimize_order:
            processed_paths = PathOptimizer.optimize_layer(paths, start_machine_pos=(0.0, 0.0))
        else:
            processed_paths = paths

        for p_idx in range(1, passes + 1):
            lines.append(f"; --- Przejście {p_idx}/{passes} ---")
            for subpath in processed_paths:
                if len(subpath) < 2:
                    continue

                start_pt = subpath[0]
                lines.append(f"G0 X{start_pt[0]:.3f} Y{start_pt[1]:.3f}")
                
                p1 = subpath[1]
                lines.append(f"G1 X{p1[0]:.3f} Y{p1[1]:.3f} F{feed:.1f} S{s_val}")

                for pt in subpath[2:]:
                    lines.append(f"G1 X{pt[0]:.3f} Y{pt[1]:.3f}")

            lines.append("M5 S0 ; Wyłączenie na nawrocie")

        return lines
    # =========================================================================
    # 3. ZOPTYMALIZOWANY SKANER RASTRÓW (M4 RASTER WITH RLE & OVERSCAN)
    # =========================================================================
    def generate_raster_m4(
        self,
        pil_image: Image.Image,
        origin_x: float,
        origin_y: float,
        width_mm: float,
        height_mm: float,
        line_interval_mm: float,
        feed: float,
        power_min_pct: float,
        power_max_pct: float,
        overscan_mm: float = 3.0
    ) -> List[str]:
        """
        Przekształca bitmapę na skanowanie poziome linia po linii z kompresją RLE,
        obsługą trybu M4 oraz wybiegiem dynamicznym (Overscanning).
        """
        lines = []
        lines.append("; --- Skanowanie rastrowe M4 ---")

        # 1. Przygotowanie macierzy szarości
        gray_img = pil_image.convert("L")
        
        # Obliczenie docelowej liczby linii i punktów
        num_rows = int(math.ceil(height_mm / line_interval_mm))
        num_cols = int(math.ceil(width_mm / line_interval_mm))
        resized = gray_img.resize((num_cols, num_rows), Image.Resampling.BILINEAR)
        img_arr = np.array(resized)

        # Odwrócenie osi pionowej, aby czytać od dołu (Y=0 maszyny) do góry
        img_arr = np.flipud(img_arr)

        pixel_w_mm = width_mm / num_cols
        s_min = int(round((power_min_pct / 100.0) * self.s_max))
        s_max = int(round((power_max_pct / 100.0) * self.s_max))

        reverse_row = False

        for r_idx in range(num_rows):
            row_y = origin_y + r_idx * line_interval_mm
            row_pixels = img_arr[r_idx, :]

            # Kompresja RLE dla wiersza: ciągi identycznych/zbliżonych jasności
            if reverse_row:
                col_indices = list(range(num_cols - 1, -1, -1))
                run_dir = -1
                start_x = origin_x + width_mm
                end_x = origin_x
            else:
                col_indices = list(range(num_cols))
                run_dir = 1
                start_x = origin_x
                end_x = origin_x + width_mm

            # Pomijanie całych białych wierszy (jasność >= 250)
            if np.all(row_pixels >= 254):
                reverse_row = not reverse_row
                continue

            # Overscan: Rozpędzenie głowicy przed właściwym obrazem
            pre_x = start_x - (overscan_mm * run_dir)
            post_x = end_x + (overscan_mm * run_dir)

            lines.append(f"G0 X{pre_x:.3f} Y{row_y:.3f}")
            lines.append(f"G1 X{start_x:.3f} F{feed:.1f} S0")  # Rozpęd do prędkości zadanej

            # Kodowanie RLE
            cur_brightness = row_pixels[col_indices[0]]
            cur_run_length = 1

            for c in col_indices[1:]:
                b = row_pixels[c]
                # Agregacja pikseli (tolerancja drobnych szumów kwantyzacji)
                if abs(int(b) - int(cur_brightness)) <= 2:
                    cur_run_length += 1
                else:
                    # Emisja segmentu
                    seg_end_x = start_x + (c * pixel_w_mm * run_dir)
                    # Wartość S: 255 (biały) -> S0, 0 (czarny) -> s_max
                    s_calc = int(s_max - (cur_brightness / 255.0) * (s_max - s_min))
                    if s_calc < s_min or cur_brightness >= 254:
                        s_calc = 0

                    lines.append(f"G1 X{seg_end_x:.3f} S{s_calc}")
                    cur_brightness = b
                    cur_run_length = 1

            # Ostatni segment w wierszu
            lines.append(f"G1 X{end_x:.3f} S0")

            # Wybieg i hamowanie głowicy
            lines.append(f"G1 X{post_x:.3f} S0")

            reverse_row = not reverse_row

        return lines

    # =========================================================================
    # 4. GŁÓWNY EKSPORTER PROJEKTU
    # =========================================================================
    def build_full_gcode(self, job_blocks: List[List[str]]) -> str:
        """Łączy nagłówek, poszczególne bloki operacji i stopkę w kompletny program NC."""
        output = []
        output.extend(self._header())
        for block in job_blocks:
            output.extend(block)
            output.append("")
        output.extend(self._footer())
        return "\n".join(output)