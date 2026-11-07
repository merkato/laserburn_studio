from __future__ import annotations
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QFormLayout, QListWidget, QListWidgetItem, QPushButton,
    QLineEdit, QDoubleSpinBox, QSpinBox, QPlainTextEdit, QTableWidget, QHeaderView, QCheckBox,
    QWidget, QLabel, QMessageBox, QAbstractSpinBox
)

from material_library import (
    Material, MaterialLibrary, MaterialLibraryError, OPERATIONS, OPERATION_LABELS, OPERATION_FIELDS,
    _FIELD_SPECS, operations_from_layers
)

_COLUMNS = [("speed", "F [mm/min]"), ("power", "Moc [%]"), ("passes", "Pętle"),
            ("kerf", "Kerf [mm]"), ("hatch_interval", "Odstęp [mm]"), ("hatch_angle", "Kąt [°]")]
_DECIMALS = {"speed": 0, "power": 1, "kerf": 2, "hatch_interval": 2, "hatch_angle": 0}


class MaterialLibraryDialog(QDialog):
    """
    Edycja bazy materiałów: lista po lewej, parametry operacji (Cięcie/Linia/Fill/Raster) po prawej.
    "Pobierz z warstw" kopiuje bieżące ustawienia tabeli warstw do materiału,
    "Zastosuj do warstw" ustawia warstwy wg operacji (po zapisaniu bazy).
    """
    apply_requested = Signal(str)  # id materiału

    def __init__(self, library: MaterialLibrary, layers_provider: Callable[[], list],
                 current_id: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Baza materiałów")
        self.resize(880, 520)
        self.library = library
        self.layers_provider = layers_provider
        self._dirty = False        # baza w pamięci różni się od pliku
        self._form_dirty = False   # formularz bieżącego materiału zmieniony
        self._loading = False
        self._current: Optional[Material] = None
        self._row_widgets: Dict[str, Dict[str, QWidget]] = {}
        self._setup_ui()
        self._reload_list(select_id=current_id)

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list)
        h = QHBoxLayout()
        for text, slot in (("➕ Nowy", self._new), ("⧉ Duplikuj", self._duplicate), ("🗑 Usuń", self._delete)):
            b = QPushButton(text); b.clicked.connect(slot); h.addWidget(b)
        left.addLayout(h)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self.ed_name = QLineEdit(); self.ed_name.textEdited.connect(self._mark_dirty)
        self.sp_thickness = QDoubleSpinBox(); self.sp_thickness.setRange(0.0, 100.0); self.sp_thickness.setDecimals(2)
        self.sp_thickness.setSuffix(" mm"); self.sp_thickness.valueChanged.connect(self._mark_dirty)
        self.ed_notes = QPlainTextEdit(); self.ed_notes.setFixedHeight(54); self.ed_notes.textChanged.connect(self._mark_dirty)
        form.addRow("Nazwa:", self.ed_name)
        form.addRow("Grubość:", self.sp_thickness)
        form.addRow("Notatki:", self.ed_notes)
        right.addLayout(form)

        self.table = QTableWidget(len(OPERATIONS), len(_COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels(["Operacja"] + [c[1] for c in _COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, op in enumerate(OPERATIONS):
            widgets: Dict[str, QWidget] = {}
            chk = QCheckBox(OPERATION_LABELS[op])
            chk.setToolTip("Zaznacz, aby materiał ustawiał warstwy tej operacji")
            chk.toggled.connect(lambda on, o=op: self._toggle_op(o, on))
            self.table.setCellWidget(r, 0, chk)
            widgets["_enabled"] = chk
            for c, (fname, _title) in enumerate(_COLUMNS, start=1):
                if fname not in OPERATION_FIELDS[op]:
                    blank = QLabel("–"); blank.setAlignment(Qt.AlignCenter); blank.setEnabled(False)
                    self.table.setCellWidget(r, c, blank)
                    continue
                typ, lo, hi, default = _FIELD_SPECS[fname]
                sp = QSpinBox() if typ is int else QDoubleSpinBox()
                sp.setRange(lo, hi)
                if typ is not int:
                    sp.setDecimals(_DECIMALS.get(fname, 2))
                sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
                sp.setValue(default)
                sp.valueChanged.connect(self._mark_dirty)
                self.table.setCellWidget(r, c, sp)
                widgets[fname] = sp
            self._row_widgets[op] = widgets
        right.addWidget(self.table)

        self.lbl_info = QLabel(f"Plik: {self.library.path}")
        self.lbl_info.setStyleSheet("color: #888;")
        self.lbl_info.setWordWrap(True)
        right.addWidget(self.lbl_info)

        h2 = QHBoxLayout()
        self.btn_from_layers = QPushButton("⬇ Pobierz z warstw")
        self.btn_from_layers.setToolTip("Kopiuje ustawienia bieżących warstw (wg operacji) do materiału")
        self.btn_from_layers.clicked.connect(self._from_layers)
        self.btn_apply = QPushButton("⬆ Zastosuj do warstw")
        self.btn_apply.setToolTip("Zapisuje bazę i ustawia warstwy wg ich operacji")
        self.btn_apply.clicked.connect(self._apply)
        self.btn_save = QPushButton("💾 Zapisz")
        self.btn_save.clicked.connect(self._save)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.close)
        for b in (self.btn_from_layers, self.btn_apply):
            h2.addWidget(b)
        h2.addStretch(1)
        h2.addWidget(self.btn_save)
        h2.addWidget(btn_close)
        right.addLayout(h2)
        root.addLayout(right, 3)

    # ------------------------------------------------------------------ lista
    def _reload_list(self, select_id: Optional[str] = None):
        self._loading = True
        self.list.clear()
        target_row = 0
        for i, m in enumerate(self.library.list()):
            it = QListWidgetItem(m.label)
            it.setData(Qt.UserRole, m.id)
            self.list.addItem(it)
            if m.id == select_id:
                target_row = i
        self._loading = False
        if self.list.count():
            self.list.setCurrentRow(target_row)
        else:
            self._show(None)

    def _on_select(self, current, previous):
        if self._loading:
            return
        if previous is not None and self._current is not None:
            self._commit_form()
        mid = current.data(Qt.UserRole) if current else None
        self._show(self.library.get(mid))

    def _show(self, m: Optional[Material]):
        self._loading = True
        self._form_dirty = False
        self._current = m.copy() if m else None
        enabled = m is not None
        for w in (self.ed_name, self.sp_thickness, self.ed_notes, self.table, self.btn_from_layers, self.btn_apply):
            w.setEnabled(enabled)
        self.ed_name.setText(m.name if m else "")
        self.sp_thickness.setValue(m.thickness_mm if m else 0.0)
        self.ed_notes.setPlainText(m.notes if m else "")
        for op, widgets in self._row_widgets.items():
            params = (m.operations.get(op) if m else None) or {}
            widgets["_enabled"].setChecked(bool(params))
            for fname, w in widgets.items():
                if fname == "_enabled":
                    continue
                w.setValue(params.get(fname, _FIELD_SPECS[fname][3]))
                w.setEnabled(bool(params))
        self._loading = False

    # ------------------------------------------------------------------ edycja
    def _mark_dirty(self, *_):
        if not self._loading:
            self._dirty = True
            self._form_dirty = True

    def _toggle_op(self, op: str, on: bool):
        for fname, w in self._row_widgets[op].items():
            if fname != "_enabled":
                w.setEnabled(on)
        self._mark_dirty()

    def _form_material(self) -> Optional[Material]:
        if self._current is None:
            return None
        m = self._current.copy()
        m.name = self.ed_name.text().strip() or "Bez nazwy"
        m.thickness_mm = self.sp_thickness.value()
        m.notes = self.ed_notes.toPlainText().strip()
        ops = {}
        for op, widgets in self._row_widgets.items():
            if widgets["_enabled"].isChecked():
                ops[op] = {f: w.value() for f, w in widgets.items() if f != "_enabled"}
        m.operations = ops
        return m

    def _commit_form(self):
        m = self._form_material()
        if m is not None and self._form_dirty:
            self.library.update(m)
            self._current = self.library.get(m.id).copy()
            self._form_dirty = False

    def _new(self):
        self._commit_form()
        m = self.library.add(Material(id="", name="Nowy materiał", thickness_mm=3.0,
                                      operations=operations_from_layers(self.layers_provider())))
        self._dirty = True
        self._reload_list(select_id=m.id)
        self.ed_name.setFocus(); self.ed_name.selectAll()

    def _duplicate(self):
        m = self._form_material()
        if m is None:
            return
        self._commit_form()
        m.name = f"{m.name} (kopia)"
        new = self.library.add(m)
        self._dirty = True
        self._reload_list(select_id=new.id)

    def _delete(self):
        m = self._current
        if m is None:
            return
        if QMessageBox.question(self, "Usuń materiał", f"Usunąć „{m.label}” z bazy?") != QMessageBox.Yes:
            return
        self.library.remove(m.id)
        self._current = None
        self._dirty = True
        self._reload_list()

    def _from_layers(self):
        ops = operations_from_layers(self.layers_provider())
        self._loading = True
        for op, widgets in self._row_widgets.items():
            params = ops.get(op)
            if not params:
                continue
            widgets["_enabled"].setChecked(True)
            for f, w in widgets.items():
                if f != "_enabled" and f in params:
                    w.setValue(params[f]); w.setEnabled(True)
        self._loading = False
        self._dirty = True
        self._form_dirty = True

    # ------------------------------------------------------------------ zapis
    def _save(self) -> bool:
        self._commit_form()
        try:
            self.library.save()
        except MaterialLibraryError as e:
            QMessageBox.critical(self, "Baza materiałów", str(e))
            return False
        self._dirty = False
        current_id = self._current.id if self._current else None
        self._reload_list(select_id=current_id)
        self.lbl_info.setText(f"Zapisano: {self.library.path}")
        return True

    def _apply(self):
        if self._current is None or not self._save():
            return
        self.apply_requested.emit(self._current.id)

    def closeEvent(self, event):
        self._commit_form()
        if self._dirty:
            ans = QMessageBox.question(self, "Baza materiałów", "Zapisać zmiany w bazie materiałów?",
                                       QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                event.ignore()
                return
            if ans == QMessageBox.Save and not self._save():
                event.ignore()
                return
            if ans == QMessageBox.Discard:
                try:
                    self.library.load()  # odrzuć zmiany w pamięci
                except MaterialLibraryError:
                    pass
        event.accept()
