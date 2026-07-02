# SWEEP — PRODUCENT GRAFIKU (`load_schedule` pipeline: Google Sheets → shift_start/shift_end)

**Audyt 2.0 Ziomka — lane: PRODUCENT GRAFIKU.** Tryb READ-ONLY wobec produkcji (zero edycji kodu/flag/systemctl/commit; analizy na kopiach w scratchpadzie; jeden bezpieczny read-only GET publicznego CSV arkusza).
**Data:** 2026-07-01 wieczór (UTC). **git HEAD:** `e41d598`.
**Kontekst:** `shift_start` = kotwica reguł pre-shift i **korzeń L4** (floor `pickup ≥ max(now, shift_start)`, patrz `tools/pickup_floor_guard.py`). Audyt 1.0 sweepował KONSUMENTÓW; ten sweep bierze PRODUCENTA (fetch + parser + cache + loader).

**Metoda dowodu:** każdy finding potwierdzony DRUGĄ metodą — repro na kopii (`scratchpad/repro_*.py`, import realnych funkcji, monkeypatch in-memory) **oraz** żywe dane (cache dziś, cron log, ledgery `dispatch_state/*.jsonl`, `systemctl`, precyzyjny GET arkusza 42 dni).

---

## 0. MAPA PIPELINE (producent → cache → loader → konsumenci)

```
Google Sheets  (SHEET_ID 1Z5kSGUB…OSK8, gid 533254920, PUBLIC csv export, brak API key)
   │  urllib GET (timeout 20s, utf-8-sig)
   ▼
scripts/fetch_schedule.py                         ← PRODUCENT
   ├─ fetch_csv()                    :63
   ├─ find_date_columns(header,date) :72   (2 kolumny/datę, po pozycji)
   ├─ parse_hour(raw)                :42   (HH:MM | H_int | None)
   ├─ is_valid_courier_name(name)    :26   (SKIP_PATTERNS substring + ≥2 słowa/≥4 zn.)
   ├─ parse_schedule()               :87   (entry = {start,end} if BOTH else None)
   └─ main()                         :128  (today = datetime.now()=UTC serwera!) → OUTPUT
   ▼
dispatch_state/schedule_today.json   (+ schedule_today_backup.json)   ← CACHE (dysk)
   ▲                                    ▲
   │ cron 0 6 / 0 8 (UTC!)              │ T3 hot-refresh subprocess (TTL 10 min)
   │ (crontab -l)                       │
   ▼                                    ▼
scripts/schedule_utils.py                          ← LOADER
   ├─ load_schedule()                :112  (mtime→TTL→_trigger_fetch→reload, fail-open)
   ├─ _trigger_fetch()               :75   (subprocess python3 fetch_schedule.py, debounce 30s)
   ├─ is_on_shift(name,sched,now=?)  :374  (now param MARTWY — nadpisany _now_warsaw)
   ├─ match_courier_strict()         :283  (#195 prefix-match)
   ├─ schedule_age_sec()/is_schedule_stale() :182/203
   └─ write_schedule_today_backup()  :214
   ▼  (load_schedule / is_on_shift / match_courier)
KONSUMENCI:
   • courier_resolver.dispatchable_fleet()  :1407  ← GŁÓWNY: buduje cs.shift_start/end/shift_start_min, pre_shift, pos_source
        _shift_start_dt :1276  _shift_end_dt :1293  _mins_to_shift_start :1259  (rodzina helperów — brak logiki północy)
   • feasibility_v2.py :658-785 (Gate1 NO_ACTIVE_SHIFT / D2 / FAIL12; Gate2 PICKUP_POST_SHIFT; Gate3 PRE_SHIFT_TOO_EARLY)
   • dispatch_pipeline.py :5208,5942 (V324A pre_shift clamp; 8 odwołań shift_*)
   • route_simulator_v2.py :262-277 (earliest_departure = shift_start clamp — twin)
   • plan_recheck.py (floor `_floor_pickups_to_committed`, twin path co 5 min)
   • auto_proximity_classifier.py :165 (_shift_end_edge)
   • telegram_approver.py :2280,3063 (age /status; format scheduled time)
   • new_courier_pairing.py (dispatch-new-courier-watch — JEDYNY żywy nie-silnikowy konsument load_schedule)
   • shift_notifications/worker.py — STALE_SCHEDULE_AGE alert + backup  ← RETIRED 2026-06-15 (patrz F1)
```

