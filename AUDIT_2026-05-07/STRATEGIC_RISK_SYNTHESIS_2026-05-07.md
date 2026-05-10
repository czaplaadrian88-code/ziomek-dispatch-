# 🧬 Strategic Risk Synthesis — Ziomek dispatch_v2

**Data:** 2026-05-07 wieczór
**Wkład:** ARCHITECTURE_AUDIT (20 ryzyk + 10 god objects), STATE_OWNERSHIP (F1-F20), META_AUDIT (6 RC)
**Branch:** `sprint-07-05-event-bus-opcja-c` +32 commits ahead `master@10c754d`
**Cel:** meta-synteza nad trzema audytami — wyodrębnienie najbardziej ryzykownych obszarów, root-cause grouping, klasyfikacja, TOP-5 listy, scenariusze katastrofy, recommended audit order.

---

## 1️⃣ Najbardziej ryzykowne obszary (cross-audit ranking)

Sortowane po **kompoundowanym sygnale** — pojawia się w ≥2 auditach + R≥12 + P0/P1.

| # | Obszar | Sygnały | Kompozyt |
|---|---|---|---|
| **A** | **`learning_log.jsonl` triple-writer corrupt** | R-4 R=16 + F1 P1 + RC4 + `panel_watcher:139, telegram_approver:132+, shadow_dispatcher:610` | **TOP-1** — blocker dla Z3/LGBM |
| **B** | **`telegram_approver.py` god object** | R-1+R-3 R=20+16 + Tier A audit + RC5 + 3240 LOC + 52 except | **TOP-2** — najwięcej bug surface, "de facto immutable" |
| **C** | **Single server SPOF + brak HA** | R-2 R=15 + RC1 (filesystem-as-IPC = collapses pod multi-host) | **TOP-3** — Hetzner reboot = pełny outage |
| **D** | **Silent cron failures** | F2 P0 (4-dni overrides-reset martwy) + RC3 (observability tylko dla anticipated) | **TOP-4** — blokuje 90% autonomy goal |
| **E** | **Per-process cache drift** | R-9 R=12 + F3+F4+F5+F16 + RC1+RC2 — 4 procesy × 5 cache'y × stale invalidation | **TOP-5** — kompounduje multi-tenant |
| **F** | **`common.py` god hub** | R-6 R=16 + 61 fan-in + 1645 LOC + 92 flag drift + brak testów | **TOP-6** — każda zmiana = blast 60+ modułów |
| **G** | **subprocess.run blokuje asyncio loop** | R-3 R=16 + `telegram_approver:1452, :1710` (×2) | **TOP-7** — zamraża 4 asyncio tasks 30s window |
| **H** | **State ownership emergent** | F20 P2 + RC5 + brak compile-time guard, każdy moduł `open(orders_state, "w")` | **TOP-8** — nieuchronna regresja przy nowym module |

---

## 2️⃣ Konsolidacja findings w grupy root-cause (re-mapping META RC1-RC6 + RC7 NEW)

20 ryzyk × 20 findings = **40 sygnałów**. Tworzą 7 strukturalnych klas:

### RC1 · Multi-process bez koordynacji (10 sygnałów)
F1, F4, F5, F16, F20 + R-4, R-9 + R-2, R-7 (single-server premise)
**Klasa:** filesystem jako IPC bus, każdy proces = własna kopia stanu, brak shared coordination layer.

### RC2 · Cache invalidation jako afterthought (5 sygnałów)
F3, F6, F7, F11, F16 + R-9
**Klasa:** każdy cache = własny pattern (mtime / TTL / never / restart-only). Brak globalnej semantyki.

### RC3 · Observability tylko dla anticipated failures (4 sygnały)
F2, F12, F13, F15 + R-19 (brak SLO)
**Klasa:** parser_health = jeden komponent z 4-warstwową obroną; cała reszta = blind spot.

### RC4 · Append-only logs bez writer discipline (3 sygnały)
F1, F10, F19 + R-4, R-14 (110MB unbounded growth)
**Klasa:** JSONL multi-writer >PIPE_BUF bez fcntl = stochastic corruption.

