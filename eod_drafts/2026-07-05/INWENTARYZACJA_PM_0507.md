# INWENTARYZACJA PM — Ziomek AI dispatcher (2026-07-05 ~21:30 UTC)

**Metoda:** 3 agentów read-only (komponenty: CODEMAP/ARCHITECTURE/ADR/INVARIANTS · audyty: tracker AUDYTY_1i2 + docs/audyt + memory · backlog: tech_debt + priorytety + handoffy S0-S3) + weryfikacja NA ŻYWO (flags.json, git, atq, systemctl, journal, ground_truth). Zero zmian w systemie podczas inwentaryzacji.
**Korekta metodyczna:** handoffy sprintów z rana 05.07 opisują S0/S1/S2/K5 jako TODO — wszystkie zostały tego dnia UKOŃCZONE (todo_master + konsolidacja „STAN KOŃCA DNIA" + żywe flagi). Statusy poniżej = stan faktyczny.

---

## 1. STATUS KOMPONENTÓW

Kanon: 10-warstwowy pipeline (ADR-001), shadow-first (ADR-002), always-propose (ADR-003), flagi=3 światy (ADR-004), stan poza repo (ADR-005), 3 interpretery (ADR-006), worktree multi-sesja (ADR-007), rdzeń nieprzenoszony (ADR-008). Baseline ~4236/0, master==origin (`0ce5b09`), inwarianty **0 VOID / ~27 strażników / 16 SLOT**, flags.json = 266 kluczy.

| Komponent | Stan / co działa | Wersja/commit | Status |
|---|---|---|---|
| Silnik dispatch (shadow_dispatcher, dispatch_pipeline, feasibility_v2, scoring, route_simulator_v2+tsp_solver, bliźniaki objm_lexr6/sla_anchor) | LIVE; restart K1 05.07 18:44 podniósł L6.C+L5+S1; **K2 `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK=True` ON** (żywo), obserwacja do wt 07.07 ~18:52 | `0ce5b09`; L6.C `d8328b2`, L5 `69727c9` | 🔵 W trakcie (okno flipów S2) |
| Kanon planu (state_machine, plan_manager, plan_recheck) | L3+L4+GC ON (żywo); GC w DRY do at-205 (Pn 12:40) | flip at-202/203 04.07 | ✅ (GC-realny Pn) |
| Ingest panelu (panel_watcher, panel_client) | Sentinel-ingest L2.1 ON (at-201 pozytywny: eject 0/dz vs 28/dz); L2.2/L2.3 + V328_POISON_ALERT ON | `eb016c1` | ✅ |
| Telemetria floty (courier_resolver, dispatch-gps) | last-known-pos ON; legacy :8765/:8443 wygaszone 05.07 za ACK; GPS-02 ~17.07 | — | 🔵 |
| courier_api :8767 | B4 hardening LIVE; **5b `POST /arrival` ŻYWY** (restart 16:40) | `313e6e5`, `e5b3dc0` | ⛔ adopcja: `gps_arrived_at` **1/553** (żywo 21:20) |
| Apka kuriera | vc70/0.9.56 oficjalny (169 testów); 5b od vc60 | vc70 | 🔴 flota bez vc60+ = RYZYKO #1 |
| Konsola koordynatora | podjazdy+carried-first, reassign-ghost 2-klik LIVE; **K5 live-resweep ZBUDOWANY, `PENDING_RESWEEP_LIVE=False`** (żywo) | `5b4d6e1` | 🟡 flip za ACK |
| O2 cap-Z (detour≤8 ∧ carried≤20) | `_capz_reseq_plan`; replay Z=20: **+10,2% improved, regres 0**; kluczy O2 BRAK w flags.json (dopisać przy flipie) | `4dcc3aa` | 🟡 ACK po at-208 (Pn 19:30) |
| L5 ETA load-aware | zbudowana; `ENABLE_ETA_LOAD_AWARE=false` (żywo, K4a); shadow zbiera `eta_la_*`; replay PASS bias −3,73→+0,42 | `69727c9` | 🟡 K4b za ACK (trade-off p90 +6→+11) |
| Claim ledger | zbudowany; `ENABLE_ENGINE_CLAIM_LEDGER=False` (żywo) | `d8328b2` | 🟡 K3 po zielonym K2 (~07-08.07) |
| Autonomia AUTON-01 | executor dormant, `ENABLE_AUTO_ASSIGN` OFF; blokery zdjęte poza dowodem 5b | — | ⏳ decyzja Adriana (S3) |
| Integracje (OSRM :5001, mosty papu/drtusz/epaka, Telegram) | OSRM za Cloud FW, lokalnie 200; mosty na timerach; **Telegram WYCISZONY 26.06 — nietykalny** | — | ✅ |
| Security P0 | Cloud FW przypięty + zweryfikowany z zewnątrz; host-most usunięty 20:30 (backup `.secrets/fw_backup/`); rotacje C1/C2/C3 | — | ✅ zamknięte 05.07 |
| Obserwowalność (night-guard, fingerprint, entropy, inwarianty) | night-guard: **unit FAILED z 01:15 05.07** — 2 faile (`test_flag_doc_coverage`, `test_v3273 kill-switch`), oba naprawione w dzień (`65d497c`, FLAGREG); bieg 06.07 01:15 zweryfikuje | `52f57d0` | 🟡 czeka zielony bieg |
| Ops finansowe (cod-weekly, git-push) | cod-weekly: fix w masterze + auto-create ON; unit FAILED (ostatni fail 29.06); run Pn 06:00, czujka tmux 20; git-push wrapper z tagami LIVE | `git_push_hourly.sh` | 🟡 domknięcie = zielony run Pn (⚠ split Ambiguous → Rafał) |
| ML/LGBM | offline, zero kontaktu z live | — | 💤 |

## 2. WERYFIKACJA AUDYTÓW

**Zaimplementowane (dowód):** K1 golden route-order 25 case'ów + parity LIVE (`5d24bc9`,`d729603`) · K2=L3 ON · K3=L5 OFF+replay PASS · K4 tripwire R-DECLARED ON (6,5%/dz naruszeń mierzone) · K5=L2.1 ON · K6 claim ledger OFF + D3 · K7 monitor parytetu ON (rozjazdy 0/dz od 29.06) · Security P0 komplet · 11 kłamiących przyrządów naprawione z oracle (12 commitów) · VOID 4→0 (Sprint 1) · PERF_LAZY (p50 −27%) + H1 desync LIVE (`9eeb9ab`).

**Pominięte/porzucone (świadomie, z pomiarem):** surowy O2 (łamie carried-first) · quant=1 (31% flipów gorszych) · feas_carry #483000 (rollback; wróci po 5b) · B3 no_gps · LAP/Hungarian (ROI ~0,12 min/zlec) · B-lite (MIXED) · COD backfill (bezprzedmiotowy) · martwa R7. Żadne = cichy dług; wszystkie mają werdykt liczbowy.

**Rozbieżności → wpływ na stabilność:**
1. ŚREDNI — kanon flag opisany sprzecznie (N2); klucze O2 trzeba DOPISAĆ, nie przestawić.
2. ŚREDNI — stare routery aider (N5/N13) sprzeczne z Przykazaniem #0 → ryzyko ominięcia protokołu przez inną sesję.
3. ŚREDNI — `schedule_utils.py` NIETRACKOWANY (fix grafiku poza gitem).
4. NISKI — stale liczby: „44-75 rozjazdów/dz" (realnie 0 od 29.06), lookup COMMIT_DIVERGENCE, baseline DoD 3611 vs ~4236, „master ahead" (żywo: ==origin).
5. NISKI/pilne — 2 unity FAILED (night-guard, cod-weekly): fixy w masterze, weryfikacja automatyczna 06.07 rano. Jeśli NIE zielone = realna regresja, nie pozostałość.

## 3. GAP ANALYSIS + DATY

**🔴 Krytyczne:** adopcja 5b 1/553 (blokuje werdykt→O2/feas_carry/autonomia; brak planu dystrybucji vc60+ w plikach) · 2 klucze DR off-machine (tylko Adrian).
**🟠 Wysokie (za ACK, FLIPMASTER):** K2-werdykt → K3 → K4b → K5 (dry-run driver; geometria MUSI zostać ON) → K6 O2-K1 (po at-208) → O2-K2 (parytet picku n≥10, `tools/o2_k2_pick_parity.py` po peaku Pn).
**🟡 Średnie (S3 ~10-31.07):** perf H2 transit-matrix (po pomiarze H1 wt) → H3 · Fala A kalibracja odbioru per segment (mapa 0a od ~10.07) → B histereza (41-43% flickera) → C (werdykt zasady Adriana) → D · przygotowanie 1. autonomii · raport feas_carry po 5b.
**🟢 Niskie:** 66 flag json→rejestr (PO oknie flipów) · bare-except `scripts/` za ACK · naprawa N2/N5/N12/N13 + tracking schedule_utils · 80 JSON bez schematu → SQLite? · BFG (Adrian) · HA (Adrian).

| Data | Wydarzenie | Bramkuje |
|---|---|---|
| Pn 06.07 01:15 | nocny strażnik po fixach | czyści FAILED, potwierdza baseline |
| Pn 06.07 06:00/06:12 | cod-weekly + czujka tmux 20 | WD-13 (⚠ Ambiguous split → Rafał) |
| Pn 06.07 12:40/14:30 | at-205 GC realny / at-206 verify | bezpieczeństwo GC |
| Pn 06.07 19:30 | at-208 review λ=0 | **ACK O2-K1** + parytet picku |
| wt 07.07 ~18:52 | koniec obserwacji K2 | ACK K3; raport K4b od wt wieczora |
| ~07-08.07 | werdykt 5b (OD adopcji!) | O2/feas_carry/autonomia |
| 10.07 | monitor route-order wygasa SAM | nic (następca aktywny) |
| ~10.07 | Sprint 3: Fala A + mapa 0a | fale A-D |
| ~17.07 | flip GPS-02 | telemetria W4 |
| 25-26.10 | DST | deploy TZ gastro_assign DUŻO wcześniej |

Braki dat (luka): 1. włączenie autonomii (celowo — Adrian) · BFG/DR/HA · dystrybucja vc60+ (a to ścieżka krytyczna).

## 4. PLAN SPRINTÓW WIELOAGENTOWYCH

Zasady twarde: worktree per agent mutujący (ADR-007) · **TYLKO FLIPMASTER (tmux 20) dotyka flags.json/restartów; 1 flip = 1 ACK** · Telegram nietykalny · peak = zakaz · merge seryjnie · pełna regresja vs baseline przed merge.

### Sprint F „Dowody i flipy" (06-09.07)
| Agent | Zadania | Effort | Granice (bezkolizyjność) |
|---|---|---|---|
| FLIPMASTER (tmux 20) | at-205/206/208 · parytet picku · dopisanie kluczy O2 + flipy K3→K4b→K5→K6 za ACK · k5_dryrun_driver | Średni | WYŁĄCZNOŚĆ: flags.json, restarty, atq. Zero edycji kodu. |
| Adopcja-5b | diagnoza czemu flota bez vc60 (dystrybucja APK, wersje z logów) + plan na Pn rano | Niski | READ-ONLY: courier-app (⚠ dzielone z sesją 15; courier_api = jej domena), nginx logi, ground_truth. |
| Ops-Zielone-Unity | weryfikacja night-guard 01:15 + cod-weekly 06:00; wsad dla Rafała przy Ambiguous; reset-failed po zielonych | Niski | journald + read-only; bramkowane czasem (jutro rano); cod-weekly czujka JUŻ w tmux 20 — nie dublować. |
| Docs-Spójność | N2 (kanon 3 światów), N5/N13 (routery aider), N12, stale liczby; tracking schedule_utils.py | Niski | TYLKO dokumenty (/root/CLAUDE.md, dispatch_v2/CLAUDE.md góra, ADR-004, 02-NIEZGODNOSCI) + git add schedule_utils; zero .py silnika. |

### Sprint G „Perf + Fala A + higiena" (~10-17.07)
| Agent | Zadania | Effort | Granice |
|---|---|---|---|
| Perf-H2 | transit-matrix w tsp_solver (eliminacja 53,7M callbacków) z parytetem replay; bramkowane pomiarem H1 (wt) | Wysoki | worktree: tsp_solver.py + interfejs route_simulator_v2; NIE scoring/feasibility/pipeline. |
| Fala-A | mapa 0a → kalibracja optymizmu odbioru per segment + wariancja dostawy ±17 | Wysoki | worktree: calib_maps.py, eta_truth_map; bliźniaki best_effort↔objm_lexr6 RAZEM; merge seryjnie z H2. |
| Flag-Hygiene | 66 flag json→rejestr (PO oknie flipów — ratchet!), DEPRECATE scoring.py, bare-except scripts/ za ACK | Średni | worktree: tools/flag_registry, LOGIC_REFERENCE, luźne scripts/gastro_*. |
| FLIPMASTER | GPS-02 ~17.07 + ew. flip Fala-A po replay | Niski | j.w. |

### Sprint H „Autonomia + bomby TZ" (18-31.07)
| Agent | Zadania | Effort | Granice |
|---|---|---|---|
| Autonomia-E2E | E2E executor na zleceniu testowym, monitor+stop-loss PRZED ON, runbook max_per_hour=1; WŁĄCZA Adrian | Wysoki | worktree: auto_assign_executor/gate + testy; flaga zostaje OFF (flip poza zakresem). |
| TZ-Deploy | deploy staged gastro_assign (ZoneInfo) + weryfikacja fixów TZ FALA-1 | Średni | gastro_assign + deploy_staging; restart za ACK przez FLIPMASTER-a. |
| Fala-B histereza | histereza propozycji (cel ~41-43% flickera) — flaga OFF + replay | Średni | dispatch_pipeline (sekcja propose) — SEKWENCYJNIE po merge Fali-A (ten sam plik). |
| Feas-Carry | raport outcome-join po werdykcie 5b → rekomendacja #483000 (tylko raport) | Niski | READ-ONLY tools+ledger. |

**Reguła kolizji:** równolegle tylko rozłączne pliki; wszystko dotykające dispatch_pipeline.py/common.py (huby, in-deg 85) — seryjnie; flags.json/restarty/deploye = jeden FLIPMASTER; apka/courier_api = koordynacja z sesją 15.

## 5. RYZYKA (wprost)
1. **Adopcja 5b 1/553** — jedyny realny bloker łańcucha dowodów. 🔬 ZDIAGNOZOWANE 21:40 (`ADOPCJA_5B_diagnoza.md`): to NIE dystrybucja (vc70 na serwerze + ogłaszany) — penetracja apki v2 ≈ 1 kurier (cid=492); update miękki pull-only; plan Pn = ręczny onboarding 3-5 kotwic (123/393/484/370) + metryka w DISTINCT kurierach; werdykt realnie ~08-09.07 tylko przy onboardingu.
2. Decyzje tylko-Adriana bez terminu: 2 klucze DR (krytyczne), BFG (tokeny w historii git mimo rotacji), HA, „objm ON na stałe", termin autonomii.
3. ~~`schedule_utils.py` poza gitem~~ → KOREKTA 05.07 wieczór: nietrackowany ŚWIADOMIE (decyzja Adriana 19.06, memory `schedule-utils-untracked.md`: wersjonowanie tylko .bak; repo nad `scripts/` = mailek.git — commit mieszałby projekty). Czyste wyjście długoterminowe = refaktor do dispatch_v2 (hot-path, za ACK), nie commit.
4. ~~Miny dokumentacyjne N2/N5/N13~~ → NAPRAWIONE 05.07 wieczór (agent docs-spójność, commit `8b5af8a`): N2 kanon 3 światów dopisany w CLAUDE.md (ADR-004 już kompletny), N12 lookup COMMIT_DIVERGENCE prowadzi żywym stanem; N4/N5/N13 okazały się już rozwiązane wcześniej (`ab9ac2d`, DoD 03.07, workspace/CLAUDE.md usunięty).
5. Jutrzejsze biegi night-guard/cod-weekly: jeśli nie zielone → realna regresja.
