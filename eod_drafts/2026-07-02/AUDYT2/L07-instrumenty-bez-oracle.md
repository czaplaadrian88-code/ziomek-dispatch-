# L07 — INSTRUMENTY BEZ ORACLE (2. fala) · AUDYT 2.0 · PAS 0.D

**Data:** 2026-07-01/02 noc · **Tryb:** READ-ONLY (zero mutacji prod; jedyny zapis = ten plik)
**Metoda:** recipe C9 z Fazy 1 — dla każdego przyrządu policz prawdę DRUGĄ metodą na realnej próbie; oznacz VALIDATED / VOID / UNTESTED. Szczególnie: „czy alert »system żyje« FAKTYCZNIE odpaliłby przy awarii".
**Zakres:** 12 przyrządów karmiących decyzje/Telegram/alerty — cała `observability/` (meta-strażnicy) + 5 narzędzi decyzyjnych (ziomek_pred_calibration, decision_outcomes, shadow_outcome_enricher, faza7 --telegram, daily_rule_report).

**CAVEAT PRAWDY (stosowany):** `delivered_at`/`picked_up_at` = prawda PRZYCISKOWA (±~3 min, 0/377 auto_geofence GT). Wszędzie oznaczam **proxy** (button-truth) vs **ground-truth** (fizyczny odczyt stanu/journala/offsetu).

---

## PODSUMOWANIE: 4 VALIDATED · 4 VOID/BROKEN · 3 PARTIAL · 1 INFO

**Najgroźniejsze (meta-strażnicy sami niewiarygodni — dokładnie teza 2.0):**
1. **`watchdog` — 5-dniowa CISZA WYKONANIA** (06-26 18:50 → 07-01 22:45, BEZ rebootu). Detektor stale-cronów sam był martwy 124 h i nikt nie zauważył. **[P1]**
2. **`dispatch-cod-weekly.service` — FAILED od 06-29 06:00 (2 dni), zero alertu** — bo NIE ma `OnFailure` I nie jest w ledgerze cron_health I nie ma progu w watchdog. Trzy siatki bezpieczeństwa, żadna nie złapała. **[P1]**
3. **`cron_health` ledger KŁAMIE** — 3 zdrowe oneshoty stoją `status=failed` na zawsze (nigdy nie zapisują success). **[P2]**
4. **`koord_cascade_monitor` — docstring obiecuje »kto pilnuje strażnika« (OnFailure), którego NIE MA** + nie w ledgerze. Martwy tripwire. **[P2]**

---

## 🔴 VOID / BROKEN

### 1. `observability/watchdog.py` — 5-dniowa cisza wykonania (meta-guard martwy) · **P1**
- **Co mierzy:** co 4 h iteruje `cron_health.json`, dla units z progiem sprawdza staleness → Telegram STALE alert. To JEDYNY danowy detektor „cron przestał chodzić".
- **Dowód (ground-truth, journal + uptime):** `journalctl -u dispatch-watchdog.service` pokazuje równe ticki co 4 h do **2026-06-26T18:50:03**, potem NIC aż do **2026-07-01T22:45:43**. `uptime` = 5 tygodni (boot 2026-05-27) → to NIE reboot ani nie vacuum journala (logi 06-25/26 są obecne). `ActiveEnterTimestamp` timera = 2026-07-01 22:45:07 → timer re-armował się dopiero przy restarcie dziś. **Realna dziura = 124 h bez ani jednego przebiegu.**
- **Przyczyna (hipoteza z konfiguracji):** timer ma tylko `OnUnitActiveSec=4h`+`OnBootSec=15min`, BEZ `OnCalendar`. Każdy `stop`/`daemon-reload` bez re-armu gubi kotwicę → cisza do następnego restartu. `Persistent=true` ratuje tylko przez reboot (którego nie było).
- **Materialność:** 124 h z wyłączoną detekcją stale. W tym oknie crony pozostały zdrowe (brak potwierdzonego przegapionego alertu), więc szkoda = **nie policzona, tylko istnienie miny**: następny stale-cron w takim oknie przejdzie niezauważony. Dodatkowo watchdog `checked=14`, a system ma ~50+ timerów → i tak pilnuje 1/4 floty.
- **Rekomendacja:** dodać `OnCalendar=*-*-* 00/4:00` (kotwica kalendarzowa niezależna od reloadów) + `dispatch-liveness-probe` (biega co 2 min, żywy) niech raz/tick sprawdzi `is_active dispatch-watchdog.timer` i wiek `last watchdog run`. Zarejestrować watchdog we własnym ledgerze (self-heartbeat), by cisza samego watchdoga alarmowała.

