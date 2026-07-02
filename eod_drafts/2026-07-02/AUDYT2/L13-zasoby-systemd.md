# L13 — PAS „ZASOBY-NA-LATA" + GOVERNANCE SYSTEMD (AUDYT 2.0 Ziomka)

**Tryb:** READ-ONLY (czytanie / grep / du / df / systemctl / journalctl). Zero edycji, restartów, flag, gita, wysyłki. Jedyny zapis = ten plik.
**Data:** 2026-07-02, ~02:15 UTC. Host `Ziomek`, uptime 35 dni.
**Zakres:** retencja/GC append-only, prognoza dysku, governance systemd (limity/watchdog/OnFailure/retire), pamięć długo-żyjących + swap.

---

## STAN BAZOWY (zweryfikowany świeżo, z liczbami)

| Zasób | Wartość | Uwaga |
|---|---|---|
| Dysk `/` | 150G total, **76G used (53%)**, 68G avail | inody 12% — bez ryzyka |
| RAM | 7,6G total, 302M free, **3,99G available** (z cache) | umiarkowana presja, load 0.78 |
| Swap | 8,0G total, **3,0G used (37%)** | winowajcy NIE-Ziomkowi (niżej) |
| `dispatch_state/` | **1,2G** | ścieżka realna = `/root/.openclaw/workspace/dispatch_state` (NIE pod `scripts/dispatch_v2/` — tam jest atrapa 14M) |
| ├─ `observability/` | **324M / 119 plików** | dzienne pliki, BEZ GC (niżej) |
| ├─ luźne `.jsonl` .1 (rotowane) | 206M | trzymane nieskompresowane |
| ├─ `.bak` / `.archive` cruft | **104M / 97 plików** | ręczne backupy, BEZ GC |
| ├─ `events.db` | 31M | auto_vacuum=0 |
| `scripts/logs/` | **734M** | objęte logrotate `*.log` |
| journald | **2,3G** on-disk | cap domyślny ~10% = ~15G |
| `/root/diag_2026-06-16/` | **771M** | STARY snapshot (16 dni), retire |
| `/mnt/storagebox` | **1,0T, 4,3G used, 1020G free (1%)** | zamontowany sshfs, offload GOTOWY ale NIEUŻYWANY |

Uwaga metodologiczna: liczby z zadania (`dispatch_state ~1.2GB`, `observability 322MB`, `logs ~0.7GB`, dysk 53%) potwierdzone 1:1. Rozbieżność „obj_replay_capture 91MB / learning_log ~183MB" — to nie osobne katalogi, tylko pojedyncze pliki `.jsonl` w `dispatch_state/` (`obj_replay_capture.jsonl` 91M; `learning_log.jsonl` 82M + `learning_log.jsonl.1` 101M = 183M razem).

---

## 1. RETENCJA / GC APPEND-ONLY

### 1a. Logrotate — CO DZIAŁA
- Config `/etc/logrotate.d/dispatch-v2` **istnieje, jest przemyślany** (25+ ścieżek, 4 grupy A/B/B-2/C, komentarze inwentarza).
- `logrotate.timer` **aktywny**, ostatni bieg **2026-07-02 00:00** (status file świeży). System GC dla `*.log` i wymienionych `.jsonl` **działa** (copytruncate).
- Pokrycie nazwane: `scripts/logs/*.log`, `learning_log.jsonl`, `v319c_read_shadow_log.jsonl`, `shadow_decisions.jsonl`, `sla_log.jsonl`, `consumer_stuck_alert_evaluations.jsonl`, `obj_replay_capture.jsonl`, `eta_calibration_log.jsonl`, `drive_min_enriched.jsonl`, `drive_min_calibration_log_v2.jsonl`, `plan_recheck_log.jsonl`, `czasowka_eval_log.jsonl`, `geocoding_log.jsonl`.