---

## FINDINGS (P0-P3, 🔥 live / 🧊 latent, dowód drugą metodą)

### 🔥 F1 [P1] — Alert `STALE_SCHEDULE_AGE` OSIEROCONY: staleness grafiku nie ma proaktywnego alertowania od 2026-06-15

**Plik:** `shift_notifications/worker.py:767-787` (jedyny nadawca) · zależność `feasibility_v2.py:686` · `common.py:2480-2485`.

Alert wysyłający „grafik N min nie odświeżany > 30 min" żyje **wyłącznie** w `shift_notifications/worker.py` (`_mp15_check_schedule_staleness`, tick co minutę). Ten worker był hostowany przez `dispatch-shift-notify.timer` — **retired 2026-06-15**.

**Dowód (metoda 1 — systemctl):**
```
systemctl is-active  dispatch-shift-notify.timer → inactive
systemctl is-enabled dispatch-shift-notify.timer → not-found
/etc/systemd/system/dispatch-shift-notify.{service,timer}.retired-2026-06-15
```
**Dowód (metoda 2 — grep żywych nadawców):** `grep STALE_SCHEDULE_AGE` po całym repo → nadawcą jest TYLKO `worker.py`. `telegram_approver.py:2280,2516` czyta `schedule_age_sec()` **pasywnie** do linii `/status`, nie wysyła alertu. `feasibility_v2.py:686` (gałąź D2) ma komentarz *„Alert: polegamy na istniejącym STALE_SCHEDULE_AGE (shift_notifications.worker)"* — czyli kod jawnie DELEGUJE obserwowalność do martwego workera, **a sam D2 jest OFF** (F-tri niżej).

