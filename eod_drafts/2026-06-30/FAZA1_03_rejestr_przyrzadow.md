# FAZA 1 — DELIVERABLE #3: REJESTR PRZYRZĄDÓW (validated / void / untested) — CZEMU UFAĆ PRZY FLIPACH

**Audyt spójności Ziomka, sesja tmux 2, 2026-06-30. TRYB READ-ONLY. Lane RUNTIME-ORACLE (C9/C11) — KAŻDY przyrząd odpalony/odczytany na realnej próbie, prawda policzona DRUGĄ metodą.**

> **Po co ten dokument:** to lista, której nie miał poprzedni 86-agentowy audyt (był read-only → przeoczył oba P0 alokacji właśnie tu). Przed KAŻDYM flipem, którego walidacja opiera się na przyrządzie — sprawdź tu jego status. **VOID/UNTESTED = werdykt przyrządu NIE jest dowodem; flip na jego liczbie może cicho przepchnąć złą decyzję albo zabić realne ulepszenie.**

## PODSUMOWANIE: 24 VALIDATED · 19 VOID · 6 UNTESTED (z 49 werdyktów)

⚠ **CAVEAT FUNDAMENTU:** `delivered_at`/`picked_up_at` = prawda-PRZYCISKOWA, nie fizyczna (0/377 dostaw ma auto_geofence GT; odbiór panel ~192s przed GPS). Każdy `proxy-certified` = button-truth ±~3min. Jedyny GROUND-TRUTH fizyczny producent = `gps_delivery_validation` (VALIDATED). OSRM route==table na osi PEAK = certyfikowany czysty (n=2644).

## 🔴 VOID — instrument KŁAMIE lub mierzy proxy zamiast zmiennej decyzyjnej (NAPRAW POMIAR przed użyciem werdyktu) (19)

_Werdykt tych przyrządów NIE jest dowodem. Część z nich była deklarowana 'naprawione 29.06' — oracle pokazał, że naprawa pomiaru jest niepełna LUB sam fix-claim jest void. NIE flipuj/nie-flipuj na ich liczbie._

### bug4_reseq — INWARIANT delta>=0 + health-gate suspect<=10% + materialnosc drive (verdict-tool)
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** brute-force OSRM: min-DRIVE permutacja == FROZEN (cid=515: frozen 11.3 = optimum, fresh 16.2 = +4.9 drive SWIADOMIE); klucz solve plan_recheck.py:1670 = (sla_violations, total_duration_min, sequence) NIE drive; 48/123 suspect ma deliv_seq_differs=False (carried-first interleave); replikacja verdict-math: suspect 11.5%>10% → GO=False + 'WCIAZ SKAZONY' MIMO material 22.5%≥20 i median 5.6≥1.5
- **inwarianty:** delta>=0 NARUSZONY 123/1074=11.5% (mis-spec: objective=total_duration nie OSRM drive); flicker per-worek deliv_seq 66/267=25% / invariant_violation 48/267=18%; fresh_drive liczone na sort-ts proxy (events.sort) nie na plan.sequence; stale verdict.txt klamie 'logger nic nie zapisal'
- **co flipuje / decyzja jaką napędza:** ten sam GO/WAIT 02.07 — obecnie WAIT z FALSZYWEGO powodu (zly inwariant), NIE z braku materialnosci (materialnosc spelniona)
- _(agent C: C02-bug4-reseq)_

### feas_carry_readmit_replay.py (bramka ENABLE_FEAS_CARRY_READMIT — 'dowód pozytywnego wpływu')
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Niezależny recompute (wr=915/54,4%, live=914/54,3% — ZGODNE z narzędziem, 2× deterministyczne) + JOIN feas_carry_blind_shadow.jsonl × decision_outcomes.jsonl po order_id (886 would_redirect z r6): realny r6_breach 11,7% / chosen-wykonał realny breach 14,8% vs predykcja 99,7%; kontrfakt redirect_cid obserwowalny tylko 167/902=18,5%
- **inwarianty:** delta>=0 FAIL (sum sentinelowy mean302≫med9,7; 5,9% regret>60); Pareto redirect_objm<chosen FAIL 2/914 (lex_qual wielokluczowy vs objm jednokluczowy); trigger fantomowy 99,7%pred vs 14,8%real; kontrfakt 81,5% nieobserwowalny
- **co flipuje / decyzja jaką napędza:** ENABLE_FEAS_CARRY_READMIT (napędził flip ON 27.06 ~22:18, od tego czasu rollback); replay DALEJ drukuje 'materialność ✅ / benefit ✅ med9,7 sum276444' → ryzyko re-flipu VOID-akcji
- _(agent C: C03-feas-carry)_

### feas_carry_blind_review.py (przegląd shadow B2/P-6)
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Grep dowód: 0 referencji decision_outcomes/delivered_at/r6_actual; liczy redirect_pct/regret_mean wyłącznie z predykcyjnych pól shadow.jsonl (regret_min=chosen_objm−rej_objm); regret_mean zawyżony sentinelami
- **inwarianty:** regret_mean sentinelowo skażony; trigger fantomowy (ta sama populacja chosen_objm>0)
- **co flipuje / decyzja jaką napędza:** rekomendacja build-czy-nie fixu B2 (próg :87 redirect_pct>=30 & regret_mean>=5) → 'REKOMENDUJE budowę' na predykcji
- _(agent C: C03-feas-carry)_

### feas_carry_readmit_postflip.py (monitor post-flip)
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Code-trace + stan flagi: tripwire bad_regret=regret<=0 NIGDY nie odpali (regret=chosen−rej>0 z definicji would_redirect = martwy strażnik); werdykt CLEAN pusty gdy flaga OFF (0 redirectów w journalu → trywialnie czysto, nieodróżnialne od 'wyłączony'); cap-check na journal newbag działa ale loguje tylko gdy flaga ON
- **inwarianty:** bad_regret tripwire strukturalnie martwy; CLEAN nieodróżnialny od disabled
- **co flipuje / decyzja jaką napędza:** hot rollback ENABLE_FEAS_CARRY_READMIT=false na ALARM (alarm strukturalnie nie odpali z bad_regret)
- _(agent C: C03-feas-carry)_

### _SYNTH_POS/_usable_pos/_REAL_POS — klasyfikator pozycji (3 kopie w 1 pliku)
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** crosstab 3 taksonomii × wszystkie żywe tokeny pos_source (42750 wystąpień a+b) + grep producentów courier_resolver.py:738-1590; 7/10 tokenów daje rozjazd werdyktu
- **inwarianty:** _SYNTH_POS={none,pin,pre_shift,''} trafia 0 żywych fikcji poza pre_shift (no_gps/None→a_real=True błędnie); _REAL_POS exact-match FAIL na wszystkich sufiksowanych last_* (14733 last_assigned_pickup→trusted=False); _usable_pos jedyny poprawny
- **co flipuje / decyzja jaką napędza:** a_real/b_real (l.359-360, _why reason text + notify-display) + _pos_trusted notify trusted-only filter (latentny — TG off); decyzja quality RATOWANA przez poprawny _usable_pos
- _(agent C: C04-reassign-fwd-quality)_

