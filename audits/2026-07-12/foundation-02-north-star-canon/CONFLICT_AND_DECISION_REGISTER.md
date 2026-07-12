# Rejestr konfliktów i chronologia decyzji

Każdy konflikt jest rozstrzygany osobno dla intencji, implementacji i effective runtime. Rozstrzygnięcie faktu technicznego nie awansuje go do kanonu produktu.

## C-01 — R6: 35 / 40 / `tier`

| Pole | Ustalenie |
|---|---|
| Strona A | Najnowsza decyzja właściciela: 35 w normalnym trybie, 40 tylko w Alarmie/ratunku dla wszystkich, nigdy jako przywilej klasy kuriera (`BR-002`, `BR-003`). |
| Strona B | ADR-001, fragmenty architektury/Logic Reference, komentarze „Tier-3” i klasowy gold-p80 branch sugerują 35/40 lub ulgę zależną od klasy (`CF-001`, `IMP-004`, `IMP-005`). |
| Chronologia | Starsze dokumenty i kod używały `tier` dla klasy/profilu; kanon 29.06 oddzielił klasę od Alarmu; invariants skorygowały dial; Prompt 02 z 12.07 ponownie zamknął znaczenie. |
| Źródła | Prompt 02 §14 H1/§20; `memory:ZIOMEK_REGULY_KANON.md@ca55742b`; `ZIOMEK_INVARIANTS.md@c7de9f2`; `feasibility_v2.py@c7de9f2`; `mode_layer.py@c7de9f2`. |
| Status | **Intencja rozstrzygnięta. Implementacja i dokumentacja w konflikcie.** |
| Rozstrzygnięcie | Nie otwierać progu ani związku z klasą. Gołe `tier` rozdzielić w przyszłej migracji na `courier_capacity_class`, `late_pickup_risk_level`, `escalation_level`, `alarm_mode`. |
| Nierozstrzygnięte | R27 `±10` wobec nietykalnego/max `±5` commit (`UNK-003`) oraz execution authority (`UNK-006`). Trigger auto po S1+S2 i scope per-decision są potwierdzone (`BR-016`). |
| Wpływ | Krytyczny: obecny klasowy branch może zmieniać feasibility; szkielet Alarmu nie steruje egzekucją. |

## C-02 — Potwierdzony Alarm S1/S2/S3 kontra niewpięty szkielet

| Pole | Ustalenie |
|---|---|
| Strona A | Kanon mówi: S1 normalnie, S2 defer, następnie automatyczny Alarm per decyzja, gdy S1 i S2 są niewykonalne; R6=40 dla wszystkich (`BR-016`). |
| Strona B | `mode_layer.py` definiuje tryb jako globalny, trwały stan FSM (`IMP-019`), choć kanon wymaga scope per-decision. Observer nie dostarcza `s2_infeasible_rate` i celowo blokuje capitulation marker wartością zastępczą; helpery 35/40 i ±5/±10 nie mają konsumenta decyzyjnego. |
| Chronologia | 29.06: owner verdict o auto Alarmie; później powstała czysta warstwa S1-S3; na `c7de9f2` pozostaje shadow/default-off i niewpięta. |
| Źródła | `memory:ZIOMEK_REGULY_KANON.md@ca55742b`; `mode_layer.py`, `tools/mode_observer.py`, `dispatch_pipeline.py@c7de9f2`; flag registry. |
| Status | Intencja triggera/scope/R6 rozstrzygnięta; implementacja niekompletna i ma błędny globalny scope. |
| Powód braku wpięcia | Exact machine predicate, retry i observability są problemem technicznym. W samym kontrakcie Alarmu nieredukowalna kwestia dotyczy R27 precedence (`UNK-003`); cross-mode semantyka interwału R6 jest osobno w C-02A. |
| Wpływ | Krytyczny przed jakimkolwiek egzekwowaniem 40. |

## C-02A — Kotwica R6: ready / physical pickup / delivery endpoint

