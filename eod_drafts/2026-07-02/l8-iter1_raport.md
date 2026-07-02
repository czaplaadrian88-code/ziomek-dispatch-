# L8 iteracja 1 — raport (pas C: usunięcie 2 martwych modułów P1)

**Data:** 2026-07-02 · **Branch:** `fix/l8-iter1` (worktree `/root/.openclaw/workspace/wt-l8-iter1`) · **Charakter:** P1, chirurgiczne kasowanie udowodnionego martwego kodu. ZERO flipów/restartów/push.

## Cel
Usunąć 2 moduły z klasy P1 mapy `eod_drafts/2026-07-02/L8_deadcode_mapa.md` §1:
- `eta_error_report.py` (185 LOC) — raport postępu kalibracji ETA, standalone.
- `td20_caller_report.py` (117 LOC) — jednorazowy raport TD-20 rozkładu `caller=` dla haversine sentinel (0,0).

Razem **302 LOC** usunięte.

## ETAP 0 — re-weryfikacja dowodów NA ŚWIEŻO (dowody mapy powtórzone, nie zaufane)

Mapa powstała ~4h wcześniej; 5 pasów mergowało od tego czasu. Wszystkie dowody powtórzone.

### Dowód 1 — ZERO importerów (repo + scripts + testy)
```
grep -rn "eta_error_report\|td20_caller_report" --include='*.py' .   (worktree)
grep -rn ... /root/.openclaw/workspace/scripts/*.py
grep -rln ... /root/.openclaw/workspace/scripts/dispatch_v2
grep -rln ... /root/.openclaw/workspace/wt-l8-iter1/tests
```
Wynik: **jedyne trafienia = wewnątrz samych kasowanych plików** (docstring/usage/print) + dokumenty audytowe w `eod_drafts/` (A1_module_layer_map.md, L8_deadcode_mapa.md, ROADMAPA_PO_NAPRAWACH_DEEPDIVE.md — opisy, nie kod). Zero `import`/`from ... import`/`-m` w żywym kodzie i testach. **Brak dedykowanego testu** dla obu (`find tests -iname '*eta_error*' -o -iname '*td20*'` = pusto).

### Dowód 2 — nie w systemd / cron / at
```
grep -rln ... /etc/systemd/system        → pusto
crontab -l | grep ...                     → pusto (przejrzano też pełny crontab)
atq (joby 200-206) + at -c per-job grep   → żaden nie referuje modułów
```

### Dowód 3 — brak subprocess / wywołań pośrednich w całym workspace
```
grep -rln ... /root/.openclaw/workspace  (excl. eod_drafts + same pliki + __pycache__)
```
Tylko 2 trafienia, oba NIE-callery:
- `scripts/logs/td20_caller_report.log` — **output** modułu, mtime **2026-05-19 19:00** (54 B, jednorazowy manualny run 1,5 mies. temu; żaden serwis nie pisze do tego logu — `grep td20...log /etc/systemd/system` = pusto).
- `scripts/logs/restic_backup.log` — 1247 trafień = listing inwentarza plików w snapshotach backupu (przypadkowy substring, nie wywołanie).

**Werdykt ETAP 0:** oba moduły martwe, dowody trzymają. Zero trafień poza samym plikiem = brak STOP.

## Wykonanie
- `git rm eta_error_report.py td20_caller_report.py` (jawnie, martwy kod).
- Brak osieroconych artefaktów do domknięcia (zero dedykowanych testów; sprzężonych flag brak — moduły standalone, nie czytają własnej flagi ENABLE_*).
- `python -m compileall -q . -x '(eod_drafts|\.bak)'` → **exit 0** (nic nie importowało kasowanych; pakiet kompiluje się czysto).

## Regresja — dowód delta=0 (identyczny harness, pliki obecne vs usunięte)

Domyślny bieg pytest z worktree importuje `dispatch_v2` z KANONU (conftest `_SCRIPTS_ROOT`), więc kasowanie byłoby niewidoczne. Uruchomiłem suitę PRZEZ worktree: pkgroot w scratchpad z `dispatch_v2 → wt-l8-iter1` (symlink) + `flags.json → kanon`, `ZIOMEK_SCRIPTS_ROOT=pkgroot`. Potwierdzone: `importlib.find_spec('dispatch_v2.eta_error_report')` = `None`, `...td20_caller_report` = `None`.

| Bieg | passed | failed | skipped | xfailed | xpassed |
|---|---|---|---|---|---|
| Pliki OBECNE (restore) | 3970 | 23 | 26 | 9 | 2 |
| Pliki USUNIĘTE (git rm) | 3970 | 23 | 26 | 9 | 2 |

**Delta = 0.** Kasowanie martwego kodu nie zmienia żadnej liczby.

### O 23 „failed" — artefakt harnessu, NIE regresja
Te 23 (`test_courier_reliability.py`, `test_a2_selection_shadow.py`) rekonstruują ścieżkę absolutną zakładając, że katalog pakietu nazywa się dosłownie `dispatch_v2`: `Path(__file__).resolve().parent.parent / "dispatch_v2" / "tools" / ...`. W worktree `.resolve()` idzie po symlinku do `wt-l8-iter1`, więc ścieżka celuje w nieistniejący `/root/.openclaw/workspace/dispatch_v2/tools/...` → test sam rzuca `SkipTest` (custom wyjątek → traktowany jako fail). Występuje niezależnie od mojej zmiany (identyczne w obu biegach) i **NIE pojawia się na kanonie**, gdzie katalog nazywa się `dispatch_v2`.

**Uzgodnienie z baseline `3993/0/26/11xf`:** `3970 passed + 23 (self-fail pod nazwą worktree) = 3993` (dokładnie pass-count kanonu); `26 skipped` = zgodne; `9 xfailed + 2 xpassed = 11` markerów xfail. Wszystko się spina — zero nowych/prawdziwych failów z kasowania.

## Commit
Na `fix/l8-iter1`, `git rm` jawnie obu plików (patrz hash w wiadomości finalnej).

## Następni kandydaci P2 na iterację 2 (NIE ruszane — tylko wskazanie 2-3 najłatwiejszych)
Z mapy §1 P2, najniższe ryzyko do świeżej weryfikacji:
1. **`deploy_staging/scripts/gastro_assign.py`** (~120 LOC) — wg mapy IDENTYCZNY (md5) z żywym `scripts/gastro_assign.py`, nieaktualizowany mirror staging, nigdzie nie wołany. Re-weryfikacja: md5 vs żywy + grep `deploy_staging` w ExecStart/cron. ⚠ NIE dotykać żywego `gastro_assign` (partycja innych pasów).
2. **`sprint2_analysis/` (7 plików, 774 LOC)** — jednorazowa analiza sprintu 2, zero entry, referują tylko siebie + testy. Weryfikacja: grep basename'ów w systemd/cron/at + czy testy sprzężone (skasować razem).
3. **`speed_tier_tracker.py` (211 LOC)** — C4 standalone, żywy odpowiednik = `tools.build_speed_tiers` (cron). Weryfikacja: potwierdzić że cron woła `tools.build_speed_tiers` a nie ten moduł + sprzężona flaga `ENABLE_SPEED_TIER_LOADING_PLANNED` (skasować U ŹRÓDŁA razem z modułem).

⚠ Wymagają PYTANIA Adriana przed ruchem (mapa): `flags_admin.py` (panel `auto_assign_flag.py→flags_admin` — surface administracji flag), `replay_failed.py` (offline debug tool), `core/flags_io.py` (możliwa świeża infra L0.1). NIE w iteracji 2 bez ACK.
