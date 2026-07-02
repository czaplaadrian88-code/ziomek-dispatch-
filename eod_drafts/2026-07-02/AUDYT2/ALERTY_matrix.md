# AUDYT 2.0 Ziomka — Macierz alertów + alerty danowe + diagnoza cod-weekly

**Data:** 2026-07-01 (wieczór, snapshot na żywo)
**Lane:** ALERTY DANOWE + higiena jednostek + diagnoza cod-weekly
**Tryb:** READ-ONLY wobec produkcji (zero mutacji: brak edycji, systemctl start/stop/restart/enable/disable, git, Telegram). Dane z `systemctl show`/`journalctl`/odczytu plików. Skrypty: `scratchpad/alert_matrix.py` (+ `alert_matrix.json`), `gen_table.py`.

---

## TL;DR (5 zdań)

1. **Pokrycie alertami = 58%** — 50/86 jednostek `dispatch-*`/`nadajesz-*`/`ziomek-*` ma jakikolwiek alert (OnFailure ∪ cron_health ∪ liveness); **36 jest odsłoniętych** (padnie po cichu).
2. **`dispatch-cod-weekly.service` = jedyny FAILED teraz** — pada co poniedziałek na braku kolumny tygodnia w arkuszu; nie ma drop-inu OnFailure, nie ma go w cron_health → awaria serwisu niewidoczna (sieć bezpieczeństwa preflight/lastcall alarmuje o arkuszu, ale to inny kanał).
3. **`dispatch-watchdog.timer` MARTWY od 26.06 19:43** (disabled+inactive) — cała warstwa alertów staleności (czytanie cron_health co 4h) NIE DZIAŁA od 5 dni; nigdy w historii nic nie zaalarmował (tylko `stale=0`).
4. **26.06 19:43 wyłączono JEDNOCZEŚNIE 3 rdzeniowe monitory** (watchdog + delivered-integrity + state-panel-monitor, w 1 sekundę) — wygląda na przypadkowy zbiorowy `disable`, nie świadomy retire; nikt nie zauważył, bo strażnik staleności był jednym z zabitych (ślepy punkt samo-referencyjny).
5. **Alerty są PROCESOWE, nie DANOWE** — 162 call-site `send_admin_alert` to w ~95% dzienne werdykty/reminders eksperymentów shadow; tryby awarii DANYCH (sentinel-rate, pusta pula feasible, stale grafik/pozycje, ledger-stall, lying-instrument, kolejka time_recheck) mają **0 alertów na żywo** — poza `parser_health` (parser) i `detector_419` (419-storm).

---

## 1. MACIERZ ALERTÓW

Kolumny: **OnFailure** = drop-in `OnFailure=dispatch-onfailure-alert@%n` (Telegram na exit≠0). **cron_health (thr)** = jednostka zarejestrowana w `dispatch_state/cron_health.json` z progiem staleności [h] (czytanym przez watchdog). **Watchdog** = `WatchdogSec` systemd (heartbeat procesu). **MemMax** = `MemoryMax`. **liveness** = objęta `liveness_probe` (5 hot-serwisów). `last_success` z cron_health.

> ⚠ Dwie warstwy alertu = różne tryby awarii: **OnFailure** łapie *crash/exit≠0*; **cron_health+watchdog** łapie *ciszę / brak tiku*; **liveness** łapie *śmierć długobieżnego*. Jednostka bez żadnej z nich pada niewidzialnie. **Uwaga:** OnFailure bez żywego timera = pokrycie martwe (serwis nigdy nie startuje → nie ma czego łapać — patrz delivered-integrity).

### A. FAILED teraz

| Jednostka | Typ | Stan | OnFailure | cron_health (thr h) | Watchdog | MemMax | Restart | liveness | last_success |
|---|---|---|---|---|---|---|---|---|---|
| dispatch-cod-weekly | oneshot | **FAILED** (exit-code) | — | — | — | — | no | — | — |

### B. Long-running (8) — resilience = Restart + liveness_probe