| Pole | Ustalenie |
|---|---|
| Strona A | Główna tabela kanonu mówi „dostawa ≤35 min od odebrania” i „jedzenie w aucie nigdy >40”, co wskazuje physical pickup jako start, ale nie rozdziela delivery arrival od handoff. |
| Strona B | Owner-correction lineage z 10.05 i change protocol utrwalają dla nieodebranego `pickup_ready_at`, dla odebranego `picked_up_at`; baseline porównuje ten hybrid start z `predicted_delivered_at` (`IMP-020`, `IMP-021`). |
| Dalszy drift | Baseline hard-rejectuje nieodebrane przekroczenia, a carried przekroczenie tylko przy wykrytym wpływie nowego pickup; samo pre-existing przekroczenie jest śledzone (`IMP-022`). |
| Obserwowalność | Brak kompletnego physical pickup i customer handoff event (`GT-004`, `GT-006`); last-inside i delivery arrival mają węższy zakres. |
| Chronologia | 10.05 owner correction ready-anchor → 29.06/03.07 bieżący kanon używa „od odebrania” i jednocześnie zachowuje ready-anchor jako świeżą decyzję/naprawę split-brain → 11.07 kontrakt ETA wiąże eventy jako unbound. |
| Status | **Aktywny konflikt semantyczny.** Progi 35/40, Alarm i zakaz class privilege pozostają rozstrzygnięte; nierozstrzygnięty jest interval R6 (`CF-009`, `UNK-007`–`UNK-009`). |
| Wpływ | Krytyczny: zmienia feasibility, znaczenie freshness, oracle, KPI i sposób mierzenia naruszenia. |
| Decyzja | `OD-07-R6-INTERVAL`; technika nie może wybrać fizycznego znaczenia z aktualnej hybrydy kodu. |

## C-03 — HARD przed SOFT kontra późny re-admit

| Pole | Ustalenie |
|---|---|
| Strona A | HARD przed SOFT (`BR-001`). |
| Strona B | `ENABLE_FEAS_CARRY_READMIT` może w selection zmienić `NO→MAYBE`; feasibility guard tylko loguje (`IMP-001`, `IMP-002`). |
| Chronologia | Filtr feasibility → dodane guardy obserwacyjne → dodana flagowana ścieżka re-admit; registry snapshot na baseline false. |
| Źródła | Prompt 02; ADR-001; architecture/invariants; `core/selection.py@c7de9f2`; flag registry. |
| Status | Intencja rozstrzygnięta; latentny drift implementacji. |
| Rozstrzygnięcie | Nie uznawać re-admit za zatwierdzony wyjątek. Brak process attestation pozostaje `UNKNOWN`, nie OFF. |
| Wpływ | Krytyczny przy przyszłym flipie. |

## C-04 — R-DECLARED-TIME: HARD kontra tripwire

| Pole | Ustalenie |
|---|---|
| Strona A | Deklaracja jest HARD i nie może być fałszowana (`BR-004`). |
| Strona B | Baseline ma obserwacyjny tripwire, który jawnie nie rejectuje; Logic Reference §8 opisuje mocniejszy enforcement niż kod (`IMP-006`). |
| Chronologia | Reguła biznesowa → rozproszone użycie ready/committed → 03.07 tripwire obserwacyjny → na baseline brak pełnej blokady. |
| Źródła | Prompt 02; kanon pamięci; `state_machine.py@c7de9f2`; `ZIOMEK_INVARIANTS.md`; Logic Reference §8. |
| Status | Kanon potwierdzony; implementacja częściowa; opis §8 conflicted. |
| Rozstrzygnięcie | Tripwire nie jest HARD enforcementem. |
| Wpływ | Krytyczny: system może obserwować regułę bez gwarancji jej zachowania. |

## C-05 — R27: SOFT ±5, protected commit, „hard frozen” i ±10

| Pole | Ustalenie |
|---|---|
| Strona A | Normalne ±5 jest SOFT, commitment po assignment pozostaje chroniony, a C6 mówi max ±5 (`BR-005`, `BR-006`). |
| Strona B | Ten sam kanon w C5/§3a mówi R27 ±10 w Alarmie; kod ma 5/10 zależne od load poza S3 i stare hard-frozen komentarze (`IMP-007`, `CF-002`). |
| Chronologia | Pierwotne hard ±5 → reachability/soft solver → owner C5 Alarm ±10 i C6 max ±5 w tym samym werdykcie → techniczny load 5/10 na baseline. |
| Źródła | Prompt 02; kanon pamięci; `common.py`, `route_simulator_v2.py`, `core/candidates.py`, `auto_assign_gate.py@c7de9f2`. |
| Status | Normalne ±5 i ochrona zapisu rozstrzygnięte; znaczenie Alarmowego ±10 wobec niezmienionego commitment conflicted. |
| Powód braku pełnego rozstrzygnięcia | Nie wiadomo, czy ±10 jest wyłącznie tolerancją planu/ALERT bez zmiany commitment, czy wyjątkiem od C6 max ±5. |
| Wpływ | Wysoki: kilka konsumentów może rozumieć ten sam termin inaczej. |

