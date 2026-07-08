# SPRINT E — Tablica zdrowia + budżet błędu (SLO) — RAPORT

**Data:** 2026-07-08 · **Sesja-wykonawca:** tmux 40 · **Branch:** `obs/error-budget-scoreboard`
**Worktree:** `/root/.openclaw/workspace/scripts/wt-error-budget` · **Baseline master:** `8a13b77`
**Charakter:** czysto READ-ONLY obserwowalność. Zero mutacji kolektorów/silnika. Zero flipów. Zero restartów.

---

## 1. CO ZROBIONE (jednym zdaniem)
Nowy **standalone agregator** `tools/health_scoreboard.py`, który czyta 6 żywych logów, liczy **budżet błędu / SLO-burn** per metryka i wypisuje **dzienną kartę 🟢/🟡/🔴** (`dispatch_state/health_scoreboard_card.{md,json}`) + sekcję „co wymaga uwagi Adriana". Jedyna zmiana stanu jaką robi = zapis TEJ karty.

## 2. ETAP 0 — STAN NA ŻYWO (dowody, nie deklaracje)
- Worktree czysty na HEAD `8a13b77` (git status pusty przed pracą).
- **Baseline pełnej regresji (przed pracą):** `1 failed, 4465 passed, 27 skipped, 10 xfailed` (136s).
  - Jedyny fail = `test_grafik_fetch_schedule.py::test_parity_live_equals_staged_mirror[fetch]` — **PRE-EXISTING, NIE MÓJ**: to stale-mirror sprintu grafik-S2 (żywy `fetch_schedule.py` dostał None-safe sort `x[1]['start'] or "99:99"`, staged mirror w `deploy_staging/scripts/` nie zaktualizowany; commit `03bf2cf`). Poza zakresem Sprint E (C1 — nie tykam cudzego pliku/live).
- **Multi-sesja recon (C1):** 5 sesji tmux (34/35/38/39/40 — jestem 40). Brak świeżych `.bak` w repo (izolacja worktree). Bezkolizyjnie: mój jedyny zapis to nowy plik `health_scoreboard_*`, którego nic innego nie czyta ani nie pisze.

## 3. E1 — 6 ŹRÓDEŁ (zbadany REALNY schemat, nie założony)
| # | Log | Ścieżka | Co czytam | Uwaga |
|---|---|---|---|---|
| 1 | `shadow_decisions.jsonl` | `scripts/logs/` (70MB, rot. `.1`) | verdict/auto_route, KOORD, best_effort, redirecty, latency_ms, pula feasible | byte-tail + rotation-aware |
| 2 | `eta_calib_metrics.jsonl` | `dispatch_state/` | `legs.{pickup,delivery}.coverage.ONTIME_operacyjna`, spoznien_pct, MAE, target_ontime | CIEŃ (flaga ETA OFF) |
| 3 | `pending_global_resweep.jsonl` | `dispatch_state/` (5.9MB) | `g_claim_ledger_breaches`, would_repropose, no_courier, reason | live |
| 4 | `proposal_churn.log` | `scripts/logs/` | wiersze per-doba `≥1% ≥3% śr flick_same%` | raport TEKSTOWY (dedup per-doba, last-wins) |
| 5 | `night_guard_history.jsonl` | `dispatch_state/` | `pytest.failed`, entropy poison_live/instr, verdict | nightly |
| 6 | `pickup_slip_monitor.jsonl` | `dispatch_state/` | mediana poślizgu solo/bundle (ważona n), segmenty | DODATNI=optymistyczny |

Rotation-aware (C16 / L1.2): własny lekki reader siblingów `path`,`.1`,`.2`,… (stdlib, bez couplingu do `ledger_io` — moduł ma być standalone). Sprawdzone: dla okna 24h live pokrywa (earliest≤since → `.1` pominięty), więc 0.66s mimo 70MB.

## 4. E2 — BUDŻET BŁĘDU / SLO-BURN (definicje UDOKUMENTOWANE)
Wzór burn (% zjedzonego budżetu; 100% = budżet w pełni skonsumowany):
- **cel „≥ X%"** (on-time): budżet = 100−X; zjedzone = 100−actual; `burn = zjedzone/budżet`.
- **cel „== 0"** (breaches, pytest.failed, no_courier): 0 → 0% 🟢; ≥1 → naruszenie 🔴.
- **cel „≤ C"** (KOORD-rate, latency p95, flicker): `burn = actual/C`.
- **Kolory:** 🟢 burn <75% · 🟡 75–100% · 🔴 >100% · ⚪ za mało danych.

| Metryka | Cel | Rodzaj | Źródło celu |
|---|---|---|---|
| Claim-ledger breaches | = 0 | 🔒 twarda | inwariant no-double-book (Sprint B) |
| BRAK KANDYDATÓW (no_courier) | = 0 | 🔒 twarda | reguła always-propose |
| Nocny pytest failed | = 0 | 🔒 twarda | pełna regresja nocna |
| ETA on-time odbiór/dostawa | ≥ 80% | 🔒 twarda | `target_ontime` z DANYCH kalibracji |
| KOORD-rate | ≤ 10% | ≈ prowizoryczna | heurystyka — **ACK Adriana** |
| Latencja p95 (shadow) | ≤ 2500 ms | ≈ prowizoryczna | pułap operacyjny shadow; ideał 500ms=pre-Hetzner — **ACK** |
| Flicker ≥3 zmian | ≤ 45% | ≈ prowizoryczna | heurystyka — **ACK Adriana** |
| Poślizg odbioru | — | informacyjna | wejście do bufora, NIE alarm |

