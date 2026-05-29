# HANDOFF SESJI — Audyt Ziomka V3.28 + quick-winy

**Data:** 2026-05-29
**Typ sesji:** READ-ONLY audyt jakości (zero zmian w kodzie produkcyjnym tej sesji)
**Plik pełnego audytu:** `/root/.openclaw/workspace/AUDIT_ZIOMEK_2026-05-28.md` (349 linii)
**Po co ten plik:** żeby następna sesja wystartowała od razu, bez ponownego grzebania.

---

## 1. O CO CHODZIŁO (cel audytu)

Adrian chciał przejrzeć całego Ziomka (rule-based dispatcher dla kurierki NadajeSz
Białystok, wersja V3.28) pod kątem trzech rzeczy:

1. **Lepszy bundling** (łączenie zleceń w trasy).
2. **Lepszy dispatch** (komu i co przydzielać).
3. **ZERO cichych awarii** — najważniejsze. Był incydent: Z3 (jedna z usług) leżała
   12 godzin i nikt nie dostał alertu. To „nie może się powtórzyć".

Audyt był READ-ONLY: czytanie kodu, szukanie problemów, spisanie findingów z oceną
ważności (CRITICAL / HIGH / MED / LOW). Nic w produkcji nie ruszaliśmy bez zgody.

---

## 2. CO JUŻ NAPRAWIONE I DZIAŁA NA ŻYWO (LIVE + wypchnięte do gita)

To zostało zrobione we wcześniejszych sesjach tego audytu, jest przetestowane,
wdrożone i wypchnięte. Tu tylko dla kompletności obrazu.

### E1 — Liveness probe (najważniejszy fix, CRITICAL ZAMKNIĘTY)
- **Co to:** osobna sonda, która co 2 minuty sprawdza, czy 5 kluczowych usług żyje.
  Jak coś leży — leci alert na Telegram. To bezpośrednia odpowiedź na incydent „12h
  ciszy".
- **Ważne:** sonda TYLKO wykrywa i alarmuje. NIGDY sama nie restartuje usług
  (świadomy wybór — restart bez człowieka to ryzyko).
- **Usługi pod nadzorem:** dispatch-panel-watcher, dispatch-shadow, dispatch-telegram,
  dispatch-sla-tracker, dispatch-gps.
- **Commit:** `0952238` | **Tag:** `liveness-probe-e1-2026-05-28`
- **systemd:** `dispatch-liveness-probe.timer` (co 2 min).

### E3b — Domknięcie fałszywych alertów + uzbrojenie cross-checku (3 commity)
- `16be755` — bramka na `worker_stuck`/`worker_slow`: alarmuj tylko gdy faktycznie
  były nowe zlecenia w ostatniej godzinie (`new_orders_1h > 0`). Bez tego w nocy przy
  zerowym ruchu leciały fałszywe „worker zamarł".
- `6be20b7` — deduplikacja alertów po `order_id` (DISTINCT). Jeden fantomowy alert
  potrafił się zliczyć 8× i sztucznie przebić próg `manual_alerts > 5` → fałszywy
  status `degraded`.
- `d798815` — uzbrojenie uśpionego cross-checku downstream na porcie `:8888`
  (`dispatch-downstream-crosscheck.timer`, co 5 min). Teraz realnie sprawdza serwer
  i wysyła Telegram.
- **Tagi:** `parser-health-e3b-gate-2026-05-28`, `reconcile-dedupe-order-id-2026-05-28`,
  `arm-downstream-crosscheck-2026-05-28`.

> Stan gita na koniec sesji: `master == origin/master`, wszystko powyższe wypchnięte.

---

## 3. CO ZWERYFIKOWAŁEM DZIŚ (potwierdzone w żywym kodzie, diffy gotowe, czekają na ACK)

To są trzy „quick-winy" — małe, bezpieczne poprawki (każda < 30 linii). Przeczytałem
realny kod, potwierdziłem że problem istnieje, i mam gotowy plan poprawki. NIC z tego
nie zostało jeszcze zmienione — czekają na Twoje „rób".

