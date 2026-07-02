# ZIOMEK — AUDYT 2.0: „NIEZAWODNOŚĆ · JAKOŚĆ · SKALA" (PROJEKT do ACK)

**Status:** PROPOZYCJA (zero wykonania) · **Data:** 2026-07-01 wieczór · **Autor:** sesja główna (analiza dziur audytu 1.0 + 3 agenty: sweep zaplecza / diff pokrycia / digest wcześniejszych audytów)
**Zleceniodawca (Adrian):** „poszukaj dziur i braków w audycie z nocy, nowych tropów — Ziomek ma być najlepszym dispatcherem, skalowalnym i wolnym od błędów, pracującym latami."

**Czym 2.0 RÓŻNI SIĘ od 1.0:** Audyt 1.0 (Faza 1, 30.06, ~100 agentów) odpowiedział na JEDNO pytanie: *czy system jest spójny i czy przyrządy mówią prawdę* (entropia). Zrobił to dobrze — rdzeń decyzyjny gęsto pokryty, oracle-lane, adwersaryjny pas, jawne luki. **Ale cel Adriana to TRZY pytania, których 1.0 nie zadawał:**
1. **Czy decyzje są DOBRE?** (jakość vs osiągalne optimum — nie tylko „zgodne same ze sobą")
2. **Czy system przeżyje AWARIE i CZAS?** (odporność, degradacja, lata pracy)
3. **Czy system przeżyje WZROST?** (×2-×10 wolumenu, drugie miasto, autonomia)

Plus **PION 0** — domknięcie dziur samego 1.0 (audyt audytu). Metoda = ta sama co w 1.0 (ledger pokrycia 100%, macierz metoda×oś, runtime-oracle obowiązkowy, adversarial verify, dedup do rootów, jawne luki nie cisza).

**Relacja do Fazy 3 (naprawy L2.1→L8):** Faza 3 pozostaje GŁÓWNYM wątkiem. 2.0 nie konkuruje — lane'y read-only idą równolegle (jak Faza 1); eksperymenty (game-day/load/wyścigi) = osobne ACK, off-peak, shadow/replika/`--dry`. Pion 0.E może znaleźć nowe P0 → wtedy re-priorytetyzacja fal L za zgodą Adriana.

---

## 0. DZIURY AUDYTU 1.0 (dowody — co dokładnie przegapiono / nie sprawdzono)

### 0.1 Jawnie przyznane przez sam 1.0 (ledger §3, raport §5/§8 — uczciwie zadeklarowane)
- **14 rootów P2/P3 BEZ adwersaryjnej weryfikacji** (cap 64-thunków) — m.in. `shift-start-midnight-anchor`, `shared-state-no-lock-rmw`, `naive-datetime-tz-convention-split`, `unbounded-append-only-caches`.
- **~120/145 `tools/` nieczytane liniowo**; **zero `py_compile`/import żadnego narzędzia** („świeży jsonl dowodzi że collector biega, nie że werdykt-tool odpali").
- **Parytety deklarowane Z LEKTURY, nie z odpalenia** (lex_qual kanon vs frozen przy OBU stanach flagi; `_count_sla` A vs B).
- **DEAD?-hipotezy nieosiągalności** dla modułów flag-OFF (`commitment_emitter`, `pending_queue_provider`, `traffic_v2_aggregator`) — niepotwierdzone.
- `scoring.py` ~19 kar `bonus_` niezmapowane do warstw; wnętrze Kotlin apki (PRODUCENT pozycji/statusów) nieczytane; button-truth zamiast fizycznej dla większości przyrządów.

**Plus granularne (sweep 71/71 plików backing — pełna lista w ANEKS, raport 1). Najcięższe:**
- **MATERIALNOŚĆ NIGDY NIE POLICZONA** — cały sweep B/D/F deklaruje ISTNIENIE ścieżki z lektury, nie „ile worków/dzień psuje". Systemowo nie wiadomo, czy root to P1 czy kosmetyka (magnitudę dostało tylko 49 przyrządów Fazy C).
- **`pytest tests/` = oracle ETAP-4 tylko CZĘŚCIOWO wiarygodny**: audyt sam nie odpalił świeżej pełnej regresji, a conftest-leak (C19) zostawia **62 flagi**, dla których testy biegną z wartością PROD (np. `R6_SOFT_PEN_CAP` cap-ON w każdym teście, autor sądzi że OFF) → regresja efektu flagi niewidoczna mimo zieleni.
- **Env per-proces kilku ŻYWYCH serwisów nigdy nie zmierzony** (`dispatch-czasowka`, reassign-timery, carried-guard) — stan flag ścieżek liczących decyzje = deklaracja; „is-active" ~90 timerów niesprawdzone.
- **B22 demaskuje stary monitor route-order**: porównywał konsola↔apka, NIGDY ↔kanon silnika (wspólny dryf = fałszywy parytet 0 mismatch; `trust_canon_ok=True` hardkod pomijał invalidated). Golden L6.A go zastąpił — lekcja: „zielony monitor" ≠ dowód, dopóki sam nie przeszedł oracle.
- **37× `except Exception` w `plan_recheck` policzone, NIE zinspektowane** (może połykać błąd kanonu = cichy drift kolejności); ~110/119 exceptów `dispatch_pipeline` poza hot-path nie 1:1.
- `best_effort_fastest` pos_source martwe 81/81 (blind-check fikcyjnej pozycji strukturalnie martwy — flip fastest-pickup byłby ślepy); `sequential_replay` z latentną INWERSJĄ werdyktu (higher-better→NO-GO), 0 testów.

### 0.2 Znalezione TERAZ (świeże, zweryfikowane pomiarem/grepem 01.07)
- **(a) REJESTRY FINDINGÓW NIESCALONE** — audyt 27.06 („deep audit", 86 agentów) zostawił **81 findingów** (2×P1↓, 31×P2, 48×P3) w `ZIOMEK_DEEP_AUDIT_FINDINGS.json`; Faza 1 z 30.06 skonsumowała TYLKO CZĘŚĆ. Twardy przykład: potwierdzony P2 **`osrm-fallback-double-traffic`** (awaria OSRM w peaku → fallback-prędkości „z korków" mnożone DRUGI raz przez traffic-multiplier → czasy ×1,5 → sztuczne breache R6 dokładnie gdy system już kuleje) — **grep w CAŁYM korpusie 30.06 = 0 trafień**, nie ma go w rootach ani w roadmapie L0-L8. Ironia: findingi audytów same łamią K1 (żyją w ≥3 rejestrach bez statusu).
- **(b) KLASY BEZ METODY RUNTIME** — wg własnej macierzy 1.0 (FAZA1_06 §2) klasy **A1/A2 (kopie), B, C, F, H, K, L (TZ), N (progi), O (wyścigi)** zweryfikowano TYLKO czytaniem/grepem. To dokładnie ta ślepota, przez którą 86-agentowy read-only przeoczył oba P0. Wyścigi klasy O nigdy nie zostały ZREPRODUKOWANE; krawędzie TZ nigdy nie zostały PRZEJECHANE.
- **(c) POMIARY JEDNODNIOWE** — kluczowe liczby oracle (sentinel „8 ofiar", V328 2046+14456, pile-on) = próbka z 30.06; zero okna 14-dniowego / sezonowości peak-weekend.
- **(d) TOP-10 POWIERZCHNI POZA SWEEPEM** (agent coverage-diff; INWENTARZ-only lub całkiem BRAK):
  1. **`gps_server.py` / `dispatch-gps`** — PRODUCENT prawdy o pozycji (górny bieg całej rodziny K5 sentineli!) — audytowano konsumentów, nie ingest.
  2. **Loader grafiku Sheets→`shift_start/shift_end`** — producent kotwicy pre_shift (korzeń L2/L4!), TTL/fail-open/staleness niesweepowane.
  3. **`coordinator_time_recheck.py`** — świeży (30.06) ZAPIS do committed `czas_kuriera` w obie strony z ominięciem anti-wobble — rodzina HARD R27 bez strażnika.
  4. **`gastro_edit.py`** — realny zapis decyzji do panelu gastro (źródła prawdy).
  5. **~90 narzędzi + ~15 ŻYWYCH timerów-instrumentów BEZ oracle** (oracle dostało 49) — w tym cała warstwa `observability/` (watchdog, liveness_probe, delivered_integrity, koord_cascade): **„warstwa która pilnuje czy system żyje — sama niezweryfikowana"**.
  6. **`manual_overrides.py`** — HARD wykluczenia z puli (klasa „BRAK KANDYDATÓW").
  7. **courier_api ops-internals** (`schedule_escalation_cron`, `delivery_town`, `cost_*`, `payment_override`, `gate_audit_poller`…) — genuinie BRAK, a żyją w LIVE procesie autorytetu apki.
  8. **`auto_assign_executor.py`** — jedyny moduł fizycznie przydzielający autonomicznie; „inert" tylko dzięki `ENABLE_AUTO_ASSIGN=OFF`; 1. flip odsłania niesweepowany kod.
  9. **Konsola API-endpointy** (`app/api/{coordinator,ziomek,dispatch,fleet,parcel_ops}.py`) — warstwa przyjmująca KLIKNIĘCIA koordynatora (audytowano render, nie akcje).
  10. **`auto_koord.py`** — decyduje co idzie do człowieka vs auto-hold.
- **(e) ŻYWY DOWÓD klasy „silent cron": `dispatch-cod-weekly.service` = FAILED od pon. 29.06 06:00 UTC (exit 1), 2 dni bez reakcji** (zweryfikowane `systemctl` 01.07 wieczór). Dokładnie klasa F2/RC3 z audytu 05.07 („overrides-reset martwy 4 dni") — powtórka dzieje się TERAZ.
- **(f) WYDAJNOŚĆ ZREGRESOWAŁA I NIKT NIE PATRZY** — zmierzone dziś na żywym ledgerze (n=1210): **p50 decyzji 838 ms · p95 1880 ms · max 6005 ms** (pole `latency_ms` shadow). W kwietniu (ta sama rodzina metryki): p50 ~375 ms, cel po Hetzner CPX32 = 150-200 ms. **~2× regres w 2 miesiące, zero SLO/alertu na trend.**
- **(g) ROZROST OPERACYJNY BEZ NADZORU** — dziś **115 jednostek `dispatch-*.service` + 56 timerów** (audyt 05.07 zastał 16 serwisów + 12 timerów). Cykl życia jednostek (retire) nie istnieje; alerty są PROCESOWE (OnFailure szeroko), ale **nie DANOWE** — stąd sentinel z 2046+14456 zdarzeniami bez żadnego alertu.
- **(h) STAN ROŚNIE BEZ POLITYKI** — `dispatch_state/` 1,2 GB (observability 322 MB, obj_replay_capture 91 MB, learning_log ~183 MB) + `logs/` 0,7 GB; dysk 53%. Root `unbounded-append-only-caches` = odłożony P3 bez weryfikacji.
- **(i) ZERO trzech całych osi w historii projektu:** benchmarku jakości (hindsight-optimum — grep 0), testów obciążeniowych (0), mutation/property testów strażników (0) — a `ZIOMEK_INVARIANTS.md` sam przyznaje: alokacja/feasibility = sloty 🔴, 21 slotów bez strażnika.
- **(j) BEZPIECZEŃSTWO NIGDY NIE AUDYTOWANE** — ani 05.07, ani żaden czerwcowy (agent prior-audits: „biały obszar").

### 0.3 Kontekst z wcześniejszych audytów (czego NIE powtarzać — pełny digest u agenta prior-audits)
**Zamknięte (nie badać ponownie):** load>clock; poślizg ODBIORU nie jazda (drive-speed correction wycofane); wagi scoringu OK (zło wymuszone podażą); akceptacja koordynatora = zła bramka autonomii; carried-first naprawione u źródła + strażnik; 11 kłamstw at-jobów naprawione 29.06; feas-carry-readmit rolled back; EARLYBIRD zbędne; checkpoint-TZ CLEAN.
**Otwarte z właścicielem (należą do Fazy 3 / bramek — 2.0 ich NIE dubluje):** L2.1 sentinel (flip za ACK), bundle-calib O2 02.07, objm at-200 03.07, load-aware ETA 04.07, pre-shift L0-L6, auto-assign kroki 2-5, LGBM eval, geofence Phase 2, route-order golden (zrobione 01.07).
**Audyt 05.07 (architektura):** oś wydajność/skala/odporność (RC1 filesystem-as-IPC, RC4 JSONL unbounded, single-server SPOF, brak SLO, roadmapa M1-M5 Postgres/Redis/liveness) — **NIETKNIĘTA od 2 miesięcy**; RC1 to ten sam korzeń, który 30.06 na nowo nazwano K1 (dowód, że strukturalny).

---

## 1. PION 0 — DOMKNIĘCIE AUDYTU 1.0 (read-only, agenci, bez ACK poza budżetem)

| # | Lane | Co konkretnie | Produkt |
|---|---|---|---|
| 0.A | **Jeden rejestr findingów** | Merge 05.07 (20 ryzyk/RC1-7) + 27.06 (81) + 30.06 (53 rooty + 81 konfliktów) + pion-audyty (preshift/alokacja/no-gps) → JEDEN rejestr z statusem `open/fixed/deferred/refuted` + właścicielem (fala L / bramka / nikt). Reguła trwała: każdy przyszły audyt DOPISUJE, nie tworzy. | `ZIOMEK_FINDINGS_LEDGER.md` + metryka „findingi-bez-właściciela"=0. Natychmiast wyłapie sieroty typu `osrm-fallback-double-traffic`. |
| 0.B | **Dokończenie pasa adwersaryjnego** | 14 odłożonych rootów P2/P3 + sieroty z 0.A (2 refuterzy jak w 1.0). | werdykty C/P/R |
| 0.C | **Runtime-oracle dla klas bez metody runtime** | O: REPRODUKTOR wyścigów (pending_proposals 3-writer, orders_state multi-proces, load_plan side-effect — równoległe writery na kopii stanu); L: HARNESS krawędzi czasu (północ = root `shift-start-midnight-anchor`, DST październik za ~4 mies., rollover panelu 22:00); N: sonda WARTOŚCI EFEKTYWNYCH progów per proces; A1: parytety ODPALONE (lex_qual oba stany flag, `_count_sla` A vs B); H: GC-probe (zombie po tranzycjach). | dowody zamiast lektury; wsad do L7.5/L6 |
| 0.D | **Oracle 2. fali przyrządów** | ~90 tools + ~15 żywych timerów bez werdyktu — priorytet: karmiące decyzje/Telegram (`ziomek_pred_calibration`, `decision_outcomes`, `shadow_outcome_enricher`, `faza7_daily_kpi`, `daily_rule_report`) oraz CAŁA `observability/` (watchdog/liveness/delivered_integrity/koord_cascade — meta-strażnicy). Metoda = recipe C9 z 1.0. | rozszerzenie `FAZA1_03` do pełnego rejestru |
| 0.E | **Sweep powierzchni ZAPISU i PRODUCENTÓW danych** ⭐ | TOP-10 z §0.2d: `gps_server` (ingest pozycji — górny bieg K5), loader grafiku (producent `shift_start`), `gastro_edit` + `coordinator_time_recheck` (ręce piszące do źródła prawdy), `manual_overrides`, `auto_koord`, `auto_assign_executor` (PRZED 1. włączeniem!), konsola API akcji, courier_api ops-internals, `panel_html_parser` parytet z `panel_client`. Pełny sweep 15 klas + oracle na każdej. | nowe rooty/P0-kandydaci; bezpieczeństwo flipu AUTON |
| 0.F | **Okno 14 dni dla sond jednodniowych** | Re-run sond oracle 1.0 na 14-dniowych ledgerach (sentinel-ofiary/d, V328, pile-on, spread po de-pile) → rozkład, nie punkt. | kalibracja severity (peak vs spokój) |
| 0.G | **Higiena narzędzi** | `py_compile`+import 145 tools; potwierdzenie DEAD?-hipotez (3 moduły); `scoring.py` 19 kar → mapa reguła→warstwa. | wsad do L8 (sprzątanie) |
| 0.H | **Pomiar SIŁY strażników** | Mutation-testing (mutmut) na hot-paths `feasibility_v2`/`dispatch_pipeline`/`plan_recheck` + property-based (Hypothesis) dla inwariantów (`pickup≥shift_start`, „nigdy (0,0)", HARD-przed-SOFT). Mierzy, czy 19 zielonych testów-strażników COKOLWIEK łapie. | mutation-score jako metryka; wsad do F6/L0 |

## 2. PION 1 — JAKOŚĆ DECYZJI („NAJLEPSZY dispatcher")

| # | Lane | Co | Uwagi |
|---|---|---|---|
| 1.A | **Hindsight-benchmark** ⭐ | Replay zamkniętego dnia z PEŁNĄ wiedzą → plan referencyjny (OR-Tools/LNS na całym dniu, choćby dolne ograniczenie) → **GAP Ziomka i koordynatora do optimum** w SLA/km/R6/idle. Pierwszy w historii pomiar „ile zostawiamy na stole". | Nowy północny-gwiazdozbiór zamiast „zgody koordynatora" (obalonej 30.06). Stoi na geofence GT + L1.1 (już LIVE). |
| 1.B | **Worst-N miner** | Codzienny automat: N najgorszych FIZYCZNYCH wyników dnia (breach/waste/idle z gps_truth) + auto-atrybucja warstwy (feasibility? scoring? selekcja? plan? egzekucja kuriera?). | Zastępuje ręczne Q&A; karmi Fazę 3 świeżymi case'ami. |
| 1.C | **Objective-alignment** | Czy `lex_qual`/score PRZEWIDUJE fizyczny wynik? (korelacja rank→outcome na joinach). NIE dubluje Track 2 (wagi) ani kalibracji (czas) — bada CAŁĄ funkcję celu: waste-km, fairness kurierów, utrzymanie restauracji. | Jeśli score nie przewiduje wyniku — strojenie wag jest bez sensu niezależnie od ich wartości. |
| 1.D | **Kanon→kod conformance** | Każda reguła z `ZIOMEK_REGULY_KANON.md` → wykonywalny test na korpusie golden. Wykryta w 1.0 sprzeczność WEWNĄTRZ kanonu (K-M: §4:86 „No-GPS zawsze równo" vs §7:151 „pre-shift −20 żywe") → werdykt Adriana + poprawka kanonu. | Kanon przestaje być prozą, staje się suitą. |

## 3. PION 2 — ODPORNOŚĆ I LATA PRACY

| # | Lane | Co | Dowód potrzeby |
|---|---|---|---|
| 2.A | **Macierz zależność×awaria + game-day** ⭐ | Dla KAŻDEJ zależności (panel gastro down/HTML-drift/wolny, OSRM down, Sheets down/literówka/quota, Telegram, GPS backend, dysk pełny, reboot-ordering, NTP-skok): oczekiwane zachowanie (fail-loud? pesymistycznie-bezpieczne? degradacja?) → TEST wstrzyknięciem off-peak za ACK (shadow/replika). | `osrm-fallback-double-traffic` (system pod awarią OSRM AKTYWNIE pogarsza decyzje); grafik=SPOF fail-open 24/7 w nocy; 119 bare-except. |
| 2.B | **Alerty DANOWE (nie tylko procesowe)** ⭐ | Inwentarz trybów awarii → który ma alert → test wstrzyknięciem. OnFailure pokrywa padnięcie PROCESU; nie pokrywa: kłamiącego pomiaru, zamrożonego pliku, sentinela w danych, pustej puli. | Sentinel: 2046+14456 zdarzeń, 0 alertów. `cod-weekly` FAILED 2 dni. `b_route` czytał zamrożony plik tygodniami. |
| 2.C | **Harness krawędzi CZASU** | Symulowany przejazd 23:00→01:00 (północ; root midnight-anchor), zmiana DST (październik!), rollover panelu 22:00 UTC — replay syntetyczny przez krawędź, inwarianty włączone. | klasa L bez metody runtime w 1.0; nawracające bugi TZ. |
| 2.D | **Zasoby na LATA** | Polityka retencji/GC dla append-only (1,2 GB + 0,7 GB dziś), logrotate-coverage (readerzy rotation-aware — wzorzec `min_delivered` już się przejechał), trend tygodniowy w dashboardzie, pamięć długo-żyjących procesów. | dysk 53%; P3 root niezweryfikowany. |
| 2.E | **DR-drill** | Odtworzenie `dispatch_state` z restic na czystym środowisku + runbook „serwer padł" + pomiar RTO. Single-server SPOF (05.07) świadomie zostaje — ale odtwarzalność musi być UDOWODNIONA, nie zakładana. | migracje M1-M5 niewykonane; restic loguje, nikt nie testował restore. |
| 2.F | **Security lane (PIERWSZY w historii)** | Sekrety (`.secrets`, env drop-iny, tokeny w logach?), auth konsoli (`gps.nadajesz.pl/admin`, `/api/coordinator/auto-assign` — przycisk autonomii!), PIN/JWT apki kuriera, sesje gastro, ekspozycja portów. Inwentarz + top ryzyka (nie pentest). | „biały obszar" — zero pokrycia od początku projektu; przycisk auto-assign w konsoli = nowa powierzchnia. |
| 2.G | **Cykl życia jednostek systemd** | 115 units / 56 timerów: które żywe/martwe/do-retire; WatchdogSec/MemoryMax/CPUQuota (R-12 z 05.07); standard onfailure+cron_health dla WSZYSTKICH. | rozrost 5× w 2 mies. bez governance. |

## 4. PION 3 — SKALA I PRZYSZŁOŚĆ

| # | Lane | Co | Dowód potrzeby |
|---|---|---|---|
| 3.A | **Budżet wydajności + SLO** ⭐ | Profil per warstwa (OSRM/TSP/scoring/IO/panel-login) → budżet per decyzja i per tick → metryka p95 w dashboardzie + alert regresji. | p50 838 ms dziś vs 375 ms w kwietniu (cel 150-200) — 2× regres, nikt nie zauważył. |
| 3.B | **Load-replay ×2/×5** | Peak-taśma z podwojoną gęstością zleceń/floty na replice — gdzie pęka pierwsze: OR-Tools 200 ms cap? OSRM? fcntl-contention na JSON? tick 1-min? Weryfikuje tezę 05.07 „przy 10× wykłada się PERSISTENCE, nie compute" → dostarcza LICZBĘ, kiedy M1 (Postgres) staje się obowiązkowa (pre-Restimo Q3). | zero testów obciążeniowych w historii. |
| 3.C | **Multi-city inventory** | Grep-sweep założeń jedno-miastowych: `BIALYSTOK_BBOX`, `HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37`, `BIALYSTOK_CENTER`, districts_data, tabele traffic, godziny peak, `EXCLUDED_CIDS` → mapa „co sparametryzować pod 2. miasto" + koszt. | strategia Adriana: Warszawa/Restimo/Wolt Drive; dziś walidator-prawdy JEST bboxem Białegostoku. |
| 3.D | **Autonomia game-day** | Scenariusz „auto-assign się pomylił": jak szybko WIDZIMY (monitoring), jak szybko COFAMY (killswitch/stop-loss), E2E gastro_assign na sucho. Uzupełnia (nie dubluje) kroki 2-5 planu AUTON-02. | 1. wciśnięcie „Włącz" = nieprzetestowane E2E (jawne ostrzeżenie w memory). |

---

## 5. TRWAŁE BRAMKI (rozszerzenie dashboardu entropii o metryki 2.0)
`perf-p95-decyzji` · `alert-coverage-%` (tryby awarii z alertem/wszystkie) · `DR-drill-freshness` (dni od udanego restore) · `unbounded-files-count` · `single-city-hardcodes` · `quality-gap` (hindsight, %) · `mutation-score strażników` · `findings-bez-właściciela` (=0) · `instrumenty-bez-oracle` (dziś ~90+15). Zasada bez zmian: żaden sprint nie pogarsza żadnej metryki.

## 6. CZEGO 2.0 NIE ROBI (anty-dublowanie)
Nie powtarza zamkniętych (§0.3); nie wchodzi w naprawy Fazy 3 (L2.1-L8 mają właścicieli); nie forsuje migracji M1-M5 (dostarcza pomiar KIEDY); STOP na dyspozytorni bez zmian (Mailek/Papu poza; most papu/parcel = tylko styk); geocode 363 pary + pin lokalizacji = istniejący osobny temat.

## 7. KOLEJNOŚĆ STARTU (rekomendacja TOP-5, reszta za nimi)
1. **0.A rejestr findingów** — 1 sesja, zero ryzyka, natychmiast wyłapuje sieroty (już 1 znaleziona). NAJPIERW — porządkuje wszystko dalej.
2. **0.E sweep zapisu+producentów** (gps_server / grafik / gastro_edit / time_recheck / auto_assign_executor) — najgroźniejsza ciemna strefa; obowiązkowo PRZED 1. włączeniem autonomii.
3. **2.B alerty danowe** + od ręki: diagnoza `cod-weekly` FAILED (żywy przykład klasy). Mały koszt, wielki zwrot „na lata".
4. **3.A budżet wydajności** — regres już widoczny (838 ms), pomiar+SLO+alert zanim urośnie do awarii peaku.
5. **1.A hindsight-benchmark** — pierwszy pomiar „najlepszości"; ustawia północ dla Fazy 3 i autonomii.
Eksperymenty (2.A game-day, 2.C krawędzie czasu, 3.B load-replay, 2.E DR-drill) — po ACK, off-peak, na replice/shadow.
Kalendarz: nie kolidować z bramkami 02.07 (O2) / 03.07 (objm) / 04.07 (load-aware ETA) / L2.1 flip.

**Pre-bramkowe checki z tej analizy (do wzięcia przy bramkach, nie osobny sprint):**
- **02.07 O2:** (a) wyrównać kotwicę bundle_calib `min(ck,pu)` vs engine pu-only (C01) ZANIM porównania; (b) bug4_reseq gate „suspect≤10%" pada z powodu ŹLE ZDEFINIOWANEGO inwariantu (delta≥0 na złym obiektywie, C02) — WAIT z fałszywego powodu, nie brać werdyktu za dobrą monetę; (c) sprawdzić świeże `eta_source`/SLA-detail w ledgerze (weryfikacja L1.1 rano — handoff pkt 1).
- **04.07 load-aware:** monitor poślizgu tika od 30.06 22:30 (zweryfikowane 01.07) → będzie ~4-5 punktów dziennych. CIENKO — jeśli wynik graniczny, przedłużyć pomiar zamiast flipować; plus C13: bundle pooled bez de-konfundacji reseq (clean-bundle under-buffered ~7-8 min).
- **Każdy flip na liczbie przyrządu:** najpierw status w `FAZA1_03` (VOID/UNTESTED = nie jest dowodem) — zasada już w INVARIANTS ⑤, tu przypomnienie.

## 8. SKALA / CAVEATY
- Pion 0: ~40-60 agentów read-only (jak Faza 1). Piony 1-3: po ~15-30 na lane'y analityczne; eksperymenty wyceniane osobno przy ACK.
- Liczby w §0.2 zmierzone 01.07 wieczór (dryfują — każdy lane robi świeży ETAP 0).
- Surowe raporty 3 agentów (sweep backing 71/71 · diff pokrycia · digest wcześniejszych audytów): **`AUDYT_2.0_ANEKS_raporty_agentow.md`** (obok tego pliku).
- To PROJEKT: zero wykonania bez „go" Adriana; każdy lane dotykający żywego systemu = protokół ETAP 0→7 + ACK.