## C-06 — No-GPS/pre-shift: równość kontra score-veto

| Pole | Ustalenie |
|---|---|
| Strona A | No-GPS i pre-shift nie mogą mieć ukrytej kary; realna niewykonalność ma osobny HARD (`BR-007`). |
| Strona B | Equal bucket/penalty flags istnieją, lecz pre-shift FAR zachowuje około `-1000` w SOFT, obok jawnego hard rejectu dla dalekiego startu (`IMP-010`). |
| Chronologia | Lokalne demotion fixes → equal-treatment flag family → nadal zachowany FAR veto → bieżąca dyrektywa wymaga semantycznego rozdzielenia. |
| Źródła | Prompt 02; kanon/no-GPS memory; `dispatch_pipeline.py@c7de9f2`; flag registry. |
| Status | Intencja rozstrzygnięta; implementacja częściowa. |
| Wpływ | Wysoki: hidden score może zachowywać się jak niejawny HARD. |

## C-07 — Always-propose kontra częściowa implementacja

| Pole | Ustalenie |
|---|---|
| Strona A | Przy istniejącej flocie zawsze feasible albo least-damage/ALERT; time hold i zero fleet są jawne (`BR-008`, `BR-009`). |
| Strona B | Flaga omija tylko podzbiór silence gates; `no_solo_candidates` może zwrócić `best=None` przy obiekcie floty; inne data/quality gates zwracają KOORD/SKIP (`IMP-008`, `IMP-009`, `IMP-016`). |
| Chronologia | Cichy KOORD/best-effort → dyrektywa Always-propose → ADR-003 → flagowane bypassy → nadal niepełne fallbacki na baseline. |
| Źródła | Prompt 02; kanon pamięci; ADR-003; `dispatch_pipeline.py`, `core/selection.py`, `core/gates.py@c7de9f2`; tests (nieuruchomione). |
| Status | Intencja rozstrzygnięta; implementacja niepełna. |
| Nierozstrzygnięte | Czy technicznie nieocenialna, lecz nominalnie istniejąca flota jest osobnym hold/fallback oraz czy least-damage może być auto-executed (`UNK-006`). |
| Wpływ | Wysoki: operator może nadal zobaczyć brak decyzji tam, gdzie produkt obiecuje jawny problem. |

## C-08 — Fizyczny `core/` i ADR-008 (`CF-003`)

| Pole | Ustalenie |
|---|---|
| Strona A | ADR-008 z 03.07: w tamtym audycie nie przenosić fizycznie rdzenia; ewentualna pakietyzacja to osobny sprint pod pełnym #0. |
| Strona B | 06.07 utworzono fizyczny `core/`: pierwsza fasada `92cd23c`, potem gates/candidates/selection/scorer/planner; baseline go zawiera (`ARCH-003`). |
| Chronologia | 03.07 time-scoped no-move → 06.07 osobny program strangler/refactor → 12.07 baseline z `core/`. |
| Źródła | ADR-008@`c7de9f2`; git commits `92cd23c`, `7b08789`, `dfeef03`, `0045494`, `1b6c80b`, `2e7d359`; branch docs `a359e909`. |
| Status | **Rozstrzygnięty chronologicznie.** |
| Rozstrzygnięcie | Przestarzał fakt o layout; pozostaje ważny scope ADR: nie robić przypadkowej przeprowadzki, chronić cross-repo imports i stosować strangler/#0. |
| Wpływ | Niski produktowo; ważny dla klasyfikacji dokumentacji. |

## C-09 — Parser flag: snapshot kontra registry kontra effective

| Pole | Ustalenie |
|---|---|
| Strona A | Starszy human inventory dokumentował drift/fallback env. |
| Strona B | Baseline code i machine registry mają flags.json-first; Prompt 01 zaatestował procesowo `USE_V2_PARSER=true` i zdrowy v2. |
| Chronologia | env-frozen per service → dual carrier → flags.json-first i flip 10.07 → P01 runtime 12.07. |
| Źródła | `panel_client.py@c7de9f2`; `tools/flag_lifecycle_registry.json@c7de9f2`; Prompt01 report/manifest `14e7a5e`; historyczny flag inventory. |
| Status | **Rozstrzygnięty dla okna P01:** implemented i effective v2; nie North Star. |
| Wpływ | Niski produktowo, wysoki metodologicznie: runtime flag nie może pochodzić z docs/defaults. |

