from __future__ import annotations
import base64
import io
import json
import math
import os
import tempfile
from typing import List, Dict, Any, Optional, TYPE_CHECKING

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QPointF, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QPen, QBrush, QPainterPath, QTransform, QPixmap, QImage
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem

if TYPE_CHECKING:
    from laserburn_studio import LaserBurnStudio


PROJECT_FORMAT = "LaserBurn_Project"
LEGACY_FORMATS = ("LaserCAM_Project",)
PROJECT_VERSION = 2

_ITEM_FLAGS = (
    QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
)

# Klucze item.data(0), które mają sens po zapisie (proste wartości JSON)
_PERSISTENT_DATA_KEYS = ("type", "power", "speed", "passes", "name")


class ProjectFormatError(ValueError):
    """Plik nie jest projektem LaserBurn albo ma nieobsługiwaną wersję."""


# ==============================================================================
# ŚCIEŻKI: dokładny zapis elementów QPainterPath (+ odczyt starego formatu "svg_d")
# ==============================================================================
def path_to_elements(path: QPainterPath) -> List[List[float]]:
    """QPainterPath -> [[typ, x, y], ...] – bezstratny zapis (typy elementów Qt 0..3)."""
    out = []
    for i in range(path.elementCount()):
        el = path.elementAt(i)
        out.append([int(el.type.value if hasattr(el.type, "value") else el.type), round(el.x, 6), round(el.y, 6)])
    return out


def elements_to_path(elements: List[List[float]]) -> QPainterPath:
    """Odtwarza QPainterPath z listy [[typ, x, y], ...]."""
    path = QPainterPath()
    i = 0
    n = len(elements)
    while i < n:
        t, x, y = int(elements[i][0]), float(elements[i][1]), float(elements[i][2])
        if t == 0:
            path.moveTo(x, y)
            i += 1
        elif t == 1:
            path.lineTo(x, y)
            i += 1
        elif t == 2 and i + 2 < n:
            c2 = elements[i + 1]
            end = elements[i + 2]
            path.cubicTo(x, y, float(c2[1]), float(c2[2]), float(end[1]), float(end[2]))
            i += 3
        else:
            i += 1  # Osierocony element danych krzywej – pomijamy
    return path


def path_to_svg_d(path: QPainterPath) -> str:
    """Konwertuje QPainterPath na ciąg komend SVG Path 'd' (np. do eksportu)."""
    d_parts = []
    elem_count = path.elementCount()
    i = 0
    while i < elem_count:
        el = path.elementAt(i)
        t = el.type
        if t == QPainterPath.ElementType.MoveToElement:
            d_parts.append(f"M {el.x:.4f} {el.y:.4f}")
            i += 1
        elif t == QPainterPath.ElementType.LineToElement:
            d_parts.append(f"L {el.x:.4f} {el.y:.4f}")
            i += 1
        elif t == QPainterPath.ElementType.CurveToElement and i + 2 < elem_count:
            p2 = path.elementAt(i + 1)
            p3 = path.elementAt(i + 2)
            d_parts.append(f"C {el.x:.4f} {el.y:.4f} {p2.x:.4f} {p2.y:.4f} {p3.x:.4f} {p3.y:.4f}")
            i += 3
        else:
            i += 1
    return " ".join(d_parts)


def svg_d_to_path(d_str: str) -> QPainterPath:
    """Odtwarza QPainterPath z uproszczonego ciągu 'd' (format projektów v1)."""
    path = QPainterPath()
    tokens = d_str.replace(",", " ").split()
    idx = 0
    try:
        while idx < len(tokens):
            cmd = tokens[idx]
            if cmd == "M":
                path.moveTo(float(tokens[idx + 1]), float(tokens[idx + 2]))
                idx += 3
            elif cmd == "L":
                path.lineTo(float(tokens[idx + 1]), float(tokens[idx + 2]))
                idx += 3
            elif cmd == "C":
                v = [float(t) for t in tokens[idx + 1: idx + 7]]
                path.cubicTo(v[0], v[1], v[2], v[3], v[4], v[5])
                idx += 7
            elif cmd in ("Z", "z"):
                path.closeSubpath()
                idx += 1
            else:
                idx += 1
    except (IndexError, ValueError):
        pass  # Uszkodzony ogon ścieżki – zachowujemy to, co udało się odczytać
    return path


# ==============================================================================
# RASTRY
# ==============================================================================
def _png_b64_from_qpixmap(pix: QPixmap) -> str:
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.WriteOnly)
    pix.save(buffer, "PNG")
    return base64.b64encode(byte_array.data()).decode("ascii")


