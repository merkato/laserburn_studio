"""
raster_processor.py
Moduł przetwarzania grafiki rastrowej dla LaserCAMStudio.
Obsługuje konwersję obrazów, dithering Floyd-Steinberga, tworzenie elementów
sceny Qt oraz generowanie zoptymalizowanego G-code pod GRBL ($32=1, M4).
"""

from typing import Tuple, List, Optional
import numpy as np
from PIL import Image

from PySide6.QtWidgets import QGraphicsPixmapItem
from PySide6.QtGui import QImage, QPixmap


class RasterProcessor:
    """Klasa narzędziowa do przetwarzania i wypalania rastrów."""

    @staticmethod
    def dither_floyd_steinberg(
        image_path: str,
        target_width_mm: float,
        dpi: float = 254.0,
        gamma: float = 1.0,
        invert: bool = False
    ) -> Tuple[np.ndarray, float, float]:
        """
        Konwertuje obraz na tablicę 1-bitową (0 = białe/brak wiązki, 1 = czarne/wypalenie).
        
        :param image_path: Ścieżka do pliku graficznego.
        :param target_width_mm: Szerokość docelowa na stole w mm.
        :param dpi: Rozdzielczość (254.0 DPI = 10 linii/mm, krok 0.1 mm).
        :param gamma: Korekcja gamma (np. 1.2 rozjaśnia cienie dla drewna).
        :param invert: Odwrócenie kolorów (przydatne przy grawerowaniu na ciemnym podłożu).
        :return: (binary_grid, dots_per_mm, target_height_mm)
        """
        img = Image.open(image_path).convert("L")
        dots_per_mm = dpi / 25.4
        w_px = max(1, int(round(target_width_mm * dots_per_mm)))
        aspect = img.height / img.width
        h_px = max(1, int(round(w_px * aspect)))
        target_height_mm = h_px / dots_per_mm

        img = img.resize((w_px, h_px), Image.Resampling.LANCZOS)
        arr = np.array(img, dtype=np.float32)

        if gamma != 1.0:
            arr = 255.0 * ((arr / 255.0) ** (1.0 / gamma))

        if invert:
            arr = 255.0 - arr

        h, w = arr.shape
        # Floyd-Steinberg Error Diffusion
        for y in range(h):
            for x in range(w):
                old_val = arr[y, x]
                new_val = 255.0 if old_val > 127.5 else 0.0
                arr[y, x] = new_val
                err = old_val - new_val

                if x + 1 < w:
                    arr[y, x + 1] += err * (7.0 / 16.0)
                if y + 1 < h:
                    if x > 0:
                        arr[y + 1, x - 1] += err * (3.0 / 16.0)
                    arr[y + 1, x] += err * (5.0 / 16.0)
                    if x + 1 < w:
                        arr[y + 1, x + 1] += err * (1.0 / 16.0)

        # 1 = punkt do wypalenia (czarny), 0 = tło (biały)
        binary_grid = (arr < 128).astype(np.uint8)
        return binary_grid, dots_per_mm, target_height_mm

    @staticmethod
    def create_canvas_pixmap_item(
        binary_grid: np.ndarray,
        width_mm: float,
        height_mm: float,
        color_hex: str = "#111827"
    ) -> QGraphicsPixmapItem:
        """
        Tworzy obiekt QGraphicsPixmapItem z przezroczystym tłem do wyświetlenia na scenie CAD.
        """
        h_px, w_px = binary_grid.shape

        # Tworzymy obraz RGBA (czarne kropki wypalenia, białe tło jako 100% przezroczyste)
        rgba = np.zeros((h_px, w_px, 4), dtype=np.uint8)
        
        # Parsowanie koloru podglądu
        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)

        burn_mask = (binary_grid == 1)
        rgba[burn_mask] = [r, g, b, 230]       # Kropki grawerunku
        rgba[~burn_mask] = [255, 255, 255, 0]  # Puste tło

        qimg = QImage(rgba.data, w_px, h_px, w_px * 4, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qimg.copy())

        item = QGraphicsPixmapItem(pixmap)
        # Skalowanie pikseli do wymiarów w milimetrach sceny CAD
        item.setScale(width_mm / w_px)
        item.setZValue(15)  # Nad obrysem plastra (z=5), pod celownikiem (z=100)
        return item

    @staticmethod
    def generate_gcode(
        binary_grid: np.ndarray,
        dots_per_mm: float,
        origin_x: float,
        origin_y: float,
        feedrate: int = 3000,
        s_max: int = 1000,
        overscan_mm: float = 2.5
    ) -> List[str]:
        """
        Generuje dwukierunkowy G-code w trybie GRBL M4 (Dynamic Laser Power).
        
        :param binary_grid: Tablica 1-bitowa (1 = burn, 0 = skip).
        :param dots_per_mm: Rozdzielczość (np. 10 px/mm).
        :param origin_x: Lewy dolny róg w osi X na stole.
        :param origin_y: Lewy dolny róg w osi Y na stole.
        :param feedrate: Prędkość posuwu roboczego (mm/min).
        :param s_max: Maksymalna wartość mocy GRBL ($30).
        :param overscan_mm: Dobieg poza krawędź grafiki na rampy przyspieszeń.
        """
        h_px, w_px = binary_grid.shape
        step_mm = 1.0 / dots_per_mm

        lines: List[str] = [
            "; --- START RASTER ENGRAVE (Floyd-Steinberg) ---",
            "G21 ; mm",
            "G90 ; Pozycjonowanie bezwzgędne",
            "M4 ; Dynamic laser power",
            f"G0 F{feedrate * 2}"
        ]

        for row_idx in range(h_px):
            # Oś Y w grafice rośnie w dół, a w układzie obrabiarki w górę
            cur_y = origin_y + (h_px - 1 - row_idx) * step_mm
            row = binary_grid[row_idx, :]

            # Pomijanie pustych linii (brak jakiegokolwiek punktu)
            if not np.any(row):
                continue

            indices = np.where(row == 1)[0]
            first_x_idx = int(indices[0])
            last_x_idx = int(indices[-1])

            traverse_right = (row_idx % 2 == 0)

            if traverse_right:
                scan_slice = row[first_x_idx : last_x_idx + 1]
                start_x = origin_x + first_x_idx * step_mm

                # Najazd z dobiegiem z lewej
                lines.append(f"G0 X{start_x - overscan_mm:.3f} Y{cur_y:.3f}")
                lines.append(f"G1 X{start_x:.3f} F{feedrate} S0")

                curr_val = scan_slice[0]
                for i, val in enumerate(scan_slice):
                    if val != curr_val:
                        x_pos = origin_x + (first_x_idx + i) * step_mm
                        lines.append(f"G1 X{x_pos:.3f} S{s_max if curr_val == 1 else 0}")
                        curr_val = val

                end_x = origin_x + (last_x_idx + 1) * step_mm
                lines.append(f"G1 X{end_x:.3f} S{s_max if curr_val == 1 else 0}")
                lines.append(f"G1 X{end_x + overscan_mm:.3f} S0")

            else:
                # Skanowanie w lewo
                scan_slice = row[first_x_idx : last_x_idx + 1][::-1]
                start_x = origin_x + (last_x_idx + 1) * step_mm

                # Najazd z dobiegiem z prawej
                lines.append(f"G0 X{start_x + overscan_mm:.3f} Y{cur_y:.3f}")
                lines.append(f"G1 X{start_x:.3f} F{feedrate} S0")

                curr_val = scan_slice[0]
                for i, val in enumerate(scan_slice):
                    if val != curr_val:
                        x_pos = origin_x + (last_x_idx + 1 - i) * step_mm
                        lines.append(f"G1 X{x_pos:.3f} S{s_max if curr_val == 1 else 0}")
                        curr_val = val

                end_x = origin_x + first_x_idx * step_mm
                lines.append(f"G1 X{end_x:.3f} S{s_max if curr_val == 1 else 0}")
                lines.append(f"G1 X{end_x - overscan_mm:.3f} S0")

        lines.extend([
            "M5 S0 ; Wylaczenie lasera",
            "G0 X0 Y0",
            "; --- KONIEC RASTRA ---"
        ])
        return lines