### 1b. Logrotate — CO NIE DZIAŁA JAK POMYŚLANO
- **ZERO plików `.gz` w całym `dispatch_state/` i `scripts/logs/`** mimo `compress`. Przyczyna: `size 100M` + `delaycompress`. Duże pliki przekraczają 100M dopiero co ~5-8 dni → rotacja zaszła DOTĄD tylko RAZ (istnieją same `.1`, brak `.2`/`.gz`). `delaycompress` kompresuje przy DRUGIEJ rotacji — która jeszcze nie nastąpiła. Efekt: 206M `.1` trzymane **nieskompresowane**, a `rotate 30` (retencja 30-dniowa) **de facto nigdy się nie łańcuchuje** — każdy plik plateauuje na ~2×100M zamiast się kompresować/archiwizować.
- Rotowane `.1` są **nad limitem** (`shadow_decisions.jsonl.1`=113M, `v319c...jsonl.1`=106M) — copytruncate przy 100M, ale plik urósł do kolejnej doby zanim rotacja się odpaliła.

### 1c. Observability — GC ATRAPA (najważniejsze ustalenie retencji)
- Komentarz w logrotate (l.113-116) twierdzi: dzienne pliki `observability/*_YYYYMMDD.jsonl` są sprzątane przez `dispatch_v2/observability/log_rotation.py`, retencja 14 dni via cron.
- **`log_rotation.py` NIE ISTNIEJE nigdzie na systemie** (`find / -name log_rotation.py` = pusto). Żaden cron/timer go nie woła. GC obserwability to **PHANTOM**.
- Skutek zmierzony: **119 plików, daty 2026-05-04 → 2026-07-02** (59× `candidate_decisions` + 60× `fleet_filter`). **88 plików starszych niż 14 dni** (sięgają ~60 dni wstecz). To najczystszy przykład „unbounded-append-only" — cichy, bo „udokumentowany jako rozwiązany".
- Tempo: ostatnie 6 dni avg **~18M/dobę** (2 pliki/dobę) → ~0,5G/mies. bez ograniczenia.
- Jedyny realny GC w `observability/` to `ground_truth_gc.py` (zaplanowany `dispatch-ground-truth-gc.timer`), ale on sprząta `courier_ground_truth.json` (stan JSON), **nie** dzienne `.jsonl`.

### 1d. Luźne `.jsonl` POZA logrotate (unbounded)
- **49 luźnych `.jsonl` w `dispatch_state/` NIE jest w configu = 129M** i rosną bez capa. Najwięksi:
  `r6_breach_shadow.jsonl` 29M · `courier_match_debug.jsonl` 25M · `reassignment_shadow.jsonl` 20M · `c2_shadow_log.jsonl` 13M · `gps_quality_shadow.jsonl` 5,8M · `bundle_calib_shadow.jsonl` 5M · `fleet_position_history.jsonl` 3,8M · `pending_global_resweep.jsonl` · `backfill_decisions_outcomes_v1.jsonl` · `pending_pool_log.jsonl` · `feas_carry_blind_shadow.jsonl` · `outcomes_clean_shadow.jsonl` · `b_route_shadow.jsonl` · `reconciliation_log.jsonl` · `ready_at_log.jsonl` · `pickup_lateness_shadow.jsonl` … (49 sztuk).
- To głównie logi shadow zaparkowanych eksperymentów — pojedynczo małe, zbiorczo rosną i nikt ich nie ogranicza.

### 1e. events.db — auto_vacuum OFF + fałszywa deklaracja retencji
- `PRAGMA auto_vacuum = 0` → skasowane wiersze **nigdy nie zwracają miejsca** do OS bez jawnego `VACUUM`. `freelist_count=0` (teraz gęsty = rośnie monotonicznie, nic się nie kasuje).
- `audit_log` = **80 666 wierszy, span 2026-04-11 → 2026-07-01 (81 dni)**. `events`=2279, `processed_events`=2163.
- Komentarz logrotate (l.130): „audit_log >90d delete. Empirical: zero rows >30d, no-op." — **FAŁSZ**: wiersze sięgają 81 dni. Próg 90d zostanie przekroczony **~2026-07-10**; nawet gdy delete się odpali, bez `VACUUM` (którego nikt nie planuje na events.db — jedyne `VACUUM` w kodzie to `telegram_approver.py` na innej bazie) miejsce nie wróci.