| Jednostka | Typ | Stan | OnFailure | cron_health | Watchdog | MemMax | Restart | liveness | last_success |
|---|---|---|---|---|---|---|---|---|---|
| dispatch-gps | simple | active | ✅ | ✅ | — | ✅ | on-failure | ✅ | 2026-07-01 22:11 |
| dispatch-monitor-419 | simple | active | ✅ | ✅ | — | ✅ | always | — | 2026-05-07 08:09¹ |
| dispatch-panel-watcher | simple | active | ✅ | ✅ | — | ✅ | on-failure | ✅ | 2026-07-01 22:11 |
| dispatch-shadow | simple | active | ✅ | ✅ | — | ✅ | on-failure | ✅ | 2026-07-01 22:11 |
| dispatch-sla-tracker | simple | active | ✅ | ✅ | — | ✅ | on-failure | ✅ | 2026-07-01 22:11 |
| dispatch-telegram | simple | inactive² | ✅ | ✅ | — | ✅ | on-failure | ✅ | 2026-07-01 22:11 |
| **nadajesz-ordering** | simple | active | **—** | **—** | — | — | on-failure | **—** | — |
| **nadajesz-panel** | simple | active | **—** | **—** | — | ✅ | always | **—** | — |

¹ `monitor-419` żyje (active), ale `last_success` w cron_health zamrożony na 07.05 (wpis nieużywany) + `enabled=disabled` → **nie wstanie po reboocie**.
² `dispatch-telegram` świadomie `disable --now` (C1 2026-06-26 — „Ziomek nie wysyła propozycji na telegramie"); liveness_probe to wie i tłumi alert DOWN.

**Luka B:** `nadajesz-panel` (backend konsoli gps.nadajesz.pl/admin) i `nadajesz-ordering` — jedyna ochrona to `Restart=`; **brak OnFailure, brak liveness, brak cron_health**. Crash-loop tych 2 produkcyjnych serwisów = cisza (restart maskuje, ale flapping nie zaalarmuje).

### C. Oneshot POKRYTE (OnFailure lub cron_health) — 44 szt.

Wszystkie mają OnFailure ✅. Zarejestrowane w cron_health (z progiem staleności) — kluczowe wpisy:
`cod-weekly-preflight (192h)`, `czasowka (0.1h)`, `daily-accounting (96h)`, `event-bus-cleanup (25h)`, `overrides-reset (25h)`, `plan-recheck (0.2h)`, `r04-evaluator (25h)`, `state-reconcile (1h)`, `restic-backup/faza7-kpi/retro-learning/downstream-crosscheck/cod-panel-ingest (thr=—, brak progu)`.
Pełna lista 44 → `scratchpad/matrix_table.md` sekcja C.

> ⚠ **cron_health z thr=None** (faza7-kpi, retro-learning, cod-panel-ingest, downstream-crosscheck, restic-backup) — zarejestrowane, ale watchdog **NIE liczy im staleności** (`is_stale` wymaga progu). To „pokrycie na papierze": OnFailure łapie crash, ale cichy zanik tiku — nie.
> ⚠ **delivered-integrity** ma OnFailure ✅+MemMax ✅ ale jego **timer jest disabled** (patrz §4) → serwis nigdy nie startuje → OnFailure nie ma czego pilnować = pokrycie MARTWE.

### D. Oneshot BEZ ŻADNEGO ALERTU — 34 szt. (+2 long-running z §B = **36 odsłoniętych**)

| Jednostka | Stan timera | Klasa | Ryzyko cichej awarii |
|---|---|---|---|
| **dispatch-cod-weekly** | Mon 08:00 (żywy) | **FINANSE** — rozliczenie kurierów | **WYSOKIE** (realizuje się co tydzień) |
| **dispatch-watchdog** | **disabled** | **META-ALERT** (strażnik staleności) | **KRYTYCZNE** — martwy = ślepota całej warstwy |
| dispatch-koord-cascade | daily (żywy) | monitor danowy (KOORD-regres) | średnie — docstring deklaruje OnFailure, wpięcia BRAK³ |
| dispatch-state-panel-monitor | **disabled** | monitor danowy (state↔panel drift) | wysokie (wyłączony 26.06) |
| dispatch-parcel-merge | 30s (żywy) | most paczek → orders_state | wysokie (LIVE tor produkcyjny) |
| dispatch-pending-pool / postpone-sweeper / pending-resweep-watchdog | żywe | operacyjne (kolejki) | średnie |
| dispatch-carried-first-guard / pickup-slip-monitor / later-promises-monitor / nogps-equal-watch | mieszane | strażnicy danowi read-only | średnie (sam strażnik bez strażnika) |
| dispatch-drtusz-bridge / papu-bridge | żywe | mosty firm/Papu | średnie |
| dispatch-eta-calibration / shadow-enrichment / state-snapshot / orders-state-prune | żywe | pipeline pomocniczy | niskie-średnie |
| dispatch-pickup-slip-review / ziomek-time-route-review / pending-resweep-review | review (future) | dzienny werdykt | niskie |
| **13× nadajesz-*** (alerts-tick, roster-sync, history-ingest, customer-*, econ-rollup, fc21-eval, ksef-cost-sync, overflow-*, panel-backup, parcel-shadow, payment-capture, shadow-ingest) | żywe | zasilanie/rozliczenia konsoli | **średnie-wysokie** (payment-capture, roster-sync, ksef-cost-sync, panel-backup = finanse/grafik/backup) |

³ `koord_cascade_monitor.py` docstring: „Non-zero TYLKO gdy monitor sam się wywali → systemd OnFailure (kto pilnuje strażnika)". **Rzeczywistość: brak drop-inu OnFailure** — deklarowana ochrona nie istnieje.

---

## POKRYCIE

| Metryka | Wartość |
|---|---|
| Jednostek primary (service) | **86** (78 oneshot + 8 long-running) |
| FAILED teraz | **1** — `dispatch-cod-weekly.service` |
| Pokryte OnFailure | **50 / 86 = 58%** |
| Pokryte JAKIMKOLWIEK alertem (OnFailure ∪ cron_health ∪ liveness) | **50 / 86 = 58%** |
| **Odsłonięte (żaden alert)** | **36 / 86 = 42%** |
| — w tym FINANSE/produkcja | cod-weekly, nadajesz-panel, nadajesz-ordering, payment-capture, roster-sync, ksef-cost-sync, panel-backup, parcel-merge |
| cron_health: zarejestrowanych | 20 jednostek; z realnym progiem staleności — **13** (7 ma thr=None = watchdog ich nie liczy) |
| Warstwa staleności (watchdog) | **MARTWA od 26.06** — realne pokrycie ciszy = **0%** dopóki nie wstanie |

**Wniosek:** deklarowane 58% OnFailure to górna granica; **realne pokrycie ciszy/staleności ≈ 0** (watchdog down), a OnFailure łapie tylko crash oneshotu który **żyje i wystartuje** — dla 5 disabled timerów i cod-weekly (poza cron_health) nie łapie nic.

---

## 2. ALERTY DANOWE — inwentaryzacja vs tryby awarii

**Istniejące alerty DANOWE (żywe):**

| Alert | Plik:linia | Warunek | Stan |
|---|---|---|---|
| Parser anomaly (STUCK/ZERO_OUTPUT) | `parser_health.py:1,55-80` (w panel-watcher) | count parsowanych = 0 / plateau vs baseline | ✅ ŻYWY |
| 419 storm (śmierć sesji panelu) | `monitoring/detector_419.py:94-118` | ≥5 zdarzeń 419 w oknie, cooldown 300s | ✅ ŻYWY (monitor-419 active) |
| KOORD-cascade regres | `observability/koord_cascade_monitor.py` | KOORD `all_candidates_low_score` z pool_feasible≥1 > próg(1) | ✅ ŻYWY, ale **LOW/cichy bot**, dedup 1/dzień; to ODWROTNOŚĆ pustej puli |
| Delivered-integrity (delivered_at=None) | `observability/delivered_integrity_monitor.py:9` | doręczone dziś z pustym delivered_at | ⚠ KOD ŻYWY, **timer DISABLED 26.06** → OFF |
| SLA/R6 breach | `sla_tracker.py` (2 call-site) | naruszenia R6 (obecnie BAG_TIME >30 tłumione) | ✅ ŻYWY (długobieżny) |
| Liveness 5 hot-serwisów | `observability/liveness_probe.py:316` | `is_active`=False → `[ZIOMEK LIVENESS] DOWN` | ✅ ŻYWY |
| Staleność cron (meta) | `observability/watchdog.py:87` | `is_stale(unit, thr_h)` z ledgera | ❌ **MARTWY** (timer disabled 26.06) |

**162 call-site `send_admin_alert`** — dominują `tools/*` (werdykty/reminders per eksperyment: reassignment, objm-lexr6, bundle-calib, pickup-floor, obj-fresh…). To alerty **PROCESOWE** (czy eksperyment wart flipu), nie **strażnicy danych na żywo**.

**Tryby awarii DANYCH bez alertu (checklist zespołu):**

| Tryb awarii danych | Alert? | Dowód / gdzie |
|---|---|---|
| **sentinel-rate (0,0)/BIALYSTOK_CENTER** | ❌ **BRAK** | `common.py:542` loguje sentinel, **nie alarmuje na rate**; jedyny licznik = jednorazowy deploy-verify `tools/verify_obj_f4_k2_2026-05-21.py:55`. (Dowód zespołu: 2046+14456 zdarzeń bez alertu.) |
| **pusta pula feasible (0 kandydatów / BRAK KANDYDATÓW)** | ❌ **BRAK** (częściowo) | Brak alertu na `feasible==0`. `koord_cascade` łapie tylko *feasible≥1 wepchnięte w KOORD* (odwrotność), na cichym bocie. |
| **stale grafik / roster** | ❌ **BRAK** | grep `schedule.*stale/roster.*stale` + `send_admin` = pusto |
| **stale pozycje % (GPS)** | ❌ **BRAK** | last-known-pos store istnieje (`courier_resolver`), ale **brak agregatowego alertu** na % stale/no_gps |
| **ledger przestał rosnąć (shadow_decisions / cron_health)** | ❌ **BRAK** | brak strażnika „append-rate=0"; canary czyta shadow_decisions tylko dla %verdykt, nie na stall. Watchdog (ledger staleness) = martwy. |
| **werdykt-tool czyta zamrożony plik (lying instrument)** | ❌ **BRAK** | brak guardu freshness pliku wejściowego w tools; Faza-1 audytu: 19/49 przyrządów kłamie — zero runtime-alertu |
| **parser anomaly** | ✅ JEST | `parser_health.py` (STUCK/ZERO_OUTPUT) |
| **kolejka time_recheck zalega** | ❌ **BRAK** | grep `time_recheck.*backlog/queue.*depth` + `alert` = pusto |
| delivered-integrity (delivered_at=None) | ⚠ JEST kod, OFF | timer disabled 26.06 |
| 419 storm | ✅ JEST | detector_419 |

---

## 3. DIAGNOZA `dispatch-cod-weekly` (READ-ONLY, bez naprawy)

**Co pękło (root-cause, natychmiastowy):** awaria **danych/ops, nie kod**. Arkusz „Wynagrodzenia Gastro" nie miał wpisanego bloku tygodnia docelowego. Run 29.06 06:00 UTC: `find_target` (payday=01-07-2026) → brak, AUTO-DETECT (zakres 22-28.06.2026) → brak → `TARGET COLUMN FAIL` → **exit 1**. Wzorzec **powtarzalny**: padło też 08.06, 15.06, 22.06; człowiek czasem dodaje kolumnę na czas (01.06, 22.06 = OK). Pełny traceback: `logs/cod_weekly.log` (29.06 06:00).

**Czy OnFailure był podpięty i czy zadziałał:** **NIE i NIE.** `dispatch-cod-weekly.service` **nie ma katalogu drop-in** (`.service.d/` nie istnieje) — brak `OnFailure=`, brak `record_oneshot_success`, brak MemoryMax. W przeciwieństwie do swoich bliźniaków (preflight/lastcall MAJĄ oba drop-iny). Systemd na exit 1 **nie wysłał nic**. Serwis nie jest też w `cron_health.json` → watchdog (nawet gdyby żył) by go nie sprawdził. **Potrójna cisza na poziomie serwisu.**

**Dlaczego nikt nie wiedział (pełna analiza obrony):**
1. **Sieć bezpieczeństwa preflight/lastcall CZĘŚCIOWO zadziałała.** `cmd_preflight` na braku kolumny robi SOFT-FAIL: wysyła Telegram do właściciela COD (Rafał) i **zwraca exit 0** (świadome, od 30.05 — żeby nie zatruwać cron_health). Logi potwierdzają: **28.06 21:00** (preflight, nd) i **29.06 05:00** (lastcall, pn) → oba `PREFLIGHT SOFT-FAIL … alert wysłany, exit 0`. Czyli 2 alerty o arkuszu POSZŁY — ale nie zadziałano (luka ludzka), a idą do kanału COD, nie do dyżuru dispatchu.
2. **Sama awaria serwisu (pn 08:00) była CICHA na obu poziomach:** (a) brak OnFailure (systemd milczy); (b) ścieżka wczesnego `find_target`-fail w `cmd_write` (linia ~598-600) **NIE wysyła własnego Telegrama** — tylko loguje i wychodzi 1. Kontrast: 22.06 padło PÓŹNIEJ (empty_check po znalezieniu kolumny) i `cmd_write` WYSŁAŁ swój raport `[COD WEEKLY] FAILED` → wtedy było widać. Wczesny fail = zero telemetrii poza plikiem logu.
3. Serwis zostaje w stanie `failed` (systemd) bez eskalacji; watchdog by to złapał tylko gdyby (i) cod-weekly był w cron_health i (ii) watchdog żył — żadne nie jest prawdą.

**Bliźniaki preflight/lastcall — czemu „inactive":** to `oneshot` — `inactive (dead)` między tikami timera jest NORMALNE. Odpaliły się poprawnie (28.06 21:00 / 29.06 05:00, „Deactivated successfully"), wykryły brak i zaalarmowały. Nie są zepsute — po prostu ich alert to inny kanał niż awaria serwisu.