### RC5 · State ownership emergent, nie enforced (5 sygnałów)
F1, F4, F5, F11, F20 + R-7 (flags.json) + R-17 (no schema_version)
**Klasa:** Python "consenting adults" + zero typing dla side effects + folklor zamiast kontraktu.

### RC6 · Replayability fragmented (3 sygnały)
F1 (corrupt history), F8 (race test gap), F18 (replay re-runs current code)
**Klasa:** `shadow_decisions.jsonl` istnieje ale `replay_failed.py` re-runs pipeline → bug już naprawiony, real verdict zaginął.

### RC7 · God objects + cognitive blast radius (NEW, 5 sygnałów)
R-1, R-3, R-5, R-6, R-18 + Tier A modules (`telegram_approver.py`, `common.py`, `dispatch_pipeline.py`)
**Klasa:** 3240 LOC + 1645 LOC + 2706 LOC = każdy refactor obarczony "co tu się może wywalić". 52+45 except = defense-in-debt.

---

## 3️⃣ Klasyfikacja per-dimension

| Problem | ARCH | OPS | SCAL | MAINT |
|---|:---:|:---:|:---:|:---:|
| RC1 filesystem-as-IPC | ⬛⬛⬛ | ⬛ | ⬛⬛⬛ | ⬛ |
| RC2 cache invalidation | ⬛⬛ | ⬛⬛ | ⬛⬛ | ⬛⬛ |
| RC3 observability gaps | — | ⬛⬛⬛ | ⬛⬛ | ⬛ |
| RC4 append-only race | ⬛⬛ | ⬛⬛ | ⬛⬛⬛ | — |
| RC5 ownership emergent | ⬛⬛⬛ | — | ⬛⬛ | ⬛⬛⬛ |
| RC6 replay fragmented | ⬛⬛ | ⬛⬛ | ⬛ | ⬛⬛ |
| RC7 god objects | ⬛⬛ | — | ⬛ | ⬛⬛⬛ |
| Single-server SPOF | ⬛⬛⬛ | ⬛⬛⬛ | ⬛⬛⬛ | — |
| subprocess in asyncio | ⬛ | ⬛⬛ | — | ⬛ |
| 342 .bak + naming drift | — | ⬛ | — | ⬛⬛⬛ |
| Brak peak-hour enforcement | — | ⬛⬛⬛ | — | — |
| Brak logrotate | — | ⬛⬛⬛ | ⬛⬛ | — |

**Czyste architektoniczne** (struktura systemu): RC1, RC5, single-server, subprocess
**Czyste operational** (działanie codzienne): RC3, peak-hour, logrotate, F2 silent cron
**Czyste scalability** (10× / multi-tenant): RC1, RC4, single-server, hardcoded BIALYSTOK
**Czyste maintainability** (cognitive cost zmiany): RC5, RC7, .bak proliferation, naming drift

---

## 4️⃣ TOP-5 × 3 listy

### 🔬 TOP 5 — wymagają **deep MAX audit**

1. **`telegram_approver.py`** — 3240 LOC + 52 except + asyncio + subprocess.run + JSONL + state file. Audit cel: split na `bot/router.py` + `bot/proposals.py` + `bot/callbacks.py` + `bot/admin_cmds.py`; persistent pending state; replace `subprocess.run` z `asyncio.to_thread`. **Effort: ~5 dni; ROI: -60% blast radius.**
2. **`common.py`** — 61 fan-in + 1645 LOC + 92 flag entries + zero dedicated tests. Audit cel: split na `flags.py` + `constants.py` + `tz_utils.py` + `logger_setup.py` + `districts.py`. **Effort: ~3 dni; ROI: cała reszta refactoringu odblokowana.**
3. **`learning_log.jsonl` write paths** — 5+ writers, 110MB, avg 6962 B/linia (>PIPE_BUF). Audit cel: scan na broken JSON lines (czy interleaving już występuje), burst rate w peak, schema drift per writer. Migracja → `events.db` audit_log lub fcntl wrap.
4. **`dispatch_pipeline.py`** — 2706 LOC + ThreadPoolExecutor 10w + in-memory cache bez bounded LRU + 45 except. Audit cel: assess_order branch complexity (regular/czasówka/proactive/auto_proximity), eviction race w `_v327_evict_old_pre_recheck_entries`, parallel efficiency vs 2 vCPU.
5. **systemd unit hardening** — żadnego `WatchdogSec=`, żadnego `OnFailure=`, brak `MemoryMax`/`CPUQuota`, brak peak-hour gate. Audit cel: 16 services + 12 timers, deklaracyjny manifest co MUSI mieć każdy unit.

