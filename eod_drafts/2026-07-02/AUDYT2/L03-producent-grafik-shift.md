# AUDYT 2.0 — Pas L03: Producent grafiku (Google Sheets → shift_start/shift_end)

**Data:** 2026-07-01 (wykonane ~22:30 UTC / 00:30 Warsaw)
**Tryb:** READ-ONLY na produkcji. Zero edycji kodu/flag/serwisów. Jedyny zapis = ten plik.
**Pas:** PAS 0.E — loader grafiku = PRODUCENT kotwicy `pre_shift`/`shift_end`, konsumowanej przez feasibility (Gate 1/2/3), dispatchable_fleet, pre_shift synthetic, shift-notify worker, pickup_floor_guard, plan_recheck. **Producent nigdy nie sweepowany** (potwierdzone).

## Mapa producenta (co gdzie żyje)

| Warstwa | Plik:linia | Rola |
|---|---|---|
| Fetcher (Sheets→JSON) | `scripts/fetch_schedule.py` (167 l.) | cron 06:00+08:00 UTC + hot-refresh; pisze `dispatch_state/schedule_today.json` |
| Loader + cache TTL | `scripts/schedule_utils.py:112 load_schedule` | TTL 10 min, lazy `_trigger_fetch` subprocess, fail-OPEN na stale |
| Parser godzin | `scripts/fetch_schedule.py:42 parse_hour` | "HH:MM"/"HH" → norm; kropka/przecinek/tekst → None |
| Coupling entry | `fetch_schedule.py:121` | `entry = {start,end} if (start AND end) else None` |
| is_on_shift | `schedule_utils.py:374` | fail-OPEN w 3 gałęziach |
| shift_start/end dt | `courier_resolver.py:1276/1293 _shift_start_dt/_shift_end_dt` | naiwne „dziś-HH:MM" Warsaw |
| Konsument HARD | `feasibility_v2.py:679/752` Gate 1 + Gate 3 | floor gatowany `if shift_start is not None` |
| Monitor (jedyny) | `shift_notifications/worker.py:720` STALE_SCHEDULE_AGE | tylko mtime>30min |

**Stan danych (live):** `schedule_today.json` date=`01-07-26`, fetched_at=`22:15:20 UTC`, 12 pracuje / 52. Hot-refresh działa (mtime 22:15 >> cron 08:00). Fetch failów: **0 w ~30 dniach** cron logu (2×/d).

---

## USTALENIA

### [P1] SPOF: pusty grafik → cicha, cało-flotowa utrata egzekucji zmian (fail-open, zero strażnika)
**Powierzchnia:** `schedule_utils.py:134-135` + `:375-376` (is_on_shift) + `courier_resolver.py:1562` + `feasibility_v2.py:690`
**Dowód (ground-truth lektura + empiryczny):**
- `load_schedule` gdy plik znika **i** fetch pada → `return {}` (l.134-135).
- `is_on_shift(name, {})` → `(True, "brak grafiku")` — zweryfikowane uruchomieniem: fail-OPEN dla **każdego**.
- W żywej ścieżce `dispatchable_fleet` pusty `schedule` powoduje, że blok `if schedule and cs.name:` (l.1562) jest POMINIĘTY → `cs.shift_start`/`cs.shift_end` **nigdy nie ustawione (None)** → w feasibility Gate 1 `shift_end is None`; przy `ENABLE_FAIL12_SCHEDULE_FAILOPEN=True` (LIVE, flags.json) kurierzy z bagiem/świeżym GPS przechodzą fail-OPEN **bez pre-shift floor i bez post-shift cap** (Gate 3 gatowany `if shift_start is not None`, l.752 — cicho pominięty).
**Materialność:** SPOF nieobserwowany w 7 dniach (0× „return empty schedule", 0× „date != today" w journald). Wymaga PODWÓJNEJ awarii (plik zniknął + Sheets down) — niskie p-stwo (fetcher NIGDY nie pisze pustego/częściowego pliku: `return 1` przed zapisem l.136/146). ALE: brak jakiegokolwiek alertu na „schedule pusty" / „0 kurierów zmatchowano"; jedyny monitor to mtime-age (patrz P2-sweep). Realność objawu „24/7 cała flota on-shift" jest MIESZANA: czyste `return True` bije głównie martwe ścieżki legacy (gastro_*, patrz INFO); żywa ścieżka degraduje do mieszanego fail-open/close przez FAIL12.
**Rekomendacja:** (1) w `dispatchable_fleet` rozróżnić „schedule=={}" (awaria producenta) od „schedule bez tego kuriera" i przy awarii **alertować głośno** zamiast cichego shift_end=None; (2) dodać strażnika „pool_matched==0 przy pool_panel>0" → Telegram; (3) rozważyć fail-CLOSE `is_on_shift` na pustym grafiku TYLKO jeśli równolegle jest alert (inaczej wraca #471036 BRAK KANDYDATÓW).

