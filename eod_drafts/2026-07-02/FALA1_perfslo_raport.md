# FALA 1 — lane perf-SLO (finding E audytu 2.0) — RAPORT

**Data:** 2026-07-02 · **Branch:** `fix/perf-slo` (worktree `/root/.openclaw/workspace/wt-perf`, baza HEAD `c6e2c13`)
**Commity (nie pushowane, nie na master):**
- `e37129c` — `tools/perf_budget_report.py` (NOWY, pomiar)
- `69352d5` — `tools/objm_lexr6_canary_monitor.py` (rozszerzenie o sekcję SLO za flagą)
- `4a42e78` — `tests/test_perf_budget_slo.py` (28 testów + mutation-check)

**Cel lane'u:** finding E = regres wydajności 2× (p50 ~840 ms vs kwiecień 375, cel 150-200), płaski 14 dni. **Fix samego regresu = OSOBNA przyszła fala.** Ten lane dostarcza POMIAR + SLO + ALERT — żeby regres nigdy więcej nie był niewidzialny. Zero nowej infry (rozszerzenie istniejącego canary + jeden read-only tool). Trzymam definicje z `AUDYT2/PERF_budget.md` (§5a SLO, §1 trend, §2a pole latencji).

---

## 1. STAN ZASTANY (liczby z ŻYWEGO ledgera, READ-ONLY)

Odczyt kanonem `ledger_io.iter_shadow_decisions`, okno pinowane `[2026-06-18, 2026-07-02)`:

| metryka | wartość |
|---|---|
| n (14 dni) | **3535** |
| p50 | **852 ms** |
| p95 | **1939 ms** |
| p99 | **2720 ms** |
| max | **6005 ms** |
| ogon > 1500 ms | **13,1 %** |
| vs kwiecień (375 ms) | **×2,3** |

Zgodne z PERF_budget (841/1906 team-lead; 13,1% ogona §4). Pole latencji = `latency_ms` (top-level span decyzji, `shadow_dispatcher.py:1212`) — zweryfikowane na żywym rekordzie: JEDYNY wariant klucza „latency" w `shadow_decisions` (n=3533/14d w probie).