### 🎯 TOP 5 — **quick wins** (≤4h każdy, dramatyczny ROI)

1. **F2 cron-watchdog** — `OnFailure=dispatch-cron-alert@%n.service` template + 1 watchdog timer co 6h. **30 min → eliminuje całą klasę silent-fail.** Najlepsze value/effort w całym audycie.
2. **Logrotate dla 25+ logów + `learning_log.jsonl`** — 110MB+66MB+25MB+12.7MB rosną unbounded. **1h → eliminuje "dysk pełny" risk.**
3. **systemd `MemoryMax=2G CPUQuota=200%`** per long-running. **30 min → soft preemption zamiast OOM kill.**
4. **`flags.json` atomic write helper** — replace ad-hoc `json.dump(open(p,'w'))` (R-7 R=15). **1h → eliminuje "Adrian + parallel CC = corrupt flagi".**
5. **Cleanup `Let me produce the blocks.dispatch_v2/` + nested `dispatch_v2/dispatch_v2/` + `.tmp_cr2kure6.json` 5.5MB orphaned** + 342 `.bak` files retention policy. **1h → +15% reading ergonomics, eliminuje confused-reader trap (R-15).**

### ⏳ TOP 5 — **long-term risks** (kompounduje miesiącami)

1. **JSONL log unbounded growth** (R-14) — `learning_log` 110MB, `shadow_decisions` 66MB, brak rotacji. Przy 10× orderów = 1.1GB/mies + multi-writer JSONL fizycznie się załamie.
2. **Geocode cache permanent stale** (F6) — restauracje przeprowadzają się, cache nigdy nie invalid. 1 relo/kwartał × wieloletni rozwój = stała degradacja jakości decisions, niewykrywalna oprócz "kurierzy narzekają".
3. **Naming inconsistency 45 dotted refs** (R-13) — Adrian decyzja A "deferred". Każdy nowy programmer/nowa CC sesja musi nauczyć się landminy. Multi-tenant zablokowany do cleanup.
4. **`common.py` god hub kompounduje fan-in** — każdy nowy moduł importuje, fan-in rośnie, blast radius rośnie. Bez split refactor staje się niemożliwy do bezpiecznego wykonania (~6 mc).
5. **State ownership emergent** (F20/RC5) — bez `core/state_io.py` boundary, każdy nowy serwis (Bolt Food, Restimo) doda własne `open(orders_state, "w")`. Race + corruption gwarantowana w T+6 mc.

---

## 5️⃣ Trzy scenariusze katastrofy

### 💥 Co najprawdopodobniej **wyłoży system przy 10×** (300 → 3000 ord/d)

**Główny kandydat: kombinacja R-4 (learning_log race) + RC1 (filesystem-as-IPC) + R-2 (single server).**

Mechanizm:
- `learning_log.jsonl` przy 10× = ~1.1GB/mies + burst rate 100 zapisów/sec = **interleaving rate eksploduje** (P=4 → P=5 w R=P×I scoring)
- `events.db` SQLite WAL przy 10× = ~220MB; lock contention rośnie kwadratowo, `BEGIN IMMEDIATE` retry exhaustion
- `ThreadPoolExecutor 10w × 2 vCPU oversubscribe 5×` = przy 10× kandydatów concurrent = diminishing returns kompletnie negatywne, p95 latency >2s
- `shadow_decisions.jsonl` 66MB → 660MB; tail read seek time linearnie

**Verdict:** system zawiesza się na **persistence layer** (RC1+RC4), nie na compute. Postgres + Redis to **must-have** przed 5× (nie 10×) — czyli pre-Restimo Q3 2026.

