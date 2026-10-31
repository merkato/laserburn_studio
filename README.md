# ⚡ LaserBurn Studio

Nowoczesne środowisko CAD/CAM dedykowane dla grawerek i wycinarek laserowych opartych na kontrolerach GRBL (ze szczególnym uwzględnieniem mostka sieciowego Raspberry Pi / WebSocket). 

Aplikacja została zaprojektowana od podstaw z myślą o precyzyjnym pozycjonowaniu projektów na nieregularnych materiałach naturalnych (np. plastry drewna, deski oflisowe) oraz bezkompromisowej kontroli nad parametrami technologicznymi warstw.

![Zrzut ekranu aplikacji LaserBurn Studio w ciemnym motywie. Na siatce stołu roboczego widać czerwony wektorowy wzór kwiatów i liści z prostokątną ramką. Po prawej znajduje się panel Warstwy z czterema kolorowymi warstwami, a u góry paski narzędzi i menu Projekt, Edycja, Maszyna. Pasek stanu pokazuje GRBL: Rozłączony.](docs/anatomia_okna.png)

---

## 🚀 Kluczowe Możliwości

* **Wektorowy silnik CAD (PySide6):**
  * Zorientowany pod układ współrzędnych CNC ($Y$ rosnące w górę stołu roboczego).
  * Wielopunktowy manipulator przekształceń (`TransformBoxItem`) ze skalowaniem proporcjonalnym, obrotem i translacją z przyciąganiem magnetycznym (`SnapEngine`).
  * Obsługa schowka wewnętrznego (Kopiuj `Ctrl+C`, Wklej `Ctrl+V`, Duplikuj `Ctrl+D`).
  * Zarządzanie poziomami renderowania (Z-Order: Na wierzch / Na spód / Stopniowanie).

* **Import SVG (Inkscape-ready):**
  * Dokładne odwzorowanie geometrii z przeliczeniem jednostek 96 DPI na milimetry ($25.4 / 96.0$).
  * Rzutowanie tekstów i glifów czcionek na krzywe Béziera z zachowaniem zakotwiczenia (`text-anchor`).
  * Rozpoznawanie atrybutów kolorów obrysów oraz stylów inline CSS z automatycznym przypisaniem do warstw technologicznych.

* **Dedykowany silnik rastrowy (Floyd-Steinberg Dithering):**
  * 1-bitowa kwantyzacja rozpraszania błędu dla uzyskania półtonów na drewnie.
  * Generowanie dwukierunkowej ścieżki skanowania poziomego (boustrophedon).
  * Dynamiczna kontrola mocy lasera w trybie `M4` z dobiegami i wyhamowaniami (`overscan 2.5 mm`), eliminującymi przypalenia na nawrotach osi.

* **Moduł „Plaster Drewna” (Boundary Mapping & Auto-Fit):**
  * Rejestracja punktów krawędzi materiału za pomocą plamki celującej lasera (jogging).
  * Generowanie gładkiej otoczki wielokątnej (Shapely Convex Hull) z wyznaczeniem środka geometrycznego (centroidu).
  * Funkcja **Auto-Fit**: automatyczne wpisanie, przeskalowanie z marginesem bezpieczeństwa i wycentrowanie grafiki wewnątrz nieregularnego plastra.

* **Dwukierunkowa telemetria GRBL:**
  * Komunikacja w czasie rzeczywistym przez WebSocket z mostkiem na Raspberry Pi (`ws://malina.local:8000/ws`). Kod dostępny w osobnym repo.
  * Bezpośredni podgląd współrzędnych roboczych `WPos` i statusu maszyny (Idle, Run, Jog, Hold).
  * Płynny wskaźnik głowicy lasera (`LaserReticleItem`).
  * Natychmiastowe zatrzymanie awaryjne E-STOP (Feed Hold `!` + `M5` + Jog Cancel).

---

## 🛠️ Architektura i Struktura Projektu

Projekt oparty jest na modułach:

```text
laserburn_studio/
├── laserburn_studio.py     # Główna aplikacja, UI (PySide6), orkiestracja CAD i CAM
├── svg_engine.py           # Parser SVG (svgelements + XML), mapowanie stylów i fontów
├── raster_engine.py        # Algorytmy ditheringu PIL/NumPy oraz generator ścieżek M4
├── gcode_visualizer.py     # Symulator wygenerowanego kodu G-code (estymacja trapezoidalna)
├── jog_panel.py            # Widżet sterowania osiami (Jogging krokami 0.1-50 mm)
├── rulers.py               # Dynamiczne linijki stołu roboczego powiązane z zoomem
├── time_estimator.py       # Estymator czasu pracy z modelem dynamiki ruchu
├── project_manager.py      # Serializacja i deserializacja projektów (.lcam)
├── INSTRUKCJA.md           # Szczegółowa instrukcja obsługi krok po kroku
└── README.md               # Prezentacja projektu i dokumentacja techniczna
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