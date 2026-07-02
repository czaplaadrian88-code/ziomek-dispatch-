# Lane S2 — fix grafiku (audyt 2.0 finding H) — raport

**Branch:** `fix/grafik-h` (worktree `/root/.openclaw/workspace/wt-grafik`) · parent HEAD `72f37c8`
**Rola:** STAGED kopie + testy. Żywych `scripts/*.py` NIE dotykam (podmiana = koordynator, za ACK, z .bak). Zero flipów/restartów/push/Telegrama. Data: 2026-07-02.

Finding H (MASTER_synteza wiersz H): **2 bomby TZ ~25.10** w ładowaniu grafiku + literówka godziny kasuje kuriera z floty.

---

## Co naprawione (3 fixy, wszystkie w STAGED kopiach)

| Fix | Plik (staged) | Klasa | Flaga |
|---|---|---|---|
| **H1** — `today` grafiku w Warsaw wall-clock, nie naive-UTC | `deploy_staging/scripts/fetch_schedule.py` | poprawność | brak (zawsze ON) |
| **H1b** — `load_schedule` `today` przez `_now_warsaw` + usunięcie fixed-offset fallbacku | `deploy_staging/scripts/schedule_utils.py` | poprawność | brak (zawsze ON) |
| **H2** — literówka godziny w JEDNEJ komórce NIE kasuje wpisu kuriera | `deploy_staging/scripts/fetch_schedule.py` | decyzyjne (pula kurierów) | `ENABLE_GRAFIK_ENTRY_SALVAGE` **default OFF** |

**Root H1:** serwer=UTC. `datetime.now().strftime("%d-%m-%y")` w oknie **Warsaw 00:00-02:00** daje POPRZEDNI dzień (lato Warsaw 00:30 = UTC 22:30 wczoraj) → `find_date_columns`/`parse_schedule` wybierają kolumny WCZORAJSZEGO grafiku → cała flota dostaje zły shift; `schedule_today.json` `"date"` też wczoraj. Fix: `datetime.now(_WARSAW)` z `ZoneInfo("Europe/Warsaw")`, helper `_today_warsaw()` = **jedno źródło wyboru dnia**. `fetched_at` zostaje naive-ISO (timestamp, nie dzień).

**Root H1b:** `schedule_utils.py:157` liczyło `today` tym samym bugiem (UTC) — tylko do warning-loga porównującego `data["date"]`, nie przełącza grafiku (drobne), ALE plik miał też **fixed-offset fallback** `except ImportError: timezone(timedelta(hours=2))` = bomba TZ po końcu DST (stały +2 kłamie zimą CET=+1; `ImportError` i tak nie padnie — zoneinfo w stdlib). Fix: użycie istniejącego `_now_warsaw()` + **usunięcie fallbacku** (fail-loud, jedno źródło strefy).