### would_reassign vs quality_reassign — wiring dwóch bramek/dwóch powierzchni
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** crosstab would×quality na 5813 rek (would=4095/70%, quality=447/7.7%, both=368) + grep konsumentów 3 repo: notify l.457 bramkuje would; konsola feed.py:258 + global_select:126 bramkują quality
- **inwarianty:** at-193 waliduje quality (powierzchnia konsoli) NIE would (Telegram); would-arm mierzy osobno reassignment_shadow_eval SPENT 27.06 leans NO-GO 938/1014 never; walidacja jednej≠drugiej
- **co flipuje / decyzja jaką napędza:** Telegram notify=would (margines Δ≥15, 3727 duchów BEZ rozumowania breachu) vs konsola/de-pile=quality (mierzony przez at-193)
- _(agent C: C04-reassign-fwd-quality)_

### objm_lexr6_peak_verdict.py _g2c_note headline (at-200 03.07 18:10)
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** grep :71-72 g2c=100*len(reorder_oids&shadow_oids)/n_orders = ALL-TICK; durable txt 29.06 sam sobie przeczy (gate 'per-decyzja 3.7%' vs headline 'POSREDNIO 25.2%'); 26.06=54.7%, 28.06=62.1% wszystko all-tick
- **inwarianty:** headline g2c (all-tick) != gate g2c (per-decyzja) w TYM SAMYM pliku — twin nietkniety fixem (mtime 26.06 < fix 29.06)
- **co flipuje / decyzja jaką napędza:** headline decyzji Fazy-4 ON-na-stale-vs-rollback ZYWEJ flagi ENABLE_OBJM_LEXR6_SELECT; falszywy 'NADAL WYSOKO/over-reorder' moze pchnac ku nieuzasadnionemu rollbackowi dzialajacego selektora
- _(agent C: C05-objm-lexr6-canary)_

### objm_lexr6_canary_monitor.py G2b-auto-route
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** recompute ACK+ALERT=84.88% (oracle-match) ALE baseline 89.13% z 2026-06-25 single-day n=138 (mtime 26.06) = 5 dni stale; auto_route ustala auto_proximity_classifier (pool/margin/tier) NIE selektor objm; STOP 26.06(98.9%)/29.06(100%) = dryf systemowy (AUTON-02/equal-treatment) mis-atrybuowany do flipu objm
- **inwarianty:** obliczenie ACK+ALERT poprawne; sygnal-jako-jakosc-selektora niewazny (zla os + confound)
- **co flipuje / decyzja jaką napędza:** STOP/GO G2b -> falszywa atrybucja w werdykcie peak (czesc znana MEMORY 'G2b osobny NIE bug objm')
- _(agent C: C05-objm-lexr6-canary)_

### b_route_shadow_review (werdykt, timer dispatch-b-route-shadow-review odpalil 30.06 07:00 UTC)
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** odczyt logu werdyktu (MIXED, real_joined=0) + dowod overlapu order_ids: corpus∩stale-dispatch_state-sla=0, corpus∩LIVE-scripts/logs-sla=376/394 (differs_b 289/300) → repoint sciezki na scripts/logs → real_joined≈289 nie 0
- **inwarianty:** real_joined=0 (powinno ~289); _verdict() :138 nieosiagalny GO-KANDYDAT bez real_joined>0; review nie honoruje served_synthetic markera (12/192 differs_b = syntetyczny baseline)
- **co flipuje / decyzja jaką napędza:** werdykt GO-KANDYDAT/NO-GO/MIXED B-lite — decyzja Adriana 30.06 'budowac B-lite czy zamknac temat'; VOID bo ground-truth arm martwy (czyta zamrozona kopie sla_log z 20.06)
- _(agent C: C06-b-route-shadow)_

### eta_source (dispatch_pipeline:4051/5289/5864/5879)
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** Codebase grep = 6 compute-sites with 6 distinct values (haversine/plan/r07_chain_eta/soon_free/no_gps_fallback/pre_shift) vs ledger grep = 0/917 occurrences (also eta_src=0, drive_source=0). Sibling-key contrast in the SAME dict literal (dispatch_pipeline:5283-5294): pos_source=872, pos_from_store=872, drive_min=872 serialize, but eta_source/eta_pickup_utc/eta_drive_utc=0 -> eta_source specifically falls through (not in _AUTO_PROP_PREFIXES; c.metrics['eta_source'] @5864/5879 stripped by prefix filter).
- **inwarianty:** 0 occurrences across full 4-day window; same compute-but-vanish class as would_hard_cap PRE-d23d8a1, NOT fixed
- **co flipuje / decyzja jaką napędza:** ETA-provenance for pickup-slip-monitor / eta-calibration (real-plan vs haversine-fallback vs no_gps-fiction vs pre_shift) - currently BLIND in ledger
- _(agent C: C08-would-hard-cap)_

### global_allocate jakosc geometryczna / certyfikacja 'feasibility-validated worek 3-4 OK' (reassign_global_select_review.py:100-103)
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** ground-truth deliv_spread_km (haversine coordow dostaw przez feasibility_v2): 710/2019=35,2% alokacji multi-drop ma spread>8km (R1!), 267/710 takze r6>40 (ponad twardy cap 35/40), 175 spread>12km; smoking gun oid=484250 g_maxpile 4->2 (count-sukces) ale onto cid515 spread=18km r6=73min reason=rozjazd_kierunkow; seed 152/426 POTWIERDZONY szerzej
- **inwarianty:** ZLAMANY inwariant jakosci: de-pile redukuje LICZBE ale tworzy worki R1-lamiace; ZERO fikcyjnych pickupow OK; spread = geometria niezalezna od button-truth
- **co flipuje / decyzja jaką napędza:** MUSI ZABLOKOWAC kazdy flip PENDING_RESWEEP_LIVE / live de-pile dopoki czlon geometrii nie wejdzie do lex_qual (P0-A) — inaczej przepchnie 279 propozycji spread>8 (do r6=73, spread=24) do Telegrama
- _(agent C: C10-global-allocate)_

### min_delivered_at_verdict.py (at-166, odpalony 27.06 07:00)
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Re-run logiki werdyktu na zywym pliku (802 non-null, 33.2% changed) + per-day bucket .1 archiwum (357 non-null w 25-26.06, NIEWIDOCZNE dla toola) + logrotate.d/dispatch-v2 cadence=daily. Werdykt raportowal 'non-null:0, changed:0, malo danych' = FALSZYWY NEGATYW z rotation-blindness (czyta tylko zywy jsonl, nie .1/.gz; whole-file + SINCE 25.06 => widzi tylko biezacy dzien) + odpalony 07:00 po nocnej rotacji przed peakiem.
- **co flipuje / decyzja jaką napędza:** Uzasadnienie odroczenia A/B — 'malo danych/INCONCLUSIVE' falszywe; re-run rotation-aware przerzuca na MATERIAL(33%) + wlasna klauzula regresji floty (194>79.8) => leans NEITHER
- _(agent C: C14-min-delivered)_