### 2. `dispatch-cod-weekly.service` — realna awaria 2 dni, całkowicie niewidoczna · **P1**
- **Dowód (ground-truth):** `systemctl is-failed dispatch-cod-weekly.service` = **failed**; journal: `06-29T06:00:03 Main process exited status=1/FAILURE` (i wcześniej 06-22 tak samo — awaria co poniedziałek). `systemctl show -p OnFailure` = **puste**. Ledger cron_health (20 units) NIE zawiera `dispatch-cod-weekly.service`. Watchdog: nietrackowany → `checked=14`, poza zasięgiem.
- **Dlaczego wszystkie 3 siatki milczą:** (a) brak drop-inu `OnFailure` (porównanie: `daily-accounting`/`faza7-kpi`/`delivered-integrity` MAJĄ `dispatch-onfailure-alert@...`); (b) brak wpisu w ledgerze → watchdog go nie widzi; (c) nawet gdyby był, ma `expected_max_silence_h=None` → `is_stale` zwraca False (watchdog.py:84 `if threshold is None: continue`).
- **Materialność:** 2 poniedziałki (06-22, 06-29) rozliczenia COD nie wykonane, 0 sygnału. To ŻYWY przykład klasy „silent cron" (jak `overrides-reset` martwy 4 dni w audycie 05.07). **Policzone: ≥2 przebiegi/2 tyg. przepadły.**
- **Rekomendacja:** dodać `OnFailure=dispatch-onfailure-alert@%n` (standard jak reszta) + zarejestrować w cron_health z progiem `expected_max_silence_h` (tydzień+bufor). Osobno: zdiagnozować exit 1 (to nie mój lane — L07 tylko demaskuje niewidoczność).

### 3. `observability/cron_health.py` — ledger trwale mylnie oznacza zdrowe oneshoty jako `failed` · **P2**
- **Co mierzy:** per-unit ledger success/failure czytany przez watchdog i (pośrednio) `/health/all`.
- **Dowód (ground-truth, 2. metoda = journal vs ledger):** dump `cron_health.json` → `dispatch-downstream-crosscheck.service`, `dispatch-retro-learning.service`, `dispatch-cod-panel-ingest.service` mają `status=failed, last_success=None`. Journal tych samych units: **wszystkie skończyły exit-0 świeżo** (downstream-crosscheck `22:46:42 Deactivated successfully`, retro-learning `07-01T04:30:03 Finished`, cod-panel-ingest `06-29T06:32:09 Finished`). Grep: `record_run_success` NIE występuje w `downstream_crosscheck_poll.py` — te oneshoty NIGDY nie zapisują sukcesu; ledger uczy się o nich tylko z `OnFailure` → po jednej awarii są „failed" na zawsze (dokładnie klasa false-positive z komentarza liveness_probe.py:180-185, załatana TYLKO dla 5 long-running, nie dla oneshotów).
- **Materialność:** ≥3 units permanentnie „failed" mimo zdrowia → każdy konsument ledgera (health summary, przyszły dashboard) widzi 3 fałszywe pożary; **realna przyszła awaria tych units nieodróżnialna** (już „failed"). Nie policzone ile/dzień — istnienie + stała liczba 3.
- **Rekomendacja:** oneshoty w cron_health powinny wołać `record_run_success` na końcu ExecStart (jak liveness dla long-running) — albo `OnSuccess=` drop-in symetryczny do `OnFailure`. Alternatywa: watchdog czyta `is_active`/ostatni exit z systemd zamiast ufać failure-only ledgerowi.

