# ZIOMEK — JEDEN REJESTR FINDINGÓW (LEDGER) · PAS 0.A audytu 2.0

**Data:** 2026-07-02 · **Tryb:** READ-ONLY (zero zmian produkcji; jedyny zapis = ten plik + `AUDYT2/L01-*.md`) · **Autor:** lane L01-rejestr-findingow
**Cel (anty-K1 dla samych audytów):** scalić findingi z 4 rejestrów w JEDEN z ujednoliconym STATUSEM + WŁAŚCICIELEM, żeby żaden nie był SIEROTĄ. Reguła trwała: **każdy przyszły audyt DOPISUJE tutaj, nie tworzy nowego rejestru.**

**Źródła scalone:**
- **(a) 27.06** — `eod_drafts/2026-06-27/ZIOMEK_DEEP_AUDIT_FINDINGS.json` (**81 findingów**: 2×P1, 31×P2, 48×P3) + `ZIOMEK_DEEP_AUDIT_REPORT.md`.
- **(b) 30.06 Faza 1** — `FAZA1_01` (53 rooty→26 przetrwałych) + `FAZA1_02` (13 klastrów konfliktu) + `FAZA1_03` (49 przyrządów, 19 VOID) + `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family` (11 rootów) + `ZIOMEK_UNIFIED_AUDIT` (K1-K7 = fundament F1-F7) + preshift-audit.
- **(c) 05.07** — `AUDIT_2026-05-07/` (20 ryzyk R + F1-F20 + RC1-RC7 + roadmapa M1-M5).
- **(d) memory pion-audytów** — preshift-pickup-floor + allocation-family (już scalone w K1-K7).

**Legenda STATUS:** `fixed-live` (naprawione i LIVE) · `fixed-partial` (część dostarczona) · `deferred` (kod/plan gotowy, flip/wykonanie za bramką/ACK) · `open` (ma właściciela, nie wykonane) · `refuted` (adwersaryjnie obalone jako osobny otwarty root) · **`ORPHAN`** (open, właściciel = NIKT).
**Legenda WŁAŚCICIEL:** `L0..L8` = fala Fazy 3 (roadmapa `FAZA1_05`) · `bramka DD.MM` = data-gated · `deferred #N` = zaparkowane z właścicielem (TOP-10) · `2.0 PionX` = PROPOZYCJA audytu 2.0 (nie-ACK) · **`NIKT`** = sierota.

---

## 1. STATYSTYKA (nagłówek — „ile findingów / ile bez właściciela")

| Miara | Wartość |
|---|---|
| **Raw findingi skatalogowane (4 audyty, przed cross-dedup)** | **~247** = 81 (27.06) + 53 rooty + 49 przyrządów + 13 klastrów (30.06) + 40 (05.07: 20R+20F) + 11 (allocation). Unified K1-K7 i preshift = konsolidacje (nie double-count). |
| **Distinct rooty po cross-dedup** | ~26 rootów koherencji (30.06, absorbują allocation+preshift+większość 81) + 7 RC strukturalnych (05.07; RC1≈K1 nakładka) + sieroty. |
| **27.06 (81): rozkład właścicielstwa** | 65 owned/closed · **16 ORPHAN** (11 indywidualnych + 5 klaster postpone). |
| **30.06 (26 przetrwałych rootów)** | 26/26 **owned** przez L0-L8 (100%). +13 refuted, +14 deferred-cap→L6.E/L7/L8, +49 przyrządów→higiena L0/L1/L6/L7/L8. |
| **05.07 (40 findingów→7 RC)** | quick-wins F2/logrotate/MemoryMax = **partial** (OnFailure na większości, ale NIE cod-weekly); struktura RC1/RC2/RC4/RC5/RC6/SPOF/SLO/security = **open, proposed-2.0-nie-ACK** (~10 pozycji un-owned) + **1 żywa sierota: `dispatch-cod-weekly` FAILED+SILENT**. |
| **allocation (11)** | owned (L4/L5/L6/L7) lub refuted. |
| **⭐ OPEN BEZ WŁAŚCICIELA (sieroty) — RAZEM** | **~27**: **16 „cichych sierot" 27.06** (nikt nigdzie nie śledzi) + **1 żywa** (cod-weekly, silent) + **~10 strukturalnych 05.07** (znane-otwarte, czekają na ACK 2.0). „Cichych sierot z fix-właścicielem=NIKT" = **17**. |

**Najważniejszy wniosek PAS 0.A:** rejestry NAPRAWDĘ nie były scalone. **16 findingów z 86-agentowego audytu 27.06 NIE weszło do korpusu 30.06** (grep całego korpusu = 0 trafień — patrz §4) i nie mają fix-właściciela. Flagowy przykład z designu 2.0 (`osrm-fallback-double-traffic`) POTWIERDZONY jako sierota + znaleziono 15 rodzeństwa. Dodatkowo **oś 05.07 (RC1/RC4/SPOF/SLO/security) leży 2 miesiące** i JEST żywo szkodliwa (cod-weekly failed+silent 2 dni, dowód poniżej).

---

## 2. MASTER-TABELA: findingi 27.06 (81) → STATUS + WŁAŚCICIEL