### carried_first_guard (strażnik #1, dispatch-carried-first-guard.timer 3min LIVE)
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** A/B-replay reużytej funkcji silnika (plan_recheck._start_anchor + _apply_canon_order_invariants) pod DWIEMA konfiguracjami env na identycznym żywym stanie, tool --dry write=False (zero zapisu do dispatch_state), 2x każda: CONFIG A = env -i puste (jak biegnie usługa strażnika) -> no_position=5 risk=5/5; CONFIG B = 14 flag env z dispatch-plan-recheck (ENABLE_GPS_FREE_ANCHOR=1 itd) -> ok=2 canon_divergence=3 risk=0/5. Deterministyczne (A1==A2, B1==B2). + niezależna re-derywacja smell carried-first (własna impl bez importu toola) na rekordzie cid=123: fires at i=3, same-stop-set PASS. + join 11 no_position cids do courier_last_pos.json: wszystkie 11 maja last-known-pos store (silnik je kotwiczy, strażnik nie).
- **inwarianty:** ten-sam-zbiór+liczba-stopów PASS (7==7 na rekordzie cf); ZERO fikcyjnych pickupów PASS; struktura-kolejności PASS; determinizm PASS (2x2 runs); FIDELITY guard≡silnik = FAIL (risk 5/5 guard vs 0/5 silnik na identycznym stanie)
- **co flipuje / decyzja jaką napędza:** Strażnik-bramka dla 'czy carried-first wrócił?' (Adrian 29.06 'zamknąć carried-first żeby nie wracał 12 raz') — ma dać ALERT w minutę na nawrót. VOID = ta siatka bezpieczeństwa jest niefunkcjonalna: 90% rekordów (1058/1177) to fikcyjne risk=no_position, a 1 carried_first to false-positive vs okrojony kanon. Prawdziwy nawrót w silniku zostałby przegapiony lub utopiony w szumie. Read-only (zero wpływu na decyzje dispatchu) ale jako instrument-prawda = void.
- _(agent C: C15-carried-first-guard)_

### address_mismatch_review.py verdict .txt (at-189, 07:00 snapshot)
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** konwersja ts kazdego wiersza jsonl vs mtime werdyktu (07:00 UTC): werdykt mowi max=7598m/6 wpisow, zywy log max=14036.1m (Ogrodniczki logged 14:46 UTC, 7,7h PO werdykcie) + 8 wpisow
- **inwarianty:** max-live 14036 > werdykt 7598; 8>6 text_coords i 8>5 town; tool liczy poprawnie ale plik = zamrozony snapshot
- **co flipuje / decyzja jaką napędza:** feeds decyzje A/B/C Adriana — STALE snapshot under-reprezentuje severity (gubi najwiekszy rozjazd 14 km); brak TTL/marker stale na .txt
- _(agent C: C16-address-mismatch)_

### a2_selection_shadow.jsonl (A2 reliability soft-score selection-change trend)
- **werdykt:** VOID · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Code-diff: live _selection_bucket (dispatch_pipeline:2459-2460, _equal_bucket_on -> 0 dla no_gps/pre_shift) vs shadow _pos_bucket (a2_selection_shadow:182 -> 2 bezwarunkowo, gate :281 blokuje). Kwantyfikacja z master-ledger slice 894 PROPOSE: 17.8% (121/681) decyzji-z-alt = populacja rozjazdu (informed best + wykonalny no_gps/pre_shift alt blokowany w shadow, dozwolony live); 32.2% (219/681) live wybiera no_gps/pre_shift jako BEST. Run x2 md5 stabilny.
- **inwarianty:** bucket parytet live==shadow FAILS dla positionless (live=0/shadow=2 = stale model sprzed equal-treatment 24.06); jednostronny false-negative (blokuje WYGRANE no_gps/pre_shift); reader weekly_a2_digest BEZ timera (klasa K)
- **co flipuje / decyzja jaką napędza:** A2 reliability soft-score COEFF calibration (ENABLE_A2_RELIABILITY_SOFT_SCORE LIVE@100) — better:worse=147:119@coeff100 zaniżone dla slice equal-treatment, biased-pesymistycznie; trend-monitor 'czy A2 net-positive'
- _(agent C: C17-shadow-selection)_

### c5_shadow_log.jsonl (C5 wave_scoring adjustment shadow)
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** grep: 0 prod-callerow compute_wave_adjustment (tylko test+def+docstring) potwierdza DEAD producent (wave_scoring.py:4 Z-22). Distinct-value proof: 1388 rekordow = DOKLADNIE 4 fixture {5.0,7.5,8.0,15.5} kazda x347, context.order_id=None dla WSZYSTKICH; 06-30 = 6 identycznych burstow po 4 o czasach przebiegow pytest (08:55..13:17). Run x2 md5 stabilny.
- **inwarianty:** prod-write-count = 0 (producent DEAD); fixture-value-set zamkniety (4 wartosci); mtime 13:17 'FRESH' = artefakt pytest baseline tego audytu, NIE decyzja; konsument analyze_shadow_logs bez timera
- **co flipuje / decyzja jaką napędza:** Reaktywacja ENABLE_WAVE_SCORING / C5 czytalaby ten plik jako 'shadow evidence' -> przeczytalaby FIXTURY testow jako sygnal (landmine)
- _(agent C: C17-shadow-selection)_

### best_effort_fastest_pickup_shadow
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** AUDYT-REASON 'stale hardcoded bucket' OBALONY: (a) git a8cdb95 29.06 11:24 unify->_selection_bucket; (b) _selection_bucket:2459 equal-treatment-aware (no_gps/pre_shift->0 gdy flagi ON, wszystkie ON); (c) bucket TERTIARY bije 2/81 remisow ETA, 52/54 would_differ osia pickup-ETA. REALNY void-defekt: direct ledger parse pos_source non-null=0/81 (getattr(best,'pos_source') l.6812/6815 czyta nieistniejacy atrybut) -> blind-check 'fikcyjny ETA?' martwy
- **inwarianty:** pos_source non-null 0/81 (DEAD); bucket-bite 2/81 ETA-ties; 0/54 would_differ pick blind/none; would_differ 52/54 driven primary pickup-ETA; headline would_differ/earlier_min sound ale safety-annotation klamie
- **co flipuje / decyzja jaką napędza:** flip ENABLE_BEST_EFFORT_FASTEST_PICKUP (selekcja 'najszybszy odbior' shadow->live)
- _(agent C: C18-void-claimed)_

### conftest._isolate_flags_json / _stripped_flags_copy (ETAP4 strip, conftest.py:307/:190)
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** Niezalezny recompute strip-setu z importu common.* (ETAP4 59 + NUMERIC 25 + INFRA 3) odjety od flags.json + faithful runtime: monkeypatch common.FLAGS_PATH na stripped tmp + _flags_cache=None (DOKLADNIE co robi fixture), potem C.flag/decision_flag. RUN1==RUN2.
- **inwarianty:** strip-set z importu nie seedu; json!=fallback per-key; 0 fikcyjnych flag (kazda z flags.json); 71 survivors == A3 static 71 (cross-walidacja static<->runtime); R6_SOFT_PEN_CAP flag()=True (leak) vs PLN_QUALITY_AWARE flag()=False (isolated)
- **co flipuje / decyzja jaką napędza:** Dowolny test czytajacy 1 z 62 survivor-flag przez C.flag/decision_flag oczekujac stalej-modulu OFF biegnie prod-ON -> regresja efektu flagi niewidoczna; np. cap kary R6 (dispatch_pipeline:4230). 14 truly-decision (scoring/feasibility/selekcja/filtr floty).
- _(agent C: C19-conftest-leak)_

