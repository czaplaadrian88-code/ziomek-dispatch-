# RAPORT SESJI tmux 8 (noc 01/02.07) — L1.2 + L2.2/L2.3 + D.3 [Faza 3 audytu, fale 4-6]

**Handoff wejściowy:** `eod_drafts/2026-07-01/HANDOFF_sesja_tmux8_L12_L22_D3.md` (`60bf4da`). **Status: WYKONANY w całości poza 1 punktem bramkowanym** (bundle_calib_review → PO 08:00, patrz OTWARTE). Zasady ruchu dotrzymane: ZERO restartów silnika / flipów / dotknięć O2·bundle_calib·R6 / panelu / at-jobów 168·200·201 / flags.json / Telegrama. Multi-agent: 2 agentów recon+build (rozłączne pliki, bez gita) + koordynator commitował. Pamięć: **[[l12-l22-d3-sesja-tmux8-2026-07-02]]** (topic) + relay [[ziomek-unified-audit-2026-06-30]] FAZA 3 zaktualizowany.

## Commity (master, wszystkie wypchnięte na origin)
| Commit | Co |
|---|---|
| `fec417e` | **L1.2/T1** — ledger_io += `iter_sla`+`parse_sla_ts`; no_gps_eta_error + prep_bias_r6_replay (WRONG-SOURCE), pickup_slip_monitor (bramka 04.07), daily_rule_report, objm_lexr6_canary_monitor (at-200) |
| `f8ae4ce` | **L2.2+L2.3 BUILD-ONLY** — klasyfikacja data_poison/real_bug w `_v328_eval_safe`, serializacja `v328_fail_causes`, alert za flagą `ENABLE_V328_POISON_ALERT` OFF; SHIFT_FAIL_OPEN w schedule_utils |
| `3ba0fdc` | **L1.2/T2** — 10 replayów rotation-aware (iter_jsonl_lines) |
| `97f27e9` | **T1b+D.3** — b_route_shadow_review (3. WRONG-SOURCE; real_joined 0→322) + raport D.3 + adendum FAZA1_03 |
| `6fad935` | uzupełnienia D.3 + adnotacja L1.2 w #3 dashboardu entropii |
| `da2fa9b` | **L1.2/T3** — 15 tooli hardcoded [.jsonl,.1] (agent tier3b, zweryfikowany) |
| `e8a95d2` | **T3b dokładka** — 9 tooli puli bonusowej; klasa „hardcoded rotacja w tools/" WYCZERPANA (razem 40 narzędzi tej nocy) |

**Finalna pełna regresja: 3709 passed / 0 failed / 9 xfailed (4 stare + 5 slotów L0.4) / 2 xpassed (stare food-age).** Dowody parytetu per narzędzie: scratchpad sesji `l12_parity/`, `l12_tier2/`, `l12_tier3/` (backupy starych wersji + runy old/new + harnessy).

## ⭐ Wiedza trwała z tej sesji (dla każdej następnej)
1. **Żywy `scripts/logs/sla_log.jsonl` ≠ schemat martwego `dispatch_state/sla_log.jsonl`** (zamrożony 20.06): stemple `picked_up_at`/`delivered_at` są **naive=WARSAW** (writer sla_tracker/panel Rutcom), NIE ma pola `on_time` (ready-anchor) — jest `sla_ok` (delivered−picked_up≤35, kotwica ODBIÓR). Ślepe przepięcie źródła = +2h błędu joinu (zmierzone). **Kanon tej wiedzy = `ledger_io.parse_sla_ts` + docstring `iter_sla`** — każdy nowy konsument sla idzie przez nie.
2. **Kolejność plików rotacji a first/last-wins:** zmierzone 0 kolizji oid żywy↔.1 → dziś równoważne; kanon chronologiczny (.1→żywy) poprawny na przyszłość. Nowe narzędzia: wybór rekordu JAWNY (max ts / prio), nie przez kolejność iteracji.
3. **`schedule_utils.py` jest NIETRACKOWANY w żadnym repo** (workspace/scripts) — kandydat do adopcji do repo ziomka; zmiany tam mają rollback tylko przez .bak (scratchpad `l22_backup/`).
4. Lejek WSZYSTKICH post-eval returnów `assess_order` = `_classify_and_set_auto_route` (11 call-site'ów) — tam doczepiaj order-level telemetrię.

## DO DECYZJI ADRIANA (rano)
1. **Deploy L2.2** (klasyfikacja przyczyn + SHIFT_FAIL_OPEN): kod inert; wchodzi naturalnie w oneshot-timery (świeży proces), w dispatch-shadow po najbliższym ZBIORCZYM restarcie off-peak >14:00 — **za ACK**. Flip alertu = `ENABLE_V328_POISON_ALERT=true` w flags.json (hot-reload, bez restartu), rollback tak samo.
2. **D.3 migracja env-frozen→flags.json**: raport `D3_RECON_migracja_env_frozen_flags.md` (ten katalog). Fale A (12 neutralnych) i B (para V326 atomowo) = standardowy ACK; **Fala C (2 flagi asymetryczne) i D (USE_V2_PARSER) = pod-ACK**.
3. **Werdykt B-lite**: b_route_shadow_review ma wreszcie żywy ground-truth join (0→322) — re-run i decyzja „budować B-lite czy zamknąć" możliwa na PRAWDZIWYCH liczbach.

## OTWARTE (przejmuje ta sesja po budziku / następna)
- **`bundle_calib_review` = 4. WRONG-SOURCE** (martwy sla w outcome-joinie, fallback klik; gps_truth-arm żywy) — przepięcie DOPIERO PO bramce 08:00 (at-168); recepta = jak b_route_shadow_review (iter_sla + parse_sla_ts). Budzik sesji tmux 8 uzbrojony (Monitor 08:05).
- `min_delivered_at_verdict.py` (VOID, rotation-blind) — przepiąć przy ewentualnym re-runie (at-166 już odpalony, nie ruszane).
- OPEN z D.3 przed falą C/D: (1) czy recanon/redecide w panel-watcher wchodzą w gałęzie COMMITTED_PROPAGATION/LIVE_ETA_REFRESH, (2) mapa wołających parse_panel_html.
- ⚠ Uwaga porządkowa: parity-runy `obj_fresh_verdict_atrun` nadpisały durable `eod_drafts/2026-05-30/obj_fresh_verdict.md` (werdykt 06-06) — **przywrócone z gita** w tej sesji; przy przyszłych runach porównawczych tego toola pamiętać o jego side-efekcie zapisu.