### B1 — Plik `rule_weights.json` KŁAMIE w komentarzu
- **Plik (POPRAWNA ścieżka):** `/root/.openclaw/workspace/dispatch_state/rule_weights.json`
  - ⚠️ Uwaga: audyt podał błędną ścieżkę (`dispatch_v2/dispatch_state/...`). Realna
    ścieżka to **workspace root**, nie podkatalog dispatch_v2. Wyszło to z kodu w
    `dispatch_pipeline.py:2353`. (Z2 trust-but-verify się opłacił.)
- **Problem:** w pliku jest komentarz „Aktualizowane przez learning_analyzer", a
  **żaden kod tego pliku nie zapisuje**. Wartości są strojone ręcznie. Komentarz
  wprowadza w błąd następną osobę (pomyśli, że to się samo uczy).
- **Fix (B1-b):** zamienić kłamliwy komentarz na uczciwy:
  „STATIC — strojone ręcznie, ostatnia zmiana 2026-04-16". To wszystko. Bez pisania
  writera (patrz trade-off niżej — to świadoma decyzja, nie zaniedbanie).
- **Ważne:** plik jest STATE (poza repo, poza gitem). Edycja state = wymaga ACK.

### G5 — Podwójny shadow-log fałszuje kalibrację drive_min
- **Plik:** `auto_proximity_classifier.py`
- **Łańcuch (potwierdzony greppem):** `_append_drive_min_calibration_shadow` (def 195,
  wołane 282) siedzi w `_maybe_apply_drive_min_calibration` (def 216) →
  wołane @334 wewnątrz `_build_context` (def 287) → a `_build_context` jest wołane
  z DWÓCH miejsc: `classify_auto_route` (565... właściwie 507) i
  `build_context_for_logging` (565). Efekt: **każde zlecenie loguje kalibrację 2×.**
- **Skutek:** kalibracja drive_min dostaje zdublowane dane → liczby są przekłamane →
  to z kolei karmi G3 (decyzja o autoryzacji tras AUTO). Czyli błąd propaguje się dalej.
- **Korekta audytu:** audyt mówił „append wprost w `_build_context` linia 282". To
  skrót — append jest faktycznie w helperze `_maybe_apply_drive_min_calibration`
  wołanym z `_build_context`. Rekomendacja taka sama: wynieść I/O kalibracji z
  `_build_context` do JEDNEGO jawnego miejsca (tylko ścieżka realnej klasyfikacji,
  nie ścieżka logowania).

### B2 — Cichy fallback przy wczytywaniu rule_weights + hardcoded ścieżka
- **Plik:** `dispatch_pipeline.py:2350-2357`
- **Problem 1:** jak plik się nie wczyta (`except Exception`), kod cicho leci dalej z
  pustym `{}`. To znaczy: wszystkie kary R1/R5/R8 znikają, a NIKT się nie dowie. To
  dokładnie ten typ cichej awarii, który mamy tępić.
- **Problem 2:** ścieżka zaszyta na sztywno w kodzie.
- **Problem 3:** plik czytany per-kandydat (przy każdym zleceniu od nowa) — niepotrzebne I/O.
- **Fix:** cache na poziomie modułu z porównaniem `mtime` (czytaj raz, przeładuj tylko
  gdy plik się zmienił) + GŁOŚNY log błędu zamiast cichego `{}`.

**Kolejność robienia quick-winów:** B1-b → G5 → B2 (wszystkie wymagają ACK; B1 i tak
dotyka state).

---

## 4. CO ZOSTAŁO DO PRZEMYŚLENIA — trade-offy (moje rekomendacje)

To nie są bugi „napraw-i-zapomnij", tylko decyzje projektowe. Dla każdej dałem
rekomendację, żebyś nie musiał wybierać z listy — tylko zaakceptować albo odrzucić.

