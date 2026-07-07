# B1 — naprawa kłamiących przyrządów (scheduled_flip_gate) + diagnoza (0,0)

Data: 2026-07-07. Agent: HIGIENA (worktree, izolowana kopia — ZERO merge/flip/restart).
Gałąź: `worktree-agent-a3c3127c8de36e50c` (od `master@39fb1c9`).
Plik zmieniony: `tools/scheduled_flip_gate.py` (+ nowy test `tests/test_scheduled_flip_gate_verify.py`).
Backup: `tools/scheduled_flip_gate.py.bak-pre-verify-filefix-2026-07-07`.

---

## ZADANIE 1 — `cmd_verify` czytał zły log (fałszywe marker_hits=0)

### Root cause (u źródła, nie łatka)
Markery plan_recheck (`L3_REGEN_*`, `L4_ANCHOR_FLOOR`, `GC_COURIER_PLANS`) emituje
logger `plan_recheck` przez `StreamHandler()` → **stderr** (`plan_recheck.py:35-43`).
Serwis `dispatch-plan-recheck.service` ma:
```
StandardOutput=append:/root/.openclaw/workspace/scripts/logs/plan_recheck.log
StandardError=append:/root/.openclaw/workspace/scripts/logs/plan_recheck.log
```
`append:<plik>` kieruje stdout/stderr **do PLIKU, NIE do journala**. Dlatego
`journalctl -u dispatch-plan-recheck` pokazuje **0 markerów** — a stare `cmd_verify`
liczyło markery TYLKO z journala (`journalctl -u dispatch-shadow -u dispatch-plan-recheck`).
Efekt: wieczne `marker_hits=0` = fałszywy sygnał „flip nie zadziałał". Ugryzło przy
GC-verify at-206 06.07 („marker_hits=0/err_burst=25 = KŁAMSTWO PRZYRZĄDU"; prawda =
22 markery GC-real w pliku).

Serwer = `Etc/UTC`, formatter `datefmt='%Y-%m-%d %H:%M:%S'` → prefiks czasu w pliku
jest UTC = to samo co `_now()` w narzędziu (naiwne porównanie UTC bezpieczne).

### Fix
1. Nowa stała `PLAN_RECHECK_LOG = <scripts>/logs/plan_recheck.log`.
2. `_read_log_window(path, since, now=None)` — strumieniowe czytanie pliku z oknem
   czasowym po prefiksie (linia-kontynuacja Tracebacka dziedziczy stan ostatniej
   sparsowanej, więc multi-line nie wypada z okna). Brak pliku → `[]`.
3. `_count_markers(lines, tok)` — substring (`L3_REGEN` łapie `_REJECTED` i `_BOTH_BREACH`).
4. `cmd_verify` skanuje **DWIE ścieżki**: journal (dla err_burst + backstop markera na
   przyszłość) + PLIK `plan_recheck.log` (REALNE źródło markerów, okno 2h). Źródła są
   wzajemnie rozłączne przez konfig systemd → suma bez podwójnego liczenia.
   Dodatkowo naprawiono utajony `NameError` (gdy `journalctl` rzuci wyjątkiem przed
   przypisaniem `r`, stary kod sięgał `r.stdout` w liczeniu markerów).

### Dowód ON≠OFF (przed=0, po=realna liczba)
- **Live (okno 2h, 2026-07-07 ~18:00 UTC):**
  | marker | STARE (journalctl -u dispatch-plan-recheck) | NOWE (plik, okno 2h) |
  |---|---|---|
  | GC_COURIER_PLANS | 0 | **24** |
  | L3_REGEN | 0 | **21** |
  | L4_ANCHOR_FLOOR | 0 | 0 (rzadki; 22 all-time wcześniej) |
- **Test jednostkowy** `test_markers_from_file_not_journal_before_zero_after_real`:
  ta sama treść → journal-scan (stare) = 0, plik-scan (nowe) = 3 (z time-window; wiersz
  −180 min odrzucony).
- **E2E** `test_cmd_verify_reports_file_markers_end_to_end`: pełne `cmd_verify` z pustym
  journalem → `marker_hits=2` (z pliku), gdzie stara ścieżka dałaby 0.