## C-10 — Pickup: arrival / last-inside / exit / click / physical pickup

| Pole | Ustalenie |
|---|---|
| Strony | Legacy nazywa click lub last-inside „real/physical pickup/departure”; kontrakt v1 rozdziela arrival, last-inside i proxy; confirmed exit/possession nie są kompletnym eventem. |
| Chronologia | Kalibracja 07–08.07 używała proxy → 11.07 kontrakt v1 ograniczył semantykę i ustawił KPI unbound → Prompt 02 zachowuje rozdzielenie. |
| Źródła | `docs/eta/01..05@c7de9f2`; `docs/eta/06_ground_truth_contract.md@c7de9f2`; `tools/eta_ground_truth.py`; Prompt 02. |
| Status | Zakres observable rozstrzygnięty; docelowy KPI `UNKNOWN` (`UNK-001`). |
| Wpływ | Krytyczny: bez decyzji nie wolno promować modelu jako physical pickup quality. |

## C-11 — Delivery: arrival / click / customer handoff

| Pole | Ustalenie |
|---|---|
| Strony | Legacy „physical delivered” bywa arrival/click; kontrakt v1 dowodzi wyłącznie arrival i jawnie nie handoff. |
| Chronologia | GPS validation/app geofence → historyczne raporty ETA → kontrakt v1 11.07 → Prompt 02. |
| Źródła | `docs/eta/01..06`; `tools/eta_ground_truth.py`; legacy GPS tools; Prompt 02. |
| Status | Arrival observable rozstrzygnięty; customer handoff i docelowy KPI `UNKNOWN` (`UNK-002`). |
| Wpływ | Krytyczny dla service KPI; routing ETA może mieć inny end event. |

## C-12 — Feedback operatora i ground truth

| Pole | Ustalenie |
|---|---|
| Strona A | Historyczne `PANEL_OVERRIDE = primary training signal`, agreement/override jako miara modelu. |
| Strona B | Nowsza decyzja: klik jest decyzją człowieka, a uczenie ma optymalizować zweryfikowany outcome (`GT-001`, `LRN-001`). |
| Chronologia | ML/behavior cloning historycznie → 05/23/30.06 korekta outcome-over-agreement → Prompt 02 12.07. |
| Źródła | historyczny `ZIOMEK_MASTER_KB.md`; `memory:lessons.md`; autonomy readiness; Prompt 02; ETA contract. |
| Status | **Intencja rozstrzygnięta; stare BC twierdzenie historyczne.** |
| Wpływ | Wysoki: nie wolno trenować ani oceniać autonomii samym agreement. |

## C-13 — ETA quantile/proxy verdict kontra physical KPI

| Pole | Ustalenie |
|---|---|
| Strona A | Dokumenty 03/05 podają P75/P80, target on-time i jakościowy verdict na click/Rutcom. |
| Strona B | Nowszy kontrakt 06 ustawia `canonical_kpi_event=unbound`, bez progu PASS/FAIL i zakazuje proxy promotion. |
| Chronologia | 07–08.07 model/validation → 11.07 semantic contract → Prompt 01/02. |
| Źródła | `docs/eta/03`, `05`, `06@c7de9f2`. Te dokumenty 01–05 mają wspólny lineage i nie są niezależnymi dowodami. |
| Status | Historyczne wyniki pozostają evidence o proxy; physical KPI/progi nierozstrzygnięte (`UNK-005`). |
| Wpływ | Krytyczny przed promocją ETA. |

## C-14 — Trwała korekta kontra incydentalny wyjątek

| Pole | Ustalenie |
|---|---|
| Strona A | Najnowsza jawna decyzja właściciela ma pierwszeństwo; wiedza ma być utrwalana. |
| Strona B | Jeden case nie tworzy globalnej reguły; źródła nie definiują aktu promotion-to-canon. |
| Chronologia | Feedback/lessons → żywy change protocol → Prompt 02 jawnie zamyka automatyczne uogólnienie. |
| Źródła | Prompt 02; `memory:ziomek-change-protocol.md@ca55742b`; `memory:lessons.md@ca55742b`. |
| Status | `UNKNOWN`; wymaga `UNK-004`. |
| Wpływ | Krytyczny dla konstytucji Codexa i bezpiecznego uczenia. |

## C-15 — 100% decyzji kontra human-gated ALERT/przerzut