### 🌪 Co najprawdopodobniej **wywoła chaos operacyjny**

**Główny kandydat: F2 (cron silent fail) + RC3 (observability gaps) + R-12 (brak peak-hour enforcement).**

Mechanizm: jeden cron umiera w piątek wieczorem (już zdarzyło się 03-07.05 z `overrides-reset` 4 dni martwy). Adrian zauważa dopiero przez przypadek (analiza tech debt, nie alert). Następne ofiary:
- `r04_evaluator` (czyta corrupt learning_log → fałszywe tier promotions)
- `daily_accounting` (rozliczenie tygodnia chybione, Bartek księgowość niezgodna)
- `cod_weekly` (już disabled; re-enable 11.05 — jeśli zapomnimy włączyć alert na fail = silent stop COD)
- `state-reconcile` (phantom backlog rośnie, fałszywe BEST candidates)

**Plus:** `Restart=on-failure` może odpalić `dispatch-shadow` w środku peak (11-14 / 17-20 Warsaw), bez `ExecStartPre=` peak guard. Adrian explicit ban tylko organizacyjny, nie technical.

**Verdict:** chaos przyjdzie z wielu blind-spot'ów jednocześnie. Lekarstwo: **F2 + cron health framework** (~1 dzień łącznie) blokuje 80% klas.

### 🩻 Co najprawdopodobniej **spowoduje hidden corruption**

**Główny kandydat: F1 (learning_log race) + F18 (replay re-runs current code) + F10 (V3.20 ghost duplicate audit).**

Mechanizm cichy:
- 3 procesy × peak burst × avg 6962 B/line × PIPE_BUF=4096 = **interleaved JSONL silent**
- R-04 evaluator (cron 03:00) konsumuje corrupt → stochastic tier promotions/demotions (już zaobserwowane w pre-fix vs post-fix mismatch tier_suggestions)
- LGBM validation gate dostaje skewed agreement_rate (kluczowe dla Z3 pivot autonomy)
- `replay_failed.py` post-incident "PASS" bo aktualny kod nie ma już bug-a → **post-mortem mówi co innego niż real-time decyzja**
- F10 ghost duplicate `COURIER_DELIVERED` → courier double-credit → fałszywy tier promotion (próg deliv≥50 łatwo przeskoczyć)

**Verdict:** corruption jest **niewykrywalna bez invariantów**. JSONL parser gubi linie bez alarmu (brak "linia per second X powinna istnieć"). Naprawa: fcntl wrap (~1h) + migracja do `events.db` audit_log + decision snapshot w shadow_decisions jako primary evidence.

---

## 6️⃣ Rekomendowana kolejność dalszych audytów

Logika sekwencji: każdy etap **odblokowuje** następny + ma diminishing returns gdy zrobiony out-of-order.

### Etap 1 (Tydzień 1, 8-15.05) — Eliminacja silent-fail klas

**Audyt celowany: cron health + observability gap mapping**

- **A1.** Cron-watchdog framework (F2 + RC3) — 30 min implementacja, 2h audit jakie cronty + jakie metryki
  → eliminuje **TOP-4 chaos operacyjny**
- **A2.** Logrotate manifest dla 25+ logów + retention SLA — 1h
- **A3.** systemd hardening audit (WatchdogSec/OnFailure/MemoryMax/CPUQuota) per-service — **deep audit Tier A** dla #5 powyżej
- **A4.** `replay_failed.py` extension: `--replay-at-commit <sha>` + snapshot-based replay (F18 + RC6)

### Etap 2 (Tydzień 2, 15-22.05) — Audit trail integrity

**Audyt celowany: append-only writer discipline + decision authority**

- **B1.** `learning_log.jsonl` audit: scan istniejących linii na corrupt JSON, burst rate measurement, schema drift per writer — **deep audit #3**
- **B2.** Migracja consolidation: 3 writery → `events.db` audit_log via `core/jsonl_appender.py` z fcntl wrap (krótkoterm) → events.db (długoterm)
- **B3.** F8 V3.27.5 race test (30 min) — strukturalny guard regresji
- **B4.** F10 ghost duplicate event_id deterministic fix

