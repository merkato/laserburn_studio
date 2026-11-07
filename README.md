# ⚡ LaserBurn Studio

Nowoczesne środowisko CAD/CAM dedykowane dla grawerek i wycinarek laserowych opartych na kontrolerach GRBL (ze szczególnym uwzględnieniem mostka sieciowego Raspberry Pi / WebSocket). 

Aplikacja została zaprojektowana od podstaw z myślą o precyzyjnym pozycjonowaniu projektów na nieregularnych materiałach naturalnych (np. plastry drewna, deski oflisowe) oraz bezkompromisowej kontroli nad parametrami technologicznymi warstw.

![Zrzut ekranu aplikacji LaserBurn Studio w ciemnym motywie. Na siatce stołu roboczego widać czerwony wektorowy wzór kwiatów i liści z prostokątną ramką. Po prawej znajduje się panel Warstwy z czterema kolorowymi warstwami, a u góry paski narzędzi i menu Projekt, Edycja, Maszyna. Pasek stanu pokazuje GRBL: Rozłączony.](docs/anatomia_okna.png)

---

## 🚀 Kluczowe Możliwości

* **Wektorowy silnik CAD (PySide6):**
  * Układ współrzędnych CNC ($Y$ rośnie w górę stołu roboczego).
  * Manipulator przekształceń (`TransformBoxItem`) ze skalowaniem proporcjonalnym, obrotem i przyciąganiem (`SnapEngine`).
  * Schowek (Kopiuj `Ctrl+C`, Wklej `Ctrl+V`, Duplikuj `Ctrl+D`) i kolejność rysowania (Z-Order).
  * **Kształty i symbole** z dokładnymi wymiarami: linia, prostokąt (z zaokrągleniem), elipsa/okrąg, wielokąt foremny, gwiazda, serce (menu *Wstaw*).

* **Import SVG (Inkscape, Illustrator i inne):**
  * Parser `svgelements` z obsługą `viewBox`, jednostek i transformacji; pliki Illustratora zapisane w px rozpoznawane jako 72 DPI.
  * Obiekty zachowują położenie ze strony dokumentu i kolory (obrys, a przy jego braku wypełnienie); raport kolorów bez warstwy.

* **Warstwy technologiczne i baza materiałów:**
  * Operacje **Cięcie**, **Linia**, **Fill** (kreskowanie z otworami wg reguły parzystości) i **Raster**.
  * Tryb dynamicznej mocy `M4`, gaszenie wiązki (`M5`) przed każdym przejazdem jałowym, kolejność ścieżek metodą najbliższego sąsiada.
  * **Baza materiałów** (JSON w katalogu użytkownika) – jednym kliknięciem ustawia parametry warstw dla wybranego materiału.
  * **Generator testu materiałowego** – siatka pól moc × prędkość dla cięcia i wypełnienia.

* **Silnik rastrowy:**
  * Dithering Floyd–Steinberga (NumPy/Pillow), uwzględnienie orientacji EXIF i przezroczystości.
  * Dwukierunkowe skanowanie `M4` z dobiegiem 2.5 mm na nawrotach.

* **Moduł „Plaster Drewna” (Boundary Mapping & Auto-Fit):**
  * Rejestracja krawędzi materiału plamką celującą, otoczka (Shapely) z opcjonalnym wygładzeniem splajnem.
  * **Auto-Fit**: wpisanie projektu w nieregularny materiał z marginesem, w razie potrzeby zmniejszenie lub najmniejszy konieczny obrót.

