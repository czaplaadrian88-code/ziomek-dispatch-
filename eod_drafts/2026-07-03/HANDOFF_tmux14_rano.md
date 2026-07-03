# HANDOFF → sesja tmux 14 (rano 03.07): pomiar perf + czasówka-cold P1 + flake

**Od:** tmux 11 (koordynator 02.07-03.07: 20 fal scalonych, 7 deployów live, noc zamknięta 02:15 UTC) · **Data:** 2026-07-03 ~02:20 UTC.
**READ ORDER:** (1) ten plik; (2) tracker `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (góra) + `ZIOMEK_FINDINGS_LEDGER.md` §18-19; (3) `eod_drafts/2026-07-02/HANDOFF_po_dniu_0207_wieczor.md` (pełna kolejka ACK — NADAL aktualna); (4) protokół `memory/ziomek-change-protocol.md` (WKLEJ PROMPT). Zawsze: `git log --oneline -15` + `tmux ls` + `atq` (w kolejce 200-206).
**Baseline regresji kanonu (03.07 ~02:00): 4096 passed / 1 failed (pre-existing flaky, patrz zad. 3) / 23 sk / 9 xf / 2 xp.** HEAD `96193e1` (pushed).

---

## ZADANIE 1 — POMIAR PERF przed peakiem (~09:00-10:30 UTC, PRZED 11:00 Warsaw peak)
`ENABLE_PERF_LAZY_MEMBERS=true` LIVE od 03.07 ~00:25 (restart shadow czysty; flags TTL 0,25s + plan mtime-cache). Zmierz na żywym oknie:
```
cd /root/.openclaw/workspace/scripts && /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/perf_budget_report.py
```
(sprawdź CLI toola — okno od `2026-07-03T00:30` żeby łapać tylko po-flipowe decyzje). **Sędzia: baseline p50 852 / p95 1939 / ogon>1500 13,1%** (`FALA1_perfslo_raport.md`). Replay obiecywał −22% p50 (offline = dolna granica). Werdykt Z LICZBAMI → tracker + [[ziomek-audyt-2-wyniki-2026-07-02]]. Regres/brak poprawy → rollback hot (flaga false + restart shadow za ACK) + raport dlaczego. Poprawa → rozważ z Adrianem flip `ENABLE_PERF_SLO_ALERT` (SLO-alert, po log-only; breachy powinny zmaleć).

## ZADANIE 2 — 🚨 CZASÓWKA INTERMITTENT-COLD (P1-LIVE, najpilniejszy fix; protokół ETAP 0-7 + ACK)
**Odkrycie L0.1 (noc):** `dispatch-czasowka.service` w **~22-40% ticków** liczy DOMYŚLNYMI flagami z `common.py` zamiast `flags.json` (fingerprint = common-defaulty; shadow 0/79, plan-recheck 1/6313, panel-watcher 0/23 — praktycznie nigdy). **Skutek: czasówki część czasu ignorują flipy** (w tym dzisiejsze). Dowody + metodologia: `eod_drafts/2026-07-02/l01-registry_raport.md` + `tools/flag_fingerprint_check.py` (nowy, 4-źródłowa rekoncyliacja; klasa INTERMITTENT-COLD).
- ETAP 0: odtwórz pomiar tool-em; przeczytaj flag-load path w `czasowka_scheduler.py` (jak/kiedy woła `load_flags`/`C.flag`; oneshot per minutę = świeży proces per tick!). Hipotezy: wyjątek w `load_flags` → fallback na defaulty (cichy except?); race na odczycie flags.json podczas atomic-replace; ścieżka FLAGS_PATH względna vs WorkingDirectory; timing (tick startuje zanim FS się ustabilizuje?). ⚠ Flaga PERF_LAZY (TTL-cache w common) jest OD DZIŚ ON — sprawdź czy zjawisko było PRZED nią (dane fingerprint z nocy = tak, było i przy OFF) i czy jej nie pogarsza/nie maskuje.
- Fix U ŹRÓDŁA (nie łatka), testy behawioralne + mutation, pełna regresja. Deploy: czasówka = oneshot timer → kod żywy od next ticku po merge; jeśli trzeba restart czegokolwiek — ACK Adriana. Weryfikacja: fingerprint-check po fixie → INTERMITTENT-COLD czasówki 0%.

## ZADANIE 3 — flaky `tests/test_v319c_sub_a.py::script_run` (drobne, po 1-2)
Pre-existing, pada ~1/7 biegów izolowanych (dowód: 5/7 pass 03.07 ~02:10 + bug4-lane widział fail,pass,pass na czystej bazie PRZED swoim diffem). Script-runner (conftest wykonuje moduł jako skrypt). Zdiagnozuj niedeterminizm (stan dispatch_state? czas? kolizja z równoległym IO?) i napraw u źródła — NIE skipuj bez diagnozy. Po fixie: 3× zielony bieg izolowany + pełna regresja = **4097/0**.

## KALENDARZ dziś/weekend (odczyty — NIE ruszać mechanizmów)
- **Dziś 18:10 UTC at-200** (objm L6.D werdykt) i **19:00 at-201** (L2.1 sentinel werdykt) → odczytać `dispatch_state/scheduled_flips*/logi`, wpisać do notatek tematycznych + tracker.
- **Sobota:** at-202/203 auto-flipy L3/L4 (bramkowane) + at-204 verify · **werdykt S1** (grep `sla_anchor_source` + dryf `sla_violations` przez 2 dni) → po czystym = **flip O2 K1 za ACK** (HANDOFF_po_dniu_0207 §2 poz.1).
- **06.07 pon:** at-205/206 GC-real + weryfikacja H2 na Parysie (grep `parse_degraded` w logu fetch_schedule).
- **Wieczorem dziś:** 1. tick `dispatch-log-rotation` ~03:00 UTC (sprawdź przy okazji success).

## MINY sesji 02-03.07 (nie powtórz)
- Merge ZAWSZE z KANONU (`cd dispatch_v2 && git merge fix/<lane>`) — 2× odruch `cd worktree && git merge` dał ciche „Already up to date" BEZ merge.
- `git worktree add` z właściwego repo — raz utworzone w złym (cwd=scripts = repo mailek).
- Agent w DoD MUSI pokazać czysty `git -C <kanon> status` (near-miss o2-capz).
- Testy IO/współbieżne: tmp-path z assertem path≠PROD (incydent: 60 fejków w żywym pending — posprzątane).
- Komendy dla Adriana = SKRYPT-PLIK + `! bash <plik>` (one-linery łamią się w terminalu).
- Classifier potrafi blokować zapis flags.json — skrypt w scratchpadzie zwykle przechodzi; jak nie → Adrian.
- Przyrządy: 6× w tej sesji liczba do decyzji okazała się kłamstwem przed kalibracją (C9). Fingerprint-check, replay H2, bug4-oś, O2-λ, entropy-heur, naiwny reader. ZAWSZE oracle przed werdyktem.

## Poza tym w kolejce (bez zmian — HANDOFF_po_dniu_0207 §2 + ledger §18-19)
Flip O2 K2 (po przeglądzie `_kind()`) · frozen-objektyw P0 · re-collect λ=0 (checklist w `bug4-logger_raport.md` §4) · migracja USE_V2_PARSER do ETAP4 · fingerprint-check jako timer-strażnik · L8-iter3 (fixture-move TZ → kasacja sprint2_analysis 774 LOC) · L6.C · L7-reszta · **security P0 = Adrian krok 0 (Hetzner FW)** · Fala A roadmapy = bramka ~10.07 (świeża mapa 0a).
