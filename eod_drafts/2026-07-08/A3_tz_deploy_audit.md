# A3 — Audyt deploymentu fixów stref czasowych (TZ) FALA-1 przed DST 25.10.2026

> **Zweryfikowane przez wykonawcę sprintu (tmux 36, 2026-07-08):** niezależnie
> potwierdzone grepem — 3 żywe pliki scripts-root (`gastro_assign`/`fetch_schedule`/
> `schedule_utils`) mają `ZoneInfo("Europe/Warsaw")` (nie fixed-offset); jedyny
> rezydualny `timezone(timedelta(hours=1/2))` w żywym silniku = `tools/ontime_lib.py`
> i jest POPRAWNYM wzorem DST-aware (`warsaw_tz_for` liczy last-Sunday marzec→CEST/
> październik→CET, l.49-68). Werdykt „TZ deploy KOMPLETNY przed 25.10, zero luki"
> = potwierdzony. Jedyny FAIL = nie-TZ drift `fetch_schedule` live↔staged (None-sort,
> cudza sesja/grafik). READ-ONLY.

**Data audytu:** 2026-07-08 | **Tryb:** READ-ONLY (zero mutacji repo/systemd/flag) |
**Repo kanon:** `/root/.openclaw/workspace/scripts/dispatch_v2` (branch `master`) |
**Żywe scripts-root:** `/root/.openclaw/workspace/scripts/`

## Werdykt jednozdaniowy