Progi „prowizoryczne" są JAWNIE oznaczone w karcie (`≈` + „ACK Adriana") — nie udają twardego SLO.

## 5. E3 — WPŁYW ON/OFF ŻYWYCH FLAG (uczciwie: obserwacja, NIE istotność)
Karta pokazuje TREND per-doba/rekord (ETA on-time, nocny guard, churn, poślizg) ze znacznikami znanych flipów (K2/K3 ~05.07, O2-K1 werdykt ~09.07, CHECK claim-ledger ACK 08.07, ETA w cieniu). **Jawnie napisane, że to obserwacja, nie test istotności** — okna 2-dniowe nie są domknięte, przyczynowości NIE orzekamy (watchpoint handoffu: gdzie za mało danych → „za mało danych", nie „poprawa”).

## 6. E4 — KARTA Z REALNYCH LOGÓW (DoD #2, przykład)
Wygenerowana `2026-07-08T17:44Z`, **stan ogólny 🟡**, 0 czerwonych:
```
🟢 Claim-ledger breaches   suma=0 w 513 rek.                         burn 0%
🟢 BRAK KANDYDATÓW          0/513 (0.0%)                             burn 0%
🟢 Nocny pytest             failed=0 passed=4451 verdict=OK          burn 0%
🟡 ETA on-time ODBIÓR       81.6% (cel ≥80%, spóźnień 18.4%, MAE 5.33)  burn 92%
🟡 ETA on-time DOSTAWA      83.2% (cel ≥80%, spóźnień 16.8%, MAE 7.38)  burn 84%
🟡 KOORD-rate               20/245 (8.2%); best_effort 10.2%         burn 82%
🟡 Latencja p95 (shadow)    p95=2242ms p50=995ms max=3073ms          burn 90%
🟢 Migotanie ≥3 zmian       2026-07-07: 30.1%                        burn 67%
```
Źródła odczytane: shadow_n=245, resweep_n=513, eta/churn/night_guard/pickup_slip = OK.
**Co wymaga uwagi Adriana** (same 🟡, żadne czerwone): ETA on-time odbiór blisko granicy budżetu (92%); KOORD-rate 82%; latencja shadow (ogon peak = flota/kontencja, sprint A perf w toku — NIE regresja).

## 7. DoD — DOWODY
1. **Regresja ZIELONA** (po pracy): `1 failed, 4490 passed, 27 skipped, 10 xfailed` — delta vs baseline = **+25 passed (moje testy), 0 NOWYCH failów** (ten sam pre-existing grafik-fail). ✓
2. **Karta z REALNYCH logów** (nie mock) — patrz §6 + `dispatch_state/health_scoreboard_card.{md,json}`. ✓
3. **Testy agregacji** `tests/test_health_scoreboard.py` — **25/25**: matematyka burn (ge/le/zero), parse_ts, percentyl, wszystkie 6 loaderów, **przypadki brzegowe**: pusty log, brak pola, okno bez danych, rotation-aware, breach→🔴, pytest-fail→🔴, brak danych→⚪ (nie zmyślony 🟢), main() pisze WYŁĄCZNIE do out-dir (asercja anty-PROD). Testy NIE piszą do prod `dispatch_state` (zweryfikowane: brak `health_scoreboard_*` po biegu testów). ✓
4. **Timer PRZYGOTOWANY, NIE zainstalowany** — komentarz na końcu `health_scoreboard.py` (wzór `dispatch-proposal-churn.{service,timer}`, OnCalendar 05:30 UTC po churn/eta_calib). Instalacja = ACK Adriana. ✓
5. **Commit przed końcem** + ten raport. ✓ (patrz §9)

## 8. ANTY-KOLIZYJNOŚĆ (dlaczego bezpieczne wobec C/D/A/B + cień ETA)
Jestem czystym KONSUMENTEM. NIE tknięto: route_order/render (C), pipeline (D), solver OR-Tools (A), feasibility/claim-ledger (B), route_simulator, ETA/obietnica. Zero flag, zero restartów, zero timerów zainstalowanych. Jedyna zmiana stanu = nowy plik karty (nazwa `health_scoreboard_*`, nic innego jej nie czyta/pisze).

## 9. GIT / ROLLBACK
- Commit na branchu `obs/error-budget-scoreboard` (worktree): `tools/health_scoreboard.py` + `tests/test_health_scoreboard.py` + ten raport.
- **Rollback:** `git revert <commit>` (nowe pliki, brak zależności — zero wpływu na silnik). Karta w `dispatch_state/` = artefakt runtime, można skasować bez konsekwencji.
- **Merge do master = SERYJNY po ACK** (C12c). Nic do flipowania.

## 10. OTWARTE / DO DECYZJI ADRIANA
1. **Progi prowizoryczne** (KOORD-rate ≤10%, latency p95 ≤2500ms, flicker ≥3 ≤45%) — potwierdzić / poprawić.
2. **Instalacja timera** (05:30 UTC dziennie) — ACK.
3. Ew. rozszerzenie E3 o realny test istotności okien 2-dniowych, gdy flipy się domkną (dziś: za mało domkniętych okien).
