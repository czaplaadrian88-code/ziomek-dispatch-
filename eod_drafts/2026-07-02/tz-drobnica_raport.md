# Lane TZ-drobnica (FALA-2) — raport

**Branch:** `fix/tz-drobnica` · **HEAD:** `6670b54` (parent `60084fa`)
**Rola:** kod+testy, BEZ deployu/flipu/restartu/push. Data: 2026-07-02.

---

## Część 1 — fix `drive_speed_overshoot_verdict.py` → ZoneInfo (ZROBIONE)

### Co naprawione
`tools/drive_speed_overshoot_verdict.py:29`
`WARSAW = timezone(timedelta(hours=2))` → `WARSAW = ZoneInfo("Europe/Warsaw")`
(+ `from zoneinfo import ZoneInfo`, wzór FALA-1: fail-loud, ZERO fixed-offset fallbacku).

**Dlaczego to bomba:** tool liczy bias dostawy interpretując naiwny `delivered_at`
jako Warsaw wall-clock (`.replace(tzinfo=WARSAW)`), a potem odejmuje `delivery_pred_last`
sparsowany z offsetem UTC → **różnica NA GRANICY strefy**. Stały +2 po końcu DST
(25-26.10.2026, CET=+1) zawyża odjemną o 1h → bias fałszywie przesunięty o −60 min
(fałszywy „pesymizm" / mis-werdykt CLEAN↔ALARM).

### Kill-test (zima/lato) — z liczbami
Nowy plik `tests/test_drive_speed_overshoot_tz.py` (3 testy, behawioralne C13):

| Test | delivered_at (naive Warsaw) | pred (UTC) | ZoneInfo | mutacja +2 |
|---|---|---|---|---|
| ZIMA kill+mutation | `2026-12-15T10:30` (CET=09:30 UTC) | `09:30 UTC` | bias **0.0 min** | bias **−60.0 min** |
| LATO parytet | `2026-07-15T10:30` (CEST=08:30 UTC) | `08:30 UTC` | bias **0.0 min** | bias **0.0 min** (identyczne) |

MUTATION-CHECK: podmiana `WARSAW`→`FIXED2` zimą MUSI dać −60 (dowód że strażnik ma zęby).
Lato: ZoneInfo == FIXED2 == 0 (zmiana neutralna dziś). Wszystkie 3 zielone.

### Allowlista ratcheta TZ — przed/po
`tests/test_tz_zoneinfo_consolidation.py` `_ALLOWLIST` (pliki PRODUKCYJNE z fixed-offset):

| | zawartość | liczba |
|---|---|---|
| PRZED | `ontime_lib.py`, `drive_speed_overshoot_verdict.py` | **2** |
| PO | `ontime_lib.py` (poprawny wzór CET/CEST, para warsaw_tz_for) | **1** |

Allowlista SKURCZYŁA się (2→1) = dowód postępu. Jedyny pozostały wpis (`ontime_lib.py`)
to POPRAWNY DST-aware wzór, nie bug.

**Rozwiązanie kolizji strażnik-vs-ratchet:** mój nowy test-strażnik trzyma stały
offset jako BAZĘ MUTACJI (podmienia ZoneInfo→+2 by udowodnić fix). To NIE produkcyjny
fixed-offset. Zamiast dopisywać go do produkcyjnej allowlisty (która ma tylko się
kurczyć), wprowadziłem osobną kategorię `_GUARDIAN_TESTS = {consolidation-test,
drive_speed-tz-test}` wyłączaną ze skanu — tak samo jak dotąd wyłączony był `_SELF`.
Architektonicznie czyste: testy-strażnicy MUSZĄ mieć buggy stałą do mutacji.

### Resztki w allowliście / skanie ratcheta
Po skanie `_scan_fixed_offset()`: jedyny offender = `tools/ontime_lib.py` (= allowlista,
poprawny wzór). `EXTRA (offenders − allowlist) = []`. **Brak resztek** spoza rdzenia /
spoza partycji innych pasów do zgłoszenia w tym skanie.

⚠ **Blind-spot ratcheta (do zgłoszenia, NIE moja partycja):** skan chodzi po
`_WT_ROOT` = repo dispatch_v2. Pliki grafiku (`schedule_utils.py`, `fetch_schedule.py`)
leżą w `scripts/` = O POZIOM WYŻEJ, poza repo → ratchet ICH NIE WIDZI. Mają fixed-offset
fallback (`schedule_utils.py:24 timezone(timedelta(hours=2))` w `except ImportError`) —
latentne (zoneinfo=stdlib, ImportError nie padnie), ale poza zasięgiem strażnika.
Następna iteracja: rozszerzyć ratchet o skan `scripts/` albo osobny strażnik dla tego repo.

---

## Część 2 — grafik-recon (finding H) — WERDYKT: OBA bugi = SERIAL

### Lokalizacja (dokładna)
Ładowanie grafiku i wybór dnia dzieje się w **`/root/.openclaw/workspace/scripts/fetch_schedule.py`**
(cron 06:00/08:00 Warsaw + T3 hot-refresh co 10 min przez subprocess z `schedule_utils.load_schedule`).

**Bug H1 — dzień grafiku liczony w UTC:** `fetch_schedule.py:130`
```python
today = datetime.now().strftime("%d-%m-%y")   # serwer=UTC (potwierdzone: timedatectl → Etc/UTC)
```
Warsaw wyprzedza UTC (+1 zima / +2 lato). W oknie **Warsaw 00:00-02:00** UTC to jeszcze
POPRZEDNI dzień (np. lato Warsaw 00:30 = UTC 22:30 dnia poprzedniego) → `today` = wczoraj →
`find_date_columns`/`parse_schedule` wybierają KOLUMNY WCZORAJSZEGO grafiku → cała flota
dostaje zły shift. Zapis nadpisuje `schedule_today.json` (`"date": today` też wczoraj).
To dokładnie objaw z MASTER_synteza wiersz H.

**Bug H1b (pochodna, drobna):** `schedule_utils.py:157` też liczy `today =
datetime.now().strftime(...)` (UTC) — ale TYLKO do warning-loga porównującego `data["date"]`,
nie przełącza grafiku. Plik MA już `_now_warsaw()` (linie 21-26), którego tu nie użyto —
niespójność. Fix trywialny (użyć `_now_warsaw()`), ale wciąż plik poza repo.

**Bug H2 — literówka godziny kasuje CAŁY wpis kuriera:** `fetch_schedule.py:121`
```python
schedule[name] = {"start": start_fmt, "end": end_fmt} if (start_fmt and end_fmt) else None
```
`parse_hour` (l.42) zwraca `None` na każdej nieparsowalnej komórce (literówka: „1O:00" z
literą O, „9;00", stray char). Jeśli PADNIE choć jedna z dwóch (start LUB end) → cały wpis
kuriera = `None` → traktowany jako NIE-w-grafiku → wypada z puli → feasibility odrzuca
(`v325_NO_ACTIVE_SHIFT`). **Jedna literówka w jednej komórce usuwa pracującego kuriera z floty.**

### Klasyfikacja partycji
Oba pliki (`fetch_schedule.py`, `schedule_utils.py`) są w **`scripts/`, POZA repo dispatch_v2**
(`git ls-files` = 0 trafień; `git rev-parse --show-toplevel` = worktree dispatch_v2). **Nie da
się ich commitnąć z tego worktree.** Dlatego:

- **H1 (today UTC)** — czysta poprawność (WOLNO by mi było gdyby był w moim repo), ale
  fizycznie poza repo → **SERIAL** (fala serial aplikuje w repo/lokalizacji `scripts/`).
- **H2 (parse_hour coupling)** — **ZMIENIA PULĘ KURIERÓW = decyzyjne** → z definicji zadania
  **DESIGN ONLY / SERIAL za ACK**, niezależnie od repo.

`fetch_schedule.py` ma **ZERO testów** (grep w tests/ = puste) — trzeba je dopisać przy fixie.

### Gotowy design fixu (dla fali SERIAL)

**H1 — `fetch_schedule.py`:**
```python
# nagłówek: dodać obok `from datetime import datetime`
from zoneinfo import ZoneInfo
_WARSAW = ZoneInfo("Europe/Warsaw")
# l.130:
today = datetime.now(_WARSAW).strftime("%d-%m-%y")   # Warsaw wall-clock, nie UTC
# l.159 fetched_at: zostawić UTC/aware wg konwencji (nie miesza z wyborem dnia)
```
+ w `schedule_utils.py:157` użyć istniejącego `_now_warsaw().strftime("%d-%m-%y")`.
**Testy behawioralne (nowy plik dla fetch_schedule):** zamrozić `datetime.now` na
`2026-07-14T23:30:00Z` (=Warsaw 15.07 01:30 lato) → `today` MUSI = `15-07-26`, nie `14-07-26`;
mutacja (UTC `datetime.now()`) daje `14-07-26` (kill). Analog zimowy `2026-12-14T23:30Z`
(Warsaw 00:30 CET) → `15-12-26`. Czysta poprawność, offset-neutralna w dzień → BEZ flagi.

**H2 — `fetch_schedule.py:121` (DECYZYJNE, za flagą + ACK):**
Zamiast kasować cały wpis na jednym `None`, DEGRADUJ miękko / waliduj z widocznością:
```python
# szkic (za flagą ENABLE_SCHEDULE_PARTIAL_HOUR_SALVAGE, default OFF):
if start_fmt and end_fmt:
    schedule[name] = {"start": start_fmt, "end": end_fmt}
elif (start_fmt or end_fmt) and FLAG_ON:
    # literówka w JEDNEJ komórce: zachowaj wpis z sygnałem, NIE kasuj kuriera z puli.
    schedule[name] = {"start": start_fmt, "end": end_fmt, "parse_degraded": True}
    log(f"UWAGA parse_hour degraded {name}: start={row[col_start]!r} end={row[col_end]!r}")
else:
    schedule[name] = None
```
Konsument (feasibility/is_on_shift) musi umieć obsłużyć `parse_degraded` (np. fallback okno
albo KOORD zamiast twardego odrzutu) — to część decyzji. **Test ON≠OFF:** wpis z jedną
zepsutą komórką → OFF: kurier znika z puli (`None`); ON: kurier zostaje z `parse_degraded`.
**Plan replay:** policzyć na historii ile wpisów/dzień pada na literówce (ilu kurierów tracimy),
okno 2 dni, dowód pozytywnego wpływu (mniej fałszywych `NO_ACTIVE_SHIFT`) PRZED flipem.
⚠ Nie implementować bez ACK — dotyka puli kurierów i konsumenta `is_on_shift`.

---

## Regresja (pełna, worktree)
Baseline zadania (KANON 12:25 UTC): **3907 passed / 0 failed / 23 skipped / 11 xfailed**.

W worktree path-layout `test_courier_reliability.py` liczy `MODULE_PATH = REPO/"dispatch_v2"/…`
zakładając kanoniczny układ `scripts/dispatch_v2/`; w worktree `wt-tz-drobnica` rozwiązuje się
do nieistniejącego `/root/.openclaw/workspace/dispatch_v2/…` → `unittest.SkipTest` liczone jako
FAILED. To **artefakt worktree, niezależny od TZ** (na KANON te 23 przechodzą → stąd baseline 0).

Dowód że NIE wniosłem regresji (ten sam worktree, apples-to-apples):

| commit | passed | failed | skipped | xfailed |
|---|---|---|---|---|
| parent `60084fa` | 3884 | 23 (courier_reliability path-artifact) | 23 | 11 |
| mój `6670b54` | **3887** | **23** (te same) | 23 | 11 |

Δ = **+3 passed** (moje 3 testy tz), **0 nowych failów**. Wszystkie 23 faile = jeden plik,
jeden root-cause (SkipTest path), obecne przed moją zmianą.

## Pliki
- `tools/drive_speed_overshoot_verdict.py` — WARSAW→ZoneInfo (+import)
- `tests/test_tz_zoneinfo_consolidation.py` — allowlista 2→1 + kategoria `_GUARDIAN_TESTS`
- `tests/test_drive_speed_overshoot_tz.py` — NOWY, 3 testy kill/parytet
- `eod_drafts/2026-07-02/tz-drobnica_raport.md` — ten raport

## Rollback
`git revert 6670b54` (albo `git checkout 60084fa -- tools/drive_speed_overshoot_verdict.py
tests/test_tz_zoneinfo_consolidation.py && git rm tests/test_drive_speed_overshoot_tz.py`).
Zero efektu runtime (tool read-only, uruchamiany ręcznie/at-jobem; zmiana czysto poprawnościowa).

## Ryzyka
- Żadne runtime: `drive_speed_overshoot_verdict.py` to read-only werdykt (obecnie i tak N/A,
  flaga OFF). Zmiana wpływa tylko na poprawność bias PO 25.10 (DST).
- 23 „failed" w worktree to szum path-layout — po merge do KANON znikają (baseline 0 potwierdza).
- Grafik (H1/H2) NIE dotknięty kodem — czeka na fala SERIAL (H1 poprawność bez ACK w repo
  scripts/; H2 za flagą+ACK, bo zmienia pulę kurierów).