### status 'conftest flag-leak NAPRAWIONE 257d315' / ledger '11 klamiacych przyrzadow naprawione 29.06'
- **werdykt:** VOID · **typ prawdy:** ground-truth
- **prawda drugą metodą:** git show 257d315 (dodal TYLKO stala ENABLE_PLN_QUALITY_AWARE=False) + ETAP4 membership diff (3 seed-flagi w common.py:137-139, R6_SOFT_PEN_CAP poza krotka) + runtime demo (3 isolated, R6 leaks ON).
- **inwarianty:** 257d315 = latka-na-3-instancje; 62 silent-ON survivors pozostaja; R6_SOFT_PEN_CAP (4. flaga z tego samego seedu) wciaz przecieka
- **co flipuje / decyzja jaką napędza:** Ledger 'klasa conftest-leak zamknieta' -> klasa cicho wraca przy NASTEPNEJ fladze decyzyjnej dodanej do flags.json bez ETAP4 (historycznie: ENABLE_BEST_EFFORT_OBJM_R6_KEY).
- _(agent C: C19-conftest-leak)_

## 🟡 UNTESTED — nie dało się policzyć prawdy (ścieżka niewpięta / brak danych / brak durable logu) (6)

_Brak werdyktu = brak dowodu. Nie traktować jako 'no-benefit/DONE' ani jako 'OK'._

### reassignment_quality_replay — PRECYZJA RATUNKU (at-193, PENDING 01.07 19:00)
- **werdykt:** UNTESTED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** niezależny loader sla_log+gps_delivery_truth, ręczny join per-oid, breach=dt>35∨ok=False → 5/7=71% ZREPLIKOWANE co do sztuki; Wilson 95%CI=[36%,92%]; n=7 skupiony na 2 kurierach (400×3,492×4); 2/7 predykcji FAŁSZYWYCH (484203 pred70→real24, 484243 pred41→real24)
- **inwarianty:** determinizm 2× identyczny; #7-fix (wyklucz rescue_infeasible a_pred=None z mianownika l.109) POPRAWNY; arytmetyka honest nie kłamie; n=7 strukturalnie za małe na 'validated'→werdykt powinien=przedłuż-shadow
- **co flipuje / decyzja jaką napędza:** decyzja ghost→live ramienia ratunek 01.07 (GO jeśli precyzja+over-eager wysokie)
- _(agent C: C04-reassign-fwd-quality)_

### reassign_global_select — de-pile przerzutów (poboczne)
- **werdykt:** UNTESTED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** freshness+pola: timer aktywny 16:41 ale jsonl ostatni wpis 12:06 (event-driven, pisze tylko przy pile-onie; brak popołudniowych pile-onów→brak fresh okna)
- **inwarianty:** bierze quality_reassign=True (l.126), odpala PRAWDZIWY global_allocate/_tentative_assign → bucket dziedziczony z _selection_bucket (NIE własna fikcja, A6 potwierdza); seed VALIDATED 5/5 NIE re-derywowany
- **co flipuje / decyzja jaką napędza:** rozbijanie pile-on przerzutów (overlay konsoli, hidden_out)
- _(agent C: C04-reassign-fwd-quality)_

### _objm_lexr6_shadow (dispatch_pipeline.py:1097)
- **werdykt:** UNTESTED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** grep gate ENABLE_OBJM_LEXR6_SELECT_SHADOW :6249 = effective OFF (A3) -> NIE wykonuje sie, 0 sygnalu live; bucket JUZ scalony :1115 (framing 'pre-equal-treatment bucket' NIEAKTUALNY); frozen tylko inline _lex_qual ~:1122 (3-krotka)
- **inwarianty:** podwojnie zabezpieczone OFF: SHADOW flag off + _lex_qual rozjazd z kanonem tylko pod POST_SHIFT_OVERRUN_PENALTY (off); hygiena-shadow gate :346 lapie double-compute ale NIE cicha dywergencje lex_qual
- **co flipuje / decyzja jaką napędza:** nic dzis; latentna mina M pod POST_SHIFT∧SHADOW; = R1 frozen _lex_qual (A6 grupa-1) NIE double-count
- _(agent C: C05-objm-lexr6-canary)_

### pending_global_resweep LIVE re-proponowanie (akcja silnika)
- **werdykt:** UNTESTED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** code read pending_global_resweep.py:419-421 = live_acted=0 + warning no-op ('sciezka live niewpieta'); PENDING_RESWEEP_LIVE=false; live tylko ENABLE_GLOBAL_ALLOC_WRITE=true = display overlay (global_alloc.json FRESH 17:22) = display-live ENGINE-shadow
- **inwarianty:** n/d (akcja nie istnieje); pomiar shadow+overlay validated jako miernik
- **co flipuje / decyzja jaką napędza:** flip engine-level re-propose (PENDING_RESWEEP_LIVE) — niezaimplementowany, nie ma czego walidowac
- _(agent C: C10-global-allocate)_

### pickup_lateness_shadow (5min, forward 'odbior bedzie pozniej' badge)
- **werdykt:** UNTESTED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** proxy-join distinct flagged order_ids (dzis 134, alarmow 76) -> realny picked_up_at z eta_calibration_log: predykcja median 18.4 vs REALNE pickup-vs-committed median 7.0; 74% naprawde pozno(>2min) na matched=118
- **inwarianty:** FORWARD re-prognoza (inna winieta niz zamrozona decyzja pickup_slip); BRAK outcome-joinu w przyrzadzie (sibling feas_carry blind-shadow); kierunek 74% OK, magnituda PRZESZACOWANA ~2.6x (18.4 vs 7.0)
- **co flipuje / decyzja jaką napędza:** restaurant-facing pickup-late BADGE deploy (frontend NIE wdrozony); NIE karmi flipu load-aware buforu silnika
- _(agent C: C13-pickup-slip)_

### sequential_replay._determine_verdict
- **werdykt:** UNTESTED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** pure-function import (PYTHONHASHSEED=0, bez re-exec) + recznie policzona prawda: _gini [4,0,0,0]=0.75 [2,2,2,2]=0 [3,1]=0.25 ALL OK; pile_ratio [4,1,1]=2.0 OK; verdict gates C1-C6 (GO/NO-GO/blocked-by) ALL == hand-truth; C7 ujawnia inwersje couriers_used. grep tests/ PUSTE (0 testow); grep callers run_diff = tylko komentarze + niezwiazane _run_diff w innych testach; brak zapisanych raportow
- **inwarianty:** gini 5/5 cases == reczna prawda; verdict gates C1-C6 6/6 == reczna prawda; C7 inwersja higher-better wykryta; pure-logika sound dla lower-better celow; 0 testow + 0 zywych wolaczy = unproven E2E
- **co flipuje / decyzja jaką napędza:** fleet-level GO/NO-GO ETAP-5 (flipy bundling/objm: target sla_breaches|best_effort|gini|pile_ratio + tolerancje gini_tol/pile_tol)
- _(agent C: C18-void-claimed)_

## 🟢 VALIDATED — oracle PASS, można ufać liczbie (z zaznaczonym typem prawdy) (24)