### Etap 3 (Tydzień 3-4, 22.05-05.06) — God object decomposition

**Audyt celowany: blast radius reduction**

- **C1.** `common.py` deep audit (#2 powyżej) — Single-Responsibility violations, lifetime semantics per flag/const, fan-in matrix per moduł → split plan
- **C2.** `telegram_approver.py` deep audit (#1 powyżej) — callback handler restart behavior, event loop blocking points, race między 4 asyncio tasks → split plan
- **C3.** `dispatch_pipeline.py` deep audit (#4 powyżej) — assess_order branch complexity, ThreadPoolExecutor efficiency, eviction race
- **C4.** `core/state_io.py` boundary read paths (F20 + RC5)

### Etap 4 (Tydzień 5-8, 05.06-03.07) — Foundation dla Postgres

**Audyt celowany: schema design + dual-write strategy**

- **D1.** Schema design audit (orders, couriers, decisions, plans, events, audit) + JSONB columns dla snapshot
- **D2.** Dual-write strategy per typ stanu — risk matrix consistency window
- **D3.** Cache invalidation audit: events.db `CONFIG_RELOAD` event + 4 procesy SUBSCRIBE (RC2)
- **D4.** Per-tenant config layer audit (`tenant_config.py` z city-specific districts, sheet IDs, exclusions)

### Etap 5 (przed Restimo onboarding Q3 2026) — Multi-tenant readiness re-audit

**Cel:** zweryfikuj że RC1+RC4+RC5+RC6 fundamentalnie zaadresowane przed onboarding-iem 2-go tenanta. Cofnięcie się staje się operacyjnie niemożliwe po tym momencie (incident-rate × 2 tenanty × 2× ekspozycji folklor = nie utrzymasz).

### Etap 6 (post-Restimo W2) — Re-run pełen audyt

**Cel:** które klasy dalej żyją? Empiryczny test 6-mc roadmapy.

---

## Sumaryczne wnioski

System jest **inżyniersko świetny per-fix** (V3.27.5 Path B, V3.28 parser_health 4-layer, courier_admin atomic 4-file rollback) ale **architektonicznie ad-hoc per-system**. 40 sygnałów z 3 auditów = symptomy 6-7 strukturalnych klas (RC1-RC7), nie 40 niezależnych problemów.

**Najlepszy ruch dziś (30 min):** F2 cron-watchdog. Najlepsze value/effort w całym 6-miesięcznym programie.

**Najgorsza ścieżka:** "Polished symptoms" (każdy fix per-finding) zamiast architectural migrations — klasa wraca przy następnym serwisie / tenancie / refactor cycle.

**Punkt zwrotny:** moment przejścia do 2-go tenanta (Restimo / Wolt Drive Q3 2026). Pre-tym musisz mieć M1 (Postgres) + M2 (Redis) + M5 (liveness) zrobione, inaczej onboarding odsłoni 6 root causes naraz w produkcji.

---

## Cross-ref

- **Companion documents w tym folderze:**
  - `ARCHITECTURE_AUDIT_2026-05-07.md` — top 20 ryzyk (R=P×I), 10 god objects, 10 modułów Tier A/B/C, scores: Maint 5/10 / Scal 3/10 / Prod 6/10
  - `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md` — F1-F20 findings (P0-P3) z scenariuszami failure i fixami punktowymi
  - `META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md` — 6 RC + 5 architectural migrations + 6-mc roadmap
- **Memory:**
  - `tech_debt_backlog.md` — 18/22 DONE post-evening 07.05
  - `lessons.md` #32 (silent except), #47 (service-scoped audit), #48 (recurring bug), #80-#83 (firmowe konto), #87-#88 (resolve_cid v2 + AIDER timeout)
- **Re-audit cadence:**
  - **Pre-Faza 7 100% flip** (~Tydzień 4, ~30.05): zweryfikuj F2/F1/F8/F5 zrobione
  - **Pre-Restimo onboarding** (Q3 2026): zweryfikuj M1+M2+M5 LIVE
  - **Post-Restimo W2** (Q3 2026): re-run pełen audyt