**Propozycja fixu (OPIS, zero kodu):**
1. **Parytet z bliźniakami:** dodać `dispatch-cod-weekly.service.d/` z (a) `onfailure.conf` (`OnFailure=dispatch-onfailure-alert@%n`), (b) `resource_limits.conf` (MemoryMax jak batch), (c) `ExecStopPost=record_oneshot_success.sh %n`. Zamyka ciszę systemd (parytet z 44 innymi jednostkami).
2. **Rejestracja w cron_health.json** z progiem ~192h (tygodniowa kadencja, jak preflight) → watchdog flaguje zaległy/failed run.
3. **Domknięcie u źródła w `cmd_write`:** ścieżka wczesnego `find_target`-fail powinna wysłać własny Telegram (parytet z późniejszym empty_check i z SOFT-FAIL preflightu) — awaria głównego runu nigdy niema, nawet bez drop-inu.
4. **Eliminacja przyczyny (Z3):** payday/zakres są deterministyczne — auto-tworzyć blok tygodnia w arkuszu (albo eskalować alert preflight na grupę dispatchu, nie tylko Rafała), żeby usunąć powtarzalny ręczny krok.

---

## 4. GOVERNANCE

**Liczba jednostek (pliki w `/etc/systemd/system`):**
- `dispatch-*`: 67 service + 61 timer (bez `@`/`.retired`)
- `nadajesz-*`: 17 service + 15 timer
- `ziomek-*`: 2 service + 2 timer
- `dispatch-onfailure-alert@*`: **52 instancje** (handlery alertów — infrastruktura, nie „praca")
- `.retired`: 2 (`dispatch-shift-notify` .service/.timer, 15.06)
- **Primary services (analizowane): 86** | **Timery: 78**

**Timery: realnie odpalone 7 dni vs martwe:**
- **Odpaliło w 7 dni: 71 / 78**
- **Uzbrojone na przyszłość (OK): 2** — `bundle-calib-review` (07-02), `pickup-slip-review` (07-04) = jednorazowe review
- **MARTWE (disabled + inactive): 5** ↓

| Timer | Wyłączony | Klasa | Werdykt |
|---|---|---|---|
| **dispatch-watchdog** | 26.06 19:43:42 | META — strażnik staleności (MP-#4) | **PRZYWRÓCIĆ** — nie retire; śmierć = ślepota warstwy ciszy |
| **dispatch-delivered-integrity** | 26.06 19:43:41 | monitor danowy (delivered_at=None, B3/B5) | **PRZYWRÓCIĆ** — rdzeniowa integralność |
| **dispatch-state-panel-monitor** | 26.06 19:43:41 | monitor danowy (state↔panel drift, Faza 5) | **PRZYWRÓCIĆ / triage** |
| dispatch-nogps-equal-watch | 23.06 05:20 | obserwacja override no_gps (co h) | retire-kandydat (eksperyment) |
| dispatch-checkpoint-tz-shadow | 27.06 21:09 | shadow measure-first (korpus OFF/ON) | retire-kandydat (czeka na werdykt flip) |

> **⭐ Kluczowe:** 3 pierwsze zabito **w tej samej sekundzie 26.06 19:43** → jeden zbiorowy `disable`/`stop` (prawdopodobnie skutek uboczny prac liveness/telegram-off 26-27.06), **nie 3 świadome decyzje**. To 3 RDZENIOWE monitory (staleność + 2 integralności danych), nie shadowy. Nikt nie zauważył, bo strażnik staleności (watchdog) sam był w tej trójce = ślepy punkt.

**Kandydaci do retire (świadomego, po ACK):**
- `dispatch-checkpoint-tz-shadow`, `dispatch-nogps-equal-watch` — eksperymenty shadow po oknie pomiaru (jeśli werdykt zapadł).
- Rodzina `objm-lexr6-smoke-*` (flip/verdict/morning-summary) — jednorazówki z 26.06; jeśli faza zamknięta → retire (dziś inactive, OnFailure ✅).
- `dispatch-monitor-419` — **NIE retire**, ale `enable` (żywy, lecz `enabled=disabled` → nie wstanie po reboocie).

---

## JAWNE LUKI

1. **[KRYTYCZNE] Warstwa staleności martwa** — `dispatch-watchdog.timer` disabled od 26.06; realne pokrycie „ciszy/braku tiku" = 0%. Wszystkie progi w cron_health są bezużyteczne dopóki nie wstanie.
2. **[KRYTYCZNE] 3 rdzeniowe monitory zabite zbiorczo 26.06** (watchdog, delivered-integrity, state-panel-monitor) bez śladu decyzji — przypadkowy collateral; brak alertu „monitor zniknął" (bo to właśnie te monitory).
3. **[WYSOKIE] cod-weekly (finanse) całkiem odsłonięty** — brak drop-inu OnFailure + brak w cron_health; wczesny fail nie wysyła Telegrama. Root-cause powtarzalny (brak kolumny w arkuszu), fix = parytet z bliźniakami + auto-blok.
4. **[WYSOKIE] Zero alertów DANOWYCH na żywo** dla: sentinel-rate (0,0), pusta pula feasible (0 kand.), stale grafik, stale pozycje %, ledger-stall, lying-instrument (zamrożony plik wejścia), kolejka time_recheck. Alerty w systemie są PROCESOWE (OnFailure/werdykty), nie DANOWE.
5. **[ŚREDNIE-WYSOKIE] Produkcja konsoli bez alertu** — `nadajesz-panel`/`nadajesz-ordering` mają tylko `Restart=`; crash-loop nie zaalarmuje. Plus 7 finansowo-krytycznych `nadajesz-*` oneshotów (payment-capture, roster-sync, ksef-cost-sync, panel-backup, econ-rollup…) bez żadnego alertu.
6. **[ŚREDNIE] Pokrycie „na papierze"** — (a) 7 wpisów cron_health z thr=None (watchdog nie liczy im staleności); (b) `delivered-integrity` ma OnFailure ale timer OFF (pokrycie martwe); (c) `koord_cascade` deklaruje w docstringu OnFailure którego nie ma wpiętego (kłamie o własnej ochronie).
7. **[ŚREDNIE] `monitor-419` i `dispatch-telegram` `enabled=disabled`** — żyją, ale nie wstaną po reboocie (419-detektor = jedyny strażnik śmierci sesji panelu).

---

*Artefakty: `scratchpad/alert_matrix.py` + `alert_matrix.json` (surowe `systemctl show` 86 jednostek), `gen_table.py` + `matrix_table.md` (pełna tabela A-D, 78 oneshot + 8 long-running). Wszystko read-only; zero mutacji produkcji.*
