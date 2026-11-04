from __future__ import annotations
from typing import Tuple, List, Optional

import numpy as np
from PIL import Image, ImageOps

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsItem


class RasterImageProcessor:
    """
    Procesor rastrów dla lasera CNC (jedyny moduł rastrowy LaserBurn Studio).

    Konwencja siatki wypalania (binary_grid, uint8): 1 = wypalenie, 0 = brak,
    wiersz 0 = DÓŁ obrazu (oś Y stołu rośnie w górę), kolumna 0 = lewa krawędź.
    Siatka jest wyświetlana bez dodatkowych przekształceń jako QGraphicsPixmapItem
    przeskalowany do mm, a G-code liczony jest z tej samej siatki – podgląd = wypalenie.
    """

    def __init__(self, default_interval_mm: float = 0.1):
        self.interval_mm = default_interval_mm  # 0.1 mm = 254 DPI (krok plamki diody)

    # =========================================================================
    # PRZYGOTOWANIE OBRAZU
    # =========================================================================
    @staticmethod
    def load_grayscale(file_path: str) -> Image.Image:
        """
        Wczytuje obraz jako skalę szarości (L):
        - orientacja z EXIF (zdjęcia z telefonu),
        - przezroczystość nakładana na białe tło (przezroczyste = nie wypalaj).
        """
        img = Image.open(file_path)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, rgba)
        return img.convert("L")

    @staticmethod
    def target_pixel_size(
        img: Image.Image,
        interval_mm: float,
        width_mm: Optional[float] = None,
        fit_within_mm: Optional[Tuple[float, float]] = None
    ) -> Tuple[int, int]:
        """
        Liczba pikseli siatki (kolumny, wiersze) przy kroku interval_mm.
        Bez width_mm obowiązuje 1 piksel obrazu = 1 krok; fit_within_mm zmniejsza raster,
        aby zmieścił się na stole (proporcje zachowane).
        """
        src_w, src_h = img.size
        if width_mm and width_mm > 0:
            w_mm = float(width_mm)
        else:
            w_mm = src_w * interval_mm
        h_mm = w_mm * src_h / src_w

        if fit_within_mm:
            max_w, max_h = fit_within_mm
            factor = min(1.0, max_w / w_mm if w_mm > 0 else 1.0, max_h / h_mm if h_mm > 0 else 1.0)
            w_mm, h_mm = w_mm * factor, h_mm * factor

        cols = max(1, int(round(w_mm / interval_mm)))
        rows = max(1, int(round(h_mm / interval_mm)))
        return cols, rows

    @classmethod
    def image_to_grid(
        cls,
        img: Image.Image,
        size_px: Tuple[int, int],
        gamma: float = 1.0,
        invert: bool = False,
        dither: bool = True,
        threshold: int = 128
    ) -> np.ndarray:
        """
        Skala szarości -> siatka wypalania (wiersz 0 = dół).
        Resampling LANCZOS, korekcja gamma/negatyw przez tablicę LUT, dithering
        Floyda-Steinberga w C (Pillow) lub próg.
        """
        gray = img.convert("L")
        if gray.size != tuple(size_px):
            gray = gray.resize(size_px, Image.Resampling.LANCZOS)

        if gamma != 1.0 or invert:
            g = max(0.05, float(gamma))
            lut = [int(round(255.0 * ((v / 255.0) ** (1.0 / g)))) for v in range(256)]
            if invert:
                lut = [255 - v for v in lut]
            gray = gray.point(lut)

        if dither:
            bw = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
            burn = ~np.asarray(bw, dtype=bool)
        else:
            burn = np.asarray(gray) < threshold

        # Obraz: wiersz 0 = góra; scena/stół: oś Y w górę -> odwrócenie wierszy
        return np.ascontiguousarray(burn[::-1, :], dtype=np.uint8)

    # =========================================================================
    # ELEMENT SCENY
    # =========================================================================
    @staticmethod
    def grid_to_pixmap(binary_grid: np.ndarray) -> QPixmap:
        """Podgląd siatki wypalania: piksel 1 = ciemny, 0 = przezroczysty."""
        grid = np.ascontiguousarray(binary_grid)
        h, w = grid.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[grid == 1] = [20, 20, 20, 240]
        qimg = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg.copy())

    @staticmethod
    def grid_scene_rect(item: QGraphicsPixmapItem) -> QRectF:
        """
        Dokładny prostokąt siatki w scenie (mm). sceneBoundingRect() rastra zawiera
        margines pół piksela, dlatego mapujemy sam obszar pikseli.
        """
        pix = item.pixmap()
        return item.sceneTransform().mapRect(QRectF(0.0, 0.0, pix.width(), pix.height()))

    def load_raster(
        self,
        file_path: str,
        initial_pos: QPointF = QPointF(50.0, 150.0),
        apply_dither: bool = True,
        width_mm: Optional[float] = None,
        fit_within_mm: Optional[Tuple[float, float]] = None,
        gamma: float = 1.0,
        invert: bool = False
    ) -> Tuple[QGraphicsPixmapItem, Image.Image, np.ndarray]:
        """Wczytuje grafikę, przygotowuje siatkę wypalania i element sceny w mm."""
        gray = self.load_grayscale(file_path)
        size_px = self.target_pixel_size(gray, self.interval_mm, width_mm, fit_within_mm)
        binary_grid = self.image_to_grid(gray, size_px, gamma=gamma, invert=invert, dither=apply_dither)

        h, w = binary_grid.shape
        item = QGraphicsPixmapItem(self.grid_to_pixmap(binary_grid))
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        item.setZValue(15)

        scale_val = self.interval_mm
        item.setTransform(QTransform().scale(scale_val, scale_val))
        item.setPos(initial_pos)

        item.setData(0, {
            "type": "raster",
            "grid": binary_grid,
            "interval_mm": scale_val,
            "w_mm": w * scale_val,
            "h_mm": h * scale_val,
            "initial_scale": scale_val
        })

        # Podgląd 1-bit w orientacji obrazu (do ewentualnego eksportu/kontroli)
        preview = Image.fromarray(((1 - binary_grid[::-1, :]) * 255).astype(np.uint8), mode="L").convert("1")
        return item, preview, binary_grid

    # =========================================================================
    # G-CODE
    # =========================================================================
    @staticmethod
    def end_position(lines: List[str]) -> Optional[Tuple[float, float]]:
        """Pozycja głowicy po wykonaniu bloku (ostatnie X i Y z ruchów G0/G1)."""
        x = y = None
        for line in reversed(lines):
            if not line.startswith(("G0", "G1")):
                continue
            for word in line.split(";")[0].split()[1:]:
                if x is None and word[0] == "X":
                    x = float(word[1:])
                elif y is None and word[0] == "Y":
                    y = float(word[1:])
            if x is not None and y is not None:
                return x, y
        return None

    @staticmethod
    def _row_runs(row: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Ciągi wypalania w wierszu: (początki, końce) – końce wyłączne, w indeksach kolumn."""
        padded = np.concatenate(([0], (row != 0).astype(np.int8), [0]))
        edges = np.flatnonzero(np.diff(padded))
        return edges[0::2], edges[1::2]

    @classmethod
    def generate_raster_gcode(
        cls,
        binary_grid: np.ndarray,
        interval_mm: float,
        origin_x: float,
        origin_y: float,
        feedrate: int = 3000,
        s_max: int = 1000,
        overscan_mm: float = 2.5,
        interval_y_mm: Optional[float] = None
    ) -> List[str]:
        """
        Dwukierunkowe skanowanie w trybie GRBL M4 (dynamiczna moc).

        origin_x, origin_y: lewy dolny róg siatki na stole (mm); wiersz 0 = dół.
        interval_mm: krok w osi X; interval_y_mm: krok w osi Y (domyślnie jak X).
        Linia skanu przechodzi przez środek wiersza pikseli. Puste wiersze są pomijane,
        a kierunek zmienia się co wypalany wiersz (najkrótszy przejazd). Przed każdym G0
        wiązka jest wyłączana (M5), M4 S0 włącza tryb dynamiczny przed rozbiegiem.
        """
        step_x = float(interval_mm)
        step_y = float(interval_y_mm) if interval_y_mm else step_x
        grid = np.asarray(binary_grid)
        h_px, _ = grid.shape

        lines: List[str] = ["; --- RASTER M4 (dwukierunkowo) ---"]
        burn_rows = np.flatnonzero(grid.any(axis=1))
        feed_set = False
        forward = True

        for row_idx in burn_rows:
            starts, ends = cls._row_runs(grid[row_idx])
            y = origin_y + (int(row_idx) + 0.5) * step_y
            # Zwykłe float Pythona: formatowanie skalarów numpy jest kilkukrotnie wolniejsze
            xs_start = (origin_x + starts * step_x).tolist()
            xs_end = (origin_x + ends * step_x).tolist()
            feed = "" if feed_set else f" F{feedrate}"
            feed_set = True

            if forward:
                lead_in = xs_start[0] - overscan_mm
                lines.append("M5")
                lines.append(f"G0 X{lead_in:.3f} Y{y:.3f}")
                lines.append("M4 S0")
                lines.append(f"G1 X{xs_start[0]:.3f}{feed} S0")
                for i in range(len(starts)):
                    if i:
                        lines.append(f"G1 X{xs_start[i]:.3f} S0")
                    lines.append(f"G1 X{xs_end[i]:.3f} S{s_max}")
                lines.append(f"G1 X{xs_end[-1] + overscan_mm:.3f} S0")
            else:
                lead_in = xs_end[-1] + overscan_mm
                lines.append("M5")
                lines.append(f"G0 X{lead_in:.3f} Y{y:.3f}")
                lines.append("M4 S0")
                lines.append(f"G1 X{xs_end[-1]:.3f}{feed} S0")
                for i in range(len(starts) - 1, -1, -1):
                    if i != len(starts) - 1:
                        lines.append(f"G1 X{xs_end[i]:.3f} S0")
                    lines.append(f"G1 X{xs_start[i]:.3f} S{s_max}")
                lines.append(f"G1 X{xs_start[0] - overscan_mm:.3f} S0")
            forward = not forward

        lines.append("M5")
        lines.append("; --- KONIEC RASTRA ---")
        return lines