**Skutek:** Sheets padnie na godziny → `load_schedule` fail-open serwuje stary cache (poprawne wg Lekcja #31), ale **nikt nie dostanie sygnału**. `is_schedule_stale()` jest liczone (courier_resolver `_sched_stale`), lecz nie ma odbiorcy-alertu. To live regresja obserwowalności — kotwica `shift_start` może cicho zjeżdżać na wczorajszy grafik.

**Uwaga poboczna (do domknięcia F1):** `schedule_today_backup.json` ma świeży mtime (dziś 19:51) mimo retirementu — jedyny znaleziony caller `write_schedule_today_backup` to również `worker.py:809-813`. Skąd świeży backup przy martwym timerze = **rozbieżność do wyjaśnienia** (albo worker jest wołany inną drogą, albo backup jest pisany skądinąd). Wymaga 5 min sprawdzenia przed jakąkolwiek naprawą F1.

---

### 🔥 F2 [P1/P2] — Wybór DATY grafiku liczony w UTC serwera (fetch + loader) → 00:00–02:00 Warsaw serwuje WCZORAJSZY grafik, CICHO

**Plik:** `fetch_schedule.py:130` (`today = datetime.now().strftime("%d-%m-%y")`) · `schedule_utils.py:157` (ten sam `datetime.now()` do date-check).

Serwer to `Etc/UTC` (`timedatectl` → `Time zone: Etc/UTC`). `fetch_schedule.main()` bierze datę z `datetime.now()` = **czas UTC**, a `find_date_columns` szuka kolumn dla tej daty. Warsaw latem = UTC+2, więc w oknie Warsaw 00:00–02:00 (= UTC 22:00–00:00) UTC-data to jeszcze WCZORAJ → fetch pobiera **wczorajsze** kolumny i zapisuje `date: wczoraj`.

**Dowód (metoda 1 — repro, `scratchpad/repro_midnight.py`):**
```
UTC 01 22:30 = Warsaw 02 00:30  fetch_date(UTC)=01-07-26  warsaw_date=02-07-26  <-- MISMATCH
UTC 01 23:30 = Warsaw 02 01:30  fetch_date(UTC)=01-07-26  warsaw_date=02-07-26  <-- MISMATCH
```
**Dowód (metoda 2 — logika loadera):** `load_schedule` ma warning `data.get("date") != today`, ale `today` liczone TYM SAMYM `datetime.now()` (UTC) → obie strony UTC → **warning nigdy nie złapie własnego bug-a producenta**. Cache dziś: `date: 01-07-26`, `fetched_at: 22:05` — spójne (bo teraz nie jest okno północne).

**Skutek:** aktywność Warsaw 00:00–02:00 (pt/sb late-shift) widzi grafik na złą dobę, oznaczony jako „dziś", bez ostrzeżenia. Zima (UTC+1) zawęża okno do 1 h (00:00–01:00). Niska aktywność w tym oknie ogranicza szkodę, ale to LIVE i CICHE. Wariant „P1" gdy pt/sb overnight, „P2" w dni robocze.

---

### 🧊 F3 [P2] — `is_on_shift`: zmiana przez północ z końcem ≠ "24:00" (np. 22:00–02:00) = `on_shift=False` CAŁĄ zmianę

**Plik:** `schedule_utils.py:396-407`.

Jedyny special-case to `end_str == "24:00"` (→ następny dzień 00:00). Każdy inny koniec „małogodzinny" (01:00–05:00) parsuje się jako `today HH:MM`, który jest < start → `now >= end` prawdziwe od startu → „zmiana skończyła się".

**Dowód (metoda 1 — repro z frozen `_now_warsaw`, `scratchpad/repro_midnight.py`):**
```
sched Cross02 = {start:22:00, end:02:00}
  now 23:30 → on_shift=False  (zmiana skończyła się o 02:00)   ← BUG (powinno True)
  now 01:30 → on_shift=False  (zmiana od 22:00 za 1230 min)
sched Cross24 = {start:22:00, end:24:00}
  now 23:30 → on_shift=True   ← OK (special-case)
```
**Dowód (metoda 2 — realny rozkład endów, `scratchpad/repro_precise_dist.py`, GET 42 dni):** koniec zmian rozkłada się 12:00→24:00; `end=24:00`: **20 wystąpień** (obsłużone), **ZERO endów w zakresie 00:00–06:00**. Więc bug jest **LATENTNY** — koordynatorzy używają konwencji „24" (nie „1"/„2"). Zamiana konwencji na „1"/„2" dla zmiany do 1–2 w nocy → cichy `off_shift` całej nocnej zmiany.

---

### 🧊 F4 [P2] — Rodzina helperów shift-dt (`_shift_start_dt`/`_shift_end_dt`/`_mins_to_shift_start`) kotwiczy do daty `now` → „+1"/skok po północy; zasila korzeń L4

**Plik:** `courier_resolver.py:1259` (`_mins_to_shift_start`), `:1276` (`_shift_start_dt`), `:1293` (`_shift_end_dt`).

Wszystkie trzy robią `now_w = datetime.now(WAW); now_w.replace(hour, minute)` — bez rozstrzygania, do której DOBY należy zmiana. Jedyny „+1" to `_shift_end_dt("24:00") = replace(0,0)+timedelta(days=1)`. Po północy `now`-data się przekręca → wszystkie czasy zmiany skaczą ~+1 dzień w przyszłość.

**Dowód (metoda 1 — repro replikacji logiki, `scratchpad/repro_midnight.py`):**
```
now=00:30 (2 Jul):
  Cross24  start=02 22:00 (Δ+1290m)  end=03 00:00 (Δ+1410m)   ← "24:00" +1 daje shift_end 24h za późno
  Late     start=02 17:00 (Δ+990m)   end=02 23:00 (Δ+1350m)   ← zmiana sprzed północy widziana jako "dziś wieczorem"
```
**Dowód (metoda 2 — konsument L4):** `tools/pickup_floor_guard.py` docstring: inwariant `pickup ≥ max(now, shift_start(cid))`, `plan_recheck._floor_pickups_to_committed`. Skoro `shift_start` = wyjście `_shift_start_dt`, to po północy floor L4 dostaje `shift_start` 21 h w przyszłość → Gate3 feasibility (`PRE_SHIFT_TOO_EARLY`, próg 30 min) odrzuci pickup TERAZ. Dotyka 20 realnych late-shiftów „24:00" w wąskim oknie 00:00–00:30 oraz każdej aktywności po północy. **Rodzina = twin-set** — poprawka północy MUSI trafić we wszystkie 3 helpery razem (i lustro w `eod_drafts/2026-06-17/courier_resolver.n1-…draft.py:1265+` jeśli aktywowany).

---

### 🧊 F5 [P2] — `parse_hour` cicho zwraca None na literówce, a reguła kompozycji zeruje CAŁY entry → kurier znika jako „nie pracuje dziś"

**Plik:** `fetch_schedule.py:42-53` (`parse_hour`) + `:121` (`schedule[name] = {…} if (start_fmt and end_fmt) else None`).

`parse_hour` łapie tylko `HH:MM` (z dwukropkiem) lub czysty int 0–24. Każda inna forma → None, bez logu per-komórka. A `parse_schedule` zeruje **cały** entry gdy PADNIE JEDNO z pól → kurier trafia do „not_working (None)" = w `dispatchable_fleet` odrzucony jako „nie pracuje dziś".

**Dowód (metoda 1 — repro realnych funkcji, `scratchpad/repro_parse_hour.py`):**
```
parse_hour('11.00')   -> None      (kropka zamiast dwukropka)
parse_hour('do końca')-> None      parse_hour('od 12') -> None    parse_hour('11;00')/'11,00' -> None
entry(start='11.00', end='19:00')      -> None   ← literówka w JEDNYM polu = cały entry None
entry(start='11:00', end='do końca')   -> None   ← "do końca" jako koniec = kurier NIE pracuje cały dzień
```
**Dowód (metoda 2 — realny arkusz, `scratchpad/repro_precise_dist.py`):** precyzyjny skan 42 dni × 2 kolumny godzinowe = 924 niepustych komórek: **100% `H_int`**, ZERO `HH:MM`, ZERO UNPARSED. Czyli literówka jest **LATENTNA** (nie występuje w bieżącym arkuszu — używają czystych intów), ale w pełni odtwarzalna i CICHA. To dokładnie mechanizm „literówka '11.00' = floor martwy cicho" z audytu pre-shift, potwierdzony u źródła. Brak jakiegokolwiek per-komórka warn/countera przy null-owaniu = niewidoczny.

---

### 🧊 F6 [P2] — `is_valid_courier_name`: SKIP_PATTERNS matchuje PODŁAŃCUCH → realne nazwiska znikają z grafiku

**Plik:** `fetch_schedule.py:17-40`.

`SKIP_PATTERNS` (m.in. `"maja"`, `"baku"`, `"linka"`, `"wkręt"`, `"w tyg"`, `"razem"`) jest sprawdzany przez `pattern in name_lower` — substring, nie słowo/wiersz. Dodatkowo: jednoczłonowe imię < 4 znaki jest odrzucane.

**Dowód (metoda 1 — repro, `scratchpad/repro_parse_hour.py`):**
```
is_valid_courier_name('Maja Kowalska') -> False   ('maja' substring)
is_valid_courier_name('Anna Majak')    -> False   ('maja' w 'majak')
is_valid_courier_name('Baku Tomasz')   -> False   ('baku')
is_valid_courier_name('Linka Anna')    -> False   ('linka')
is_valid_courier_name('Jan') / 'Ala'   -> False   (1 człon < 4 zn.)
```
**Dowód (metoda 2 — realny fetch):** cron log dziś `Pominięto 3 wierszy nie-kurierów: ['Potrzeby kadrowe','cały dzień','popołudnia']` — obecny roster NIE ma false-positive (52 kurierów przechodzi). Więc **LATENTNE**: dopiero nazwisko/imię zawierające pattern (albo krótki 1-członowy) zniknie. Skutek zależnie od konsumenta: `dispatchable_fleet` → `continue` (odrzuca z puli, „schedule_no_match"), `is_on_shift` → fail-open True. Rozjazd zachowań = dodatkowa mina.

---

### 🔥 F7 [P2] — T3 hot-refresh ma ~zerową obserwowalność (subprocess pożarty, INFO nie trafia do journald)

**Plik:** `schedule_utils.py:85-109` (`_trigger_fetch` z `capture_output=True`) + `_log.info` w `load_schedule`.

`_trigger_fetch` odpala `subprocess.run(..., capture_output=True)` — stdout/stderr `fetch_schedule.py` jest POŁKNIĘTY; przy `returncode==0` nic nie leci dalej, przy != 0 leci tylko `stderr_tail[-200:]` do `_log.warning`. Same INFO-logi loadera (`T3 schedule fetch SUCCESS`, `schedule stale`, `cache reloaded`) idą do loggera `schedule_utils`, który w usługach silnika nie propaguje do journald.

**Dowód (metoda 1 — journald):** `journalctl -u dispatch-shadow/-panel-watcher/-czasowka/-plan-recheck --since 14d | grep "T3 schedule fetch"` → **0 linii** w każdej usłudze. `grep "schedule stale"` → 0.
**Dowód (metoda 2 — cron log ma sygnał, T3 nie):** `/tmp/gastro_cron.log` (94 KB) pokazuje pełne fetch-e crona (06:00/08:00 UTC, `Pracuje dziś 12/51`, `BŁĄD total: 0`), ale te T3 in-process są niewidoczne — jedyny dowód, że T3 działa, to świeży mtime `schedule_today.json` (22:05, wiek ~2 min). Gdy T3 cicho pada (debounce 30 s, fail-open), staleness rośnie niewidocznie — a alert (F1) jest martwy. **Kombinacja F1+F7 = ślepota na degradację grafiku.**

---

### 🧊 F8 [P3] — `parse_hour` akceptuje nielegalne minuty ("9:60"→"09:60") — walidacja dopiero downstream

**Plik:** `fetch_schedule.py:45-48` (`parts[1].zfill(2)` bez sprawdzenia 0–59).

**Dowód (metoda 1 — repro):** `parse_hour('9:60') -> '09:60'`, `parse_hour('09:5') -> '09:05'`. **Dowód (metoda 2 — downstream):** `is_on_shift` `datetime.strptime('09:60','%H:%M')` → ValueError → gałąź `"błąd parsowania godzin"` fail-OPEN; a `_shift_end_dt` `now_w.replace(minute=60)` → ValueError → None → `shift_end=None` → Gate1 NO_ACTIVE_SHIFT/FAIL12. Dwa różne tryby awarii z jednego złego wpisu. Latentne (brak `HH:MM` w arkuszu, F5-dowód).

---

### 🧊 F9 [P3] — Parsowanie po POZYCJI kolumn bez walidacji, że kolumny są godzinowe → zmiana struktury arkusza = ciche złe godziny

**Plik:** `fetch_schedule.py:72-85` (`find_date_columns` bierze 1. i 2. kolumnę po dacie) + `:118-119`.

`find_date_columns` zwraca po prostu 2 pierwsze kolumny należące do daty. Zmiana układu (3 kolumny/datę, przestawienie, dodatkowa kolumna „SUMA" bez prefiksu) → `parse_hour` czyta nie tę komórkę. Brak zabezpieczenia, że wartość wygląda jak godzina.

**Dowód (metoda 1 — struktura dziś):** `scratchpad/repro_precise_dist.py`: header 97 kolumn, 42 daty, **42/42 z poprawną parą 2-kol.** — dziś OK. **Dowód (metoda 2 — sąsiednie kolumny mają nie-godziny):** szeroki skan (`repro_dst_and_realdist.py`) kolumn poza godzinowymi zwrócił wartości `50, 49, 81, -34, -75, -7…` (liczby/kwoty, w tym ujemne). `parse_hour` odrzuca >24 i ujemne (clamp 0–24 chroni), ALE wartość 0–24 w złej kolumnie zostałaby **cicho zaakceptowana** jako godzina. Fragile, latentne.

---

### 🔥 F10 [P3] — Dryf komentarz↔rzeczywistość: „fetch 06:00 Warsaw" a cron jest w UTC (faktycznie 08:00/10:00 Warsaw latem)

**Plik:** `schedule_utils.py:39` (komentarz „cron 06:00+08:00 Warsaw") · `dispatch_v2/CLAUDE.md` („fetch 06:00 i 08:00 daily") · `crontab -l` (`0 6` / `0 8` w Etc/UTC).

**Dowód (metoda 1 — crontab):** `0 6 * * * python3 …/fetch_schedule.py` i `0 8 * * *`. **Dowód (metoda 2 — TZ serwera):** `Etc/UTC`. Więc realnie 08:00 i 10:00 Warsaw latem (07:00/09:00 zimą, dryf z DST). Nieszkodliwe operacyjnie (T3 i tak dogrywa co 10 min), ale mylące dla diagnostyki i dowód, że warstwa czasu grafiku miesza UTC/Warsaw niekonsekwentnie (spójne z F2/F4).

---

## TRI-STAN fail-open / fail-close (weryfikacja na żywych danych)

Gate1 feasibility (`feasibility_v2.py:679-726`) dla `shift_end is None` ma 3 gałęzie. Stan flag zweryfikowany (`flags.json` + `common.py`):

| Gałąź | Warunek | Flaga (LIVE) | Dowód w ledgerze (dispatch_state) |
|---|---|---|---|
| **D2 soft-degrade** | stale grafik + `ENABLE_D2_STALE_SCHEDULE_SOFT` | **OFF** (env default `0`, brak w flags.json) | `d2_stale_schedule_soft`: **0** we wszystkich `*.jsonl` → NIGDY nie odpalił ✅ |
| **FAIL12 fail-OPEN** | bag>0 lub świeży GPS + `ENABLE_FAIL12_SCHEDULE_FAILOPEN` | **ON** (flags.json=True) | `fail12_schedule_failopen`: **3** (learning_log.jsonl, span 06-26→07-01 ≈5 dni) → rzadko ✅ zgodne z „3/956" |
| **NO_ACTIVE_SHIFT fail-CLOSE** | reszta | — (residual) | `NO_ACTIVE_SHIFT`/`v325_reject_reason`: **0** w grepowanych ledgerach — **NIEROZSTRZYGAJĄCE** (patrz niżej) |

**Wniosek:** PLAUSIBLE root `schedule-data-3way` z 1.0 („fail-CLOSE nigdy nie odpalił live") jest **częściowo potwierdzony, nie dowiedziony**:
- D2 fizycznie martwe (flaga OFF) — potwierdzone (0/wszystko).
- FAIL12 odpala śladowo (3 zdarzenia/5 dni) — czyli Gate1-z-None jest rzadki, bo grafik ładuje się poprawnie ~zawsze (`shift_end` wypełniony).
- „0 NO_ACTIVE_SHIFT" **NIE jest dowodem zera** — `v325_reject_reason` mógł być gubiony przez allowlist serializera (L1.1 „serializer completeness" naprawiony dopiero 01.07). Reject NO może się dziać bez trafienia do ledgera. Do rozstrzygnięcia po L1.1 LIVE (grep `v325_reject_reason` na świeżych rekordach).

Powiązane liczby (wszystkie ledgery): `PRE_SHIFT_TOO_EARLY`: **191** (Gate3 realnie pracuje), `PICKUP_POST_SHIFT`: **1**.

---

## PÓŁNOC + DST (analiza kodu + symulacja na kopii)

- **is_on_shift przez północ:** patrz F3 — tylko `"24:00"` obsłużone; end 01:00–05:00 = off całą zmianę. Realnie latentne (konwencja „24", 20 wystąpień, 0 małogodzinnych).
- **Rodzina `_shift_*_dt` „+1"/skok:** patrz F4 — po północy wszystkie czasy skaczą ~+1 dzień; `"24:00"+timedelta(days=1)` daje shift_end 24 h za późno w oknie 00:00–00:30.
- **DST październik (fold 2026-10-25, 03:00→02:00):** `scratchpad/repro_dst_and_realdist.py` — `_shift_end_dt("24:00") = replace(0,0)+timedelta(days=1)` → **26 Oct 00:00 CET (poprawna północ), BEZ crasha** mimo że doba ma 25 h. **DST = NISKIE RYZYKO** (brak wyjątku, poprawna data kalendarzowa).
- **DST wiosna (skok 2027-03-28 02:00→03:00, nieistniejąca ściana):** `replace(hour=2,minute=30)` → `02:30+01:00` (ZoneInfo wybiera offset), bez crasha. Zmiana startująca w wykreślonej godzinie = drobny błąd UTC, nie fatalny, skrajnie rzadki.
- **Realny bug TZ to NIE DST**, tylko UTC-data (F2) + kotwica-do-now (F4) — niezależne od DST.

---

## ORACLE (żywe dane)

**Cache grafiku dziś (`dispatch_state/schedule_today.json`):** mtime `2026-07-01 22:05:14 UTC` (wiek ~2 min → T3 aktywny), `date: 01-07-26` (zgodne z dziś), 52 kurierów, **12 pracuje / 40 None**. Backup `schedule_today_backup.json` fetched_at 19:51 (por. F1 rozbieżność). Format wszystkich pracujących = czyste `HH:00`.

**Cron fetch (14 dni, `/tmp/gastro_cron.log`):** `BŁĄD total: 0` (fetch nigdy nie padł w oknie logu). 72× „Pracuje dziś", counts 9–13/46–52. Header 97 kolumn, kolumny daty 70/71. Filtr śmieci działa (3 wiersze pominięte). Ostatni cron: 06:00 i 08:00 UTC.

**T3-refresh (journald 14 dni):** 0 linii `T3 schedule fetch` w żadnej usłudze silnika → **fetch-e T3 niewidoczne** (F7). Nie da się policzyć, ile T3-refreshy padło — brak sygnału. Jedyny pośredni dowód działania: świeży mtime cache.

**shift_start=None / pos_source pre_shift (ledgery):** `pre_shift`/`no_gps` obecne w `reassignment_shadow.jsonl` (24 763 linii), `learning_log.jsonl` (1 632, span 06-26→07-01), `checkpoint_tz_shadow.jsonl`, `backfill_decisions_outcomes_v1.jsonl`, `carried_first_guard.jsonl`. `fail12_schedule_failopen=3`, `d2_stale_schedule_soft=0` (jak wyżej). Bezpośredniego pola `shift_start=None` w ledgerach nie ma osobno serializowanego (metryka jest w słowniku feasibility → patrz luka serializera L1.1).

**Odporność:**
- **quota-limit / brak sieci:** `urllib.urlopen` rzuca → `main()` return 1 → `_trigger_fetch` False → `load_schedule` **fail-open stary cache** (debounce 30 s + TTL 10 min ⇒ ~6 fetchy/h steady-state, ryzyko quota niskie). Degradacja cicha (F1+F7).
- **zmiana struktury arkusza:** dwa tryby — (a) data nieznaleziona → `parse_schedule` `raise ValueError` → fetch fail → fail-open stale; (b) przesunięte kolumny 0–24 → **ciche złe godziny** (F9).
- **rozkład formatów w arkuszu (42 dni):** 924 komórki godzinowe = **100% H_int**, 0 typo, 0 `HH:MM`, 0 endów 00:00–06:00. Ryzyka F3/F5/F6/F8 = LATENTNE (odtwarzalne, dziś nieaktywne).

---

## POKRYCIE

**Przeanalizowane (kod przeczytany w całości / kluczowe sekcje + repro):**
- PRODUCENT: `scripts/fetch_schedule.py` (cały: fetch_csv, find_date_columns, parse_hour, is_valid_courier_name, parse_schedule, main).
- LOADER: `scripts/schedule_utils.py` (cały: load_schedule, _trigger_fetch, is_on_shift, match_courier_strict, schedule_age_sec, is_schedule_stale, write_schedule_today_backup).
- HELPERY shift-dt: `courier_resolver.py:1259/1276/1293` + konsument `dispatchable_fleet():1407-1610`.
- KONSUMENT feasibility: `feasibility_v2.py:658-785` (3-stan Gate1 + Gate2/3).
- Twin-paths: `route_simulator_v2.py:262-277`, `plan_recheck` floor (via `tools/pickup_floor_guard.py`), `auto_proximity_classifier.py:165`.
- Obserwowalność: `shift_notifications/worker.py` (STALE alert + backup), `telegram_approver.py:2280,2516`.
- ŻYWE DANE: cache dziś, cron log 14 dni, `flags.json`, `common.py` defaults (D2/FAIL12/V324A/V325/PRE_SHIFT), 10 ledgerów `dispatch_state/*.jsonl`, `systemctl list-timers`, `timedatectl`, precyzyjny GET arkusza (42 dni).
- Repro/symulacje (scratchpad): `repro_parse_hour.py`, `repro_midnight.py`, `repro_dst_and_realdist.py`, `repro_precise_dist.py`.
- 15 klas sweepu: fetch ✅ · parser godzin ✅ · walidacja nazw ✅ · kompozycja entry ✅ · cache/mtime ✅ · TTL/hot-refresh ✅ · loader fail-open ✅ · is_on_shift ✅ · północ ✅ · DST ✅ · UTC/TZ ✅ · tri-stan D2/FAIL12/NO_ACTIVE_SHIFT ✅ · konsumenci shift_start/end ✅ · dispatchable_fleet ✅ · odporność (quota/sieć/struktura) ✅.

## JAWNE LUKI

1. **„0 NO_ACTIVE_SHIFT" nierozstrzygnięte** — nie wiem, czy fail-CLOSE realnie nie odpala, czy metryka `v325_reject_reason` była gubiona przez allowlist serializera (L1.1 naprawiony 01.07). Do rozstrzygnięcia grepem świeżych rekordów po restarcie shadow z L1.1. NIE weryfikowałem tego (read-only, brak restartu).
2. **F1 rozbieżność backupu** — świeży `schedule_today_backup.json` (19:51) przy retired `dispatch-shift-notify`. Nie ustaliłem, kto go pisze (jedyny grep-caller = martwy worker). Wymaga śledzenia procesu/importu przed naprawą F1.
3. **journald 14-dniowy cross-service** — pełny skan wszystkich usług się zapchał (timeout 2 min); policzyłem per-usługa (0 linii T3) zamiast jednego agregatu. Nie wykluczam, że jakaś usługa poza sprawdzoną czwórką (`dispatch-shadow/-panel-watcher/-czasowka/-plan-recheck`) loguje T3 — mało prawdopodobne (ten sam logger), ale nie 100%.
4. **Realny rozkład typo HISTORYCZNIE** — GET dał SNAPSHOT arkusza (42 daty w bieżącym gid). Nie mam wglądu w usunięte/nadpisane komórki ani inne gid/arkusze. „0 typo" dotyczy widocznego snapshotu, nie całej historii.
5. **Wpływ F3/F4 na REALNE zlecenia po północy** — nie zjoinowałem ledgerów po timestampie 00:00–02:00 Warsaw z konkretnymi order_id (byłby to głębszy replay). Oszacowanie „wąskie okno, niska aktywność" oparte na braku endów 00:00–06:00 + konwencji „24", nie na policzonych zleceniach nocnych.
6. **Nie odpaliłem replaya feasibility** (`tools/replay_feasibility.py`) na oknie północnym — potwierdziłby liczbowo Gate3 `PRE_SHIFT_TOO_EARLY` po północy z bug-a F4. Read-only pozwala, ale to osobny, cięższy krok; zostawiam jako rekomendację do fazy naprawczej.

---

### TOP-5 (priorytet)
1. **F1 🔥 P1** — alert `STALE_SCHEDULE_AGE` osierocony (retired worker 06-15); staleness grafiku bez proaktywnego sygnału, a D2 explicite na nim polega (i jest OFF).
2. **F2 🔥 P1/P2** — wybór daty grafiku w UTC (fetch+loader); Warsaw 00:00–02:00 serwuje wczorajszy grafik CICHO (date-check też UTC → nie łapie).
3. **F7 🔥 P2** — T3 hot-refresh bez obserwowalności (subprocess pożarty, INFO nie w journald); z F1 = ślepota na degradację.
4. **F4 🧊 P2** — rodzina `_shift_start_dt/_shift_end_dt/_mins_to_shift_start` kotwiczy do daty `now` → „+1"/skok po północy; zasila korzeń L4 (floor pickup≥shift_start). Twin-set — poprawiać razem.
5. **F5 🧊 P2** — `parse_hour` cicho→None na literówce + kompozycja zeruje CAŁY entry → kurier znika jako „nie pracuje"; latentne (dziś 100% H_int) ale to źródło „11.00 = floor martwy cicho".