### 1f. `.bak` / `.archive` cruft
- **97 plików, 104M** ręcznych backupów pre-zmianowych, BEZ GC: `kurier_ids.json.bak-pre-add-XXX`, `courier_tiers.json.bak-pre-*`, `restaurant_coords.json.bak-pre-*`, oraz ciężkie: `events.db.bak-pre-opcja-c-2026-05-07` (18M), 3× `courier_api.db.bak-*` (16-19M), `courier_gps_commitment_shadow.jsonl.archive-pre-window-dedup-2026-06-05` (20M). Najstarsze z maja. Wzorzec „backup przed zmianą" działa, ale nic ich potem nie kasuje.

### 1g. READERZY vs rotacja — RYZYKO POPRAWNOŚCI (klasa „min_delivered")
- Istnieje **kanoniczny helper `tools/_rotated_logs.py`** (obsługuje `.1` + `gzip.open` dla `.gz`) — dobry wzorzec. Używają go m.in. `backfill_decisions_outcomes.py`, `decision_outcomes.py`, `objm_lexr6_canary_monitor.py`.
- ALE **wiele narzędzi omija helper** i hard-koduje listę `["...jsonl", "...jsonl.1"]` (tylko `.jsonl` + `.1`): `r6_overpessimism_test.py`, `shadow_signals_vs_tail.py`, `pos_age_outcome.py`, `distance_reshape_replay.py`, `base_score_decompose.py`, `fleet_t15_replay.py`, `latency_alarm.py`, `defer_hold_shadow.py`, `base_amplify_probe.py`, `soon_free_coverage.py`, `nogps_equal_override_watch.py`, `deferral_value_replay.py`, `no_gps_rescue_coverage.py`, `sequential_replay.py`, `rule_deviation_report.py` (≥15 sztuk).
- **Bomba z opóźnionym zapłonem:** dziś działają (istnieje tylko `.1`). Przy DRUGIEJ rotacji (dla `shadow_decisions`/`learning_log` to kwestia dni — 1. rotacja już była) stare dane trafią do `.2.gz`, a readerzy `.1`-only **po cichu odczytają mniej historii niż zakładają** — replaye kalibracyjne/ML na oknach 14-30 dni dostaną obcięty zbiór bez błędu. To dokładnie klasa „instrument kłamie po cichu" z AUDYT 2.0. `copytruncate` dodatkowo daje mikro-okno wyścigu (writer dopisuje między kopią a truncate), ale to marginalne wobec problemu obciętej historii.

---

## 2. PROGNOZA DYSKU

**Tempo netto-unbounded przypisane Ziomkowi (to, czego NIC nie ogranicza):**
- observability dzienne: **~18M/dobę** (zmierzone, 6-dniowa średnia)
- 49 luźnych `.jsonl` poza logrotate: **~8-12M/dobę** (szac., logi shadow niskiej częstości)
- events.db audit_log: **~0,4M/dobę**
- `.bak`/`.archive`: **~1,7M/dobę** (104M / ~60 dni)
- **RAZEM ≈ 28-32M/dobę ≈ ~1G/miesiąc netto-unbounded**

Pliki OBJĘTE logrotate (`*.log`, nazwane `.jsonl`) **nie rosną netto** — copytruncate/size-cap trzyma je na plateau (~2×100M lub 50M dla `.log`).

**Horyzont zapełnienia (68G wolnego):**
- Sam Ziomek (netto-unbounded ~30M/dobę): 68G / 30M ≈ **~2 270 dni ≈ ~6 lat**.
- **Wniosek: BRAK ryzyka zapełnienia dysku w horyzoncie tygodni/miesięcy.** Problem nie jest „dysk się zaraz zapełni", tylko: (a) cicha, bezterminowa akumulacja łamiąca cel „na lata"; (b) konkretne cache bez GC (observability, 49 jsonl, events.db); (c) 1TB offload stoi pusty.

**Zastrzeżenie (uczciwie):** nie mam historycznego baseline `df`, więc nie policzę precyzyjnie tempa CAŁEGO hosta (Ziomek dzieli 150G z Papu/panelem/dockerem/journaldem). Największe statyczne pożeracze `/`: `/var` 30G (głównie docker/containerd), `/root` 29G (w tym cache: `.npm` 5,4G, `.gradle` 4,2G, `.cache` 1,6G — reclaimable, nie-Ziomkowe; `diag_2026-06-16` 771M; `backups` 1,7G). journald 2,3G może rosnąć do ~15G capa. Konserwatywnie całość ~100-150M/dobę → ~1,5 roku — też nie-pilne.