| Pole | Ustalenie |
|---|---|
| Strona A | Horyzont: Ziomek podejmuje 100% decyzji lepiej od człowieka. |
| Strona B | Aktualny kanon: przerzut zatwierdza człowiek; w oknie P01 auto-assignment OFF. |
| Chronologia | Horyzont autonomii → etapowe AUTO/ACK/ALERT → kanon 29.06 utrzymuje human approval przerzutu → P01 effective OFF. |
| Źródła | kanon pamięci; autonomy readiness; Prompt01 runtime. |
| Status | Nie jest logiczną sprzecznością horyzontów, lecz brak decyzji o granicy awansu (`UNK-006`). |
| Wpływ | Krytyczny dla kart autonomii. |

## C-16 — `F7AGREE` jako meta-rating kontra assignment lineage

| Pole | Ustalenie |
|---|---|
| Strona A | Kontrakt ETA wymaga jawnego assignment action i actual courier; feedback/rating nie jest assignmentem. |
| Strona B | Legacy `decision_outcomes` może wybrać `F7AGREE` jako latest, po czym nowy anchor odrzuca rekord bez actual courier (`IMP-015`, `IMP-018`). |
| Chronologia | Historyczne rating/action lineage → nowszy ETA v1 assignment contract → baseline pozostawia kompatybilnościową szczelinę. |
| Źródła | `telegram_approver.py`, `tools/decision_outcomes.py`, `tools/eta_ground_truth.py`, `docs/eta/06@c7de9f2`. |
| Status | Implementacyjny konflikt coverage/provenance; nie konflikt intencji. |
| Rozstrzygnięcie | `F7AGREE` nie awansuje do assignment ani ground truth. |
| Wpływ | Wysoki dla coverage i wiarygodności joinu KPI; nie wymaga nowej decyzji biznesowej. |

## Chronologia kluczowych domen

| Domena | Pierwotna intencja | Korekty | Ostatnia jawna decyzja | Baseline implementacja | Effective P01 | Pozostały drift |
|---|---|---|---|---|---|---|
| HARD/SOFT | constraints przed scoringiem | dodane guardy i best-effort | brak inwersji, least-damage jawnie NO | filtr + log guard + latentny re-admit | niezaatestowane | re-admit/guard |
| R6 | płaskie 35; historyczna korekta ready-anchor | tier/class/quantile/40 cap; hybrid anchor | 35 normalnie; auto Alarm per-decision po S1+S2; 40 dla wszystkich, nigdy klasa; dokładny interval nadal conflicted | 35 + gold-p80 + best-effort 40 + niewpięty global mode + ready/picked-up→predicted-delivery | niezaatestowane | start/end/dual-constraint; class branch; techniczne wpięcie; R27 precedence |
| Always-propose | human KOORD/best-effort historycznie | owner „nie chowaj” i ADR-003 | feasible albo least-damage/ALERT przy flocie | częściowe bypassy, no_solo gap | flaga niezaatestowana | kompletność i execute |
| ETA/KPI | click/Rutcom calibration | semantic audit v1 | proxy ≠ truth; KPI unbound | observable split | brak physical event wg P01 | event/coverage/threshold |
| GPS | historyczne demotion/no-GPS | equal-treatment sprints | no hidden penalty | wiele flag, FAR veto | niezaatestowane | twin completeness/pre-shift |
| Operator feedback | imitation/override KPI | outcome-over-agreement | click decyzją, nie truth | agree/override logs i legacy tools | N-D | promotion protocol |
| Uczenie | behavior clone/ML | shadow, replay, outcome contract | ucz się optimum po dowodzie | kalibratory/modele shadow | brak auto-promotion proof | continuous loop, KPI |
| Autonomia | 100% target | etapowe gates/killswitch | jakość/stabilność przed tempem | executor istnieje | auto-assign OFF | execution cards |
| Core | logiczny monolit | ADR-008 time-scope | osobny strangler program | fizyczny core od 06.07 | loaded SHA niepełne | czystość/strangler completeness |
| Parser | env-frozen | dual carrier/registry | techniczna wiarygodność, nie produktowy cel | flags-first | v2 healthy | brak istotnego driftu w oknie |

## `ZIOMEK_LOGIC_REFERENCE.md` — klasyfikacja sekcja po sekcji

Najbezpieczniejsza etykieta całości: **`PARTIALLY CURRENT APPEND-ONLY IMPLEMENTATION CHANGELOG WITH STALE BODY — NOT A CANON SOURCE`**.