| # | Temat | Moja rekomendacja |
|---|---|---|
| **B1** | Czy pisać auto-writer wag reguł? | **NIE teraz.** Tylko uczciwy komentarz. Powód: kalibracja Sprint 1 PRZESTRZELA gps o ~30 min — auto-writer wpisywałby złe liczby. Najpierw napraw kalibrację, potem ewentualnie writer. |
| **B3** | Twarda kara bezpieczeństwa (sentinel -1000 @ 60 min) | **Zostaw HARD safety**, ale zamień skok -1000 na stromy gradient z górnym limitem. Powód: skok robi „klif" w score, gradient jest stabilniejszy a nadal odstrasza. |
| **C2** | Mocno ujemny gap (duża rezerwa czasowa) | **Dodaj decay/cap.** Bez limitu jeden bardzo wczesny pickup dominuje cały score. |
| **D2** | Nieświeży grafik (stale schedule) | **Soft-penalty + GŁOŚNY alert** gdy grafik się zestarzał. Nie blokuj twardo, ale krzycz. |
| **G3 / flip AUTO na authoritative** | Czy przełączyć AUTO-proximity w tryb wiążący? | **NIE flipować jeszcze.** Bramka przed flipem: (1) fix G5, (2) metryka precyzji per-AUTO (G1), (3) progi liczone z logu po filtrze override, próg ≥95%. |
| **H2** | Wystawić `/metrics` (Prometheus) dla Ziomka | **TAK, ale później** — po E i po G1/G2. Podpiąć pod istniejący stack `papu-observability`. |

---

## 5. STRUKTURA POZOSTAŁYCH FINDINGÓW (z audytu, jeszcze nietknięte)

Pełne opisy w `AUDIT_ZIOMEK_2026-05-28.md`. Skrót severity:

- **HIGH (poza zrobionym E1):** B1 (komentarz — quick-win wyżej), E2, E4, G1, G2.
- **MED:** A1, B2 (quick-win wyżej), B3, C1, C2, D2, F2, G3, H2.
- **LOW:** reszta.
- **G1/G2** — metryki jakości AUTO-proximity (precyzja per-trasa). To warunek wstępny
  do jakiejkolwiek decyzji o flipie G3.
- **C3 / H2** — obserwowalność (metryki, /metrics endpoint).

---

## 6. STAN SYSTEMU NA KONIEC SESJI

- **Repo:** `/root/.openclaw/workspace/scripts/dispatch_v2`
- **Git:** `master == origin/master` (czysto, nic nie wisi w ahead/behind poza tym
  handoffem po commicie).
- **Zmiany kodu w tej sesji:** ZERO (czysty audyt).
- **Usługi LIVE:** dispatch-panel-watcher (+ wątek health :8888), dispatch-shadow,
  dispatch-telegram, dispatch-sla-tracker, dispatch-gps,
  dispatch-liveness-probe.timer (co 2 min), dispatch-downstream-crosscheck.timer (co 5 min).
- **Untracked w repo:** dużo starych `eod_drafts/*` (POC-i, logi). Dlatego commitujemy
  TYLKO ten plik handoff przez `git add <konkretna ścieżka>`, NIGDY `git add -A`.

---

## 7. JAK ZACZĄĆ NOWĄ SESJĘ (czytaj w tej kolejności)

1. **Ten plik** — masz pełen obraz w 2 minuty.
2. `/root/.openclaw/workspace/AUDIT_ZIOMEK_2026-05-28.md` — pełne opisy findingów.
3. `memory/sprint_timeline.md` → sekcja `## CURRENT HANDOFF` na górze.
4. `memory/tech_debt_backlog.md` → otwarte findingi audytu + 3 quick-winy.

**Pierwszy ruch w nowej sesji (jak Adrian da ACK):** quick-winy w kolejności
**B1-b → G5 → B2**. Każdy z osobna: draft → ACK → `.bak` → edit → `py_compile` →
import check → test → commit → tag → (restart tylko jeśli trzeba + ACK) → verify → stop.

**Twarde zasady (bez zmian):**
- Pytaj, nie zgaduj. Cokolwiek dotyka STATE/PRODUKCJI → STOP i pytaj o ACK.
- NIE restartuj systemd bez `py_compile` + import check + ACK.
- NIE restartuj `dispatch-telegram` bez WYRAŹNEGO ACK.
- Atomowe zapisy (temp + fsync + rename). Granularne tagi gita jako punkty rollbacku.
- Routing: DECISION SELF vs AIDER. Kod / refactor / testy / cokolwiek > 30 linii → AIDER
  (`deepseek/deepseek-coder`, NIGDY `deepseek-chat`). Logika / architektura / debug → SELF.