---

## 3. GOVERNANCE SYSTEMD

### 3a. Inwentarz (rozjazd „16+12" → dziś)
- **Dispatch: 69 `.service` + 62 `.timer`** (60 timerów enabled). Nadajesz: 18 `.service` + 16 `.timer`.
- `list-units 'dispatch-*' --all` = 179 jednostek. Stany: **113 inactive/dead** (w większości oneshoty timerowe MIĘDZY biegami — to NORMALNE, nie retire), **53 active/waiting** (timery), 7 active/elapsed, **5 active/running**, **1 failed**.
- Unit-files: 64 enabled, 48 disabled, 18 static, 1 linked. (Uwaga: „disabled service" ≠ retire — dla pary timer+oneshot enable'uje się TIMER, service bywa disabled/static i to poprawne.)
- Rozrost jest realny (~87 jednostek dispatch), ale to dług entropii/utrzymania, nie zasobowy.

### 3b. Limity zasobów (odpowiedź na R-12 „BRAK" z 05.07)
- **MemoryMax: 53/87 SET, 34 bez limitu.** R-12 **CZĘŚCIOWO naprawione** — wszystkie 5 długo-żyjących running MA limit:
  `dispatch-shadow` 1,5G · `dispatch-panel-watcher` 1,5G · `dispatch-gps` 250M · `dispatch-sla-tracker` 250M · `dispatch-monitor-419` 600M.
- 34 bez MemoryMax — w większości oneshoty krótkie (mniejsze ryzyko), ale są wśród nich **usługi warte limitu**: `dispatch-drtusz-bridge`, `dispatch-papu-bridge` (mosty long-ish), `dispatch-watchdog`, `dispatch-cod-panel-ingest`, `nadajesz-ordering`. `dispatch-onfailure-alert@` to template (limit statyczny bezprzedmiotowy).
- **WATCHDOG: ŻADNA usługa nie ma skończonego `WatchdogUSec`** (wszędzie 0 lub infinity). Zero liveness-watchdog systemd na całym dispatchu — awaria „zawieszony ale żywy proces" nie jest łapana przez systemd (jest osobny `dispatch-liveness-probe` + `dispatch-watchdog`, ale to nie natywny sd_notify).
- `Restart=` niejednolity: shadow `on-failure`, panel `always`, gps `on-failure`, część oneshotów `no`.

### 3c. OnFailure (alerting)
- **62 z 69 usług dispatch ma OnFailure** (dyrektywa lub drop-in; 31 katalogów `.service.d`). Pokrycie ~90% — **dobre**. ~7 usług bez alertu awarii.

### 3d. Usługa FAILED
- **`dispatch-cod-weekly.service` — failed od 2026-06-29 06:00 (2 dni), exit status=1, ~461ms CPU** (szybki fail → prawdopodobnie auth/scrape panelu lub Google Sheets). `TriggeredBy dispatch-cod-weekly.timer` nadal uzbrojony → będzie cyklicznie failować i alarmować. Do triażu: naprawić albo retire (F2.1d COD Weekly).

### 3e. Retire-kandydaci
| Kandydat | Dowód | Rekomendacja |
|---|---|---|
| `dispatch-cod-weekly.service` | failed 2 dni, exit 1 | triage → fix lub retire (+timer) |
| `/root/diag_2026-06-16/` (771M) | snapshot 16 dni, jednorazowy diag | usunąć/offload (771M odzysk) |
| `dispatch-checkpoint-tz-shadow` | **timer disabled** (praca uśpiona) | potwierdzić i retire |
| `dispatch-nogps-equal-watch` | **timer disabled** | potwierdzić i retire |
| `dispatch-objm-lexr6-smoke-{flip,verdict,morning-summary}` | smoke zaparkowanego eksperymentu; checkpoint at-200 = 2026-07-03 | retire PO 03.07 (wg [[top10-progressive-potential-2026-06-29]]) |
| `.db.bak-*` / `.archive-*` > 30 dni | np. `events.db.bak-...05-07` (18M), `courier_gps..archive-...06-05` (20M) | offload na storagebox + skasować lokalnie |
| stare `observability/*_YYYYMMDD.jsonl` > 30-60 dni | 88 plików > 14 dni | odbudować GC (Sekcja 1c) |
| `monitor-419` running MIMO disabled unit-file | uruchomiony choć disabled | zweryfikować intencję (świadome vs dryf) |

---

## 4. PAMIĘĆ DŁUGO-ŻYJĄCYCH + SWAP

### 4a. Rdzeń dispatch = CHUDY (brak wycieku)
| Proces | RSS | Swap |
|---|---|---|
| `dispatch-shadow` | 34M | 11M |
| `dispatch-panel-watcher` | 39M | 24M |
| `dispatch-sla-tracker` | 28M | 16M |
| `dispatch-gps` (module) | 11M | 7M |
| `gps_server.py` (:root) | 2M | 9M |

Wszystkie w granicach swoich MemoryMax z ogromnym zapasem (shadow 34M / limit 1500M). Swap na nich to zimne strony (idle), nie wyciek.

### 4b. Swap 3,0G — winowajcy NIE-Ziomkowi (współlokacja hosta)
| Proces | Swap | Usługa |
|---|---|---|
| `uvicorn studio_renderer.app` | **833M** | Papu Studio Renderer (Playwright/Chromium, bind 0.0.0.0) — głównie wyswapowany (idle) |
| `uvicorn` (panel) | 236M | `nadajesz-panel.service` |
| `openclaw-gateway` | 148M | gateway |
| python worker | 142M | (multiprocessing spawn) |
| `assistant-telegram` | ~142M | `assistant-telegram.service` |
| `claude` ×2 | ~192M | **agenci tego audytu (swarm)** |
| `osrm-routed` | 72M | routing |

**Wniosek:** presja pamięci/swap to **oversubscription 8G RAM przez współlokację** (Papu studio_renderer 833M, panel, telegram, obserwability stack Prometheus 67M + Grafana 60M + Postgres, oraz sami agenci audytu), a **NIE** silnik Ziomka. Największy pojedynczy swap (studio_renderer 833M) sugeruje Chromium, który dawno nie był używany i został wypchnięty — kandydat do MemoryMax/idle-restart, ale to pas Papu, nie ten.

---

## POKRYCIE

- **Retencja/GC:** przeanalizowano cały `/etc/logrotate.d/dispatch-v2` (146 linii), status logrotate (`/var/lib/logrotate/status`, ostatni bieg + per-plik daty rotacji), obecność `.gz`/`.1`/`.2`, tempo observability (6 dni), 49 luźnych jsonl skrzyżowanych z configiem, events.db (auto_vacuum/freelist/page_count/span audit_log), cruft `.bak/.archive`, rotation-awareness readerów (grep helper vs hardcode).
- **Systemd:** `list-units --all` (stany), `list-unit-files` (enabled/disabled/static), 69 svc + 62 timer dispatch (+ 34 nadajesz), MemoryMax across 87 usług (pętla), WatchdogUSec/Restart na 6 kluczowych, OnFailure (grep drop-in, 62/69), failed unit (`systemctl status` + przyczyna), disabled-timer orphans.
- **Pamięć/swap:** `ps -eo rss --sort` top15, `/proc/*/status VmSwap` top12 z mapowaniem PID→cgroup→unit, VmSwap/VmRSS dla 5 rdzeniowych pidów, `free`.
- **Kontekst dysku:** `df -h`+`df -i`, `du` depth1 dla `/`, `/root`, `/root/.openclaw/workspace`, `dispatch_state`, `observability`, `logs`; storagebox (`mount`+`df`+`du`), diag snapshot, journald (`--disk-usage`).

## JAWNE LUKI

- **Brak historycznego baseline `df`/`du`** → prognoza CAŁEGO hosta jest szacunkiem; twarda liczba dotyczy tylko tempa netto-unbounded przypisanego Ziomkowi (~30M/dobę). Tempo 49 luźnych jsonl oszacowane (brak dat startu każdego pliku), nie zmierzone per-plik.
- **Nie odpalałem `VACUUM`/analizy zawartości events.db** poza PRAGMA + COUNT/MIN/MAX (READ-ONLY; VACUUM = zapis). Nie zweryfikowałem, czy jakikolwiek proces kasuje wiersze audit_log (deklaracja 90d nie ma widocznego wykonawcy — do potwierdzenia w kodzie zapisu).
- **Nie testowałem readerów rotation-aware na żywej 2. rotacji** (jeszcze nie wystąpiła) — ryzyko obciętej historii wywnioskowane z semantyki `delaycompress` + grep hardcode, nie z incydentu. Nie policzyłem dokładnie ilu readerów `.1`-only faktycznie czyta okna >1 rotacji (część może czytać tylko tail).
- **Nie zajrzałem głęboko w 34 usługi bez MemoryMax pod kątem realnego RSS** każdej (tylko 5 rdzeniowych + top RSS globalnie); oneshoty krótkie pominięte jako niskie ryzyko.
- **Nie audytowałem crona klasycznego** (`crontab -l` pusty dla usera; systemd-timers to główny mechanizm) — możliwe zadania w `/etc/cron.d/*` per-projekt nietknięte poza gErepem retencji.
- **storagebox przez sshfs/autofs** — nie testowałem realnego zapisu/opóźnień (READ-ONLY); „offload gotowy" = zamontowany i pusty, nie zweryfikowany write-path.
- Pas L13 = zasoby/systemd; **logika dispatchu, poprawność scoringu, treść instrumentów** poza zakresem (patrz L07 instrumenty-bez-oracle, L09 siła-strażników).

---

## 5 NAJWAŻNIEJSZYCH PUNKTÓW

1. **GC observability to ATRAPA.** `log_rotation.py` (rzekomo retencja 14d) **nie istnieje**, nic go nie woła → 119 plików / 324M, 88 starszych niż 14 dni, sięgają 4 maja, rosną ~18M/dobę bez końca. Najczystszy „unbounded-append-only" i idealny przykład „udokumentowane jako rozwiązane, faktycznie nie".

2. **Readerzy NIE są spójnie rotation-aware — bomba z opóźnionym zapłonem (klasa „min_delivered").** Jest kanoniczny helper `tools/_rotated_logs.py` (.1+.gz), ale ≥15 narzędzi hard-koduje `[".jsonl", ".jsonl.1"]`. Dziś działa (istnieje tylko `.1`), lecz przy 2. rotacji (dni!) stara historia idzie do `.2.gz` i replaye/ML **po cichu obetną okno bez błędu**. Ryzyko poprawności, nie tylko dysku.

3. **Dysk NIE grozi zapełnieniem w horyzoncie miesięcy** (~30M/dobę netto-unbounded z Ziomka → ~6 lat na sam dysk). Realny problem = cicha akumulacja łamiąca „na lata" + trzy cache bez GC (observability, 49 luźnych `.jsonl`=129M, events.db `auto_vacuum=0` z fałszywą deklaracją „0 wierszy >30d" — audit_log ma 81 dni i przekroczy 90d ~10.07) + 97 backupów `.bak/.archive`=104M bez sprzątania.

4. **Governance systemd: R-12 częściowo naprawione, dwie realne dziury.** MemoryMax ma 53/87 usług (wszystkie 5 rdzeniowych running — tak); OnFailure 62/69 (~90%). Braki: **żadna usługa nie ma natywnego systemd-watchdog** (WatchdogUSec=0/inf), oraz **`dispatch-cod-weekly` failuje od 2 dni** (timer wciąż uzbrojony → cykliczny alarm). Retire-kandydaci: cod-weekly, diag snapshot 771M, 2 usługi z disabled-timer (checkpoint-tz-shadow, nogps-equal-watch), trio objm-lexr6-smoke (po 03.07), stare `.db.bak`/`.archive`.

5. **Rdzeń Ziomka jest chudy — presja swap to współlokacja, nie Ziomek.** shadow 34M / panel-watcher 39M / gps 11M (przy limitach 250M-1,5G) — zero wycieku. 3,0G swap ciągną Papu `studio_renderer` (833M, Chromium idle), panel, telegram, stack observability i sami agenci audytu na 8G RAM. **Bonus-szansa:** `/mnt/storagebox` (1TB, 99% wolne) jest zamontowany i ma katalog `offloaded`, ale hook cold-archive z komentarza logrotate (l.118-124) **nigdy nie podpięty** — trywialny cel dla starych `.gz`/observability/`.bak`.
