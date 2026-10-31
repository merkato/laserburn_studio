from __future__ import annotations
from typing import Tuple, List, Optional
from PIL import Image
import numpy as np

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsItem


class RasterImageProcessor:
    """
    Dedykowany procesor rastrów dla lasera CNC:
    - Odczyt i skalowanie DPI/milimetry.
    - Algorytmy ditheringu (Floyd-Steinberg / 1-bit Threshold).
    - Generowanie podglądu CAD oraz dwukierunkowego G-code pod GRBL M4.
    """

    def __init__(self, default_interval_mm: float = 0.1):
        self.interval_mm = default_interval_mm  # 0.1 mm = 254 DPI (krok plamki diody)

    @staticmethod
    def apply_floyd_steinberg_dither(image: Image.Image) -> Image.Image:
        """Konwertuje zdjęcie do 1-bitowej bitmapy za pomocą ditheringu Floyda-Steinberga."""
        gray = image.convert("L")
        return gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)

    def load_raster(
        self,
        file_path: str,
        initial_pos: QPointF = QPointF(50.0, 150.0),
        apply_dither: bool = True,
        flip_vertical: bool = True  # Odbicie pod układ sceny CNC
    ) -> Tuple[QGraphicsPixmapItem, Image.Image, np.ndarray]:
        """
        Ładuje grafikę bitmapową, odwraca orientację pionową pod stół CNC,
        wykonuje dithering i przygotowuje element sceny.
        """
        raw_pil = Image.open(file_path)
        if flip_vertical:
            raw_pil = raw_pil.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

        if apply_dither:
            dithered_pil = self.apply_floyd_steinberg_dither(raw_pil)
        else:
            dithered_pil = raw_pil.convert("1")

        # Tablica binarna: 1 = wypalenie, 0 = brak
        binary_grid = (np.array(dithered_pil) == False).astype(np.uint8)

        # Generowanie QImage z przezroczystością
        h, w = binary_grid.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        burn_mask = (binary_grid == 1)
        rgba[burn_mask] = [20, 20, 20, 240]
        rgba[~burn_mask] = [0, 0, 0, 0]

        qimg = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qimg.copy())

        item = QGraphicsPixmapItem(pixmap)
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        item.setZValue(15)

        # Skala fizyczna (mm na piksel)
        scale_val = self.interval_mm
        t = QTransform()
        t.scale(scale_val, scale_val)
        item.setTransform(t)
        item.setPos(initial_pos)

        item.setData(0, {
            "type": "raster",
            "grid": binary_grid,
            "interval_mm": self.interval_mm,
            "w_mm": w * scale_val,
            "h_mm": h * scale_val,
            "initial_scale": scale_val
        })

        return item, dithered_pil, binary_grid

    @staticmethod
    def generate_raster_gcode(
        binary_grid: np.ndarray,
        interval_mm: float,
        origin_x: float,
        origin_y: float,
        feedrate: int = 3000,
        s_max: int = 1000,
        overscan_mm: float = 2.5
    ) -> List[str]:
        """
        Generuje dwukierunkowy G-code w trybie GRBL M4 (Dynamic Laser Power).
        origin_x, origin_y to współrzędne lewego dolnego rogu grafiki na stole roboczym.
        """
        h_px, w_px = binary_grid.shape
        gcode = [
            "; --- START RASTER ENGRAVE ---",
            "G21 ; mm",
            "G90 ; Pozycjonowanie bezwzgledne",
            "M4 ; Dynamic Laser Power",
            f"G0 F{feedrate * 2}"
        ]

        for row_idx in range(h_px):
            # Przeliczenie osi Y: wiersz 0 to góra grafiki
            cur_y = origin_y + (h_px - 1 - row_idx) * interval_mm
            row = binary_grid[row_idx, :]

            if not np.any(row):
                continue

            indices = np.where(row == 1)[0]
            first_x_idx = int(indices[0])
            last_x_idx = int(indices[-1])

            traverse_right = (row_idx % 2 == 0)

            if traverse_right:
                scan_slice = row[first_x_idx : last_x_idx + 1]
                start_x = origin_x + first_x_idx * interval_mm

                # Dobieg poza materiał (rampa rozpędzania)
                gcode.append(f"G0 X{start_x - overscan_mm:.3f} Y{cur_y:.3f}")
                gcode.append(f"G1 X{start_x:.3f} F{feedrate} S0")

                curr_val = scan_slice[0]
                for i, val in enumerate(scan_slice):
                    if val != curr_val:
                        x_pos = origin_x + (first_x_idx + i) * interval_mm
                        gcode.append(f"G1 X{x_pos:.3f} S{s_max if curr_val == 1 else 0}")
                        curr_val = val

                end_x = origin_x + (last_x_idx + 1) * interval_mm
                gcode.append(f"G1 X{end_x:.3f} S{s_max if curr_val == 1 else 0}")
                gcode.append(f"G1 X{end_x + overscan_mm:.3f} S0")

            else:
                scan_slice = row[first_x_idx : last_x_idx + 1][::-1]
                start_x = origin_x + (last_x_idx + 1) * interval_mm

                gcode.append(f"G0 X{start_x + overscan_mm:.3f} Y{cur_y:.3f}")
                gcode.append(f"G1 X{start_x:.3f} F{feedrate} S0")

                curr_val = scan_slice[0]
                for i, val in enumerate(scan_slice):
                    if val != curr_val:
                        x_pos = origin_x + (last_x_idx + 1 - i) * interval_mm
                        gcode.append(f"G1 X{x_pos:.3f} S{s_max if curr_val == 1 else 0}")
                        curr_val = val

                end_x = origin_x + first_x_idx * interval_mm
                gcode.append(f"G1 X{end_x:.3f} S{s_max if curr_val == 1 else 0}")
                gcode.append(f"G1 X{end_x - overscan_mm:.3f} S0")

        gcode.extend([
            "M5 S0 ; Laser OFF",
            "G0 X0 Y0",
            "; --- KONIEC RASTRA ---"
        ])
        return gcode