_Przeszły oracle drugą metodą. proxy-certified = ufaj na osi względnej / z caveatem buttonu; ground-truth = twarda prawda fizyczna._

### bundle_calib (shadow collector bundle_calib_shadow.py + review gate bundle_calib_review.py) — bramka flipu ENABLE_O2_READY_ANCHOR_SWEEP review 02.07
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** brute-force permutacji (2p+c)!/2^p==n_candidates exact 2162/2162 + OSRM osrm_client.table localhost:5001 route-timing 12 workow 2x + recompute overage z carry_ready cap-sweep {35,40} band-restricted (0 mismatch cap35, 2165 cap40) + reimplementacja gate overage-only vs overage+1.5*czas_late (954 differs) + import build_report() read-only cross-check == co do cyfry
- **inwarianty:** delta>=0 (under_z o2>=calib o2: 0/2015 viol) · ten sam zbior+liczba stopow (stops-set==order_ids 0/8049 viol) · ZERO fikcyjnych pickupow (carried nigdy pickup-stop: 0 viol) · liczba przeplotow=brute ((2p+c)!/2^p==n_candidates 2162/2162) · cap flat-35 (0 mismatch cap35 / 2165 cap40) · monotonia o2[20]>=o2[32]>=o2[35] 0-viol · lambda-key o2==overage+1.5*czas_late 0-viol · gate EXACT na 92.8% (czas_late=0 => lambda-argmin==overage-argmin)
- **co flipuje / decyzja jaką napędza:** ENABLE_O2_READY_ANCHOR_SWEEP flip 02.07 (verdict GO/NO-GO/INCONCLUSIVE + kalibracja cap-Z X/Y/Z Opcji 3); biezacy verdict=GO Z=20 @8.2%>=2% POTWIERDZONY poprawny+konserwatywny
- _(agent C: C01-bundle-calib)_

### bug4_reseq — KSIEGOWANIE (fresh_deliv_order=plan.sequence + skip fikcyjnych pickupow + wiernosc drive)
- **werdykt:** VALIDATED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** OSRM silnikowy PR._osrm_drive_min_sum start-niezaleznie (wspolny 1. wezel → leg start kasuje sie w delcie): delta zreprodukowana DOKLADNIE cid=536 -1.40 / cid=515 -4.90, oba x2 runs identyczne; + recompute booleanow z surowych pol 0/1074 mismatch; + brute-force PDP permutacji
- **inwarianty:** same-set-of-stops 0/1074 viol; bag-permutacja 0/1074; pickup-set symetria frozen↔fresh 0/1074 (zero fikcyjnych pickupow); carried cwiczony 732/1074 (68%); delta reproduced ±0.00 x2
- **co flipuje / decyzja jaką napędza:** GO/WAIT sprintu naprawy ZRODLA re-sekwencji worka (feasibility↔route_simulator↔plan_recheck, checkpoint 02.07)
- _(agent C: C02-bug4-reseq)_

### reassignment_forward_shadow — ramię RATUNEK (a_late, require_absent l.255/260)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** rekonstrukcja a_late legacy=(not a_in_pool or a_bag_time>35) z serializowanych pól; split BEFORE/AFTER env-flip 29.06 11:09:28 (rescue_suppressed_working=True jako proxy momentu flipa); POST-flip 32/32 żywych ratunków ma a_pred≠None∧>35, ZERO fikcji (a_pred=None∧holder-absent=0)
- **inwarianty:** legacy a_late 53.9%→live 20.3% (n=4534); 1523 fałszywych ratunków stłumionych; 6 anomalii=okno field-przed-flagą; seed/A4 'void 59%' = STALE (memory 29.06 sprzed flipa)
- **co flipuje / decyzja jaką napędza:** ghost→live ramię ratunek (at-193 GO/NO-GO) + konsola feed.py:258 overlay + de-pile reassign_global_select:126
- _(agent C: C04-reassign-fwd-quality)_

### objm_lexr6_canary_monitor.py G2c-reorder PER-DECYZJA (fix 397a665)
- **werdykt:** VALIDATED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** wlasny parser scratchpad/oracle_objm_c05.py (NIE importuje tool): match reorder->decyzja ±5s z shadow ts; 5 dopasowan = sub-sekundowe (0.09-0.9s, realny flip proposala) vs 30/35 all-tick = sweep 6.4-664s od proposala; oracle 3/86=3.5% == monitor 3/86=3.5% CO DO LINII
- **inwarianty:** per-dec<=all-tick OK; n==n_orders (shadow 1/order); ten sam zbior decyzji oracle==monitor n=86; eb spojny num∧denom
- **co flipuje / decyzja jaką napędza:** WARN/GO G2c -> narratyw over/under-reorder; fix poprawnie odbiera all-tick (×7-11 zawyzka) sprawczosci bramkowania
- _(agent C: C05-objm-lexr6-canary)_

### objm_lexr6_canary_monitor.py G2a-KOORD (excl early_bird, TOD)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** recompute koord_sel=0.0% eb=2 raw 2.33% (oracle-match); wykluczenie early_bird sluszne (wynik niezalezny od wyboru kuriera)
- **inwarianty:** baseline koord_by_hour godz.7-19 = same 0.0% -> exp_tod~0%; gate poprawny ale niskoinformacyjny w peaku
- **co flipuje / decyzja jaką napędza:** STOP/GO G2a
- _(agent C: C05-objm-lexr6-canary)_

### b_route_shadow (kolektor → b_route_shadow.jsonl)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** recompute 736 swiezych rekordow (route_env all-14-flag=='1' 736/736, ts 29.06 06:56->30.06 16:52 caly post-parity epoch); full systemctl-show env-diff plan-recheck<->b-route (parytet kompletny, 14 flag identyczne); niezalezny OSRM /table HTTP cross-walk localhost:5001 6 workow x2 (module drive = raw_OSRM x 1.25 traffic-mult, RUN1==RUN2 exact deterministyczny); differs_b recompute z zapisanych sekwencji 719/719; delta_drive arithmetic 719/719
- **inwarianty:** same-stop-set served/b/blite 736/719/736; ZERO fikcyjnych carried-pickupow 736/736; pickup<dropoff 736/736; carried-no-pickup 736/736; route_env all-1 736/736; widmo 2092 pre-parity zarchiwizowane do .phantom, live czysty od 29.06 06:56
- **co flipuje / decyzja jaką napędza:** differs_b/delta_drive_b → b_better/b_worse → werdykt GO/NO-GO budowy B-lite (Adrian 30.06 review)
- _(agent C: C06-b-route-shadow)_

