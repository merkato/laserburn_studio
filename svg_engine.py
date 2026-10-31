from __future__ import annotations
import math
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QPainterPath, QTransform, QFont, QFontMetricsF, 
    QPen, QColor
)
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsItem
import svgelements


class SvgDocumentImporter:
    """
    Dedykowany silnik wektorowy SVG dla LaserBurn Studio.
    Odpowiada za import, konwersję czcionek do ścieżek oraz skalowanie DPI -> mm.
    """

    def __init__(self, default_pen: Optional[QPen] = None):
        if default_pen:
            self.pen = default_pen
        else:
            self.pen = QPen(QColor(255, 0, 0), 1.0)
            self.pen.setCosmetic(True)

    @staticmethod
    def parse_transform(transform_str: str) -> QTransform:
        """Parsuje macierze i przekształcenia afiniczne SVG."""
        t = QTransform()
        if not transform_str:
            return t

        operations = re.findall(r'([a-zA-Z]+)\s*\(([^)]+)\)', transform_str)
        for op, params_str in operations:
            params = [float(p) for p in re.split(r'[\s,]+', params_str.strip()) if p]
            if op == 'matrix' and len(params) == 6:
                a, b, c, d, e, f = params
                t = QTransform(a, b, c, d, e, f) * t
            elif op == 'translate':
                dx = params[0]
                dy = params[1] if len(params) > 1 else 0.0
                t.translate(dx, dy)
            elif op == 'scale':
                sx = params[0]
                sy = params[1] if len(params) > 1 else sx
                t.scale(sx, sy)
            elif op == 'rotate':
                angle = params[0]
                if len(params) == 3:
                    cx, cy = params[1], params[2]
                    t.translate(cx, cy)
                    t.rotate(angle)
                    t.translate(-cx, -cy)
                else:
                    t.rotate(angle)
        return t

    @staticmethod
    def extract_font_props(element: ET.Element, parent_props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Wyciąga parametry tekstu z atrybutów XML oraz arkuszy stylów CSS."""
        props = parent_props.copy() if parent_props else {
            'family': 'Arial', 'size': 24.0, 'italic': False, 
            'bold': False, 'anchor': 'start', 'color': None
        }

        style = element.attrib.get('style', '')

        if 'font-family' in element.attrib:
            props['family'] = element.attrib['font-family']
        elif 'font-family:' in style:
            m = re.search(r'font-family:\s*([^;]+)', style)
            if m:
                props['family'] = m.group(1).replace("'", "").replace('"', '').strip()

        if 'font-size' in element.attrib:
            m = re.search(r'[-+]?\d*\.?\d+', element.attrib['font-size'])
            if m:
                props['size'] = float(m.group(0))
        elif 'font-size:' in style:
            m = re.search(r'font-size:\s*([\d\.]+)px?', style)
            if m:
                props['size'] = float(m.group(1))

        if element.attrib.get('font-style') in ('italic', 'oblique') or 'font-style:italic' in style:
            props['italic'] = True
        if element.attrib.get('font-weight') == 'bold' or 'font-weight:bold' in style:
            props['bold'] = True

        if 'text-anchor' in element.attrib:
            props['anchor'] = element.attrib['text-anchor']
        elif 'text-anchor:' in style:
            m = re.search(r'text-anchor:\s*([^;]+)', style)
            if m:
                props['anchor'] = m.group(1).strip()

        if 'fill' in element.attrib and element.attrib['fill'] not in ('none', ''):
            props['color'] = element.attrib['fill']
        elif 'fill:' in style:
            m = re.search(r'fill:\s*([^;]+)', style)
            if m and m.group(1) != 'none':
                props['color'] = m.group(1).strip()

        return props

    def _convert_text_to_path(self, text: str, x: float, y: float, font_props: Dict[str, Any], transform: QTransform) -> QPainterPath:
        """Renderuje glify czcionki bezpośrednio do obiektów wektorowych QPainterPath."""
        qfont = QFont(font_props['family'])
        qfont.setPointSizeF(font_props['size'] * 0.75)
        qfont.setItalic(font_props['italic'])
        qfont.setBold(font_props['bold'])

        fm = QFontMetricsF(qfont)
        text_w = fm.horizontalAdvance(text)

        draw_x = x
        if font_props['anchor'] == 'middle':
            draw_x = x - (text_w / 2.0)
        elif font_props['anchor'] == 'end':
            draw_x = x - text_w

        line_path = QPainterPath()
        line_path.addText(QPointF(draw_x, y), qfont, text)
        return transform.map(line_path)

    def load_svg(self, file_path: str, default_pos: QPointF = QPointF(10.0, 10.0)) -> List[QGraphicsPathItem]:
        """Główna metoda parsująca plik SVG z konwersją współrzędnych do układu maszyny CNC."""
        items: List[QGraphicsPathItem] = []

        # 1. Parsowanie elementów geometrycznych przez svgelements
        try:
            svg = svgelements.SVG.parse(file_path)
            for elem in svg.elements():
                if isinstance(elem, (svgelements.SVG, svgelements.Group)):
                    continue
                try:
                    geom_path = svgelements.Path(elem)
                except Exception:
                    continue

                qpath = QPainterPath()
                for seg in geom_path:
                    if isinstance(seg, svgelements.Move):
                        qpath.moveTo(seg.end.x, seg.end.y)
                    elif isinstance(seg, svgelements.Line):
                        qpath.lineTo(seg.end.x, seg.end.y)
                    elif isinstance(seg, svgelements.Close):
                        qpath.closeSubpath()
                    elif isinstance(seg, svgelements.CubicBezier):
                        qpath.cubicTo(seg.control1.x, seg.control1.y, seg.control2.x, seg.control2.y, seg.end.x, seg.end.y)
                    elif isinstance(seg, svgelements.QuadraticBezier):
                        qpath.quadTo(seg.control.x, seg.control.y, seg.end.x, seg.end.y)
                    elif isinstance(seg, svgelements.Arc):
                        for pt in seg.as_points():
                            qpath.lineTo(pt.x, pt.y)

                if not qpath.isEmpty():
                    color_hex = None
                    if isinstance(getattr(elem, 'stroke', None), svgelements.Color):
                        color_hex = elem.stroke.hex
                    elif isinstance(getattr(elem, 'fill', None), svgelements.Color):
                        color_hex = elem.fill.hex
                    
                    items.append(self._create_item(qpath, color_hex))
        except Exception as e:
            print(f"[SvgDocumentImporter] Błąd svgelements: {e}")

        # 2. Parsowanie tekstów z drzewa XML (Inkscape <text>, <tspan>)
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            for elem in root.iter():
                if '}' in elem.tag:
                    elem.tag = elem.tag.split('}', 1)[1]

            def parse_node(node: ET.Element, accum_transform: QTransform, inherited_font: Optional[Dict[str, Any]]):
                node_trans = self.parse_transform(node.attrib.get('transform', ''))
                curr_trans = node_trans * accum_transform
                curr_font = self.extract_font_props(node, inherited_font)

                if node.tag == 'text':
                    bx = float(re.search(r'[-+]?\d*\.?\d+', node.attrib.get('x', '0')).group(0)) if node.attrib.get('x') else 0.0
                    by = float(re.search(r'[-+]?\d*\.?\d+', node.attrib.get('y', '0')).group(0)) if node.attrib.get('y') else 0.0

                    tspans = [c for c in node if c.tag == 'tspan']
                    if tspans:
                        cur_x, cur_y = bx, by
                        for ts in tspans:
                            t_font = self.extract_font_props(ts, curr_font)
                            txt = "".join(ts.itertext()).strip()
                            if not txt:
                                continue
                            if 'x' in ts.attrib:
                                cur_x = float(re.search(r'[-+]?\d*\.?\d+', ts.attrib['x']).group(0))
                            if 'y' in ts.attrib:
                                cur_y = float(re.search(r'[-+]?\d*\.?\d+', ts.attrib['y']).group(0))
                            elif 'dy' in ts.attrib:
                                m_dy = re.search(r'[-+]?\d*\.?\d+', ts.attrib['dy'])
                                cur_y += float(m_dy.group(0)) if m_dy else t_font['size'] * 1.2

                            ts_trans = self.parse_transform(ts.attrib.get('transform', ''))
                            qpath = self._convert_text_to_path(txt, cur_x, cur_y, t_font, ts_trans * curr_trans)
                            if not qpath.isEmpty():
                                items.append(self._create_item(qpath, t_font.get('color')))
                    else:
                        txt = "".join(node.itertext()).strip()
                        if txt:
                            qpath = self._convert_text_to_path(txt, bx, by, curr_font, curr_trans)
                            if not qpath.isEmpty():
                                items.append(self._create_item(qpath, curr_font.get('color')))

                for child in node:
                    if child.tag not in ('tspan', 'flowPara', 'flowRegion'):
                        parse_node(child, curr_trans, curr_font)

            parse_node(root, QTransform(), None)
        except Exception as e:
            print(f"[SvgDocumentImporter] Błąd XML text: {e}")

        if not items:
            return []

        # 3. Skalowanie fizyczne (96 DPI -> mm) oraz inwersja osi Y pod stół roboczy CNC
        px_to_mm = 25.4 / 96.0
        t_scale_flip = QTransform().scale(px_to_mm, -px_to_mm)
        for item in items:
            item.setPath(t_scale_flip.map(item.path()))

        # Obliczenie wspólnej obwiedni całego pliku w milimetrach
        full_path_mm = QPainterPath()
        for item in items:
            full_path_mm.addPath(item.path())
        bbox_mm = full_path_mm.boundingRect()

        # Przesunięcie do punktu docelowego stołu
        dx = default_pos.x() - bbox_mm.left()
        dy = default_pos.y() - bbox_mm.top()

        t_translate = QTransform().translate(dx, dy)
        for item in items:
            item.setPath(t_translate.map(item.path()))

        return items

    def _create_item(self, path: QPainterPath, stroke_color: Optional[str] = None) -> QGraphicsPathItem:
        item = QGraphicsPathItem(path)
        col = QColor(stroke_color) if stroke_color and QColor(stroke_color).isValid() else self.pen.color()
        pen = QPen(col, 1.2)
        pen.setCosmetic(True)
        item.setPen(pen)

        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        item.setZValue(20)
        return item