> Owned pogrupowane po właścicielu (dla czytelności); **sieroty wyliczone indywidualnie** (§3).

### 2.1 FIXED-LIVE / FIXED-PARTIAL (na dziś 01.07)

| Grupa (findingi) | # | STATUS | WŁAŚCICIEL | Dowód |
|---|---|---|---|---|
| **Serializer/metryki**: shser-inv-feas-marker, shser-r6-tiercap, shser-eta-source, shser-effective-start-ab, shser-prefixless-families, metser-r6-hardcap-tier, metser-post-shift-overrun, metser-r1r5r8-magnitude, metser-eta-source, metser-end-of-day-salvage, metser-feasibility-batch, metser-wave-bonus | 12 | fixed-live | **L1.1** (LIVE 01.07 ~20:10) | allowlist→deny `_METRICS_EXCLUDE`, 38 kluczy/14 HARD; commit `85d92f7` |
| **Route-order/carried/panelsync**: pr-app-panel-carried-relax, pr-app-trust-canon-masked-dead, pr-route-podjazdy-not-shared, cap-carried-relax-app-console, cap-build-view-trust-canon-dead-flag, cap-console-reimpl, cap-monitor-trust-canon-env, cap-panelsync-orphan | 8 | fixed-partial | **L6.A** (golden DONE 01.07; PoC-TARGET import wspólny = pending) | golden 13/13 parytet; panelsync usunięty `0c914c4`; fail-loud import `290dd09` |

### 2.2 OPEN z właścicielem (fala L / bramka / deferred)