### Szum err_burst (COORD_GUARD) — odfiltrowany
COORD_GUARD (`osrm_client`, Lekcja #140/#81) loguje na ERROR, gdy POPRAWNIE odrzuci
współrzędną (0,0)/None/poza-bbox → sentinel infeasible. To działająca obrona, nie awaria
silnika — nabijała err_burst i groziła fałszywym HOLD/rollback (~7-25 zdarzeń/2h tła).
`_count_err_burst(lines)` zwraca `(real_errs, benign_skipped)` pomijając wzorzec
`COORD_GUARD` (tylko ten jawnie znany — realne ERROR/Traceback nadal liczone). benign
liczony osobno (`coord_guard_benign` w logu verify) — sygnał nie zgubiony, tylko nie alarmuje.

**Bliźniak RAZEM:** ten sam licznik użyty też w `_gate()` (bramka flip, punkt „shadow
żywy", próg >20) — inaczej gate i verify kłamałyby RÓŻNIE na tym samym tle. Oba przez
`_count_err_burst`.

---

## ZADANIE 2 — źródło (0,0): ZDIAGNOZOWANE, guard ZOSTAWIONY (źródło niejednoznaczne)

### Diagnoza
COORD_GUARD w `dispatch-shadow` (PID 3531955 = `shadow_dispatcher`) pochodzi z
`route_simulator_v2.py:405  osrm_client.table(points, points)`. Log „table 2 invalid
coord(s) [(0.0,0.0),(0.0,0.0)]" = JEDEN zdegenerowany punkt liczony podwójnie (raz jako
origin, raz jako destination w `table(points, points)`). `points = [n["coords"] ...]` —
czyli któryś węzeł worka/nowego zlecenia ma `pickup_coords`/`delivery_coords` = placeholder
(0,0). Drugie źródło: `czasowka.log` = 100× COORD_GUARD (osobny serwis `dispatch-czasowka`).

(0,0) to **świadomy placeholder „brak współrzędnych"** ustawiany w WIELU miejscach
pipeline'u, gdy geokod zawiedzie / panel nie ma coords:
- `dispatch_pipeline.py:1622` `pickup_coords = order_event.get("pickup_coords") or (0.0, 0.0)`
- `dispatch_pipeline.py:3450/3452` `_repair_bag_coords(...) or ... or (0.0, 0.0)`
- `dispatch_pipeline.py:3928` `delivery_coords = tuple(... or (0.0, 0.0))`
- `czasowka_scheduler.py`, `obj_harness` i in.

COORD_GUARD w `osrm_client.table/route` (Lekcja #140) to **zaprojektowany chokepoint**:
(0,0) nie może cicho dać fikcyjnej trasy (~6285 km / snap do krawędzi), więc guard
zamienia go na jawny sentinel infeasible. Pipeline ma już rozbudowaną naprawę coords
(`_repair_bag_coords`, firmowe fallback coords, parser uwag) — (0,0) docierający do guarda
= przypadki, gdzie nawet naprawa nie miała z czego odtworzyć punktu (realny geokod-miss).

### Werdykt: NIE ruszam guarda
Źródło jest **niejednoznaczne** (konwencja „brak coords → (0,0)" w ≥5 miejscach + realny
geokod-miss danych), a „fix u źródła" = albo likwidacja tej konwencji w całym pipeline
(duża zmiana architektoniczna), albo poprawa pokrycia geokodowania (zadanie danych/ML) —
żadne nie jest lokalne ani bezpieczne. Zgodnie z regułą „nie zgaduj" (Przykazanie #0):
**guard zostaje bez zmian**, `osrm_client.py` NIETKNIĘTY. Fałszywy-alarmowy aspekt tła
COORD_GUARD rozwiązany w Zadaniu 1 (filtr err_burst), bez maskowania sygnału (liczony
osobno jako `coord_guard_benign`).

---

## Regresja
- Baseline (worktree, przed zmianą): **4389 passed, 23 failed, 27 skipped, 8 xfailed, 2 xpassed**.
- Po zmianie: **4394 passed, 23 failed** (baseline +5 nowych testów; 0 nowych faili).
- 23 faile = **artefakt worktree, NIE regresja**: `tests/test_courier_reliability.py` (8) i
  `tests/test_a2_selection_shadow.py` (15) mają `REPO = Path(__file__).resolve().parents[2]`
  + join `dispatch_v2/tools/...` — w worktree plik jest 2 poziomy głębiej
  (`.claude/worktrees/agent-XXX/tests/`), więc `parents[2]` trafia w `.claude/worktrees` →
  `MODULE_PATH` nie istnieje → własny `SkipTest` wychodzi jako FAILED. **Na kanonie te 23
  przechodzą (`23 passed`)** — potwierdzone. To dokładnie ostrzeżenie ADR-007 („nigdy
  hardcode ścieżki worktree"). Poza zakresem tego zadania (osobna higiena tych 2 plików:
  `parents[2]` → samo-lokalizacja przez env/`ZIOMEK_SCRIPTS_ROOT`).

Środowisko testów worktree: `ZIOMEK_SCRIPTS_ROOT=<pkgroot>` z symlinkiem
`pkgroot/dispatch_v2 → <worktree>` (⚠ generyczny `pkgroot` bywa nadpisywany przez
równoległą sesję — użyto UNIKALNEGO `pkgroot_a3c3` i zweryfikowano `readlink -f` do MY worktree).

---

## Co zostaje / rekomendacje
- **Merge/flip = decyzja właściciela fali** (agent higieny nie merguje). Zmiana dotyczy
  tylko narzędzia diagnostycznego `scheduled_flip_gate.py` — nie silnika, nie flag, nie
  serwisów. Ryzyko wdrożenia minimalne (przyrząd off-line, uruchamiany przez at-job).
- **Backstop na przyszłość:** jeśli kiedyś `dispatch-plan-recheck.service` przestanie
  redirectować do pliku (StandardOutput→journal), `cmd_verify` dalej zadziała (skanuje oba).
- **Tech-debt (osobne):** `test_courier_reliability.py` + `test_a2_selection_shadow.py`
  `parents[2]`-hardcode → naprawić na samo-lokalizację, żeby regresja była zielona także
  z worktree.
- **Guard (0,0):** jeśli kiedyś warto policzyć realną skalę geokod-miss, dołożyć do
  `_coord_guard_log` przy ścieżce `table` info o callerze (jak ma `haversine`) — to
  obserwowalność, nie zmiana zachowania. NIE w tym zadaniu.