### [P2] Producent stempluje datę w UTC, konsumenci liczą godziny w Warsaw → co noc 00:00-02:00 Warsaw ładowany grafik ZŁEGO dnia
**Powierzchnia:** `fetch_schedule.py:130` i `:158` (`datetime.now()` naiwne) vs `schedule_utils.py:379 _now_warsaw()` / `courier_resolver.py:1286` (ZoneInfo Warsaw)
**Dowód (ground-truth, potwierdzone LIVE teraz):** serwer TZ = `Etc/UTC`. O 22:30 UTC = 00:30 Warsaw: `datetime.now().strftime('%d-%m-%y')` = `01-07-26`, a Warsaw = `02-07-26` → **DATE MISMATCH = True w tej chwili**. `find_date_columns` szuka kolumny wczorajszego (wg Warszawy) dnia; plik dostaje `date=01-07-26` mimo że w Warszawie trwa 02-07. Okno: **22:00-24:00 UTC = 00:00-02:00 Warsaw (lato)**. `pickup_floor_guard.py:61-64` już dokumentuje pochodną: naiwny `_shift_start_dt` „nocna zmiana zawija" przy |now−shift_start|>12h.
**Materialność:** deterministyczne, CO NOC, okno 2h; wolumen zleceń 00:00-02:00 niski (weekendowo-skośny) — nie policzone w zł/dzień, ale objaw pewny (nie hipoteza). T3 hot-refresh ZWĘZIŁ historyczny root „00:00-06:00 cała flota None" do dzisiejszego „00:00-02:00 zły dzień" (po 00:00 UTC datetime.now()=nowy dzień → kolumna poprawna; cron 06:00 UTC domyka rano).
**Rekomendacja:** w `fetch_schedule.py` liczyć `today` z `datetime.now(ZoneInfo("Europe/Warsaw"))` (nie naiwnie); analogicznie porównanie daty w `load_schedule.py:157`. Jednorazowa, izolowana zmiana producenta.

### [P2] Literówka w arkuszu (kropka/przecinek/tekst) → cały wpis kuriera = None → kurier znika z puli LUB traci floor/cap; CICHO, bez walidacji
**Powierzchnia:** `fetch_schedule.py:42 parse_hour` + `:121` (coupling) → `courier_resolver.py:1570` (continue) / `feasibility_v2.py:752`
**Dowód (empiryczny, uruchomione):** `parse_hour('11.00')=None`, `('11,00')=None`, `('do 19')=None`, `('')=None`; „8:00-16:00" w jednej komórce → **`'08:00-16'` (śmieć)** → później `strptime`/`split(':')` → None. Coupling l.121: start-typo `11.00` + poprawny end `19:00` → **entry = None** (cały wpis skasowany). `is_on_shift` na wpisie z niesparsowalnymi godzinami → `(True,'błąd parsowania godzin')` — fail-OPEN (zweryfikowane). W żywej ścieżce: entry None → `dispatchable_fleet:1570 continue` → kurier **wykluczony z puli** (tylko `_log.debug "nie pracuje dziś"`), grozi BRAK KANDYDATÓW jego zleceniom; jeśli mimo to trafi do feasibility (brak nazwy / override) — shift_start/end=None → floor/cap milcząco nieobecne.
**Materialność:** nie policzone — istnienie klasy błędu potwierdzone. Proxy: dzienne ratio `Pracuje N/M` stabilne (10-13), 06:00==08:00 co dzień; dzisiejszy fetch sparsował 12/12 bez śmieci → brak AKTUALNEJ literówki. Producent NIE odróżnia „kurier wolny dziś" od „kurier z niesparsowaną godziną" (oba → None; log `Pracuje dziś N/M` to zlewa).
**Rekomendacja:** w `parse_hour`/`parse_schedule` liczyć i logować `parse_failures` osobno (nazwa + surowa komórka) oraz alertować gdy >0; rozważyć fail-loud gdy kurier ma JEDNĄ godzinę wypełnioną a drugą None (silny sygnał literówki vs pusty slot).