**Wszystkie 3 segmenty SLO łamią budżet §5a** (to jest właśnie „regres uczyniony widzialnym"):

| segment (Warsaw) | n | p50 vs limit | p95 vs limit |
|---|---|---|---|
| peak 11-14+17-20 | 1893 | 857 / 700 🔴 | 1847 / 1500 🔴 |
| high-risk 14-17 | 1057 | 943 / 800 🔴 | 2215 / 1800 🔴 |
| off-peak | 585 | 638 / 450 🔴 | 1668 / 900 🔴 |

Łącznie **8 naruszeń** (6× p50/p95 + 2× ceiling: peak 8 decyzji >3000 ms, high-risk 9). Najgorsze dni: 06-21 (p50 1082, ogon 22,6%), 06-28 (1049, 16,4%), 06-26 (1008, 24,3%). Najgorsze godziny UTC 11-14 (= 13-16 Warsaw): p50 914-1029, p95 2033-2241. **To jest baseline pomiarowy przyszłego fixu regresu.**

---

## 2. ZMIANY

**A. `tools/perf_budget_report.py` (NOWY, READ-ONLY).** p50/p95/p99 + n + %ogona>1500 ms per dzień (trend 14d, data Warsaw), per godzina UTC (peak 09-12 UTC = 11-14 Warsaw) i per segment SLO (Warsaw) z porównaniem do §5a. Wyjście: tabela tekstowa + JSON (`--out`, domyślnie `/tmp/perf_budget_report.json`). Okno pinowalne (`--since/--until`) → determinizm. TZ = `ZoneInfo("Europe/Warsaw")` — **NIGDY fixed-offset +2** (świadomie, wobec bomb TZ C/D audytu). Progi SLO w JEDNYM miejscu (`SLO_SEGMENTS`, env-override) — konsumowane też przez canary → brak dryfu bliźniaczych progów.

**B. `tools/objm_lexr6_canary_monitor.py` (rozszerzenie).** Nowa sekcja SLO za flagą **`ENABLE_PERF_SLO_ALERT` (DOMYŚLNIE OFF)**:
- edge-triggered: alert TYLKO przy WEJŚCIU w breach (stan w NOWYM `dispatch_state/perf_slo_state.json` — brak kolizji, sprawdzone grepem; wzór `_notify_decision` + `objm_lexr6_canary_notify_state.json`),
- Telegram tylko przy `--notify` i tylko na zmianę werdyktu (+ rzadkie przypomnienie),
- **czysto addytywne**: nowe stałe + funkcje (`_perf_slo_enabled`, `_slo_verdict_signature`, `_slo_notify_decision`, `_load/_save_slo_state`, `_run_perf_slo`) + JEDEN gated blok przed `return 0`. `shadow_metrics`/`gates`/`_notify_decision`/istniejące metryki objm-lexr6 — NIETKNIĘTE.

**C. `tests/test_perf_budget_slo.py` (NOWY, 28 testów).** Behawioralne (C13): percentyl (ręcznie policzony), segmentacja Warsaw (DST lata), `evaluate_slo` (breach p50/p95/ceiling + gating min_n), `collect` (filtr okna + `latency_ms`, `ledger_io` zamockowany), edge-trigger (breach/persist/eskalacja/recovery/steady/remind), mutation-guard progu p95.

---

## 3. DOWODY (nie deklaracje)

**Bajt-parytet canary (flaga OFF) — KRYTYCZNE dla at-200:** OLD (`git show HEAD:…canary`) vs NEW, flaga OFF, to samo okno (`--window-min 40000 --baseline /nonexistent`), normalizacja TYLKO stempla wall-clock nagłówka:
- `diff OLD NEW` → **PUSTY** → BAJT-IDENTYCZNE.
- kontrola stabilności: `diff OLD OLD(ponownie)` → **PUSTY** (żywe pliki statyczne w oknie testu → parytet miarodajny).
- ⇒ przy fladze OFF (stan domyślny, w którym at-200 03.07 uruchamia canary) rozszerzenie jest niewidzialne. **Checkpoint at-200 bezpieczny.**

**Flaga ON ≠ OFF:** przy `ENABLE_PERF_SLO_ALERT=1` dochodzi sekcja `## PERF-SLO` (OFF=12 linii → ON=21), licząca per-segment breach identycznie jak `perf_budget_report` (to samo źródło progów). Bez `--notify` — zero Telegrama i zero zapisu stanu.

**Mutation-check (C13, in-memory → test MUSI PAŚĆ):**
- podniesienie `SLO_SEGMENTS["peak"]["p95"]` → `test_slo_peak_p95_and_p50_breach` **FAILuje**,
- podniesienie `ceiling` → `test_slo_ceiling_fires_below_min_n` **FAILuje**,
- zamrożenie `_slo_verdict_signature` (stała) → `test_slo_escalation_new_segment_sends` i `test_slo_recovery_to_ok_sends_once` **FAILują**.
- ⇒ progi i warunek edge są nośne (nie teatr).

**Determinizm raportu:** to samo pinowane okno, 2 przebiegi → JSON **IDENTYCZNY** (poza `generated_at`, wall-clock). overall n=3535, 8 breachy stabilne.

**Pełna regresja (post-merge equivalent):** uruchomiona na REALNEJ KOPII kanonu w `/tmp` z nałożonymi 3 plikami (bez symlinka → bez artefaktu `Path(__file__).resolve().parents[2]`):
- **3737 passed, 0 failed, 23 skipped, 11 xfailed** vs baseline **3709 passed / 0 failed / 23 skipped / 9 xfailed / 2 xpassed**.
- Δ passed = **+28** = dokładnie nowe testy. **ZERO nowych FAILi.**
- Przesunięcie xpass(2→0)/xfail(9→11): suma xfail-znakowanych = 11 w OBU przebiegach; to pre-istniejący, niedeterministyczny „xfail-ratchet" w `test_demote_tier_bucket_p4` / `test_invariant_slots_l04` / `test_obj_food_age_bug5` — **żaden plik nie tknięty przeze mnie** (moje 3 pliki: 0 znaczników xfail).
- Kanon nietknięty: `sha256sum -c` na canary+common.py po całym biegu → **OK**.
- `py_compile` wszystkich 3 plików → OK. Import-check (`_perf_slo_enabled()`=False domyślnie) → OK.

> ⚠ Uwaga o rozdzielczości pakietu (dla koordynatora): `tests/conftest.py` twardo wpina `_SCRIPTS_ROOT=/…/scripts` na sys.path, więc `pytest tests/` odpalone WPROST z worktree importuje `dispatch_v2` z KANONU (moje pliki niewidoczne → nowy test rzuca ImportError). Dlatego regresję biegłem na realnej kopii kanonu z nałożonymi plikami = wierny obraz PO MERGE. Po scaleniu do kanonu `pytest tests/` z kanonu zadziała wprost.

---

## 4. DEPLOY ZA ACK (rekomendacja — sam nie flipuję/nie restartuję)

Nic tu nie deployuję (produkuję, nie wdrażam). Rekomendacja kolejności:
1. **Merge SZKIELETU (3 commity) — bezpieczny PRZED at-200 (03.07).** `perf_budget_report.py` = nowy, nieimportowany przez nic żywego. Rozszerzenie canary ma dowód bajt-parytetu przy fladze OFF → canary w at-200 (flaga OFF) daje identyczne wyjście. **Nie ma powodu czekać do po 03.07.** (Gdyby koordynator wolał 0 ryzyka procesowego wobec at-200 — merge samego `perf_budget_report.py` teraz, canary tuż po 03.07; ale technicznie bajt-parytet to pokrywa.)
2. **Okres log-only (bez flipa):** obserwuj wydajność `perf_budget_report.py` (np. ad-hoc / z crona read-only). Flaga alertu ZOSTAJE OFF — to potwierdza kalibrację progów §5a i że nie ma nadmiaru alertów, ZANIM cokolwiek pójdzie na Telegram.
3. **Flip `ENABLE_PERF_SLO_ALERT=1` (flags.json) — decyzja koordynatora/Adriana, off-peak.** Pierwszy bieg canary z `--notify` po flipie = pierwszy realny alert. Rollback = flaga z powrotem OFF (hot, bez restartu — canary to timer oneshot; przy dłuższym procesie kolejny tick czyta świeżo).

⚠ Ceiling off-peak (>2500 ms pojedynczej decyzji) to najostrzejszy warunek §5a — po pierwszej dobie log-only warto zweryfikować, czy nocne outliery nie generują szumu; próg jest env-override'owalny (`PERF_SLO_OFF_CEILING`).

---

## 5. ROLLBACK

- **Domyślnie inertne:** flaga OFF = zero zmiany zachowania (bajt-parytet). Nic do cofania po samym merge.
- **Po flipie:** `ENABLE_PERF_SLO_ALERT=false` w `flags.json` (hot) → alert milknie.
- **Twardo:** `git revert 69352d5` (canary) / usunięcie `perf_budget_report.py` (standalone, read-only, nikt nie importuje przy fladze OFF) / `git revert 4a42e78` (testy).
- Brak zmian systemd/stanu/Telegrama z mojej strony → brak innych śladów.

---

## 6. POZA PARTYCJĄ (czego NIE ruszałem)

Zgodnie z zakazami: **zero** `systemctl`/`daemon-reload`, zero zapisów poza worktree (poza `/tmp` scratchpad), `flags.json`/`/etc` nietknięte, **zero Telegrama** (nawet testowego — wszystkie biegi bez `--notify`), zero `git push`, zero commitów na master, zero `pip install`. Tknięte wyłącznie: `tools/objm_lexr6_canary_monitor.py` + NOWY `tools/perf_budget_report.py` + NOWY `tests/test_perf_budget_slo.py` + ten raport. **Fix samego regresu (człony compute-zawsze) — świadomie POZA tym lane'em** (osobna fala; ten lane daje jej baseline + dozór).
