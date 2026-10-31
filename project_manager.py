from __future__ import annotations
import json
import base64
from io import BytesIO
from typing import List, Dict, Any, TYPE_CHECKING

from PySide6.QtCore import QPointF, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QPen, QPainterPath, QTransform, QPixmap, QImage
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem

if TYPE_CHECKING:
    from laserburn_studio import LayerConfig, LaserCAMStudio


def path_to_svg_d(path: QPainterPath) -> str:
    """Konwertuje QPainterPath na ciąg komend SVG Path 'd'."""
    d_parts = []
    elem_count = path.elementCount()
    i = 0
    while i < elem_count:
        el = path.elementAt(i)
        t = el.type
        # 0: MoveTo, 1: LineTo, 2: CurveToDataElement (Cubic Bezier), 3: CurveTo
        if t == QPainterPath.ElementType.MoveToElement:
            d_parts.append(f"M {el.x:.4f} {el.y:.4f}")
            i += 1
        elif t == QPainterPath.ElementType.LineToElement:
            d_parts.append(f"L {el.x:.4f} {el.y:.4f}")
            i += 1
        elif t == QPainterPath.ElementType.CurveToElement:
            p1 = el
            p2 = path.elementAt(i + 1)
            p3 = path.elementAt(i + 2)
            d_parts.append(f"C {p1.x:.4f} {p1.y:.4f}, {p2.x:.4f} {p2.y:.4f}, {p3.x:.4f} {p3.y:.4f}")
            i += 3
        else:
            i += 1
    return " ".join(d_parts)


def svg_d_to_path(d_str: str) -> QPainterPath:
    """Odtwarza QPainterPath z uproszczonego ciągu komend SVG Path."""
    path = QPainterPath()
    tokens = d_str.strip().split()
    if not tokens:
        return path

    idx = 0
    while idx < len(tokens):
        cmd = tokens[idx]
        if cmd == "M":
            path.moveTo(float(tokens[idx + 1]), float(tokens[idx + 2]))
            idx += 3
        elif cmd == "L":
            path.lineTo(float(tokens[idx + 1]), float(tokens[idx + 2]))
            idx += 3
        elif cmd == "C":
            # C x1 y1, x2 y2, x3 y3 (usuwamy ewentualne przecinki)
            c1_x = float(tokens[idx + 1].replace(",", ""))
            c1_y = float(tokens[idx + 2].replace(",", ""))
            c2_x = float(tokens[idx + 3].replace(",", ""))
            c2_y = float(tokens[idx + 4].replace(",", ""))
            end_x = float(tokens[idx + 5].replace(",", ""))
            end_y = float(tokens[idx + 6].replace(",", ""))
            path.cubicTo(c1_x, c1_y, c2_x, c2_y, end_x, end_y)
            idx += 7
        elif cmd == "Z":
            path.closeSubpath()
            idx += 1
        else:
            idx += 1
    return path


class ProjectSerializer:
    """Odpowiada za zapis i odczyt formatu *.lcam (LaserCAM Project)."""

    @staticmethod
    def serialize(studio: LaserCAMStudio) -> Dict[str, Any]:
        # 1. Warstwy technologiczne
        layers_data = []
        for l in studio.layers:
            layers_data.append({
                "name": l.name,
                "color": l.color.name(),
                "mode": l.mode,
                "speed": l.speed,
                "power": l.power,
                "passes": l.passes,
                "kerf": l.kerf,
                "output": l.output
            })

        # 2. Obiekty graficzne (wektory i rastry)
        items_data = []
        for it in studio.scene.items():
            if it.zValue() < 10 or not it.isVisible():
                continue

            trans = it.transform()
            trans_matrix = [
                trans.m11(), trans.m12(), trans.m13(),
                trans.m21(), trans.m22(), trans.m23(),
                trans.m31(), trans.m32(), trans.m33()
            ]

            if isinstance(it, QGraphicsPathItem):
                d_str = path_to_svg_d(it.path())
                items_data.append({
                    "type": "vector",
                    "svg_d": d_str,
                    "color": it.pen().color().name(),
                    "pos_x": it.pos().x(),
                    "pos_y": it.pos().y(),
                    "transform": trans_matrix
                })

            elif isinstance(it, QGraphicsPixmapItem):
                pix = it.pixmap()
                byte_array = QByteArray()
                buffer = QBuffer(byte_array)
                buffer.open(QIODevice.WriteOnly)
                pix.save(buffer, "PNG")
                b64_img = base64.b64encode(byte_array.data()).decode("utf-8")

                items_data.append({
                    "type": "raster",
                    "image_b64": b64_img,
                    "pos_x": it.pos().x(),
                    "pos_y": it.pos().y(),
                    "transform": trans_matrix
                })

        return {
            "format": "LaserCAM_Project",
            "version": "1.0",
            "bed": {
                "width": studio.bed_w,
                "height": studio.bed_h
            },
            "layers": layers_data,
            "items": items_data
        }

    @staticmethod
    def deserialize(studio: LaserCAMStudio, data: Dict[str, Any]):
        from laserburn_studio import LayerConfig

        # 1. Czyszczenie aktualnej sceny (z pominięciem celownika, siatki i linijek)
        items_to_remove = [
            it for it in studio.scene.items()
            if it not in (studio.transform_box, studio.laser_reticle, studio.crosshair, studio.guides)
            and it.zValue() >= 10
        ]
        for it in items_to_remove:
            studio.scene.removeItem(it)

        if studio.boundary_item:
            studio._clear_contour()

        # 2. Odtwarzanie warstw
        studio.layers.clear()
        for ld in data.get("layers", []):
            layer = LayerConfig(
                name=ld["name"],
                color=QColor(ld["color"]),
                mode=ld.get("mode", "Line"),
                speed=float(ld.get("speed", 1000.0)),
                power=float(ld.get("power", 50.0)),
                passes=int(ld.get("passes", 1)),
                kerf=float(ld.get("kerf", 0.0))
            )
            layer.output = bool(ld.get("output", True))
            studio.layers.append(layer)

        studio._populate_layer_table()

        # 3. Odtwarzanie obiektów graficznych
        for idata in data.get("items", []):
            itype = idata.get("type")
            m = idata.get("transform", [1, 0, 0, 0, 1, 0, 0, 0, 1])
            trans = QTransform(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8])
            pos = QPointF(idata.get("pos_x", 0.0), idata.get("pos_y", 0.0))

            if itype == "vector":
                path = svg_d_to_path(idata["svg_d"])
                col = QColor(idata["color"])
                pen = QPen(col, 1.0)
                pen.setCosmetic(True)

                path_item = QGraphicsPathItem(path)
                path_item.setPen(pen)
                path_item.setPos(pos)
                path_item.setTransform(trans)
                path_item.setZValue(15)
                path_item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                studio.scene.addItem(path_item)

            elif itype == "raster":
                raw_bytes = base64.b64decode(idata["image_b64"])
                qimg = QImage.fromData(raw_bytes, "PNG")
                pix = QPixmap.fromImage(qimg)

                pix_item = QGraphicsPixmapItem(pix)
                pix_item.setPos(pos)
                pix_item.setTransform(trans)
                pix_item.setZValue(20)
                pix_item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                studio.scene.addItem(pix_item)