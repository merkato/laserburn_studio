from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath, QTransform, QFont, QFontMetricsF, QPen, QColor
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsItem
import svgelements


MM_PER_INCH = 25.4
CSS_PX_PER_INCH = 96.0       # Standard CSS/SVG (Inkscape, przeglądarki, CorelDRAW, Affinity)
ILLUSTRATOR_PX_PER_INCH = 72.0  # Adobe Illustrator zapisuje punkty typograficzne jako "px"

# Rozmiar wzorcowy czcionki przy zamianie tekstu na krzywe (duży = dokładne krzywe glifów)
_REF_FONT_PX = 1000


@dataclass
class SvgImportReport:
    """Podsumowanie importu – co weszło, co pominięto i dlaczego."""
    file_path: str = ""
    imported: int = 0
    texts: int = 0
    skipped_hidden: int = 0
    skipped_invisible: int = 0
    skipped_images: int = 0
    skipped_empty: int = 0
    errors: List[str] = field(default_factory=list)
    px_per_inch: float = CSS_PX_PER_INCH
    page_size_mm: Tuple[float, float] = (0.0, 0.0)
    generator: str = ""

    def summary(self) -> str:
        parts = [f"obiektów: {self.imported}"]
        if self.texts:
            parts.append(f"tekstów: {self.texts}")
        skipped = self.skipped_hidden + self.skipped_invisible + self.skipped_images + self.skipped_empty
        if skipped:
            parts.append(f"pominięto: {skipped}")
        if self.px_per_inch != CSS_PX_PER_INCH:
            parts.append(f"{self.px_per_inch:.0f} DPI")
        if self.errors:
            parts.append(f"błędy: {len(self.errors)}")
        return ", ".join(parts)


