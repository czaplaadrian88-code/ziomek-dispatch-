# FAZA 1 — DELIVERABLE #6: LEDGER POKRYCIA (MODUŁ × KLASA) + MACIERZ METODA × KLASA

**Audyt spójności Ziomka · sesja tmux 2 · 2026-06-30 · READ-ONLY.** Dowód „sprawdziliśmy wszystko" — **luka JAWNA, nie cisza** (zasada DESIGN §3).

**Skala kodu (z A1):** 105 core `.py` (104 246 LOC) + 145 `tools/*.py` + 433 `tests/*.py` + cross-repo (`nadajesz_clone/panel`, `courier_api`, `courier-app`). ~38 modułów CORE-DECYZJA na ścieżce `assess_order`.

**Pokrycie:** Faza B = 22 agenty, **klasa-major** (każdy agent przeczesał swoją klasę A–O przez zadeklarowane pasma modułów). 6 szerokich klas rozbitych na 2 agentów. Każdy agent zadeklarował JAWNIE: pasma sprawdzone (`coverage_declared`) + pasma/wątki NIE-sprawdzone (`coverage_gaps`). Łącznie **~280 deklaracji pokrycia modułów + ~150 jawnych luk + 241 findingów**. Faza C = 19 agentów (przyrządy, lane runtime-oracle). Faza D = 5 agentów (konflikty). Faza A = 6 osi inwentarza.

---

## 1. KRATA KLASA × PASMO-MODUŁÓW (kto sprawdził)

Pasma: **SEL** = dispatch_pipeline/feasibility_v2/scoring/objm_lexr6/wave_scoring/auto_assign_gate/auto_proximity · **ROUTE** = route_simulator_v2/tsp_solver/plan_recheck/plan_manager/same_restaurant_grouper · **STATE** = state_machine/panel_watcher/panel_client/courier_resolver/bag_state/geocoding/osrm_client/event_bus · **SERIAL** = shadow_dispatcher/sla_tracker/telegram_approver/czasowka_scheduler/evaluator · **TOOLS** = 145× tools/ · **CROSS** = panel/courier_api/courier-app/parcel · **CONFIG** = common.py/flags.json/drop-iny · **TESTS** = tests/

| Klasa | SEL | ROUTE | STATE | SERIAL | TOOLS | CROSS | CONFIG | TESTS | Agent(y) | findings |
|---|---|---|---|---|---|---|---|---|---|---|
| **A1** N-kopii | ✓ | ✓ | ✓ | ✓ | ◐ | ✓ | ✓ | – | B01+B02 | 24 |
| **A2** N-powierzchni | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – | B03 | 9 |
| **B** asymetria-bliźniaków | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | B04 | 16 |
| **C** zła-warstwa | ✓ | ✓ | ✓ | ✓ | ◐ | ◐ | ✓ | – | B05+B20 | 16 |
| **D** dryf-flag | ✓ | ✓ | ✓ | ✓ | ◐ | ◐ | ✓ | – | B06 | 16 |
| **E** kłamiące-przyrządy (kod) | ✓ | ✓ | – | ✓ | ✓ | – | – | – | B07 | 19 |
| **F** semantyka-pól | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – | B08+B21 | 15 |
| **G** kalibracja-zła-oś | ✓ | ✓ | ◐ | ✓ | ✓ | – | ✓ | – | B09 | 5 |
| **H** cykl-życia | ✓ | ✓ | ✓ | ◐ | ✓ | – | – | – | B10 | 7 |
| **I** konflikt | →Faza D (5 agentów) | | | | | | | | D01-D05 | 2 (+81 konfl.) |
| **J** cross-repo | ◐ | ✓ | ◐ | – | ✓ | ✓ | ✓ | – | B11+B22 | 16 |
| **K** martwy-kod | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | B12+B13 | 35 |
| **L** słownictwo/TZ | ✓ | ✓ | ✓ | ✓ | ◐ | ✓ | ✓ | – | B14 | 13 |
| **M** sentinele/cicha-awaria | ✓ | ✓ | ✓ | ✓ | ✓ | ◐ | – | – | B15+B16 | 19 |
| **N** rozsyp-progów | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | B17 | 17 |
| **O** współbieżność | ✓ | ✓ | ✓ | ✓ | ✓ | ◐ | – | – | B18 | 12 |
| **test-suite** (cross-cutting) | – | – | – | – | – | – | – | ✓ | B19 | 10 |

`✓` = pasmo zadeklarowane sprawdzone · `◐` = częściowo / wybrane moduły · `–` = poza pasmem klasy (jawna luka, niżej) · `→` = obsłużone w innej fazie.

**Każda z 15 klas + cross-cutting test-suite = pokryta ≥1 agentem z jawną deklaracją.** Klasa I (koherencja) celowo w Fazie D (graf), nie w sweepie B. Faza C (19 przyrządów) pokrywa runtime-oś klasy E (czego sweep-kodu nie widzi — C11).

## 2. MACIERZ METODA × KLASA (C11 — „klasa bez metody runtime = jawna luka")