| Sekcja | Klasyfikacja | Powód |
|---|---|---|
| Preambuła i legenda | `HISTORICAL` + `CONFLICTED` | „current” i tagi LIVE pochodzą z różnych dat; własna korekta 23.06 także została później nadpisana. |
| 1. What Ziomek is | `HISTORICAL` + fragmenty `CURRENT` + `IMPLEMENTED_ONLY` | Misja/Z1-Z3 mają niezależne wsparcie; liczby, kanały i „autonomous” nie opisują effective P01. |
| 2. Runtime topology | `HISTORICAL` + `UNKNOWN` | Point-in-time bez aktualnego fingerprintu; dispatch-telegram jest świadomie wyłączony. |
| 3. Dependency graph | `IMPLEMENTED_ONLY` + `CONFLICTED` | Nie obejmuje późniejszego `core/`, fasady decide i effects. |
| 4. Decision pipeline | `IMPLEMENTED_ONLY` + `CONFLICTED` | Ogólny przepływ trwa, ale gates/candidates/selection przeniesiono; numery linii stare. |
| 5.1 Base score | `IMPLEMENTED_ONLY`, w większości zgodne z c7 | Wzory są techniczne, nie intencją. |
| 5.2 Bonuses/penalties | `IMPLEMENTED_ONLY` + `CONFLICTED` | „Exhaustive” i statusy flag są czasowe/stare. |
| 5.3 rule weights | `HISTORICAL`; effective `UNKNOWN` | Wartości runtime poza Git; P01 ich nie atestował. |
| 5.4 Selection | `CONFLICTED` | Selection jest w core; equal-treatment i późniejsze selektory zmieniły opis. |
| 5.5 Verdict gates | `CONFLICTED` | Statusy stare; Always-propose obejmuje tylko podzbiór. |
| 6.1 HARD gates | `CONFLICTED` | 35 istnieje, lecz gold-p80 robi klasowy wyjątek; „single hard rule” miesza warstwy. |
| 6.2 SOFT metrics | `IMPLEMENTED_ONLY`, częściowo current | R1/R8 jako SOFT ma niezależne wsparcie; progi nie są kanonem. |
| 7. Master knobs | `IMPLEMENTED_ONLY` + `HISTORICAL` | Snapshot diali, nie Product Canon. |
| 8. Business rules→code | `CONFLICTED` | Najgroźniejsza sekcja: przedstawia R-DECLARED jako egzekwowane i używa `HARD-ish`. |
| 9. Flags taxonomy | `CONFLICTED` | Miesza constants, snapshots, flags.json i dopiski; effective wymaga procesu. |
| 10. Shadow system | `IMPLEMENTED_ONLY` + `CONFLICTED` | Ledger istnieje; „mutates no state” nie jest bezwarunkową własnością wszystkich hooków. |
| 11. ML & autonomy | `CONFLICTED` | Auto-assign effective OFF, ale p80 R6 jest decyzyjne w kodzie/registry; metryki historyczne. |
| 12. Tests | `HISTORICAL` | Liczniki 3076/4 itd. są przestarzałe; Prompt 02 nie uruchamiał testów. |
| 13. Tech debt | `HISTORICAL` + `PROPOSED` | Część długu zmienił refactor 06.07; remediation nie jest poleceniem. |
| 14. Calibration guide | `HISTORICAL` / treść nieufna | Stary prompt dla AI; nie instrukcja ani kanon. |
| 15. Bazowy appendix | `IMPLEMENTED_ONLY` + `CONFLICTED` | Słownik/statusy 21.06 i deklaracja „match” są za szerokie. |
| Appendix D.3–fale | `IMPLEMENTED_ONLY` + `HISTORICAL` | Datowany changelog; każdy status wymaga registry/runtime. |
| Appendix `USE_V2_PARSER` | `CURRENT` dla implementacji + `EFFECTIVE_ONLY` | c7 ma flags-first, P01 potwierdza v2 healthy. |
| Appendix stage timing | `CURRENT` + `EFFECTIVE_ONLY` | P01 potwierdza flagę obserwacyjną; nie kanon produktu. |
| Appendix A360 A0/N0/I1 | `HISTORICAL` | Wyniki datowanego wydania, nie North Star. |
| Appendix P-FLAGREG | `IMPLEMENTED_ONLY` | Użyteczny pointer; statusy dopiero z registry i process fingerprint. |

## Rozbieżności zapisane, nie naprawione

Prompt 02 nie wybiera diffu ani kolejności wdrożenia. Rejestr służy do review właściciela i przygotowania późniejszych kart autonomii dopiero po decyzjach z `OWNER_DECISION_PACKET.md`.