**Wszystkie fixy TZ z FALA-1 są LIVE. Zero rezydualnych bomb fixed-offset w żywym silniku.
Zero luki deployowej TZ przed DST 25.10.** Jedyne otwarte residuum = NIE-TZ (drift stale-mirror
`fetch_schedule.py`, staged JEST ZA żywym o fix None-sort — kierunek odwrotny niż „staged-za-ACK").

Model zmiany czasu (kontekst ryzyka): ostatnia niedziela X 2026 = **25.10.2026 03:00→02:00**,
Warszawa CEST(+2)→CET(+1). Każdy hardcode `+2` po tej dacie kłamie o 1h → bomba.

---

## Tabela fixów

| # | Fix | Plik (żywy) | Live? | Dowód | Ryzyko przed 25.10 | Rekomendacja |
|---|-----|-------------|-------|-------|--------------------|--------------|
| 1 | **gastro_assign TZ** (audyt 2.0 bomba #1 — był fixed `+2`; zimą HH:MM<1h → guard „+1 dzień" → ~1410 min do panelu zamiast ~20) | `scripts/gastro_assign.py` | **LIVE** | `l.26-27 from zoneinfo import ZoneInfo; _WARSAW=ZoneInfo("Europe/Warsaw")`, `l.260 datetime.now(_WARSAW)`. `diff` żywy↔staged = **IDENTICAL**. Commit `f9de4c1` docs „fixed-live za GO". Uruchamiany jako **subprocess** przez `nadajesz-panel.service` → świeży proces, bez restartu. | BRAK (usunięta) | Żadna — domknięte |
| 2 | **fetch_schedule H1** (`today=ZoneInfo Warsaw`, drop fixed-offset; był bug: zimą ładował WCZORAJSZY grafik całej floty) | `scripts/fetch_schedule.py` | **LIVE** | `l.17 import ZoneInfo`, `l.23 _WARSAW=ZoneInfo("Europe/Warsaw")`, `l.54 datetime.now(_WARSAW).strftime(...)` — bezwarunkowe (NIE za flagą). Backup `.bak-pre-grafik-h-2026-07-02`. Cron **06:00 i 08:00** (`python3 fetch_schedule.py`) → świeży proces, bez restartu. | BRAK (TZ live) | Żadna dla TZ; patrz „Residuum" niżej (None-sort) |
| 3 | **schedule_utils H1** (usunięcie fixed-offset fallbacku strefy; stały `+2` kłamał zimą) | `scripts/schedule_utils.py` | **LIVE** | `l.28-29 from zoneinfo import ZoneInfo; _TZ=ZoneInfo("Europe/Warsaw")`. `diff` żywy↔staged = **IDENTICAL**. Backup `.bak-pre-grafik-h-2026-07-02` (19529 B) = dowód podmiany żywego na staged 02.07 ~13:31 (GO Adriana). | BRAK (usunięta) | Żadna — domknięte |
| 4 | **drive_speed_overshoot_verdict → ZoneInfo** (tz-drobnica; zdjęty z allowlisty ratcheta) | `dispatch_v2/tools/drive_speed_overshoot_verdict.py` | **LIVE (w kodzie)** | `grep ZoneInfo` OK; commit `6670b54` IN master; working tree = master HEAD (git status czysty na pliku). Narzędzie one-shot → świeże wykonanie. | BRAK | Żadna |
| 5 | **6 narzędzi TZ-consolidate** (`freshness_shadow_monitor`, `monitor_refloor_peak_2026_05_31`, `reassignment_shadow`, `sequential_replay`, `shadow_outcome_enricher`, `perf_budget_report`) | `dispatch_v2/tools/*.py` | **LIVE (w kodzie)** | Wszystkie 6 → `grep ZoneInfo` OK; zero fixed-offset (`sequential_replay:73` to tylko KOMENTARZ „był fixed +02:00"). Commity `834ae8c`+`2e68a11` IN master. Narzędzia shadow/analiza → one-shot. | BRAK | Żadna |
| 6 | **sprint2_analysis/_common.py TZ** (fixowany w `834ae8c`) | — (USUNIĘTY) | **BEZPRZEDMIOTOWY** | Katalog `sprint2_analysis/` = tylko `__pycache__`; `_common.py` skasowany jako martwy kod (`cbe566f`, L8-iter3). Zero importerów. | BRAK (kod martwy) | Żadna |

**Legenda Live?:** LIVE = żywy plik produkcyjny ma fix i jest wykonywany; LIVE (w kodzie) = plik
repo w master, wykonywany jako świeży proces (bez daemona trzymającego starą kopię).

---

## Rezydualne bomby fixed-offset — świeży grep całego silnika

`grep -rn "timezone(timedelta(hours=" --include=*.py` (bez `tests/ eod_drafts/ deploy_staging/ .claude/`):

**Jedyne trafienie w żywym silniku:** `tools/ontime_lib.py:45-46`
```
_WARSAW_STD = timezone(timedelta(hours=1))   # CET
_WARSAW_DST = timezone(timedelta(hours=2))   # CEST
```
→ **NIE bomba.** To POPRAWNY wzór DST-aware: funkcja `warsaw_tz_for(dt_utc)` (l.61) liczy
last-Sunday-marzec→CEST / last-Sunday-październik→CET i wybiera właściwy offset per chwila.
Reguła EU DST odtworzona ręcznie (bez zależności od tzdata). Na allowliście ratcheta świadomie.
Po 25.10 zwróci `+1` poprawnie. (Drobny dług: mogłoby być `ZoneInfo` — ale bezpieczne.)

**Pozostałe trafienia (wszystkie POZA żywym silnikiem, poprawnie wykluczone):**
- `tests/*` — strażnicy TZ trzymają `timezone(timedelta(hours=2))` jako **bazę mutacji** i
  **negatywne asercje** (`test_grafik_fetch_schedule.py:148 assert "...hours=2))" not in src`).
- `eod_drafts/2026-06-*` — ~20 jednorazowych skryptów analizy, datowane VI 2026, okno DST-CEST
  (komentarze „czerwiec CEST, no DST transition"). Nie-live, nie-cron, nie-import.

`grep "+02:00"/"+01:00"/utcoffset/WARSAW_OFFSET` → tylko `tests/` + `eod_drafts/` (jw.).
**W żywym silniku: ZERO.**

---

## Stan ratcheta (strażnik regresji)

`tests/test_tz_zoneinfo_consolidation.py` — 3 warstwy allowlist, wszystkie skurczone/czyste:
- `_ALLOWLIST` (repo wewn.) = **tylko `tools/ontime_lib.py`** (poprawny wzór DST). drive_speed zdjęty.
- `_EXTERNAL_ALLOWLIST` (żywe scripts-root) = **pusty set** → gastro_assign/fetch_schedule/schedule_utils
  MUSZĄ być czyste, inaczej test PADA. Test `test_ratchet_external_scripts_no_new_fixed_offset` = **PASS**.
- `_GUARDIAN_TESTS` — 3 testy trzymające fixed-offset jako mutację (osobna kategoria).

**Run dowodowy (venv dispatch):** `test_tz_zoneinfo_consolidation.py` + `test_grafik_fetch_schedule.py`
+ `test_drive_speed_overshoot_tz.py` → **32 passed, 1 failed**. Winter kill-testy, mutation-check
(rewers ZoneInfo→+2 daje 1395 min = bomba), parytet letni, ratchet zewn. — WSZYSTKIE zielone.

---

## Jedyny FAIL = NIE-TZ (stale-mirror, kierunek odwrotny)

`test_parity_live_equals_staged_mirror[fetch]` **FAIL** — żywy `fetch_schedule.py` rozjechał się ze
staged o **5 linii = fix None-sort** (`sorted(..., key=lambda x: x[1]['start'] or "99:99")` + komentarz,
dodany dziś 08.07, backup `.bak-pre-none-sort-2026-07-08`). To fix bezpieczeństwa sortowania logu
(wpisy salvage `start=None` → `TypeError` blokował zapis pliku), **NIE ma związku z TZ**.

**Kierunek driftu: ŻYWY jest DO PRZODU względem staged** (żywy = wszystko-co-staged + None-sort).
Czyli TZ w żywym jest w 100% pokryte; staged mirror jest o krok W TYLE. To odwrotność ostrzeżenia
„staged-za-ACK" — nic nie brakuje w produkcji.

**⚠ Ukryte ryzyko (nie-TZ, ale warte flagi):** gdyby ktoś zrobił naiwny deploy `deploy_staging/scripts/
→ scripts-root` (klasyczny „wypchnij staged"), **cofnąłby fix None-sort** (staged go nie ma). TZ
by przeżyło (staged ma ZoneInfo), ale wróciłby `TypeError` na wpisach salvage. Dlatego staged
mirror trzeba zsynchronizować DO PRZODU (żywy→staged), nie odwrotnie.

---

## Podsumowanie liczbowe

- **Fixy LIVE:** 5/5 klas (3 żywe scripts-root + 7 narzędzi repo) + 1 bezprzedmiotowy (skasowany kod).
- **STAGED-only (brak w żywym):** **0.**
- **Za flagą OFF:** 0 dla TZ. (`ENABLE_GRAFIK_ENTRY_SALVAGE=true` w flags.json dotyczy salvage
  wpisów grafiku, NIE TZ — fix H1 `datetime.now(_WARSAW)` jest bezwarunkowy.)
- **Rezydualne bomby fixed-offset w żywym silniku:** **0** (`ontime_lib` = poprawny DST-aware wzór).
- **Restart potrzebny by TZ było live:** **NIE** — wszystkie 3 żywe skrypty to świeże procesy
  (cron / subprocess), a narzędzia repo są one-shot.

## Komendy za-ACK (dla Adriana — NIE wykonane, tylko rekomendacja)

**Nic do domknięcia po stronie TZ.** Poniższe = higiena stale-mirror (nie-TZ), opcjonalne:

Synchronizacja staged mirror DO PRZODU (żywy→staged) by parity-test zzieleniał i przyszły
„deploy staged→live" nie cofnął fixu None-sort (NIE dotyka produkcji ani TZ):
```bash
# (za-ACK, higiena; kopiuje ŻYWY fetch_schedule → staged mirror w repo — kierunek żywy→staged)
cp /root/.openclaw/workspace/scripts/fetch_schedule.py \
   /root/.openclaw/workspace/scripts/dispatch_v2/deploy_staging/scripts/fetch_schedule.py
# potem w repo: git add deploy_staging/scripts/fetch_schedule.py && git commit
# (to zadanie osobnej sesji — patrz uwaga lead: parity[fetch] to inna sprawa/sesja)
```

Weryfikacja że TZ trzyma (dowód, read-only, w każdej chwili):
```bash
/root/.openclaw/venvs/dispatch/bin/python -m pytest \
  /root/.openclaw/workspace/scripts/dispatch_v2/tests/test_tz_zoneinfo_consolidation.py -q
# oczekiwane: all pass (winter kill + mutation + external ratchet)
```