* **Sterowanie maszyną przez [grbl-bridge](https://github.com/merkato/grbl-bridge):**
  * WebSocket z mostkiem na Raspberry Pi (`ws://malina.local:8000/ws`), telemetria `WPos` i stanu GRBL, wskaźnik głowicy.
  * Pauza i wznowienie zadania, **STOP** z czyszczeniem bufora (feed hold → soft reset, bez utraty pozycji), wyjście z *Hold* po przycisku bezpieczeństwa.

* **Samodzielny instalator dla Windows** – nie wymaga Pythona ani bibliotek na komputerze docelowym.

---

## 📦 Instalacja

**Windows 10/11 (64-bit):** uruchom `LaserBurnStudio-<wersja>-setup.exe` albo rozpakuj `LaserBurnStudio-<wersja>-portable.zip` i uruchom `LaserBurnStudio.exe`.

**Ze źródeł** (Python 3.10+):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python laserburn_studio.py
```

**Budowanie instalatora:**

```bash
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe build.py
```

Wynik trafia do `dist\`: katalog programu, archiwum przenośne i – gdy zainstalowano [Inno Setup 6](https://jrsoftware.org/isdl.php) – instalator. Szczegóły w [instrukcji](instrukcja.md#1-instalacja-i-pierwsze-uruchomienie).

---

## 🛠️ Architektura i Struktura Projektu

```text
laserburn_studio/
├── laserburn_studio.py     # Główne okno, UI (PySide6), orkiestracja CAD i CAM
├── app_info.py             # Nazwa, wersja, ścieżki danych i zasobów
├── shapes.py               # Geometria kształtów i symboli (QPainterPath w mm)
├── shape_dialog.py         # Okno „Wstaw kształt” z podglądem
├── svg_engine.py           # Import SVG (svgelements) z raportem
├── raster_engine.py        # Dithering i generator ścieżek rastrowych M4
├── cam_engine.py           # Postprocesor G-code (wektory, kreskowanie Fill)
├── path_optimizer.py       # Kolejność ścieżek (najbliższy sąsiad), wykrywanie konturów zamkniętych
├── kerf_compensator.py     # Kompensacja rzazu (przygotowana, jeszcze nieużywana w G-code)
├── material_library.py     # Baza materiałów (JSON) i przypisanie do warstw
├── materials_dialog.py     # Okno edycji bazy materiałów
├── material_tester.py      # Generator testu materiałowego
├── material_fitting.py     # Auto-Fit projektu w obrys materiału
├── bridge_client.py        # Klient grbl-bridge (WebSocket + HTTP), sterowanie zadaniem
├── jog_panel.py            # Panel Jog: ruchy osi, pauza/wznowienie/STOP
├── gcode_visualizer.py     # Symulator G-code
├── time_estimator.py       # Estymator czasu pracy (model trapezoidalny)
├── rulers.py               # Linijki stołu roboczego
├── project_manager.py      # Zapis i odczyt projektów (.lcam, format v2)
├── smoke_test.py           # Test uruchomienia wersji spakowanej (--smoke-test)
├── build.py                # Budowanie programu i instalatora (PyInstaller + Inno Setup)
├── installer/              # Skrypt instalatora Inno Setup
├── assets/                 # Ikona programu
├── tools/make_icon.py      # Generator ikony
├── instrukcja.md           # Instrukcja obsługi krok po kroku
└── README.md               # Prezentacja projektu
```

---

## ✒️ Uwagi Autora i Podziękowania

Projekt **LaserBurn Studio** narodził się z potrzeby stworzenia narzędzia łączącego elastyczność programów graficznych z inżynierską niezawodnością sterowania maszynami CNC i łatwością obsługi, skierowanej do mojego Syna. Powstał w warsztacie, gdzie zapach drewna miesza się z liniami kodu pisanymi późną nocą.

Podziękowania kieruję do społeczności open-source skupionej wokół ekosystemu GRBL oraz twórców bibliotek *Shapely*, *PySide* i *svgelements*, bez których matematyka geometrii obliczeniowej wymagałaby wyważania dawno otwartych drzwi.

Szczególne podziękowania kieruję do wspaniałych ludzi, którzy każdego dnia pokazują mi swoją postawą i słowem że moja praca ma sens, że *Zawsze warto być przyzwoitym. Nawet jeśli się to nie opłaca*: 

S. K. (dzięki Córuś)

E. K.

K. M.

B. G.

---

### 💖 Z dedykacją dla S.J.

> *Będę przeganiał diabła w nas*  
> *Wciąż i wciąż*  
> *Będę za rękę chwytać czas*  
> *Wciąż i wciąż*  
> *Będę głową o strop bić*  
> *Wciąż i wciąż*  
> *Będę się starał lepszym być*  
> *Wciąż i wciąż (...)*