### 4. `observability/koord_cascade_monitor.py` — fałszywa obietnica „kto pilnuje strażnika" + rotation-blind · **P2 / P3**
- **Co mierzy:** nocnie liczy decyzje `KOORD all_candidates_low_score` z `pool_feasible>=1` za WCZORAJ; >0 = regres polityki always-propose. Alert priority=low (cichy bot).
- **Dowód A — martwy tripwire (ground-truth):** docstring l.14-15 deklaruje „Non-zero TYLKO gdy monitor sam się wywali → systemd OnFailure". `systemctl show dispatch-koord-cascade.service -p OnFailure` = **PUSTE**. Nie ma go też w ledgerze cron_health. → jeśli monitor sam padnie, **zero alertu**; obiecana siatka nie istnieje.
- **Dowód B — oracle liczby (proxy):** re-policzyłem 06-30 drugą metodą: main-only `(cascade=0, total=231)` == main+siblings `(0, 231)` → **dla wczorajszego runu liczba PRAWIDŁOWA** (main log trzyma 06-27→07-01, 5 dni, więc „wczoraj" zawsze w main).
- **Dowód C — latentna rotation-blindness:** `count_cascade` czyta TYLKO `open(LOG)` (koord_cascade_monitor.py:44), bez zrotowanych `.1/.2.gz`. `shadow_decisions.jsonl.1` = 06-27 00:00; head main = `2026-06-27T07:53`. → replay dowolnego dnia **≤06-26 czyta main-only = 0 rekordów → fałszywe »cascade=0, czysto«**. To DOKŁADNIE bug, który enricher już naprawił (SP-B2-LOGROT), nie przeniesiony tu (bliźniak nietknięty). Pod 2× wolumenem (dzienna rotacja realnie zadziała) ryzyko wejdzie też w run nocny.
- **Materialność:** tripwire — nie policzone, istnienie. Rotation — dziś nie tnie (yesterday-run OK), realny błąd dla replayów historycznych i przy wzroście. `daily_rule_report.py:15` ma ten sam wzorzec (czyta sam `SHADOW`).
- **Rekomendacja:** dodać `OnFailure` + rejestrację w ledgerze; przełączyć `count_cascade` na wspólny iterator rotation-aware (`iter_jsonl_records` z enrichera, który już obejmuje `.1/.2.gz`).

---

## 🟠 PARTIAL (działa, ale jedna noga kłamie / cicha)

### 5. `tools/ziomek_pred_calibration.py` — cela „odbiór × last" liczona na 4% próby · **P2**
- **Co mierzy:** rozjazd real−przewidywany dla odbioru i dostawy × 2 kotwice (`assign`=obietnica, `last`=żywe ETA konsoli) × solo/bundle → `--summary` drukuje „SUGEROWANĄ KOREKTĘ" do kalibracji „dobrych czasów" (feeds decyzję o kalibracji ETA).
- **Dowód (2. metoda na `ziomek_pred_calibration.jsonl`, n=2012, świeże 1.6 h):** pokrycie non-None:
  - `rozjazd_odbior_assign` 1677/2012 = **83%** (med +2.0)
  - `rozjazd_dostawa_assign` 1715/2012 = **85%** (med +5.0)
  - `rozjazd_dostawa_last` 1951/2012 = **97%** (med −4.7)
  - **`rozjazd_odbior_last` 78/2012 = 4%** (med −1.0) ← anomalia
- **Diagnoza:** po odbiorze stop pickup znika z planu → `_plan_preds` zwraca pickup=None → `t["last"]` (aktualizowany tylko gdy pred≠None, l.166-168) nigdy nie dostaje świeżej predykcji odbioru. `run_summary` (l.260-262) i tak drukuje „sugerowana korekta odbiór (mediana last): X" liczoną na n=78 — **VOID cela**, prezentowana na równi z pozostałymi (83-97%).
- **Materialność:** 1 z 4 kotwic kalibracji odbioru to biased subsample 4%. Kotwica `assign` (83%) zdrowa → temat load-aware ETA (bramka 04.07) powinien brać `assign`, nie `last`, dla odbioru. Policzone: 78 vs 1677.
- **Rekomendacja:** albo liczyć „odbiór×last" z ostatniego snapa PRZED odbiorem (osobny bufor, nie kasowany przez zniknięcie stopu), albo jawnie oznaczyć celę jako N/A w `--summary` gdy n<próg, żeby nikt nie skalibrował ETA na 4%.

### 6. `tools/faza7_daily_kpi.py --telegram` — digest CICHY pod `quiet-until-ready` · **P3**
- **Co mierzy:** dzienne bramki gotowości (readiness) + AUTO-agreement KPI; z `--telegram --quiet-until-ready` ślij digest TYLKO gdy READY.
- **Dowód (ground-truth):** biega codziennie 04:00, ostatnie 3 runy (06-29/30, 07-01) `Deactivated successfully` (exit 0) → proces żyje, log historyczny append-only rośnie. ExecStart = `faza7_daily_kpi --telegram --quiet-until-ready`. Ponieważ AUTON nie jest READY (`ENABLE_AUTO_ASSIGN=OFF`), gałąź `if args.telegram` bramkuje digest → **żaden dzienny KPI nie trafia na Telegram**. (`--dry-run` nie wyprodukował linii readiness — send-decyzja UNTESTED z zewnątrz.)
- **Materialność:** nie policzone; skutek = człowiek NIE dostaje dziennego KPI pushem; regres KPI (np. SLA, breach) nie wywoła powiadomienia — tylko cichy wpis w `kpi_log`. Świadome (quiet-until-ready), ale realna luka obserwowalności „na lata".
- **Rekomendacja:** rozważyć minimalny „always-on" digest (1 linia: liczba propozycji, breach%, latencja) niezależny od readiness AUTON — albo alert gdy KPI przekroczy próg, nawet w trybie shadow.

### 7. `tools/daily_rule_report.py` — write-only, brak konsumenta/alertu · **P3**
- **Co mierzy:** dzienne metryki reguł (fairness Gini, firing-rates) z shadow+decision_outcomes.
- **Dowód (ground-truth):** timer `dispatch-daily-rule-report.timer` biega (ostatnio 07-01 21:30), output `logs/reports/daily_rule_report.json` świeży (1 h, 2898 B). Kod: jedyny zapis to `json.dump(rows, f...)` (l.259) — **zero `send_admin_alert`/telegram**; grep nie znalazł automatycznego konsumenta pliku (tylko własna .service/.timer). Compute-but-vanish: liczy się codziennie, nikt tego nie czyta ani nie jest powiadamiany.
- **Materialność:** nie policzone; ryzyko = regres fairness/reguł widoczny tylko przy ręcznym `cat`. Read-only, zero wpływu na dispatch.
- **Rekomendacja:** albo dopiąć próg→alert (Gini > X, firing-rate reguły spadł do 0), albo świadomie udokumentować jako „artefakt do ręcznego przeglądu" i wpiąć do dashboardu entropii.

---

## 🟢 VALIDATED (oracle PASS — można ufać, z typem prawdy)

### 8. `observability/liveness_probe.py` — VALIDATED (proxy: journal HEARTBEAT) · INFO
- Biega co ~2 min (state `liveness_probe_state.json` świeży 22:46), ostatni run: `dispatch-shadow=ok sla-tracker=ok panel-watcher=ok telegram=ok gps=ok parser-health-8888=ok`.
- **Oracle „czy odpaliłby przy awarii":** needle `HEARTBEAT` REALNIE jest w journalach (shadow 201×, sla 188× w ostatnich 300 liniach) → gdy usługa przestanie bić, wiek rośnie > 300 s → alert. To NIE martwy needle. Blind-spot świadomy: `dispatch-telegram` intencjonalnie `disable`→traktowany jako OK (uzasadnione, l.252-268). To jedyny meta-strażnik, który realnie zadziała.

### 9. `tools/decision_outcomes.py` — VALIDATED (proxy: button-truth r6) · INFO
- Świeże (`decision_outcomes.jsonl` 0.8 h, newest delivered_at 07-01 21:11), n=1915. Join OK: no_verdict 15% (290), verdict pokryty w 85%. r6 obecne 1893/1915 (99%).
- **Caveat:** `r6_breach = delivered_at − picked_up_at > 35` = **proxy ±3 min** (button-truth, nie GT). Nagłówkowe „agreement 17%" (282/(282+1343)) to metryka JUŻ obalona przez audyt jako zła bramka autonomii — instrument liczy poprawnie, problem jest w interpretacji, nie w przyrządzie.

### 10. `tools/shadow_outcome_enricher.py` — VALIDATED (nie frozen) · INFO
- `last_offset=0` to **INTENCJONALNY** pełny re-scan okna (kod l.335-337: „trzymane =0 dla zgodności formatu"), dedup przez `processed_oids` (8196), rotation-aware (l.295-298 SP-B2-LOGROT obejmuje `.1/.2.gz`). Output `drive_min_enriched.jsonl` z dzisiejszymi danymi (last `enriched_at` 07-01T20:50, off-peak brak nowych dostaw). State saved_at 22:51 → biega.
- **Caveaty (nie-lie, ale dług):** re-scan 24 h co tick (perf — należy do 3.A); `processed_oids` przycinany do 25k → order dojrzewający po przycięciu może się zdublować (rzadkie).

### 11. `observability/delivered_integrity_monitor.py` — VALIDATED (ground-truth: live orders_state) · INFO
- **Oracle 2. metodą:** re-policzyłem z `orders_state.json`: `delivered_at_null=0` na dziś → monitor poprawnie MILCZY (alert iff >0). Logika liczenia zgodna z moją. Ma `OnFailure`. Czyta żywy stan (nie log) → brak rotation-risk. Zdrowy, ale WĄSKI: łapie tylko jedną regresję (`delivered_at=None`), nie ogólną integralność dostaw.

### 12. `observability/downstream_crosscheck_poll.py` — poller VALIDATED, ale ledger o nim kłamie · INFO→P2 (patrz #3)
- Poller działa: `22:46:42 downstream_status=ok`, exit 0. Problem NIE w pollerze, tylko w tym, że jego wpis w cron_health stoi `failed` (bo nie zapisuje success — finding #3).

---

## WNIOSKI PRZEKROJOWE (dla PIONU 2.B alerty danowe)
- **Rejestracja w cron_health jest ręczna i częściowa: 14 cron-units na ~50+ timerów.** Reszta (cod-weekly, koord-cascade, downstream-crosscheck itd.) poza staleness-detekcją. To ROOT niewidzialności cod-weekly.
- **Failure-only ledger** = strukturalny generator false-positives dla oneshotów; naprawiony tylko dla 5 long-running.
- **Bliźniak rotation-aware** (`iter_jsonl_records`) istnieje w enricherze, ale koord_cascade + daily_rule_report czytają sam main log — klasyczny „bliźniacze ścieżki nie naprawione razem".
- **`OnFailure` nie jest standardem** — 2/5 sprawdzonych meta/crony bez niego (cod-weekly, koord-cascade), mimo że docstring koord-cascade go obiecuje.
- **Meta-strażnicy z pojedynczym punktem podparcia** (watchdog = tylko OnUnitActiveSec) potrafią umrzeć cicho na dni. „Kto pilnuje strażnika" realnie działa TYLKO dla liveness_probe.