**Root H2:** `parse_hour` (l.42) zwraca `None` na nieparsowalnej komórce (literówka „1O:00" z literą O, „9;00", stray char). Live: `{start,end} if (start_fmt and end_fmt) else None` → jeden `None` → cały wpis `None` → kurier traktowany jak NIE-w-grafiku → feasibility `v325_NO_ACTIVE_SHIFT`. **Jedna literówka usuwa pracującego kuriera z floty.** Fix (za flagą): gdy JEDNA z dwóch komórek parsuje → wpis ZOSTAJE z `parse_degraded: True` + **WARNING** (nazwa kuriera + surowa komórka = literówka WIDOCZNA, nie cicha). Gdy OBIE nieparsowalne (zero informacji) → `None` nawet z flagą ON. Konsument `schedule_utils.is_on_shift` przy `end=None` idzie istniejącym fail-open („brak godzin w grafiku" → dostępny), więc kurier NIE wypada z puli — bez zmian konsumenta.

> ⚠ **Nazwa flagi:** task nazwał ją `ENABLE_GRAFIK_ENTRY_SALVAGE`; szkic w `tz-drobnica_raport` używał `ENABLE_SCHEDULE_PARTIAL_HOUR_SALVAGE`. Przyjąłem nazwę z tasku (`ENABLE_GRAFIK_ENTRY_SALVAGE`) — „design wygrywa" dotyczyło MECHANIZMU degradacji (który wziąłem z designu 1:1: salvage tylko gdy ≥1 komórka parsuje + `parse_degraded` + WARNING), nie samej nazwy flagi.

---

## Diff staged vs żywy (dokładne hunki)

### `fetch_schedule.py` (removed=4 nie-puste / added=51; reszta bajt-identyczna)
Usunięte (żywe) linie:
```
        start_fmt = parse_hour(row[col_start].strip() if len(row) > col_start else "")
        end_fmt   = parse_hour(row[col_end].strip()   if len(row) > col_end   else "")
        schedule[name] = {"start": start_fmt, "end": end_fmt} if (start_fmt and end_fmt) else None
    today = datetime.now().strftime("%d-%m-%y")
```
Dodane (kluczowe): nagłówek `# STAGING …`; `from zoneinfo import ZoneInfo`; `_WARSAW = ZoneInfo("Europe/Warsaw")`; `FLAGS_PATH = "/root/.openclaw/workspace/scripts/flags.json"`; `def _flag(name, default=False)` (json.load try/except→default, fail-safe); `def _today_warsaw()` (`datetime.now(_WARSAW).strftime(...)`); `salvage_on = _flag("ENABLE_GRAFIK_ENTRY_SALVAGE", False)`; blok `if start_fmt and end_fmt / elif (start_fmt or end_fmt) and salvage_on: {…"parse_degraded": True} + log(UWAGA…) / else: None`; `today = _today_warsaw()`.

### `schedule_utils.py` (removed=10 / added=15; reszta bajt-identyczna)
Usunięte (żywe) linie: cały blok `try: from zoneinfo… except ImportError: _TZ = timezone(timedelta(hours=2)) …` (9 linii) + `today = datetime.now().strftime("%d-%m-%y")` (l.157).
Dodane: nagłówek `# STAGING …`; komentarz H1; `from zoneinfo import ZoneInfo` (bezwarunkowo, fail-loud); `_TZ = ZoneInfo("Europe/Warsaw")`; `def _now_warsaw()`; `today = _now_warsaw().strftime("%d-%m-%y")`.

Oba pliki: `py_compile` OK; import przez importlib OK (`_WARSAW.key == _TZ.key == "Europe/Warsaw"`; `_flag` przy braku klucza → False).

---

## Dowody testowe

Nowy plik `tests/test_grafik_fetch_schedule.py` (19 testów) + rozszerzenie `tests/test_tz_zoneinfo_consolidation.py` (+1 test skanu żywych scripts/). **34 passed** (15 tz + 19 grafik) razem.

### H1/H1b — kill-testy ZIMA + LATO (behawioralne C13)
Instant graniczny: `2026-07-14 23:30 UTC` = Warsaw `2026-07-15 01:30` (CEST, okno 00-02) / analog zimowy `2026-12-14 23:30 UTC` = Warsaw `15.12 00:30` (CET).

| Test | ZoneInfo (fix) | mutacja naive-UTC |
|---|---|---|
| H1 lato `_today_warsaw()` | **`15-07-26`** | `14-07-26` (wczoraj, bug) |
| H1 zima `_today_warsaw()` | **`15-12-26`** | `14-12-26` |
| H1b lato `_now_warsaw()` | **`15-07-26`** | `14-07-26` |
| H1b zima `_now_warsaw()` | **`15-12-26`** | — |

Mutacja = `_WARSAW`/`_TZ` → `None` (⇒ `datetime.now(None)` = naive UTC = bug). Test wymaga `bug != good` = strażnik ma zęby.

### H2 — OFF/ON na tym samym wejściu (CSV: kurier zdrowy `10:00–18:00` + kurier z literówką end `1O:00`)
| flaga | zdrowy kurier | kurier z literówką |
|---|---|---|
| **OFF (default)** | `{start:10:00, end:18:00}` | **`None`** (wypada z puli — jak dziś) |
| **ON** | `{start:10:00, end:18:00}` | **`{start:10:00, end:None, parse_degraded:True}`** (ZOSTAJE) + WARNING |

WARNING zawiera nazwę (`Anna Nowak`) i surową komórkę (`1O:00`) — literówka WIDOCZNA. Pokryte też: brak pliku flag → OFF; malformed JSON → OFF; obie komórki zepsute → `None` nawet ON.

### MUTATION ×2 (C13) — zweryfikowane odpaleniem na zmutowanej staged kopii, plik przywrócony (md5 identyczny)
1. **H1**: `_today_warsaw` `datetime.now(_WARSAW)` → `datetime.now()` ⇒ `test_h1_summer_window` + `test_h1_winter_window` **FAIL** (2 failed).
2. **H2**: salvage branch `{…parse_degraded}` → `None` ⇒ `test_h2_on_salvages` + `test_h2_on_off_differ` **FAIL** (2 failed).
3. **Parytet (bonus)**: stray linia w staged ⇒ `test_parity_change_counts_exact[fetch]` **FAIL** (added 52≠51).

### Parytet „nieaktualizowanego mirrora" (klasa L8)
3 testy × 2 pliki: (a) każda usunięta linia ∈ jawnej liście `_EXACT_REMOVED` (żywy plik zmieniony a staged stale → PADA); (b) wszystkie anchory fixu obecne w added (fix zgubiony → PADA); (c) dokładne liczby zmian (4/51, 10/15). **Uwaga:** po podmianie żywego pliku na staged parytet się wyzeruje — ten test wtedy trzeba **zdjąć/zaktualizować** (jest strażnikiem OKNA stagingu, nie stanu docelowego).

### Ratchet TZ blind-spot (domknięty)
`tests/test_tz_zoneinfo_consolidation.py` +`test_ratchet_external_scripts_no_new_fixed_offset`: skan JAWNEJ listy 3 żywych plików `scripts/{gastro_assign,fetch_schedule,schedule_utils}.py`. Dziś offenders = `{scripts/schedule_utils.py}` (żywy fixed-offset l.24) ⊆ `_EXTERNAL_ALLOWLIST` = `{scripts/schedule_utils.py}` → **ZIELONY**. Po deployu staged (żywy schedule_utils bez fallbacku) offenders={} → dalej zielony; **wpis do zdjęcia z allowlisty po deployu**. Mój test grafiku (trzyma literał jako NEGATYWNĄ asercję) dodany do `_GUARDIAN_TESTS` (wzór jak dla drive_speed-tz-test).

### Pełna regresja (worktree, apples-to-apples)
| | passed | failed | skipped | xfailed |
|---|---|---|---|---|
| baseline parent `72f37c8` | 3937 | 23 | 26 | 13 |
| po moich zmianach | **3957** (+20) | **23** (te same) | 26 | 13 |

Δ = **+20 passed** (19 grafik + 1 external ratchet), **0 nowych failów**. 23 „failed" = artefakt worktree (`test_a2_selection_shadow` 15 + `test_courier_reliability` 8 liczą `MODULE_PATH = REPO/"dispatch_v2"/…` zakładając układ kanoniczny `scripts/dispatch_v2/`; w worktree ścieżka nie istnieje → `SkipTest` liczony jako failed). Obecne PRZED moją zmianą, znikają na KANON (baseline zadania 3963/0). Task-baseline „3963/0/23/13xf" = liczby KANON; w worktree te 23 to szum path-layout, mój Δ liczony wewnątrz worktree.

---

## SEKWENCJA DEPLOYU dla koordynatora (za ACK Adriana, OFF-PEAK)

⚠ **T3 hot-refresh** (`schedule_utils.load_schedule`) odpala `fetch_schedule.py` jako subprocess co ≤10 min → podmieniony plik złapie się w ≤10 min sam z siebie. **Deploy poza oknem Warsaw 00:00-02:00** (żeby nie testować H1 na produkcji w dniu deployu) **i poza fetchami cron 06:00/08:00**. Kolejność: schedule_utils PRZED fetch_schedule (albo oba naraz — są niezależne).

```bash
# 0. PRZED: potwierdź świeżość i backup
cd /root/.openclaw/workspace/scripts
cp fetch_schedule.py  fetch_schedule.py.bak-pre-grafik-h-2026-07-02
cp schedule_utils.py  schedule_utils.py.bak-pre-grafik-h-2026-07-02

# 1. Podmiana (staged → żywy). Źródło = worktree wt-grafik/deploy_staging/scripts/.
#    UWAGA: staged pliki mają nagłówek "# STAGING…" — po podmianie zostaje w żywym
#    (nieszkodliwy komentarz). Jeśli koordynator chce czystości: usunąć 6 linii
#    "# STAGING…" z żywego po cp (opcjonalne, nie wpływa na działanie).
cp /root/.openclaw/workspace/wt-grafik/deploy_staging/scripts/schedule_utils.py  schedule_utils.py
cp /root/.openclaw/workspace/wt-grafik/deploy_staging/scripts/fetch_schedule.py  fetch_schedule.py

# 2. py_compile (NIGDY restart/deploy bez tego)
/root/.openclaw/venvs/dispatch/bin/python -m py_compile fetch_schedule.py schedule_utils.py && echo COMPILE OK

# 3. Ręczny bieg fetch_schedule w trybie bezpiecznym (--debug NIE zapisuje inaczej,
#    ale ładuje CSV; realny zapis idzie do schedule_today.json — to jest OK, to samo
#    robi cron/T3). Potwierdza że nowy `today` = DZISIEJSZY Warsaw:
/root/.openclaw/venvs/dispatch/bin/python fetch_schedule.py --debug 2>&1 | head -20
#    -> log "Start, data: DD-MM-YY" MUSI = dzisiejsza data Warsaw.

# 4. Weryfikacja artefaktu:
/root/.openclaw/venvs/dispatch/bin/python -c "import json;d=json.load(open('/root/.openclaw/workspace/dispatch_state/schedule_today.json'));print('date=',d['date'],'couriers=',len(d['couriers']))"
#    -> date = dzisiejsza Warsaw; couriers > 0 (nie pusto).

# 5. (H2, OPCJONALNIE i OSOBNO) flip flagi — DOPIERO po replayu „warto+bez regresji"
#    (patrz niżej). Domyślnie NIE flipować przy tym deployu.
```

**Rollback:** `cp fetch_schedule.py.bak-pre-grafik-h-2026-07-02 fetch_schedule.py` (+ analog schedule_utils) — T3 subprocess złapie stary plik w ≤10 min; cron/następny bieg fetch też. Zero restartu serwisu (fetch = fresh subprocess per bieg; schedule_utils importowany przez shadow/shift-notify — jeśli chce się natychmiast: restart `dispatch-shadow` po ACK, ale nie jest to konieczne, kolejny import procesu weźmie nowy plik przy następnym starcie; sam T3 subprocess bierze fetch_schedule świeżo zawsze).

> Uwaga do schedule_utils: jest **importowany** (nie subprocess) przez procesy długożyjące (`dispatch-shadow`, `dispatch-shift-notify` worker). Zmiana pliku NIE zadziała w już-biegnącym procesie do jego restartu. H1b to jednak tylko: (a) treść warning-loga porównania daty, (b) usunięcie martwego fallbacku (zoneinfo i tak jest) → **zero zmiany zachowania runtime dziś** (fallback nieosiągalny). Realna korekta H1b = następny naturalny restart procesu; nie wymaga wymuszonego restartu.

---

## Propozycja wpisu flagi + doc-snippet (H2)

`flags.json` (252 klucze dziś) — dodać:
```json
  "ENABLE_GRAFIK_ENTRY_SALVAGE": false
```
Doc-snippet (do CLAUDE.md / rejestru flag przy flipie):
> `ENABLE_GRAFIK_ENTRY_SALVAGE` (default OFF, czytana przez `fetch_schedule.py:_flag` z flags.json). OFF = literówka godziny w komórce grafiku kasuje cały wpis kuriera (`None` → `v325_NO_ACTIVE_SHIFT`, jak dziś). ON = wpis z ≥1 poprawną godziną ZOSTAJE (`parse_degraded:True`, druga godzina `None`) → kurier w puli; `is_on_shift` idzie fail-open „brak godzin". WARNING w logu fetch_schedule z nazwą kuriera + surową komórką. Flip: OSOBNO, po replayu (ilu kurierów/dzień tracimy na literówce) + okno 2 dni.

**Przed flipem (ETAP 5, NIE w tym zadaniu):** replay na historii — policzyć ile wpisów/dzień pada na `parse_hour=None` z JEDNĄ dobrą godziną (= ilu kurierów tracimy), dowód POZYTYWNEGO wpływu (mniej fałszywych `NO_ACTIVE_SHIFT`) + okno 2 dni. Dotyka puli kurierów i konsumenta `is_on_shift` → ACK obowiązkowy.

---

## Ryzyka
- **H1/H1b bez flagi** = czysta poprawność, offset-neutralna w dzień (lato ZoneInfo==+2; różnica tylko w oknie 00-02 i po końcu DST). Ryzyko podmiany = minimalne; jedyny efekt widoczny natychmiast: `today` w nocnym oknie 00-02.
- **H2 za flagą OFF** = zero zmiany zachowania po samym cp (flaga domyślnie OFF). Realny efekt dopiero po świadomym flipie (osobny ACK + replay).
- **T3 łapie plik w ≤10 min** — deploy poza 00-02 i poza 06:00/08:00; inaczej ryzyko że pierwszy bieg po podmianie zaskoczy operatora.
- **schedule_utils importowany** (nie subprocess) — H1b w pełni zadziała po naturalnym restarcie procesów; dziś-efekt = 0 (fallback nieosiągalny), więc brak pilności wymuszonego restartu.
- **Parytet-test wygasa po deployu** — po podmianie żywego pliku `test_parity_*` (staged↔żywy) się wyzeruje/rozjedzie; zdjąć lub przełączyć na strażnika stanu docelowego. `test_ratchet_external_scripts` — zdjąć `scripts/schedule_utils.py` z `_EXTERNAL_ALLOWLIST` po deployu (allowlista tylko się kurczy).

---

## Pliki (partycja S2)
- `deploy_staging/scripts/fetch_schedule.py` — NEW staged (H1 + H2)
- `deploy_staging/scripts/schedule_utils.py` — NEW staged (H1b + usunięcie fixed-offset)
- `tests/test_grafik_fetch_schedule.py` — NEW (19 testów)
- `tests/test_tz_zoneinfo_consolidation.py` — rozszerzony (+external scan +guardian entry)
- `eod_drafts/2026-07-02/grafik-h_raport.md` — ten raport