Krytyczne: poprzedni 86-agentowy audyt skalował JEDNĄ metodę (read-code) → ślepy na klasę E. Ten audyt przypisał metody:

| Klasa | read-code | grep-sygnatura | runtime-oracle | replay-vs-rzeczywistość | live-monitor/journal | flag-reality-probe |
|---|---|---|---|---|---|---|
| A1/A2 N-kopii | ✓ | ✓✓ (graf importów) | – | – | – | – |
| B asymetria | ✓ | ✓✓ | – | ◐ (twin parity test) | ◐ | – |
| C warstwa | ✓✓ | ✓ | – | – | – | ◐ |
| D dryf-flag | ✓ | ✓ | – | – | ◐ | ✓✓ (systemctl Environment) |
| **E przyrządy** | ◐ | ✓ | **✓✓ (Faza C, 49 werdyktów)** | **✓✓** | ✓ | ◐ |
| F semantyka | ✓✓ | ✓✓ (writer/consumer) | – | – | – | – |
| G kalibracja | ✓ | ✓ | ◐ | ✓✓ (join GPS truth) | ✓ | ✓ |
| H cykl-życia | ✓✓ | ✓ | – | – | ✓ (mtime/stale) | – |
| I konflikt | ✓✓ | ✓ | – | – | – | ✓✓ |
| J cross-repo | ✓ | ✓✓ (2 repo) | ◐ | – | ✓✓ (route-monitor jsonl) | ✓ |
| K martwy-kod | ✓ | ✓✓ (zero-importerów) | – | – | – | ✓ (flaga-OFF-na-zawsze) |
| L słownictwo/TZ | ✓✓ | ✓✓ | – | – | – | – |
| M sentinele | ✓✓ | ✓✓ (bare-except) | ◐ | – | ✓ (V328 journal) | – |
| N progi | ✓ | ✓✓ (stałe liczbowe) | – | – | – | ✓ |
| O współbieżność | ✓✓ | ✓ | – | – | ✓ | – |

**Żadna klasa runtime-manifestująca się NIE została bez metody runtime:** E → Faza C oracle (49 instrumentów); G → replay-join GPS-truth; J → live route-monitor jsonl; D → systemctl Environment probe; M → V328 journal. To domyka lukę C11 poprzedniego audytu.

## 3. JAWNE LUKI POKRYCIA (czego NIE sprawdzono + powód — zebrane z 22 agentów, NIE cisza)

**Świadomie poza budżetem / granicą:**
- **Wnętrza `tools/` (145 plików) per-funkcja** — sklasyfikowane do klasy+timera (A4), ale ~120 narzędzi nie czytanych liniowo (osierocone/historyczne; klasa K dostała próbkę `.bak`).
- **Cross-repo wnętrza konsoli/apki poza ścieżką decyzyjną** — `nadajesz_clone/panel/app/` (jobs econ/ksef/sms/payment) wymienione zbiorczo jako PERI; audytowane TYLKO pliki renderujące decyzję Ziomka (fleet_state/feed/route_podjazdy/courier_orders/status_store). **GRANICA: STOP na dyspozytorni — Mailek/Papu NIE audytowane** (decyzja Adriana).
- **courier-app Kotlin** — pokryty jako API-driven (kolejność z backendu); nie audytowano wnętrza Kotlin liniowo poza `stop_sequence`/route.
- `scoring.py` ~19 kar `bonus_` nie zmapowane 1:1 do warstwy (B01 gap → ujęte częściowo w F/N).

**Wymaga drugiej metody (zaznaczone do Fazy C/E):**
- **Parytet runtime** kanon `lex_qual` vs frozen przy OBU stanach flagi, `_count_sla_violations` A vs SLA-loop B — deklarowane z lektury, NIE z odpalenia (Faza C/E adversarial).
- **Osiągalność gałęzi (D3)** dla modułów flag-OFF (`commitment_emitter`, `pending_queue_provider`, `traffic_v2_aggregator`) — oznaczone DEAD? jako HIPOTEZA, nie potwierdzony martwy kod.
- **py_compile/import** żadnego narzędzia — „świeży jsonl" dowodzi że collector BIEGA, nie że werdykt-tool odpali bez błędu.
- **at-joby ustawione przez równoległe sesje 3/4 PO grepie** — DRYF (atq pokazał 4: 168/193/198/200; 189 odpalił dziś).

## 4. WERYFIKACJA KOMPLETNOŚCI
- **15/15 klas anty-wzorców + cross-cutting test-suite** — sprawdzone, z jawnym pokryciem i lukami.
- **49/49 werdyktów przyrządów** z lane runtime-oracle (Deliverable #3) — domyka klasę E, której read-code nie widzi.
- **81 par konfliktowych** (Deliverable #2) — oś I (koherencja).
- **Każda luka WYMIENIONA z powodem** — nie domyślna cisza.

> **DRAFT.** Backing per klasa: `backing/B01..B22_*.md` (sweep + coverage_declared/gaps), `backing/C01..C19_*.md` (oracle), `backing/D01..D05_*.md` (konflikty), `backing/A1..A6_*.md` (osie inwentarza), `backing/WF2_DIGEST.md` (skonsolidowane).