### drive_speed_overshoot_verdict (tools/drive_speed_overshoot_verdict.py)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Niezależny recompute (scratchpad/c07_oracle.py): własny split kohort [<flip]/[flip,flip_end)/[>=flip_end] + mediana bias z surowego ziomek_pred_calibration.jsonl + odczyt flags.json, BEZ współdzielenia kodu z narzędziem; replay starej logiki .bak inline; cross-check Method1(tool compute)==Method2(indep) na n+bias_med obu kohort = True; 2x determinizm w osobnych procesach (verdict N/A, identyczny dict).
- **inwarianty:** (1) flaga-OFF→N/A PRZED liczeniem kohort = data-niezależne (potwierdzone run1==run2 N/A mimo żywego przyrostu danych 902→903); (2) ON⊂[flip,flip_end]: 10 dostaw dokładnie w oknie 17:25:22-17:40:00Z (od 17:26:52 do 17:39:40, nic nie przecieka); (3) --flip-end kurczy ON 903→10 (893=98.9% wykluczonych=flag-OFF); (4) ten sam zbiór tierów {gold,std+,std}; (5) stara .bak → fałszywy CLEAN na 903 dostawach=dowód że fix realny.
- **co flipuje / decyzja jaką napędza:** Decyzja RESURRECT vs KEEP-ROLLED-BACK flagi ENABLE_DRIVE_SPEED_TIER_CORRECTION + mnożników DRIVE_SPEED_MULT_BY_TIER<1.0. Fałszywy CLEAN (stara wersja) napędziłby re-flip ON korekty wstrzykującej optymizm w nogę jazdy; poprawne N/A blokuje wskrzeszenie świadomie cofniętej mis-targeted korekty.
- _(agent C: C07-drive-speed)_

### would_hard_cap / hard_tier_bag_cap (shadow_dispatcher serialize field; feasibility_v2:463)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Independent recompute (bag_size_before+1 > hard_tier_bag_cap) from serialized inputs ONLY = 1467/1467 MATCH whole-file, 876/876 today, 0 mismatch. Decisive: per-DATE pre/post-fix boundary (27.06=0%/206 WITHOUT, 28.06=0%/267, 29.06=100%/218, 30.06=100%/181); boundary last-WITHOUT 28.06T20:22 -> first-WITH 29.06T06:44 = restart activating d23d8a1. Earlier 'interleaved' impression was a per-hour-of-day aggregation artifact across 4 days.
- **inwarianty:** whc<->hard_tier_bag_cap co-present (0 orphans both ways); cap values all in {4,5,6} (0 out-of-range); whc=True count=0/1467 (consistent with flag ON -> reject -> not serialized); reject-reason 'hard_tier_bag_cap (' =0 in ledger; determinism md5 identical 2x frozen snapshot
- **co flipuje / decyzja jaką napędza:** O2 cap-Z calibration / dispatch-bundle-calib-review 02.07 07:00 + at-168 (BUT must account for residual gap: serialized whc always False, binding events unobservable)
- _(agent C: C08-would-hard-cap)_

### post_shift_overrun_min / post_shift_overrun_penalty (shadow_dispatcher:258 prefix; dispatch_pipeline:5187-5197)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Value distribution over best+alts: 1544 nonzero / 3 zero; manual semantic check: negative=in-shift headroom, positive=overrun-past-shift-end; penalty=0.0 for all negative (correct: penalty only on positive overrun AND flag OFF), 10 nonzero penalties = post-shift deliveries. Same date-boundary as whc (first appears 29.06, same 28.06-audit batch).
- **inwarianty:** appears 1:1 with would_hard_cap (same full-eval cluster); ENABLE_POST_SHIFT_OVERRUN_PENALTY absent in flags.json -> effective OFF -> shadow-visible only
- **co flipuje / decyzja jaką napędza:** ENABLE_POST_SHIFT_OVERRUN_PENALTY flip (currently OFF, shadow)
- _(agent C: C08-would-hard-cap)_

### sla_violations (nested .best.plan / .alt.plan)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Located via deep-walk at .plan.sla_violations (NOT top-level - probe2 false-miss corrected in probe3); distribution {0:3339,1:477,2:138,3:25,4:5,5:1}, 646 nonzero across best+alts. 869 lines / 3979 occ whole-file.
- **inwarianty:** present in best.plan + every alt.plan; values bounded 0-5 (plausible bag SLA-violation counts)
- **co flipuje / decyzja jaką napędza:** SLA-anchor / O2 ready-anchor consistency (feeds bundle-calib + r6 calibration)
- _(agent C: C08-would-hard-cap)_