### [P2] Brak sweepu/strażnika producenta — jedyny monitor (STALE_SCHEDULE_AGE) jest ślepy na content-staleness, pusty roster i zero-match
**Powierzchnia:** `shift_notifications/worker.py:720` (jedyny), `schedule_utils.py:203 is_schedule_stale` (tylko mtime)
**Dowód:** `grep` po strażnikach: żaden systemd timer nie sweepuje `schedule_today.json`; `is_schedule_stale`/`schedule_age_sec` opierają się WYŁĄCZNIE o `SCHEDULE_FILE.stat().st_mtime`. Dziura: jeśli Sheets zwróci stary-ale-parsowalny CSV (znaleziona STARA kolumna daty), fetcher ZAPISZE plik → mtime=teraz → `age`=0 → alert STALE **nigdy nie odpali** mimo semantycznej nieaktualności. Brak alertu na: schedule=={}, 0 pracujących, `date != Warsaw-today` (l.157 tylko `_log.warning`, bez Telegramu i bez odrzucenia danych), spadek liczby kurierów dzień-do-dnia, liczbę parse-failów.
**Materialność:** architektoniczne (istnienie luki), nie policzone. Zgodne z tezą pasa „producent nigdy nie sweepowany".
**Rekomendacja:** osobny read-only guard (wzór `pickup_floor_guard.py`): waliduje świeżo zapisany `schedule_today.json` — `date==Warsaw-today`, `couriers` niepuste, `working>0` w oknie operacyjnym, `parse_failures==0`, delta liczby vs wczoraj — i alertuje przez istniejący kanał onfailure.

### [P3] MATCH_NOT_FOUND 21k/dzień + MATCH_AMBIGUOUS 1,8k/dzień — spam logu (25 MB/plik) + ryzyko cichego wykluczenia realnie pracującego
**Powierzchnia:** `schedule_utils.py:283 match_courier_strict` → `courier_match_debug.jsonl`
**Dowód (ground-truth count):** dziś (01-07): **MATCH_NOT_FOUND 21 396**, **MATCH_AMBIGUOUS 1 791**; ~12 różnych inputów po ~1780×/dzień (1 na tick), m.in. „Marcin Bystrowski", „Michał Rogucki", „Gabriel Jedynak", „Dawid Kr", „Antoni Tr". Większość = kurierzy zalogowani w apce ale NIE w dzisiejszym grafiku (12 pracujących ≠ te nazwy) → poprawnie wykluczeni (docstring l.298 celowo zwraca None dla „Marcin Bystrowski"→„Marcin Puszko" B≠P). `courier_match_debug.jsonl` = 25 MB, rośnie.
**Materialność:** ~25 MB/dzień jeden plik (dysk/log-hygiene); ryzyko: gdyby wśród 21k był kurier REALNIE pracujący a nazwą niezgodny z prefiksem grafiku — `dispatchable_fleet:1564 continue` cicho go wyklucza (fail-CLOSE, brak propozycji). Nie stwierdzono takiego przypadku w tej sesji.
**Rekomendacja:** rate-limit/dedup zapisu (1 wpis per (input, dzień) zamiast per-tick); okresowy alert gdy input z aktywnym bagiem/GPS trafia w MATCH_NOT_FOUND (odróżnia „niezmapowany pracujący" od „niepracujący zalogowany").