| Grupa (findingi) | # | STATUS | WŁAŚCICIEL |
|---|---|---|---|
| **R6/SLA-anchor/O2/paczka**: feas-r6-sla-anchor-gap, feas-o2-paczka-blind, feas-o2-cap-not-escalation, rst-o2-overage-cap-flat-tier, rst-greedy-step15-not-o2 | 5 | open | **L6.B / bramka 02.07** (O2-review) |
| **objm/frozen-lexqual/twin-bucket**: twin-objm-lexr6-shadow-stale, twin-fastest-pickup-key-stale, twin-pln-pure-resort, twin-post-shift-lexqual-3v4 | 4 | open | **L6.D / bramka 03.07** (objm at-200) |
| **feas-carry/hard-split**: feas-carry-readmit-verdict-relabel, twin-feascarry-shadow-vs-readmit-cap, twin-readmit-bypasses-feasibility-first | 3 | open | **L7.4 / L7** (feas-carry OFF; re-flip za protokół) |
| **recanon/committed-prop**: recanon-reassign-loser-gap, recanon-committed-change-no-resequence, pr-committed-prop-twin-path-gap, pis-cancel-disappeared-no-recanon | 4 | open | **L3** (plan_recheck GC + recanon-symmetry) |
| **flagi/conftest/env-frozen**: tests-etap4-registry-drift-isolation-leak (P1), flags-repo-shadow-override-stale, flags-a4-test-flag-dead-key, flags-plan-recheck-envfrozen-dropin, tk-pending-pool-env-frozen, flags-stale-enabled-oneshot-timers | 6 | open | **L0** (1 rejestr-flag + fingerprint + conftest strip) |
| **współbieżność pending**: tk-pending-dualwriter, dsi-pending-multiwriter-shared-tmp-no-lock, dsi-pending-no-assign-remover-ttl-only | 3 | open | **L7.5** (fcntl; gate C2 przed re-enable Telegram) |
| **martwy kod / sprzątanie**: crg-latest-order-by-event-dead, pis-cancelled-status-deadwrite, pis-parse-guard-live-doc-drift, pr-commitment-emitter-skeleton, mlcal-validation-gate-cancelled-path, tests-dead-v328-layer4-duplicate, tests-bak-file-proliferation, rst-grouping-greedy-double-pickup(→flag-coupling C3), rst-chain-eta-feeds-eta-pickup-utc(→eta-deferred) | 9 | open | **L8** (dead-code + clutter + threshold) |
| **kalibracja/R6-bagcap**: feas-r6-bagcap-untested-live (P1) | 1 | open | **L5** (⛔HARD ACK; = 🔥 „quantile luzuje HARD R6") |
| **LGBM**: mlcal-lgbm-primary-flag-not-wired, mlcal-lgbm-tier-feature-name-lookup, mlcal-prepbias-r6-anchor-twin-path, mlcal-dual-prepbias-artifacts | 4 | open | **deferred #9 LGBM eval** (weak — pokrycie tematyczne, nie itemized) |
| **testy-oracle**: tests-script-runner-xfail-masks (B19), tests-seq-replay-verdict-untested, tests-plan-recheck-tier-dwell-no-onoff | 3 | open | **L0 / B19** (oracle-erosion; xfail = F-B19-03/08) |

### 2.3 REFUTED / CLOSED

| Finding | STATUS | Dlaczego |
|---|---|---|
| p5-cancel-recanon-confirmed-fixed | refuted/closed | P-5 cancel/return recanon ZAMKNIĘTE (`0426706`); refuter potwierdził |
| drive-min-calib-main-off-intentional | closed | drive-speed correction wycofane (temat zamknięty 29.06); „nie flipuj MAIN" = by-design |

### 2.4 ⭐ ORPHAN (open, właściciel = NIKT) — §3 pełne

`osrm-fallback-double-traffic` · `osrm-v2-shadow-aggregate-full-matrix` · `pipe-postshift-gate-exclusion-gap` · `tk-watchdog-keyerror-twin` · `tk-shadow-entry-msgid-null` · `crg-ranking-bundle-skew-live` · `crg-lastpos-ttl-savetime-staleness` · `crg-gpsquality-anchor-ticktime` · `pis-closed-vs-orderids-source-divergence` · `pis-closedids-raw-html-input` · `crg-dedup-byname-bag-loss` · **klaster postpone_sweeper**: `czas-postpone-cid-key-resolution-dead` · `czas-postpone-no-order-event-reemit-dead` · `czas-postpone-assign-verdict-dead-value` · `czas-postpone-pending-schema-mismatch` · `dsi-postpone-sweeper-orders-state-schema-mismatch`.

---

## 3. ⭐ SIEROTY — pełna lista (najważniejszy produkt PAS 0.A)

**Metoda dowodowa:** dla każdego kandydata grep CAŁEGO korpusu 30.06 (`FAZA1_*` + `ZIOMEK_*` + `AUDYT_preshift*` + `backing/*`, 71+ plików) po dystynktywnym terminie. 0 trafień = nie skonsumowany = sierota (§4 pokazuje surowe liczby + kontrolę pozytywną).

| id | Sev | Powierzchnia (plik:linia z 27.06) | Co to jest | Dlaczego SIEROTA (grep) | Rekomendacja |
|---|---|---|---|---|---|
| **osrm-fallback-double-traffic** | **P2** | `osrm_client.py` fallback path | Fallback OSRM liczy traffic DWA razy: prędkość-z-korków-bucket AND `get_traffic_multiplier` → czasy ×~1.5 → sztuczne breache R6 **dokładnie gdy OSRM już kuleje** | `traffic`(8) w korpusie = TYLKO `traffic_v2_aggregator` (live-shadow, refuted-DEAD) + one-off tools; **double-count/fallback = 0** | Pojedyncze mnożenie w fallbacku; bliźniak z traffic_v2 mult. Właściciel = **2.A game-day (OSRM-down)** lub L8; DZIŚ NIKT |
| **crg-ranking-bundle-skew-live** | **P2** | `courier_ranking.py` (L10/PERI) | Leaderboard/tier-ranking używa **bundle-contaminated** metryki, LIVE via Telegram → tier promote/retire na skażonych danych | `leaderboard`=0, `bundle-contaminat`(2)=tylko pickup_slip de-konfundacja (inne). `courier_ranking` = tylko inwentarz PERI (A1), bug NIE śledzony | = klasa 05.07 RC4 (decyzje strategiczne na corrupt data). Odbundlować metrykę tier. NIKT |
| **pipe-postshift-gate-exclusion-gap** | **P2** | `dispatch_pipeline.py:2307-2315,5043-5058,6325-6348` | `post_shift_overrun_penalty` obniża `final_score`, ale BRAK w `_GATE_RANKING_DELTA_EXCLUSIONS` bramki MIN_PROPOSE → może **cicho wepchnąć decyzję w KOORD-ciszę** (dokładnie luka INV-GATE-SCORE-DELTA, którą docstring nazywa naprawioną dla r1/v319h) | `gate-exclusion`=0, `GATE_RANKING`=0. Korpus ma post_shift_overrun (serializacja VALIDATED) ale NIE tę bramkę | Dodać `post_shift` do krotki wykluczeń (jak r1_progressive/v319h). NIKT |
| **pis-closed-vs-orderids-source-divergence** | **P2** | `panel_html_parser.py` / `panel_watcher.py` | `closed_ids`(DOM marker) i `order_ids`(JS) to NIEZALEŻNE źródła; ścieżka „disappeared" pre-emptuje → cancel/return misclass | `closed_ids`(1)=tylko lag reconcile 15-90min (B18). Rozbieżność-źródeł = 0 | Jedno źródło prawdy dla stanu zlecenia w parserze. NIKT |
| **czas-postpone (klaster ×5)** | **P2** | `postpone_sweeper.py`, `czasowka_scheduler` | postpone_sweeper: (1) czyta nieistniejący klucz `cid` (orders_state ma `courier_id`), (2) re-emit NIE zrekonstruuje `order_event` (brak `raw`), (3) sprawdza verdict `('ASSIGN','PROPOSE')` gdy jest tylko PROPOSE, (4) pending-entry bez `message_id/sent_at/expires_at`, (5) `dsi` czyta zły nesting `'orders'` — **cała ścieżka resolution-detection MARTWA** | `postpone`(7) = tylko jako 3-writer współbieżny na pending (B18) + „martwy postpone schema" jako **C2-mina** (arms-on-re-enable) — ale konkretne bugi NIE itemized, BRAK fix-właściciela | Naprawić 5 dead-paths PRZED re-enable Telegrama (gate C2 tylko OSTRZEGA, nie naprawia). NIKT (fix) |
| **osrm-v2-shadow-aggregate-full-matrix** | P3 | `dispatch_pipeline.py:5663` traffic_v2 | Shadow-aggregate sumuje CAŁĄ macierz OSRM (NxN), nie nogi wybranego planu → telemetria traffic_v2 zawyżona | `aggregate.*matrix`=0. Korpus tylko REFUTUJE że aggregator DEAD; sumowanie-całej-macierzy nie śledzone | Sumować tylko legi planu. NIKT |
| **tk-shadow-entry-msgid-null** | P3 | `telegram_approver` / shadow pending | Shadow-pisane pending mają `message_id=None` → reply/postpone/keyboard-strip crashuje przy re-enable | `message_id`=0 w korpusie | Guard None lub nie pisać msgid-zależnych. NIKT (arms-on-re-enable) |
| **tk-watchdog-keyerror-twin** | P3 | `telegram_approver` watchdog expired-loop | Pętla wygasania używa `state['pending'][oid]` bez guardu (bliźniak startup MA guard) → KeyError | `watchdog`(3)=observability/systemd; expired-loop/KeyError = 0 | Symetryzować guard z bliźniakiem startup. NIKT |
| **crg-lastpos-ttl-savetime-staleness** | P3 | `courier_resolver` last-known-pos | TTL liczony od tick save-time, nie od observation-time → uratowana pozycja przeżywa realną (stale rescue) | `savetime`/`save-time`=0 | TTL od czasu OBSERWACJI GPS. NIKT |
| **crg-gpsquality-anchor-ticktime** | P3 | `courier_resolver` GPS-02 teleport | Kotwica teleportu bierze store SAVE-time (tick) nie GPS fix-time → próg teleportu shadow-kalibrowany źle | `teleport`(2)=inne konteksty; fix-time anchor = 0 | Kotwica z GPS fix-time. NIKT |
| **pis-closedids-raw-html-input** | P3 | `panel_html_parser` | `closed_ids`/address skanują RAW html, `order_ids` skanuje SVG-stripped `html_clean` → asymetria wejścia parsera | jw. closed_ids nie o tym | Jedno wejście (clean) dla obu skanów. NIKT |
| **crg-dedup-byname-bag-loss** | P3 | `courier_resolver` dedup-by-name | dedup-po-nazwisku może usunąć same-name `courier_id` NIOSĄCY aktywny bag, zostawiając pusty | `dedup-by-name`=0. **allocation R5 badał SEEDING puli (refuted 0/14 zgubionych), NIE usuwanie-z-bagiem** — inny mechanizm | dedup zachowuje wariant z bagiem. NIKT (weak — R5 dotykał sąsiedniej osi) |

**Żywa sierota operacyjna (05.07 klasa F2/RC3 — POTWIERDZONA GROUND-TRUTH DZIŚ):**

| id | Sev | Dowód (systemctl, 07-01) | Dlaczego SIEROTA |
|---|---|---|---|
| **dispatch-cod-weekly.service FAILED+SILENT** | **P2 (live)** | `is-failed`→**failed**; `Result=exit-code`, `ExecMainStatus=1`; `ExecMainExitTimestamp=Mon 2026-06-29 06:00:03 UTC` (~2 dni); **`OnFailure=` PUSTE** → **żaden alert nie poszedł** | To DOKŁADNIE klasa 05.07 F2/RC3 („overrides-reset martwy 4 dni"). Design 2.0 §0.2e nazwał; ANEKS zakładał „OnFailure jak inne 11 svc" — **grunt-prawda obala: ten svc NIE ma OnFailure**. NIKT nie naprawia; 2.B tylko PROPONUJE klasę |

**Oś strukturalna 05.07 (open, właściciel = 2.0 Pion 2/3 PROPOZYCJA — nie-ACK; efektywnie un-owned 2 mies.):**

| Root | Sev | STATUS / dowód ground-truth | Właściciel |
|---|---|---|---|
| **RC1 filesystem-as-IPC** (=K1 30.06) | P1 | open — brak Postgres/Redis dla dispatch (`systemctl` = 0 dla dispatch; papu-postgres to Papu) → M1/M2 nietknięte | 2.0 3.B (mierz KIEDY) |
| **RC4 JSONL unbounded / state growth** | P2 | open — `dispatch_state`=**1.2G**, `logs`=**729M** (ground-truth); root `unbounded-append-only-caches` deferred-cap 30.06 | 2.0 2.D + L8 |
| **single-server SPOF / brak HA** | P1 | open — restart telegrama traci pending in-memory; brak repliki | 2.0 2.E (DR-drill) |
| **RC3 brak alertów DANOWYCH / SLO** | P1 | open — tylko `latency_alarm.py` (abs, nie trend); sentinel 2046+14456 zdarzeń 0 alertów; cod-weekly silent | 2.0 2.B + 3.A |
| **RC5 state ownership emergent** (brak `state_io`) | P2 | open — każdy moduł `open(orders_state,'w')`; folklor | 2.0 (poza zakresem — mostek do M4) |
| **RC6 replay re-runs current code** | P2 | open — `replay_failed.py` na bieżącym kodzie | 2.0 (mostek do M3 event-sourcing) |
| **Bezpieczeństwo NIGDY nie audytowane** | P1? | open — „biały obszar" (0 pokrycia od początku); przycisk auto-assign = nowa powierzchnia | 2.0 2.F (pierwszy security lane) |
| **systemd lifecycle / rozrost** | P2 | open — **68 svc + 61 timerów** (ground-truth) vs 05.07=16+12; brak WatchdogSec/MemoryMax/retire | 2.0 2.G |
| **.bak proliferation** | P3 | open — **330 .bak** (ground-truth); R-16 05.07=342, tests-bak 27.06=268 → praktycznie nie ruszone | L8 / 2.0 2.D |

---

## 4. DOWÓD GREP (surowe liczby — kontrola pozytywna + kandydaci)

Grep całego korpusu 30.06 (`FAZA1_*.md ZIOMEK_*.md AUDYT_preshift*.md backing/*.md`), ERE, case-insensitive, liczba PLIKÓW z trafieniem:

```
KONTROLA POZYTYWNA (musi >0): carried=57  sentinel=53  serializer=27  route-order=36  feas.carry=28  objm=59
SIEROTY (0 = nie skonsumowany):
  osrm-fallback-double-traffic  → double.*traffic=0  fallback.*traffic=0  aggregate.*matrix=0
  pipe-postshift-gate-exclusion → gate-exclusion=0   GATE_RANKING=0
  tk-shadow-entry-msgid-null    → message_id=0
  crg-ranking-bundle-skew-live  → leaderboard=0  (bundle-contaminat=2 → pickup_slip, inne)
  crg-lastpos-ttl-savetime      → save-time=0  savetime=0
  tk-watchdog-keyerror-twin     → (watchdog=3 → observability/systemd; expired-loop/KeyError=0)
  pis-closed-vs-orderids        → (closed_ids=1 → tylko lag reconcile B18, nie rozbieżność-źródeł)
  crg-dedup-byname-bag-loss     → dedup-by-name=0  (dedup=65 → SLA/R6-anchor C3, inne)
POKRYTE (>0, NIE sieroty — kontrola anty-fałszywy-alarm):
  double-insert=9 / grouping=20 → rst-grouping-greedy-double-pickup = OWNED (I-08 flag-coupling OR_TOOLS↔GROUPING, D01-D04)
  xfail=2 → tests-script-runner-xfail = OWNED (B19 F-B19-03/08 oracle-erosion)
  postpone=7 → współbieżność OWNED (B18 O1/L7.5); ale dead-schema itemized = ORPHAN
  prep.bias=18 / chain_eta=18 → tematycznie OWNED (calibration L5 / eta-deferred)
```

---

## 5. NOTATKI SPÓJNOŚCI / CAVEATY (uczciwie)

- **Owner=„weak"** oznacza: temat/moduł jest w korpusie 30.06 jako inwentarz lub sąsiedni root, ale KONKRETNY finding nie jest itemized ani zaplanowany do fixu. Te są „pół-sieroty" — świadomie zostawione w sekcji owned-weak, nie liczone do 16, ALE oflagowane (LGBM ×4, postpone-jako-C2-mina, tier-dwell-test).
- **Klaster postpone**: umbrella „martwy postpone schema" JEST widziana (B18 jako C2-mina arms-on-re-enable-Telegram), więc technicznie ma „gate-właściciela" C2 — ALE gate tylko OSTRZEGA przed re-enable, nie ma fix-właściciela dla 5 dead-paths. Zliczam klaster jako 1 sierotę P2 (reprezentującą 5 findingów) — fix nikt nie posiada.
- **Prawda przyciskowa vs fizyczna**: severity findingów z 27.06 opiera się na `delivered_at`/`picked_up_at` = prawda-PRZYCISKOWA (±~3 min, 0/377 auto_geofence GT). Materialność „ile/dzień" dla większości = NIE policzona (audyt 27.06 deklarował ISTNIENIE, nie liczbę) — oznaczam per-finding.
- **Ground-truth (systemctl/du/find) 07-01**: cod-weekly FAILED (exit1, OnFailure puste), dispatch_state 1.2G, logs 729M, 330 .bak, 68 svc+61 timerów, brak Postgres/Redis dispatch. To PROXY chwili (dryfuje).
- **05.07 „owner=2.0"**: 2.0 to PROJEKT do ACK — dopóki Adrian nie akceptuje Pionów 2/3, oś strukturalna jest efektywnie un-owned. Zliczona osobno od „cichych sierot 27.06" (te nie ma nawet propozycji-właściciela).
- **Linie dryfują** (≥3 sesje/dzień). Każdy fix re-grepuje (ETAP 0).

**STOP przed naprawą — to audyt (read-only).** Naprawa sierot = osobne mini-sprinty ETAP 0→7 + ACK. Rekomendacja kolejności: (1) `osrm-fallback-double-traffic` + cod-weekly (żywe, tanie), (2) klaster postpone PRZED re-enable Telegrama, (3) reszta P2 do L3/L6.B/L8, (4) oś 05.07 = decyzja o ACK 2.0.

---

## 7. AKTUALIZACJA STATUSÓW — FALA-1 napraw (tmux 9, 2026-07-02 ~08:15 UTC; append-only)

Sprint wieloagentowy C12 (5 lane'ów PARALLEL-SAFE, worktree per agent, merge seryjny, regresja po każdym; pełny stan → `ZIOMEK_STAN_AUDYTY_1i2.md`):

| Finding (2.0) | Nowy STATUS | Dowód/commit |
|---|---|---|
| **C+D bomby TZ** (gastro_assign + shadow_outcome_enricher + klaster) | **fixed-partial** — 6 plików repo→ZoneInfo + grep-ratchet (złapał 7. przypadek cross-lane w perf_budget_report → naprawiony) | `872667f`+`2e68a11`; ZOSTAJE za ACK: podmiana żywego gastro_assign.py (staged) + drive_speed_overshoot_verdict.py:29; deadline 25-26.10 |
| **E regres wydajności 2×** | **fixed-partial (pomiar)** — perf_budget_report + SLO w canary za flagą OFF (bajt-parytet → at-200 nietknięty); baseline: p50 852/p95 1939/p99 2720 ms, ogon 13,1% | `e9551f1`; fix compute-zawsze = OSOBNA fala (rdzeń); flip alertu po log-only (ACK) |
| **A martwe monitory (domknięcie)** | **deferred (staged)** — rejestr progów cron_health (cod-weekly 192h + 6×thr=None) + CLI + 10 drop-inów (OnCalendar×3 [Persistent bez OnCalendar był NO-OPem], OnFailure cod-weekly, ExecStartPost×3); burst-check 3→0 | `aab1e17`; instalacja = cp+daemon-reload za ACK (`FALA1_watchdog_raport.md`) |
| **B cod-weekly FAILED+SILENT** (⭐ była „żywa sierota") | **fixed-partial** — hipoteza potwierdzona (brak bloku tygodnia, pada co pn); aktionable błąd + auto-create za flagą OFF; **4 przepadłe tygodnie zidentyfikowane** (18-24.05/01-07.06/08-14.06/22-28.06) | `46e4867`; backfill `--week A:B --write` za ACK Adriana (pieniądze); OnFailure w stagingu watchdog |
| **L13 GC observability atrapa** | **fixed-partial** — log_rotation.py (denylist-first, dry-run default); dry-run żywy: 90 plików/174 MB; ⭐ KOREKTA: event-bus-cleanup(90d) ŻYJE → ~10.07 = weryfikacja, nie klif | `a3ecf2f`; install timera + 1. --apply + events.db plan za ACK (`FALA1_gc_eventsdb_plan.md`) |

Meta: regresja finalna kanonu zielona (baseline 3709→wszystkie testy lane'ów dołączone); 2 near-missy procesowe fali (obie klasy → protokół C12): (1) ratchet-cross-lane — kod scalany równolegle nie widzi się nawzajem w worktree, strażnik-ratchet w kanonie łapie po merge; (2) test z hardcode ścieżki worktree = bomba po `git worktree remove` (fix: samo-lokalizacja `parents[1]` + try/finally na sys.modules) — `075dfe3`.

## 8. AKTUALIZACJA — fala L4 available_from SCALONA (tmux 9, 2026-07-02 ~10:00 UTC; append-only)

**F1 (K1 dostępność) = fixed-partial, FLAGA OFF.** Merge `fix/l4-available-from` (5 commitów, HEAD merge po d20bd27): jedno źródło `available_from=max(now,shift_start)` w courier_resolver (+`available_from_source`, unknown JAWNY nie None-cisza), konsumenci #1/#3/#5 za flagą `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE` (ETAP4_DECISION_FLAGS, default OFF, OFF=bajt-w-bajt), chokepoint `state_machine.COURIER_ASSIGNED`→`effective_pickup_at` OBOK deklaracji (Q2/frozen R27 zachowane), pickup_floor_guard rozwiązuje shift_start kanonem (koniec ślepoty L0.5 na on-shift; pozostałe unknown = stale plany → dług ⑦). Dowody: 25 testów + mutation×2 (C13) + parytet #1↔#3↔#5 z konstrukcji (wspólna funkcja) + regresja overlay 3806/0 + replay 14d n=3538 (dotkniętych 625): leak w shadow=0 (pre_shift już clamped przez `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` ON), zmiana zwycięzcy=0, pula 216/216 → **wpływ = strukturalny** (konsolidacja N→1 + floor leaku #5 [poza shadow, mierzy guard] + domknięcie luki no_gps/gps-przed-zmianą + odporność na minę flags.json). Bramka ETAP-5 uczciwie: brak liczbowego zysku w shadow — decyzja flipu ze świadomością (protokół dopuszcza bajt-parytet jako pozytyw refaktora). FLIP za ACK: flags.json wpis + restart dispatch-shadow/plan-recheck (+panel-watcher) off-peak, 2 dni OFF-obserwacji, potem ON + grep-c metryk. NIE zrobione (świadomie): Q2 feasibility (wymaga F4/L5), pas renderów (L3), pełny plan_recheck rebuild (L3), stale-plan GC (⑦).

## 9. AKTUALIZACJA — fala L3 plan_recheck SCALONA + deploye #6 (tmux 9, 2026-07-02 ~10:30 UTC; append-only)

**F2/K2 (plan_recheck-cofacz) = fixed-partial, FLAGI OFF.** Merge `7201ed8`: bramka zapisu regenu compare-and-keep na R6 carried-age (`ENABLE_PLAN_RECHECK_GATES`; spread=SOFT→metryka, bez nowej HARD — ETAP-2) + GC courier_plans (`ENABLE_COURIER_PLANS_GC`, DRY_RUN default; dry-run na kopii: 48→26 age + 4 no-active + 6 kept) przez plan_manager API/fcntl. twin(recanon) już spełniony (P-5), read-side-effect→0 potwierdzone. 21 testów, mutation×2, golden nietknięte. Regresja kanonu po merge: **3827/0** (3806+21). Flip = ACK, hot przez flags.json (oneshot), pw bez restartu.
**Bomba TZ #1 (finding C) = FIXED-LIVE 02.07 ~10:05:** żywy `gastro_assign.py` podmieniony za GO Adriana (diff tylko l.11-12 → ZoneInfo; backup `.bak-pre-tz-zoneinfo-2026-07-02`; subprocess per przydział = aktywne od razu, latem bajt-parytet). Pozostałość findingu C: `drive_speed_overshoot_verdict.py:29` (allowlista ratcheta) + enricher scalony w repo (finding D → aktywacja przy restarcie shadow).

## 10. OKNO DEPLOYOWE WYKONANE (tmux 9, 2026-07-02 ~11:45 UTC, GO Adriana 1/2/4/6; append-only)
**Finding A (martwe monitory) = FIXED-LIVE w całości:** 10 drop-inów zainstalowanych, 3 timery na OnCalendar (przeżyją daemon-reload; Persistent działa), cod-weekly z OnFailure+ExecStopPost, cron_health: 15 jednostek/0 luk progów/0 fałszywych alertów (sync+seed wykonane). **Finding C bomba #1 = FIXED-LIVE** (gastro_assign ZoneInfo na żywo). **Finding D (enricher) = FIXED-LIVE** (kod w repo od `872667f`, oneshot łapie od merge). **D.3 fale A+B = DEPLOYED** (17 flag KANON=flags.json, env martwy zweryfikowany fingerprintem shadow + behawioralnie w pw [REDECIDE_ON_PICKUP], para V326 spójna — 0×V326_PAIR_INCOHERENT). **L2.2 = AKTYWNA w shadow od 11:45** (weryfikacja v328_fail_causes po 1. świeżej decyzji — dołek między peakami). Restarty: shadow+pw czyste; telegram NIETKNIĘTY. Rollbacki: flags.json.bak-pre-d3-ab · drop-iny .bak-pre-d3-ab · gastro .bak-pre-tz-zoneinfo · cron_health.json.bak-pre-watchdog-close.

## 11. FALA-2 PARALLEL-SAFE SCALONA (tmux 11, 2026-07-02 ~13:05 UTC, 7 pasów C12; append-only)
Merge seryjny do master, HEAD `7acfeb1`. Regresja finalna z KANONU po worktree remove: **3963/0/23/13xf** (baseline 3907/0/23/11 → +56 testów, +2 świadome xfail). Entropia bez pogorszenia.
- **Motyw #1 (alerty danowe, 2.B) = built-partial:** `observability/data_alerts.py` (5 sygnałów edge-triggered: sentinel-rate/empty-pool/stale-grafik/stale-GPS/ledger-stall; ledger kanonem ledger_io; progi z pomiaru 3d). Flaga `ENABLE_DATA_ALERTS` OFF + timer STAGED → **deploy-za-ACK** (kolejność: instalacja+flip log-only → 1-2 dni → telegram). Smoke żywy: 0 firing dziś; backtest 2d: 1 realny edge (ledger-stall 35,2min, cichy poranek).
- **Motyw #2 (cron_health kłamie) = fixed:** systemd-truth cross-check w `is_stale`/`scan_stale` (odtworzony stan sprzed FALA-1: 3 fałszywe → 0, systemd_rescued=3; realnie padły cod-weekly NADAL alertuje). **ŻYWE od następnego ticku watchdoga** (import świeży per bieg, bez restartu); kill-switch `CRON_HEALTH_SYSTEMD_TRUTH=0`. Recorderów brak lukowych (komplet po FALA-1).
- **Motyw #6 (strażnicy-teatr) = exposed+dogęszczone:** `tools/guard_mutation_probe.py` — 3/6 HARD-bramek teatralnych (bag-cap string-match-na-dysku, verdict-gate token-nie-polaryzacja, próg SLA nieizolowany); +13 testów behawioralnych (≥2 kills/bramka) + wariant polaryzacyjny. **Zostają xfail L-TEATR-1/2** — korzeń wspólny: R6↔SLA maskują się na 35min → fix U ŹRÓDŁA = konsolidacja 35-min HARD z jawną kotwicą (te same 3 bliźniaki SLA-anchor co prerekwizyt flipu O2 — konwergencja 2 pasów).
- **Finding C (TZ) = domknięty w repo:** `drive_speed_overshoot_verdict.py`→ZoneInfo, allowlista ratcheta 2→1 (został tylko poprawny wzór ontime_lib). Kill-test: zima fixed+2 = bias −60min.
- **Finding H (grafik) = zlokalizowany, design gotowy → SERIAL:** `scripts/fetch_schedule.py:130` (today=UTC, okno 00:00-02:00 Warsaw = wczorajszy grafik) + `:121` (literówka godziny kasuje CAŁY wpis kuriera z puli). Plik POZA repo = blind-spot ratcheta TZ (zgłoszony), ZERO testów. H1=poprawność, H2=decyzyjne (flaga+ACK).
- **Motyw #8 (multi-city) = recon+design done:** ~146 nośnych hardcode POTWIERDZONE; `config/cities.json` szkielet (zero konsumentów) + `MULTICITY_plan.md`. TOP trudności: OSRM single-graf, brak city_id w stanie, districts_data, mosty (decyzja Adriana: flota wspólna vs per-market).
- **L6.B bramka O2 = SKONSUMOWANA:** review 07:00 werdykt GO-na-sprint / NO-GO-flip-as-is; polityka cap-Z=20: +214 worków (7,9%), med +10,4min O2, regres pod capem ~0, detour med 0,04min; instrument proxy-certyfikowany-konserwatywny. Prerekwizyt flipu = kotwica w silniku (finding feas-r6-sla-anchor-gap l.48 → sprint SERIAL).
- **L8 mapa = done:** 39 martwych kandydatów ~9,2k LOC (dowody: graf 794 modułów / 90 entry-pointów); P1 ~300 LOC; 0 martwych flag w flags.json (dead-flag=6 z dashboardu = rozjazdy L0.1, nie L8). Bonus: `deploy_staging/scripts/gastro_assign.py` = nieaktualizowany mirror (md5 identyczny z żywym).
Raporty: `eod_drafts/2026-07-02/{alerty-danowe,cron-health,guard-teatr,tz-drobnica}_raport.md` + `O2_bramka_odczyt_raport.md` + `L8_deadcode_mapa.md` + `MULTICITY_plan.md`.

## 12. FALA SERIAL S1+S2 + deploy punkt-1 (tmux 11, 2026-07-02 ~14:10 UTC, GO Adriana; append-only)
- **Motyw #1 data_alerts = DEPLOYED-LIVE ~13:05** (GO): timer `dispatch-data-alerts` co 5 min zainstalowany + `ENABLE_DATA_ALERTS=true` (log-only, telegram OFF; backup flags `.bak-pre-data-alerts-20260702`). 1. tick czysty: 5 sygnałów, emitted=[], exit 0. Po drodze fix u źródła: test default-flagi niehermetyczny (czytał żywy flags.json) → pinuje default w kodzie (`72f37c8`).
- **Finding H grafik = FIXED-LIVE ~13:35** (GO): żywe `fetch_schedule.py`+`schedule_utils.py` podmienione na staged (backupy `.bak-pre-grafik-h-2026-07-02`; weryfikacja date=02-07-26/53 kurierów). H1 today=Warsaw + H1b bez fixed-offset LIVE; **H2 salvage za flagą `ENABLE_GRAFIK_ENTRY_SALVAGE` OFF (flip osobno: replay ilu-kurierów/dzień + ACK)**. Testy 15 + strażnik mirrora żywy↔staged (klasa stale-mirror z L8) + external-ratchet TZ allowlista 1→0. Pierwsze testy tego pliku w historii.
- **feas-r6-sla-anchor-gap (l.48) = fixed-partial, FLAGA OFF** (S1, merge `7b59e0a`): `sla_anchor.py` jedno źródło 35-min z jawną kotwicą; 3 bliźniaki RAZEM (route_sim `_count_sla_violations` + feasibility SLA-loop + R6 per-order; `_o2_key` dziedziczy). OFF=bajt-parytet (fuzz 400/0); ON=te same decyzje + metryka `sla_anchor_source`. De-maskowanie OBSERWABILNOŚCIĄ nie reorderem (reason karmi feas_carry_readmit → reorder=zmiana decyzji=poza falą). **Probe z kanonu: 5/5 KILLED** (było 4/5); L-TEATR-1/2 zdjęte. **FLIP ZA ACK** (flags.json hot + restart shadow + 2 dni `sla_anchor_source`/dryf sla_violations; rollback hot). = PREREKWIZYT flipu O2.
- ⭐ **Cross-lane C12(f) po merge #2**: probe czytał `_SCRIPTS_ROOT` REGEXEM z conftest → S1 (env-overridable) złamał regex → fix u źródła: probe samo-lokalizuje (rodzic dispatch_v2 + env-override). Baseline flag-doc: ENABLE_SLA_ANCHOR_UNIFIED wyjęty (nie ma go w flags.json) → doc dopisany do LOGIC_REFERENCE Z GÓRY (flip przejdzie doc-coverage bez baseline).
- Conftest: `_SCRIPTS_ROOT` env-overridable `ZIOMEK_SCRIPTS_ROOT` (default kanon bez zmian) — infra C12(e) dla przyszłych fal worktree.