### gps_delivery_validation_review (#5b) — gps_delivery_truth.jsonl + verdict.txt (timer 5min --write)
- **werdykt:** VALIDATED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** Re-query raw GPS z courier_api.db:gps_history (mode=ro) + własna haversine; re-implementacja geofence/visit-cluster detect_one; FULL recompute 947 delt od zera z customer_dwell.json+orders_state.json (NIE z truth.jsonl); re-detekcja arrived_at_customer na próbie 40 zleceń; dzienne rozbicie pokrycia. 2 odpalenia IDENTYCZNE (determinizm).
- **inwarianty:** delta recompute=verdict 1:1 (median +2.12 / mean +2.26 / p90 +4.2 / |d|>3=25% / neg=5% / high +2.28); truth.jsonl 947/947 0-mismatch (wierna serializacja); arrival 39/40 EXACT (484363 PASS, przyjazd 21:24:49 dokładny, podejście 4216m→40m); gps_no_contain=60 poprawnie odrzuca drive-by; determinizm 2 runy. ⚠ first-pass min_dist/n_in/confidence ZAMROŻONE (brak --force) → ~7,5% stale-low; klaster 12-min wciąga drive-by → ~2,5% niestabilny przyjazd per-order.
- **co flipuje / decyzja jaką napędza:** FUNDAMENT: gate fizycznego pomiaru feas_carry (#3/B2) i O2/bundle-calib flip (02.07) — zastępuje prawdę-przyciskową ±3min realną prawdą GPS per-zlecenie. CAVEAT: konsumenci muszą traktować physical jako DOLNĄ granicę (offset ~+2min przekazania) i liczyć się z 19% volatile coverage (biased, low-GPS dni=brak prawdy).
- _(agent C: C09-gps-deliv-validation)_

### reassign_global_select / global_allocate de-pile COUNT (maxpile before->after redukcja)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** regroup raw new_cid (po) i proposed_cid (przed) per tick z per-order wierszy pending_global_resweep.jsonl (1859 tickow) -> 0 mismatch vs stemplowany g_maxpile_before/after; reassign: invarianty survivors+hidden==candidates & maxpile_after<=survivors OK (53 ticki); 7/7 big pile-on>=3 zredukowane
- **inwarianty:** g_maxpile regroup 0/1859 mismatch; survivors+hidden==cand 0 naruszen; maxpile_after<=survivors OK; same-set-of-orders zachowany
- **co flipuje / decyzja jaką napędza:** zaufanie do overlay de-pile (liczba) / utrzymanie ENABLE_REASSIGN_GLOBAL_SELECT=on
- _(agent C: C10-global-allocate)_

### reassign_global_select over-hide guard (claim 'over-hide=0')
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** pelny-plik recompute recepta review:71 (cand>=2 & survivors==0): 7 tickow, WSZYSTKIE benign po dropped-reasons (stays_with_holder/quality_failed_vs_global = poprawne chowanie); review '0' bylo --since post-fix one-shot scoped (29.06 18:30) = NIE klamstwo; zero false-negative na realnym bugu
- **inwarianty:** 0 genuine-hidden wsrod 7 suspects; ALE guard koarsy (konflatuje benign z bug) + werdykt STALE (29.06, brak recurring review) + 6 tickow 30.06 nieaudytowanych
- **co flipuje / decyzja jaką napędza:** rollback ENABLE_REASSIGN_GLOBAL_SELECT gdyby genuine over-hide
- _(agent C: C10-global-allocate)_

### ziomek_time_route_monitor — q3 route parytet konsola(fleet_state._build_route) vs apka(route_podjazdy.order_podjazdy)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** 3. NIEZALEZNA metoda canon_direct (scratchpad/c11_oracle.py:74): render WPROST z plan_doc['stops']/p.sequence z pominieciem OBU rendererow (skip picked_up-pickup, scal same-restaurant, dedup drop, bramka pokrycia). Trojkat con==app==canon_direct na 9/9 zywych covered workach, deterministycznie (pass1≡pass2 + swiezy proces), zgodne z monitorem last-tick 10/10. + Adversarial F4 (c11_adversarial.py): syntetyczny carried+relax interleave dowodzi ze app(trust_canon=ON)==console ale app(OFF) front-loaduje carried → con==app MA ZEBY, NIE tautologia. + Archeologia: spadek mismatchy do 0 zbiega sie z REALNYM fixem u zrodla 61381ac(06-28 12:50:19)+restart courier-api 06-28 12:50:51 (apka carried-first→kanon), monitor e3d42fd zrownany 9 min POZNIEJ — nie kalibracja do ciszy; mismatche 06-22..06-28 byly PRAWDZIWE (flaga C5 martwa, apka prod=carried-first).
- **inwarianty:** delta: con==app na 9/9 + canon_direct==con==app(covered); ten sam zbior+liczba stopow (set(con)==set(app)==bag); ZERO fikcyjnych pickupow (kazdy pickup-oid status≠picked_up); kolejnosc z p.sequence (canon_direct iteruje plan.stops bezposrednio); determinizm ≥2 odpalenia OK; teeth: F4 relax-flip rozroznialny. BRAK join GPS/delivered_at = nie ground-truth fizyczny, tylko rownowaznosc rendererow planu.
- **co flipuje / decyzja jaką napędza:** (1) decyzja stop/extend monitora regresji (review tool: clean→`systemctl disable ziomek-time-route-monitor.timer`, anomaly→przedluz). (2) JEDYNY runtime-parytet cross-repo konsola↔apka (twin #11 / root R2 'one route-order module' z A5/A6 — brak wspolnego importu repo↔repo, parytet tylko ten monitor + golden-test).
- _(agent C: C11-time-route-monitor)_

### checkpoint_tz_shadow (ENABLE_CHECKPOINT_TS_WARSAW_PARSE), klasa L (TZ)
- **werdykt:** VALIDATED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** (a) SECOND-METHOD na 588 zywych checkpointach orders_state: wiek liczony OBIEMA galeziami -> age_ON minus age_OFF median = DOKLADNIE +120.0min (offset Warszawy UTC+2), 94/588 future-dated(age<0) pod OFF vs 0/588 pod ON, 585/588 to naive(no-T). (b) Code-trace 4 bramek (interp elapsed<0->None cr:672; _bag_not_stale age<thr cr:646; ZOMBIE-01 cr:617; last_delivered 'age<0 or age>=30' cr:1057) zgodne z kierunkiem danych. (c) Re-agregacja frozen jsonl 386 tikow: interp_off=0 interp_on=958, 335 real->synth = KOREKTY fantomow (OFF robi z dostawy sprzed ~120-150min 'swieza 0-30min'), bag_dropped=5 wszystkie cid393 gps->gps = 1 realny ghost. (d) Determinizm 2-run byte-identical, no-op (delta 0) na aware(T) ts. PROXY-NOTE: ground bo dowod = deterministyczna arytmetyka (+120 exact) + struktura kodu + wlasne outputy jsonl, NIE button-proxy fizyczny; button-truth checkpointow NIE wplywa na werdykt (claim = poprawnosc TZ-interpretacji stringa, nie fizyczna pozycja).
- **inwarianty:** ON nigdy nie daje age<0 (0/588); ON nigdy nie odrzuca realnie-swiezego (<thr) checkpointu (guard age<0); zbior kurierow identyczny (54 w obu); ON nie fabrykuje pozycji (tylko upgrade->interp z realnego pickup+OSRM, lub uczciwe no_gps); aware(T) ts bez zmiany (delta 0 = bezpieczny no-op)
- **co flipuje / decyzja jaką napędza:** Naped flipu ENABLE_CHECKPOINT_TS_WARSAW_PARSE (teraz LIVE ON). Downstream: zrodlo pozycji kuriera no-GPS (interp / last_delivered / no_gps) -> normalizacja km_to_pickup -> ranking/selekcja kandydatow; integralnosc bag staleness/ZOMBIE-01 -> carry penalty / R6 / C2 shadow
- _(agent C: C12-checkpoint-tz)_

### pickup_slip_monitor (#2, daily 22:30, load-aware ETA buffer)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Niezalezna re-impl collect()/summarize() (NIE import toola) na swiezym eta_calibration_log + pool_feasible join shadow_decisions; krzyz z zywym tool --dry x2 (zgodne w 1 rekord); metryka re-policzona z surowych stempli delivered_at[Warsaw->UTC -2h] - predicted_delivered_at[UTC] median|d|=0.003min/3765; join gps_delivery_truth.jsonl per komorka load x bag
- **inwarianty:** znak DODATNI=optymistyczny zweryfikowany; TZ-sound median|d|0.003; determinizm PASS A==B; n>=30 gating dziala (unknown/solo n=2->null); monotonicznosc load ciasno>srednio>luzno + solo>bundle; join-coverage 99.8% (swieze-3d 100%); gradient PRZEZYWA na fizycznym GPS (ciasno/solo phys 23.6>>luzno 1.5)
- **co flipuje / decyzja jaką napędza:** FLIP load-aware buforu ETA (review 04.07): SOLO bufory VALIDATED uzyteczne wprost (ciasno~27-29/srednio~18-23/luzno~6-10); BUNDLE bufory contaminated-low (NIE uzywac surowych — F1)
- _(agent C: C13-pickup-slip)_

### min_delivered_at PRODUCENT (silnik, dispatch_pipeline.py:6019-6047, helper :622-630)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Niezalezny re-recompute sooner_min z ISO live/mda delivered_at (nie ufajac polu producenta) nad 802 non-null, 2x determ. (RUN1==RUN2). Inwarianty: delta>=0 (mda=argmin, live in feasible => mda<=live) = 0/802 naruszen; producent mda_delivers_sooner_min vs moj ISO-recompute = 0/802 mismatch; changed<->cid-nierownosc = 0 naruszen obie strony; 128 null = 100% pool_feasible_count=0 (slusznie pusta pula). Logika min(feasible,key=predicted_delivered_at[new]) = total=dostawa-committed POPRAWNA.
- **co flipuje / decyzja jaką napędza:** Decyzja A/B: flip 'min-total' jako PRIMARY obiektyw selekcji vs dostroic committed_pickup+food_age vs neither
- _(agent C: C14-min-delivered)_

### address_coords_mismatch (text↔pin, distance_m)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** wlasny haversine + spherical-law-of-cosines (2 niezalezne formuly) z geocode_cache.json bezposrednio (NIE przez instrument); oracle 484269 Mozna(53.1324886,23.1688403)↔Mrozna(53.1610167,23.1261602)=4262.0 m oboma=match claim 4,26km; 8/8 zywych text_coords recompute Δ≤0.06 m vs logged
- **inwarianty:** wszystkie 8 distance_m>400 (zero sub-progowych fire); logged==recompute do zaokraglenia; 2 formuly zgodne; niezalezny geokod cache==text_coords w logu
- **co flipuje / decyzja jaką napędza:** NIC bezposrednio (shadow log-only advisory); informuje decyzje Adriana A(alert koord)/B(363-par hard gate)/C(zostaw) + zmotywowal LIVE ENABLE_REGEOCODE_SYNC_TEXT source-fix
- _(agent C: C16-address-mismatch)_

### address_town_mismatch (street↔town counts, check_street_town)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** niezalezny rebuild street->town counts z geocode_cache wg logiki _street_town_counts; 5/8 exact match, 3/8 drift +1..+3 = wzrost cache miedzy log-time a teraz, 8/8 fire (bia≥5 ∧ here≤1)
- **inwarianty:** fire-condition stabilny mimo drift counts; WSZYSTKIE 8 'miast' to realne wsie-satelity (Grabowka/Kuriany/Olmonty/Kleosin/Ogrodniczki/Zascianki) -> FP-ekspozycja dla pospolitych ulic udokumentowana w verdykcie
- **co flipuje / decyzja jaką napędza:** NIC bezposrednio; ten sam advisory A/B/C — heurystyka 'suggest Bialystok' (NIE rozstrzyga ground-truth)
- _(agent C: C16-address-mismatch)_

### pending_global_resweep.jsonl (global de-pile + would_repropose parytet live==canon)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Recompute would_repropose z surowych pol (proposed_cid/new_cid/proposed_now_score/new_score/g_spread_improved, margin=15) -> 0/3073 mismatch vs zapisane; g_maxpile_after zrekonstruowane z Counter(new_cid) per sweep (NIE z pola toola) -> 0/1880 mismatch; spread>8 i pool_feasible=0 policzone count'em. Run x2 md5 stabilny.
- **inwarianty:** same-set per sweep (g_hanging==liczba wierszy); pile recompute 0 mism; would internal-consistency 0 mism; new_deliv_spread_km>8 = 35.1% (max 24.3km) faithfully logged = de-pile dziedziczy slepote geom (dowod 2-ga metoda); pool_feasible=0 20%. CAVEAT: live-path unimplemented (PENDING_RESWEEP_LIVE=false :420 no-op), review SPENT 26.06 nie-recurring
- **co flipuje / decyzja jaką napędza:** Decyzja A/B (re-ranker vs fix-u-zrodla P0-B global de-pile) + pending_global_resweep_review GO/NO-GO; would_repropose% napedza 'warto live'
- _(agent C: C17-shadow-selection)_

### c2_shadow_log.jsonl (C2 per-order 35min hard-gate kontrfaktyk)
- **werdykt:** VALIDATED · **typ prawdy:** proxy-certified
- **prawda drugą metodą:** Recompute check_per_order_35min_rule (THR=35.0, feasibility_v2:289-318) WPROST z serializowanego per_order_delivery_times kazdego z 20280 rekordow; porownanie do c2_would_reject/max_elapsed_min/violations/new_verdict_if_c2_enabled -> 0 mismatch na kazdym polu. Run x2 md5 stabilny.
- **inwarianty:** rule recompute exact (reject/maxel/violations); WSZYSTKIE 20280 = realne >35 violation, 0 fail-closed (per_order None); new_verdict zawsze NO; reader-na-timerze = ZADEN (klasa K)
- **co flipuje / decyzja jaką napędza:** USE_PER_ORDER_GATE / C2 DEPRECATE_LEGACY_HARD_GATES flip — ALE konsument MARTWY (analyze_shadow_logs bez timera), dane wierne lecz orphaned
- _(agent C: C17-shadow-selection)_

### post_shift_overrun_forward_replay
- **werdykt:** VALIDATED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** grep -o serialized-key swiezy ledger = 1699/438rek (NIE 0); niezalezny JSON recompute bramki with_pen=55>=20 (oracle_psor.py: 81 best_effort, zgodne z tool 81/55/26); crafted-candidate flip przez REALNY _best_effort_objm_pick OFF->B ON->A WYKRYTY x2 (oracle_psor_flip.py, neg-control pen=0 no-flip); decision_flag(common.py:348) fallback-do-module-attr potwierdzony (klucz NIEOBECNY w flags.json -> replay-toggle dziala); temporalna przyczyna '0' = pole live od restartu 06-29T09:25 po F2-fix shadow_dispatcher:258
- **inwarianty:** with_pen>=20 gate SAT; crafted-flip delta!=0 wykryty; neg-control delta==0; determinizm run1==run2; recompute==tool 81/55/26; same-key serializacja twin A(501)+B(885)
- **co flipuje / decyzja jaką napędza:** ETAP-5 flip ENABLE_POST_SHIFT_OVERRUN_PENALTY (shadow->live, po ACK poza peakiem)
- _(agent C: C18-void-claimed)_

### test_flag_doc_coverage::test_baseline_is_not_stale (B19, tests/:32)
- **werdykt:** VALIDATED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** Standalone flag_doc_coverage_check.compute() -> stale_baseline=[ENABLE_AUTO_ASSIGN] + git show 8024705 (commit dokumentujacy AUTO_ASSIGN w ZIOMEK_LOGIC_REFERENCE.md) + pytest live FAILED. Reguly: stale = (b not in flags) OR (b in ref).
- **inwarianty:** ENABLE_AUTO_ASSIGN w flag_doc_baseline.json:9 ORAZ teraz w ref (b in ref=True); compute() new_drift=0 (nie nowy undoc) -> czerwien tylko ze stale, nie z nowej flagi
- **co flipuje / decyzja jaką napędza:** Zielonosc baseline ETAP-4 = wiarygodnosc pytest jako bramy-regresji (protokol ETAP-0: testy bazowe ZIELONE przed zmiana). 1-liniowy fix = usun ENABLE_AUTO_ASSIGN z doc-baseline.
- _(agent C: C19-conftest-leak)_

### flag_effect_coverage_check (C-FLAG-EFFECT gate, tools/:32-36)
- **werdykt:** VALIDATED · **typ prawdy:** ground-truth
- **prawda drugą metodą:** Standalone RC=0 (59 ETAP4 / 54 tested / 91.5% / 0 new_gap) + pytest 3P; ale _etap4_flags() scope = TYLKO ETAP4 -> grep R6_SOFT_PEN_CAP tests/ = 0 i gate go nie widzi.
- **inwarianty:** zakres gate = C.ETAP4_DECISION_FLAGS; decyzyjne flagi POZA ETAP4 (R6_SOFT_PEN_CAP, OBJ_COMMITTED_PICKUP_PENALTY-ma-test, EXCLUDE_BY_CID...) exempt od wymogu testu-efektu
- **co flipuje / decyzja jaką napędza:** Zielony effect-gate daje falszywy komfort: decyzyjna flaga poza ETAP4 moze byc wpieta i 0-testow-efektu (double-blind z conftest leak).
- _(agent C: C19-conftest-leak)_