### [INFO] Martwi bliźniacy is_on_shift (gastro_scoring.py / gastro_koordynator.py) — ta sama gałąź fail-open, brak serwisu
**Powierzchnia:** `scripts/gastro_scoring.py:599/656`, `scripts/gastro_koordynator.py:237`
**Dowód:** oba importują `is_on_shift` i konsumują tę samą logikę fail-open; mtime `2026-04-10`, brak systemd unit, brak żywego procesu. Nie live → zero wpływu dziś, ale istotne dla kompletności „bliźniaczych ścieżek" gdyby kiedyś wskrzeszone.
**Rekomendacja:** oznaczyć jako deprecated/usunąć, by nie stały się rozbieżną kopią konsumenta grafiku.

### [INFO] Per-proces niezależne cache + oneshot worker → redundantne/agresywne fetch Sheets
**Powierzchnia:** `schedule_utils.py:48-51` (`_cached_*`, `_last_fetch_attempt_ts` per-proces) + oneshot `shift_notifications` worker
**Dowód (lektura):** każdy long-running serwis (shadow, panel-watcher, czasowka) ma własny cache i własny debounce (30 s) → 4+ procesy niezależnie fetchują co 10 min. Worker shift-notify jest oneshot (świeży proces/min) → `_last_fetch_attempt_ts=0` za każdym razem → gdy plik >10 min stale, KAŻDA minuta może odpalić subprocess fetch (debounce nie przenosi się między procesami). Ryzyko „młócenia" Sheets przy trwale stale pliku.
**Materialność:** normalnie plik świeży (shadow odświeża) → uśpione; hipoteza z lektury, nie zmierzone.
**Rekomendacja:** wspólny lock/marker na dysku dla `_trigger_fetch` (cross-process debounce), by wiele procesów nie fetchowało równolegle.

---

## Odpowiedzi na pytania pasa
- **(a) TTL/fail-policy:** TTL=10 min (`SCHEDULE_TTL_MIN`, env). Fail-policy = **fail-OPEN** wszędzie: stale cache przy fetch-fail (l.151), poprzedni cache przy JSONDecodeError (l.168), `is_on_shift` → True na pustym/typo/no-match. Sheets down/quota: `urlopen` timeout 20 s → `except → return 1` BEZ zapisu → plik nietknięty → serwuje stary (fail-open). **Empirycznie 0 failów/~30 dni.**
- **(b) Północ:** POTWIERDZONE LIVE — producent datuje w UTC, konsumenci w Warsaw → 00:00-02:00 Warsaw zły dzień (P2). „24:00" obsłużone (`schedule_utils.py:396`, `_shift_end_dt:1302`).
- **(c) Literówka:** POTWIERDZONE — kropka/przecinek/tekst → parse_hour None → coupling → cały entry None → kurier wykluczony/floor martwy, cicho, bez walidacji (P2).
- **(d) Staleness:** mtime-age STALE alert (>30 min) istnieje w shift-notify worker, ale ŚLEPY na content-staleness (świeży mtime + stara/zła data) i na pusty roster/zero-match (P2). „Nieodświeżony arkusz na jutro" łapany tylko przez `_log.warning` bez alertu (l.159).
- **SPOF realność:** REALNY architektonicznie (jeden plik, jeden fetcher, zero walidacji outputu, fail-open), ale najgroźniejszy wariant (pusty grafik → cała flota) wymaga podwójnej awarii i jest nieobserwowany; codzienny realny defekt to midnight-anchor (2h/noc) i klasa literówek (cicha). Materialny bieżący sygnał luk producenta = **FAIL12_SCHEDULE_FAILOPEN 31×/7d (~4-5/dzień)** — tyle razy aktywny kurier dociera do feasibility bez okna zmiany.