def _grid_to_png_b64(grid: np.ndarray) -> str:
    img = Image.fromarray((np.asarray(grid) > 0).astype(np.uint8) * 255, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _grid_from_png_b64(b64: str) -> np.ndarray:
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("L")
    return (np.array(img) > 127).astype(np.uint8)


def _grid_from_legacy_pixmap_b64(b64: str) -> Optional[np.ndarray]:
    """Projekty v1 zapisywały tylko podgląd: piksel do wypalenia = nieprzezroczysty."""
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
    except Exception:
        return None
    alpha = np.array(img)[:, :, 3]
    return (alpha > 0).astype(np.uint8)


# ==============================================================================
# POMOCNICZE
# ==============================================================================
def _transform_to_list(t: QTransform) -> List[float]:
    return [t.m11(), t.m12(), t.m13(), t.m21(), t.m22(), t.m23(), t.m31(), t.m32(), t.m33()]


def _transform_from_list(m) -> QTransform:
    try:
        vals = [float(v) for v in m]
        if len(vals) == 9 and all(math.isfinite(v) for v in vals):
            return QTransform(*vals)
    except (TypeError, ValueError):
        pass
    return QTransform()


def _num(value, default: float) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _color(value, default: str = "#000000") -> QColor:
    c = QColor(str(value)) if value is not None else QColor()
    return c if c.isValid() else QColor(default)


def _persistent_data(item: QGraphicsItem) -> Dict[str, Any]:
    data = item.data(0)
    if not isinstance(data, dict):
        return {}
    out = {}
    for key in _PERSISTENT_DATA_KEYS:
        val = data.get(key)
        if isinstance(val, (str, int, float, bool)) and not (isinstance(val, float) and not math.isfinite(val)):
            out[key] = val
    return out


class ProjectSerializer:
    """
    Zapis i odczyt projektów *.lcam (JSON, UTF-8).

    Wersja 2: bezstratne ścieżki, pełny stan obiektów (pozycja, transformacja, Z, kolory obrysu
    i wypełnienia, dane technologiczne), rastry wraz z siatką wypalania, obrys materiału.
    Odczytuje również projekty wersji 1 (format "LaserCAM_Project").
    """

    # -------------------------------------------------------------------------
    # PLIKI
    # -------------------------------------------------------------------------
    @classmethod
    def save_to_file(cls, studio: "LaserBurnStudio", file_path: str) -> None:
        """Zapis atomowy: plik tymczasowy + podmiana, aby awaria nie uszkodziła projektu."""
        data = cls.serialize(studio)
        text = json.dumps(data, indent=1, ensure_ascii=False, allow_nan=False)
        directory = os.path.dirname(os.path.abspath(file_path)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".lcam_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, file_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load_from_file(cls, studio: "LaserBurnStudio", file_path: str) -> List[str]:
        """Wczytuje projekt; zwraca listę ostrzeżeń (pominięte/uszkodzone elementy)."""
        with open(file_path, "r", encoding="utf-8-sig") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ProjectFormatError(f"Uszkodzony plik JSON: {e}") from e
        cls.validate(data)
        return cls.deserialize(studio, data)

    @staticmethod
    def validate(data: Any) -> None:
        if not isinstance(data, dict):
            raise ProjectFormatError("Plik nie zawiera obiektu projektu.")
        fmt = data.get("format")
        if fmt != PROJECT_FORMAT and fmt not in LEGACY_FORMATS:
            raise ProjectFormatError("Wybrany plik nie jest projektem LaserBurn Studio.")
        version = int(_num(str(data.get("version", "1")).split(".")[0], 1))
        if version > PROJECT_VERSION:
            raise ProjectFormatError(
                f"Projekt zapisano nowszą wersją programu (format v{version}, obsługiwany v{PROJECT_VERSION})."
            )

    # -------------------------------------------------------------------------
    # ZAPIS
    # -------------------------------------------------------------------------
    @staticmethod
    def _helper_items(studio: "LaserBurnStudio") -> set:
        names = ("transform_box", "laser_reticle", "crosshair", "guides", "boundary_item", "centroid_item")
        helpers = {getattr(studio, n, None) for n in names}
        helpers.update(getattr(studio, "boundary_markers", []) or [])
        helpers.discard(None)
        return helpers

    @classmethod
    def serialize(cls, studio: "LaserBurnStudio") -> Dict[str, Any]:
        layers_data = []
        for l in studio.layers:
            layers_data.append({
                "name": l.name,
                "color": QColor(l.color).name(),
                "mode": l.mode,
                "speed": float(l.speed),
                "power": float(l.power),
                "passes": int(l.passes),
                "kerf": float(l.kerf),
                "hatch_interval": float(getattr(l, "hatch_interval", 0.1)),
                "hatch_angle": float(getattr(l, "hatch_angle", 0.0)),
                "output": bool(l.output),
                "visible": bool(getattr(l, "visible", True)),
            })

        helpers = cls._helper_items(studio)
        items_data = []
        # Od najniższego Z, aby kolejność rysowania odtworzyła się także przy równych Z
        # Kolejność rysowania Qt (rosnąco): Z, a przy równym Z kolejność dodania – deterministyczna
        for it in studio.scene.items(Qt.SortOrder.AscendingOrder):
            if it in helpers or it.zValue() < 10 or it.parentItem() is not None:
                continue

            common = {
                "z": it.zValue(),
                "pos": [it.pos().x(), it.pos().y()],
                "rotation": it.rotation(),
                "scale": it.scale(),
                "transform": _transform_to_list(it.transform()),
                "visible": it.isVisible(),
                "data": _persistent_data(it),
            }

            if isinstance(it, QGraphicsPathItem):
                pen = it.pen()
                brush = it.brush()
                entry = {
                    "type": "vector",
                    "path": path_to_elements(it.path()),
                    "fill_rule": "winding" if it.path().fillRule() == Qt.FillRule.WindingFill else "oddeven",
                    "color": pen.color().name(),
                    "pen_width": pen.widthF(),
                    "pen_style": int(pen.style().value if hasattr(pen.style(), "value") else pen.style()),
                    "pen_cosmetic": pen.isCosmetic(),
                }
                if brush.style() != Qt.BrushStyle.NoBrush:
                    entry["brush"] = brush.color().name(QColor.NameFormat.HexArgb)
                if common["data"].get("type") == "guide":
                    entry["role"] = "guide"
                entry.update(common)
                items_data.append(entry)

            elif isinstance(it, QGraphicsPixmapItem):
                data = it.data(0) if isinstance(it.data(0), dict) else {}
                entry = {
                    "type": "raster",
                    "image_b64": _png_b64_from_qpixmap(it.pixmap()),
                }
                grid = data.get("grid")
                if grid is not None:
                    entry["grid_b64"] = _grid_to_png_b64(grid)
                for key in ("interval_mm", "w_mm", "h_mm", "initial_scale"):
                    if key in data:
                        entry[key] = float(data[key])
                entry.update(common)
                entry["data"]["type"] = "raster"
                items_data.append(entry)

        return {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "bed": {"width": float(studio.bed_w), "height": float(studio.bed_h)},
            "layers": layers_data,
            "material_boundary": [[float(p[0]), float(p[1])] for p in getattr(studio, "boundary_points", [])],
            "items": items_data,
        }

    # -------------------------------------------------------------------------
    # ODCZYT
    # -------------------------------------------------------------------------
    @classmethod
    def deserialize(cls, studio: "LaserBurnStudio", data: Dict[str, Any]) -> List[str]:
        from laserburn_studio import LayerConfig
        warnings: List[str] = []

        # 1. Czyszczenie sceny: najpierw obrys materiału, potem obiekty użytkownika
        if hasattr(studio, "_clear_boundary"):
            studio._clear_boundary()
        helpers = cls._helper_items(studio)
        for it in list(studio.scene.items()):
            if it in helpers or it.zValue() < 10 or it.parentItem() is not None:
                continue
            studio.scene.removeItem(it)
        studio.scene.clearSelection()

        bed = data.get("bed") or {}
        bw, bh = _num(bed.get("width"), studio.bed_w), _num(bed.get("height"), studio.bed_h)
        if abs(bw - studio.bed_w) > 0.01 or abs(bh - studio.bed_h) > 0.01:
            warnings.append(f"Projekt zapisano dla stołu {bw:.0f}x{bh:.0f} mm (obecny {studio.bed_w:.0f}x{studio.bed_h:.0f} mm).")

        # 2. Warstwy
        new_layers = []
        for idx, ld in enumerate(data.get("layers") or []):
            try:
                layer = LayerConfig(
                    name=str(ld.get("name", f"Warstwa {idx + 1}")),
                    color=_color(ld.get("color")),
                    mode=ld.get("mode", "Line") if ld.get("mode") in ("Line", "Fill", "Image") else "Line",
                    speed=_num(ld.get("speed"), 1000.0),
                    power=_num(ld.get("power"), 50.0),
                    passes=max(1, int(_num(ld.get("passes"), 1))),
                    kerf=_num(ld.get("kerf"), 0.0),
                    hatch_interval=max(0.01, _num(ld.get("hatch_interval"), 0.1)),
                    hatch_angle=_num(ld.get("hatch_angle"), 0.0),
                )
                layer.output = bool(ld.get("output", True))
                layer.visible = bool(ld.get("visible", True))
                new_layers.append(layer)
            except Exception as e:
                warnings.append(f"Warstwa #{idx + 1} pominięta: {e}")
        if new_layers:
            studio.layers.clear()
            studio.layers.extend(new_layers)
        else:
            warnings.append("Projekt nie zawiera warstw – zachowano bieżące.")
        if hasattr(studio, "_populate_layer_table"):
            studio._populate_layer_table()

        # 3. Obiekty
        loaded = 0
        for idx, idata in enumerate(data.get("items") or []):
            try:
                item = cls._build_item(idata)
            except Exception as e:
                item = None
                warnings.append(f"Obiekt #{idx + 1} ({idata.get('type', '?')}) uszkodzony: {e}")
            if item is None:
                continue
            studio.scene.addItem(item)
            loaded += 1

        # 4. Obrys materiału
        boundary = data.get("material_boundary") or []
        pts = []
        for p in boundary:
            try:
                pts.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                pass
        if len(pts) >= 3 and hasattr(studio, "_generate_contour"):
            studio.boundary_points[:] = pts
            studio._generate_contour()

        if hasattr(studio, "transform_box"):
            studio.transform_box.set_targets([])
        return warnings

    @classmethod
    def _apply_common(cls, item: QGraphicsItem, idata: Dict[str, Any], default_z: float) -> None:
        if "pos" in idata:
            pos = idata.get("pos") or [0.0, 0.0]
            item.setPos(QPointF(_num(pos[0], 0.0), _num(pos[1], 0.0)))
        else:  # v1
            item.setPos(QPointF(_num(idata.get("pos_x"), 0.0), _num(idata.get("pos_y"), 0.0)))
        item.setTransform(_transform_from_list(idata.get("transform", [1, 0, 0, 0, 1, 0, 0, 0, 1])))
        item.setRotation(_num(idata.get("rotation"), 0.0))
        item.setScale(_num(idata.get("scale"), 1.0) or 1.0)
        item.setZValue(max(10.0, _num(idata.get("z"), default_z)))
        item.setVisible(bool(idata.get("visible", True)))
        item.setFlags(_ITEM_FLAGS)

    @classmethod
    def _build_item(cls, idata: Dict[str, Any]) -> Optional[QGraphicsItem]:
        itype = idata.get("type")
        data = dict(idata.get("data") or {})

        if itype == "vector":
            if "path" in idata:
                path = elements_to_path(idata["path"])
            else:
                path = svg_d_to_path(idata.get("svg_d", ""))
            if idata.get("fill_rule") == "winding":
                path.setFillRule(Qt.FillRule.WindingFill)
            if path.isEmpty():
                return None

            item = QGraphicsPathItem(path)
            pen = QPen(_color(idata.get("color")), _num(idata.get("pen_width"), 1.2))
            if "pen_style" in idata:
                try:
                    pen.setStyle(Qt.PenStyle(int(idata["pen_style"])))
                except (ValueError, TypeError):
                    pass
            pen.setCosmetic(bool(idata.get("pen_cosmetic", True)))
            item.setPen(pen)
            if idata.get("brush"):
                item.setBrush(QBrush(_color(idata["brush"])))
            if idata.get("role") == "guide":
                data["type"] = "guide"
            if data:
                item.setData(0, data)
            cls._apply_common(item, idata, default_z=20.0)
            return item

        if itype == "raster":
            from raster_engine import RasterImageProcessor
            grid = None
            if idata.get("grid_b64"):
                grid = _grid_from_png_b64(idata["grid_b64"])
            elif idata.get("image_b64"):
                grid = _grid_from_legacy_pixmap_b64(idata["image_b64"])
            if grid is None or grid.size == 0:
                return None

            item = QGraphicsPixmapItem(RasterImageProcessor.grid_to_pixmap(grid))
            cls._apply_common(item, idata, default_z=15.0)

            h, w = grid.shape
            scale_val = _num(idata.get("initial_scale"), abs(item.transform().m11()) or 0.1)
            interval = _num(idata.get("interval_mm"), scale_val)
            data.update({
                "type": "raster",
                "grid": grid,
                "interval_mm": interval,
                "w_mm": _num(idata.get("w_mm"), w * scale_val),
                "h_mm": _num(idata.get("h_mm"), h * scale_val),
                "initial_scale": scale_val,
            })
            item.setData(0, data)
            return item

        return None