class SvgDocumentImporter:
    """
    Import SVG dla LaserBurn Studio, oparty w całości na svgelements:
    - jednostki dokumentu (mm/cm/in/pt/px) oraz viewBox -> milimetry stołu,
    - wykrywanie Adobe Illustrator (72 DPI zapisane jako "px"),
    - pełne dziedziczenie stylów, transformacje grup, <use>/<symbol>, klasy CSS,
    - pomijanie elementów ukrytych (display:none, visibility:hidden) i niewidocznych,
    - zachowanie położenia obiektów względem strony (lewy dolny róg strony = X0 Y0 stołu),
    - zachowanie kolorów: obrys (stroke), a gdy go brak – wypełnienie (fill); tekst: fill.
    """

    def __init__(self, default_pen: Optional[QPen] = None, px_per_inch: Optional[float] = None):
        if default_pen:
            self.pen = QPen(default_pen)
        else:
            self.pen = QPen(QColor(255, 0, 0), 1.0)
        self.pen.setCosmetic(True)
        self.px_per_inch_override = px_per_inch
        self.last_report = SvgImportReport()

    # =========================================================================
    # JEDNOSTKI I WYKRYWANIE GENERATORA
    # =========================================================================
    @staticmethod
    def _read_head(file_path: str, limit: int = 4096) -> str:
        try:
            with open(file_path, "rb") as f:
                return f.read(limit).decode("utf-8", errors="ignore")
        except OSError:
            return ""

    @classmethod
    def detect_px_per_inch(cls, file_path: str) -> Tuple[float, str]:
        """
        Zwraca (px_na_cal, generator). Illustrator zapisuje wymiary w punktach (1/72 cala)
        z jednostką "px" lub bez jednostki – wtedy 1 px = 1/72 cala. Gdy wymiary mają jednostki
        fizyczne (mm, cm, in, pt), svgelements przelicza je poprawnie i obowiązuje 96 DPI.
        """
        head = cls._read_head(file_path)
        generator = ""
        m = re.search(r"Generator:\s*([^\r\n>]+?)\s*(?:-->|,|$)", head)
        if m:
            generator = m.group(1).strip()
        elif "Inkscape" in head or "inkscape" in head:
            generator = "Inkscape"

        if "Adobe Illustrator" in head:
            m_svg = re.search(r"<svg\b[^>]*>", head, re.S)
            svg_tag = m_svg.group(0) if m_svg else ""
            m_w = re.search(r'\swidth\s*=\s*"([^"]*)"', svg_tag)
            width_attr = m_w.group(1).strip() if m_w else ""
            unit = re.sub(r"[-+0-9.eE\s]", "", width_attr).lower()
            if unit in ("", "px"):
                return ILLUSTRATOR_PX_PER_INCH, generator or "Adobe Illustrator"
        return CSS_PX_PER_INCH, generator

    # =========================================================================
    # KOLORY I WIDOCZNOŚĆ
    # =========================================================================
    @staticmethod
    def _svg_color(value) -> Optional[QColor]:
        """Kolor svgelements -> nieprzezroczysty QColor (bez kanału alfa, aby nie mylić warstw)."""
        if value is None or not isinstance(value, svgelements.Color) or value.value is None:
            return None
        return QColor(int(value.red), int(value.green), int(value.blue))

    @staticmethod
    def _is_hidden(elem) -> bool:
        values = getattr(elem, "values", {}) or {}
        if str(values.get("visibility", "")).strip().lower() in ("hidden", "collapse"):
            return True
        if str(values.get("display", "")).strip().lower() == "none":
            return True
        try:
            if float(values.get("opacity", 1.0)) <= 0.0:
                return True
        except (TypeError, ValueError):
            pass
        return False

    @staticmethod
    def _stroke_visible(elem) -> bool:
        width = getattr(elem, "stroke_width", None)
        try:
            return width is None or float(width) > 0.0
        except (TypeError, ValueError):
            return True

    def _shape_color(self, elem) -> Optional[QColor]:
        stroke = self._svg_color(getattr(elem, "stroke", None))
        if stroke is not None and self._stroke_visible(elem):
            return stroke
        return self._svg_color(getattr(elem, "fill", None))

    def _text_color(self, elem) -> Optional[QColor]:
        fill = self._svg_color(getattr(elem, "fill", None))
        if fill is not None:
            return fill
        return self._svg_color(getattr(elem, "stroke", None))

    # =========================================================================
    # GEOMETRIA
    # =========================================================================
    @staticmethod
    def _path_to_qpath(geom_path: svgelements.Path) -> QPainterPath:
        """Ścieżka svgelements (już z zastosowanymi transformacjami) -> QPainterPath w px."""
        qpath = QPainterPath()
        for seg in geom_path:
            if isinstance(seg, svgelements.Move):
                if seg.end is not None:
                    qpath.moveTo(seg.end.x, seg.end.y)
            elif isinstance(seg, svgelements.Close):
                qpath.closeSubpath()
            elif isinstance(seg, svgelements.Line):
                qpath.lineTo(seg.end.x, seg.end.y)
            elif isinstance(seg, svgelements.CubicBezier):
                qpath.cubicTo(seg.control1.x, seg.control1.y, seg.control2.x, seg.control2.y, seg.end.x, seg.end.y)
            elif isinstance(seg, svgelements.QuadraticBezier):
                qpath.quadTo(seg.control.x, seg.control.y, seg.end.x, seg.end.y)
            elif isinstance(seg, svgelements.Arc):
                # Łuk eliptyczny jako krzywe Béziera (dokładniej niż odcinki)
                for cubic in seg.as_cubic_curves():
                    qpath.cubicTo(cubic.control1.x, cubic.control1.y, cubic.control2.x, cubic.control2.y,
                                  cubic.end.x, cubic.end.y)
        return qpath

    @staticmethod
    def _matrix_to_qtransform(m) -> QTransform:
        if m is None:
            return QTransform()
        return QTransform(float(m.a), float(m.b), float(m.c), float(m.d), float(m.e), float(m.f))

    @staticmethod
    def _first_font_family(family) -> str:
        if not family:
            return "Arial"
        first = str(family).split(",")[0].strip().strip("'\"")
        return first or "Arial"

    def _text_to_qpath(self, elem) -> QPainterPath:
        """Tekst SVG -> krzywe glifów, w tych samych jednostkach (px) co reszta geometrii."""
        text = getattr(elem, "text", None)
        if not text or not str(text).strip():
            return QPainterPath()
        text = str(text)

        font_size = float(getattr(elem, "font_size", 16.0) or 16.0)
        qfont = QFont(self._first_font_family(getattr(elem, "font_family", None)))
        qfont.setPixelSize(_REF_FONT_PX)

        weight = getattr(elem, "font_weight", None)
        try:
            qfont.setBold(int(float(weight)) >= 600)
        except (TypeError, ValueError):
            qfont.setBold(str(weight).lower() in ("bold", "bolder"))
        qfont.setItalic(str(getattr(elem, "font_style", "") or "").lower() in ("italic", "oblique"))

        glyphs = QPainterPath()
        glyphs.addText(QPointF(0.0, 0.0), qfont, text)

        anchor = str(getattr(elem, "anchor", "start") or "start").lower()
        if anchor in ("middle", "end"):
            advance = QFontMetricsF(qfont).horizontalAdvance(text)
            shift = advance / 2.0 if anchor == "middle" else advance
            glyphs = QTransform().translate(-shift, 0.0).map(glyphs)

        scale = font_size / _REF_FONT_PX
        x = float(getattr(elem, "x", 0.0) or 0.0)
        y = float(getattr(elem, "y", 0.0) or 0.0)
        local = QTransform(scale, 0.0, 0.0, scale, x, y)  # baseline w punkcie (x, y)
        return (local * self._matrix_to_qtransform(getattr(elem, "transform", None))).map(glyphs)

    # =========================================================================
    # GŁÓWNY IMPORT
    # =========================================================================
    def load_svg(
        self,
        file_path: str,
        default_pos: Optional[QPointF] = None,
        keep_document_position: bool = True
    ) -> List[QGraphicsPathItem]:
        """
        Importuje plik SVG jako obiekty sceny w milimetrach stołu (oś Y w górę).

        keep_document_position=True: lewy dolny róg strony SVG trafia w X0 Y0 stołu, więc
        obiekty zachowują swoje położenie z dokumentu (np. strony Inkscape).
        keep_document_position=False: lewy dolny róg obwiedni importu trafia w default_pos.
        """
        report = SvgImportReport(file_path=file_path)
        self.last_report = report

        ppi, generator = self.detect_px_per_inch(file_path)
        if self.px_per_inch_override:
            ppi = float(self.px_per_inch_override)
        report.px_per_inch = ppi
        report.generator = generator

        try:
            svg = svgelements.SVG.parse(file_path, reify=True, ppi=CSS_PX_PER_INCH)
        except Exception as e:
            report.errors.append(f"Nie można odczytać pliku: {e}")
            return []

        raw: List[Tuple[QPainterPath, Optional[QColor]]] = []
        for elem in svg.elements():
            if isinstance(elem, (svgelements.SVG, svgelements.Group, svgelements.Use)):
                continue
            if isinstance(elem, (svgelements.Image, getattr(svgelements, "SVGImage", svgelements.Image))):
                report.skipped_images += 1
                continue
            if self._is_hidden(elem):
                report.skipped_hidden += 1
                continue

            try:
                if isinstance(elem, svgelements.Text):
                    if not getattr(elem, "text", None):
                        continue  # kontener <text> z <tspan> – treść przychodzi w osobnych elementach
                    qpath = self._text_to_qpath(elem)
                    color = self._text_color(elem)
                    is_text = True
                elif isinstance(elem, svgelements.Shape):
                    color = self._shape_color(elem)
                    if color is None:
                        report.skipped_invisible += 1  # ani obrysu, ani wypełnienia
                        continue
                    qpath = self._path_to_qpath(svgelements.Path(elem))
                    is_text = False
                else:
                    continue
            except Exception as e:
                report.errors.append(f"{type(elem).__name__} id={getattr(elem, 'id', None)}: {e}")
                continue

            if qpath.isEmpty() or qpath.elementCount() < 2:
                report.skipped_empty += 1
                continue
            raw.append((qpath, color))
            if is_text:
                report.texts += 1

        if not raw:
            return []

        # Px (96 DPI svgelements) -> mm, z korektą dla dokumentów 72 DPI (Illustrator)
        k = (MM_PER_INCH / CSS_PX_PER_INCH) * (CSS_PX_PER_INCH / ppi)

        page_w_px, page_h_px = self._page_size_px(svg, raw)
        page_h_mm = page_h_px * k
        report.page_size_mm = (page_w_px * k, page_h_mm)

        # SVG: Y w dół od góry strony. Stół: Y w górę od dołu -> y' = H - y
        to_bed = QTransform(k, 0.0, 0.0, -k, 0.0, page_h_mm)
        paths_mm = [(to_bed.map(p), c) for p, c in raw]

        if not keep_document_position:
            target = default_pos if default_pos is not None else QPointF(10.0, 10.0)
            union = QPainterPath()
            for p, _ in paths_mm:
                union.addPath(p)
            bbox = union.boundingRect()
            shift = QTransform().translate(target.x() - bbox.left(), target.y() - bbox.top())
            paths_mm = [(shift.map(p), c) for p, c in paths_mm]

        items = [self._create_item(p, c) for p, c in paths_mm]
        report.imported = len(items)
        return items

    @staticmethod
    def _page_size_px(svg: svgelements.SVG, raw: List[Tuple[QPainterPath, Optional[QColor]]]) -> Tuple[float, float]:
        """Rozmiar strony w px: width/height, następnie viewBox, a na końcu obwiednia rysunku."""
        w = float(getattr(svg, "width", 0.0) or 0.0)
        h = float(getattr(svg, "height", 0.0) or 0.0)
        if w > 1.0 and h > 1.0:
            return w, h
        vb = getattr(svg, "viewbox", None)
        if vb is not None and getattr(vb, "height", None):
            return float(vb.width), float(vb.height)
        union = QPainterPath()
        for p, _ in raw:
            union.addPath(p)
        rect = union.boundingRect()
        return max(0.0, rect.right()), max(0.0, rect.bottom())

    def _create_item(self, path: QPainterPath, color: Optional[QColor] = None) -> QGraphicsPathItem:
        item = QGraphicsPathItem(path)
        col = QColor(color) if color is not None and color.isValid() else QColor(self.pen.color())
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
