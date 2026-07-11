# A360-D0 R6-DECISION-PREP — karta decyzji B-01/B-02

**Data:** 2026-07-11  
**Zakres:** docs-only; zero zmian zachowania silnika  
**Worktree:** `/root/a360_d0_wt/dispatch_v2`  
**Branch/base:** `docs/a360-d0-r6-decision-prep` / `0721c76984a4c450dbbc58c383c205f9187e21bc`  
**Status:** materiał do decyzji, **NIE rekomendacja implementacji ani flipa**

## 0. ETAP 0 — odświeżony stan read-only

Snapshot wykonano 2026-07-11 około 10:11 UTC, bez zapisu runtime.

| Obszar | Dowód aktualny | Wniosek dla D0 |
|---|---|---|
| Git | branch `docs/a360-d0-r6-decision-prep`, HEAD/base `0721c76984a4c450dbbc58c383c205f9187e21bc`; przed raportem worktree miał tylko nowy dozwolony katalog `eod_drafts/2026-07-11/`. | D0 jest izolowane; nie scala ani nie modyfikuje mastera. |
| Właściciele | tmux61 = D0; tmux57 = T0; tmux59 = S0; tmux60/inne worktree mają własne zakresy. Najnowszy handoff `CURRENT HANDOFF 2026-07-11 10:06 UTC` potwierdza te ownershipy. | D0 nie dotyka TEST/ENGINE/PLAN/FLAG ani wspólnych handoffów. |
| Runtime | `dispatch-shadow`, `dispatch-panel-watcher`, `dispatch-sla-tracker` active; shadow PID `3659231`, `NRestarts=0`, start 2026-07-10 15:39:23 UTC. ExecStart wskazuje `python -m dispatch_v2.shadow_dispatcher` z kanonicznego drzewa, nie z worktree D0. | Analizowany runtime nie wykonuje kodu D0; brak restartu/deployu. |
| Joby | brak nowego at-joba D0; recurring world-replay/shadow timery istnieją. | Ich wyniki nie są automatycznie oraclem D0. |
| Baseline | pełna suita read-only na base: `4849 passed, 1 failed, 24 skipped, 10 xfailed`; fail `tests/test_flag_registry_f3.py::test_open_and_accepted_partition_issues` (TEST-11, własność T0). Celowane R6/firewall/always-propose: `61 passed`. | Baseline jest czerwony i blokuje H1/H2; D0 nie naprawia testu. |
| Efektywne flagi | `ENABLE_ALWAYS_PROPOSE_ON_SATURATION=true`; `ENABLE_ETA_QUANTILE_R6_BAGCAP=true`; `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40`; `BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN=90`; `ENABLE_SLA_ANCHOR_UNIFIED=true`; `ENABLE_FEAS_CARRY_READMIT=false`; READY-anchor i SLA-ready klucze nieobecne (fallback OFF). | FEAS-01 jest aktywnym konfliktem; FEAS-05 ma efektywne 90 mimo kodowego fallbacku 30. |
| Firewall/log | `core.invariant_firewall` ma `phase=A_SHADOW`, `enforcement=NONE`; `shadow_dispatcher._serialize_result` emituje `rule_verdict`. Bieżący, zredagowany odczyt logu: 15/15 świeżych rekordów miało pole. | Wiring istnieje, lecz małe okno i SPRI-04 wykluczają użycie jako oracle decyzji. |

### Rozbieżności zapisane jawnie

1. `ADR-001` i mapy nadal używają skrótu „R6=35/40 tier-aware”, natomiast
   `common.BAG_TIME_HARD_MAX_MIN`, `tests/test_inv_r6_dial_family.py` i nowszy
   inwariant rozdzielają płaski HARD 35 od capu eskalacji 40.
2. `common.BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN` ma fallback 30, lecz efektywny
   `flags.json` ma 90. FEAS-05 jest więc potwierdzony konfiguracyjnie na aktualnym
   runtime; jego skutek behawioralny 23/23 nadal wymaga R0.
3. `ENABLE_ETA_QUANTILE_R6_BAGCAP` ma default/fallback OFF, ale efektywnie jest ON.
   Kod `feasibility_v2.check_feasibility_v2` kalibruje `_gate_bt`, pozostawiając
   surowe `bag_time_min` w metryce. To jest aktywna różnica HARD-vs-telemetria.
4. `FEAS-03/04` w Audycie 360 nie miały ponownej weryfikacji snapshotu. Aktualny
   HEAD potwierdza symbole i testy osi, ale nie pełny skutek live; R0 pozostaje
   obowiązkowy.
5. Panel czyta `shadow_decisions.jsonl`, lecz nie konsumuje dziś `rule_verdict`;
   apka/courier-api nie ma bezpośredniego konsumenta R6/rule_verdict. Widoczność
   finalnego HARD dla człowieka i kuriera nie jest więc dowiedziona end-to-end.

## 0A. Mapa kompletności wartości R6

| Miejsce / symbol | Rola | Writer / consumer | Dotknięte przez przyszłe H1/H2 | Powód / test lub N-D |
|---|---|---|---|---|
| `common.BAG_TIME_HARD_MAX_MIN`, `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN`, `BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN` | Definicje diali 35, 40 i progu tier2 | writer konfiguracji; konsumenci niżej | TAK | `tests/test_inv_r6_dial_family` pilnuje rozdziału 35/40; lifecycle/effective-carrier musi zostać sprawdzony w T0/H1/H2. |
| `route_simulator_v2.r6_thermal_anchor` | Wspólna kotwica READY/picked-up oraz planowe czasy per-order | writer semantyki kotwicy; konsumowany przez feasibility i helpery SLA | TAK H1 | `tests/test_inv_r6_anchor_consistency` sprawdza delegację; fixture READY/in-bag musi mutation-probować każdą gałąź. |
| `feasibility_v2.check_feasibility_v2` (blok `r6_thermal_anchor`, `_gate_bt`, `r6_per_order_violations`) | Główny HARD; tworzy raw max/per-order oraz verdict | writer HARD i metryk | TAK H1 | Tu aktywna furtka p80 gold≤4. Testy: `test_feasibility_guards_behavioral`, `test_r6_anchor_v328`, `test_paczka_r6_exempt`. |
| `route_simulator_v2.compute_route_metrics` / plan `per_order_delivery_times` | Produkuje wartości planu i wektor per-order używany przez selection/firewall | writer metryk planu | TAK H1/PLAN | Musi zachować wspólną kotwicę i nie zamieniać wektora na sumę. `test_route_metrics`, `test_o2_capz_reseq_2026_07_02`. |
| `plan_recheck._r6_thermal_bag_min` oraz `_relax_carried_first`, `_reorder_noncarried_min_drive`, `_lex_committed_window_reorder` | Bliźniacze re-sekwencery, które mogą zaakceptować/odrzucić kolejność po decyzji | consumer R6, writer kolejności planu | TAK dopiero po H1, w PLAN/H2 | Efektywny READY-anchor fallback OFF; `test_carried_first_relax_ready_anchor_2026_06_29` dowodzi, że flaga zmienia decyzję. |
| `dispatch_pipeline._r6_soft_penalty` / `scoring.score_candidate` | SOFT koszt w strefie i agregacja score | consumer raw R6 | N-D dla H1; TAK tylko jeśli H2 zmienia ranking | N-D H1: scoring nie może definiować HARD. Test musi dowodzić, że zmiana score nie readmituje NO. |
| `core.selection.run_selection` (`_assert_feasibility_first`, cap 40, `_r6_breach_max`, redirect best-effort) | Feasible-first, potem least-damage/ALERT | consumer feasibility i metryk; writer zwycięzcy/reason/redirect | TAK H2 | `test_inv_feasibility_first`, `test_always_propose_on_saturation_2026_06_15`, `test_best_effort_r6_redirect_2026_05_26`; część starych testów jest strukturalna, więc wymagane behavior+mutation. |
| `core.invariant_firewall.evaluate_final` | Finalna obserwacyjna klasyfikacja R6/R27/SLA po selekcji | consumer planu/fleet; writer `RuleVerdict` | TAK D1/H1 | D1 musi zmienić pre-existing z VIOLATION na EXEMPT bez wpływu na wybór. `test_invariant_firewall`. |
| `dispatch_pipeline._attach_final_rule_verdict` | Wspólny lejek przyczepienia firewalla do `PipelineResult` | writer pola wynikowego | TAK D1/H1 | Fail-loud jako UNKNOWN; `test_invariant_firewall_wiring`. |
| Serializer A: `shadow_dispatcher._serialize_candidate` | Serializuje alternatywy i ich `r6_max_bag_time_min`/metryki | consumer Candidate.metrics; writer JSON alternatives | TAK D1/H1 | Parity z B wymagany; `test_serializer_location_b_parity` i `test_serializer_completeness_l11`. |
| Serializer B: `shadow_dispatcher._serialize_result` (best) | Serializuje zwycięzcę, plan, per-order, `rule_verdict`, redirect | consumer final result; writer JSON best/top-level | TAK D1/H1/H2 | `test_invariant_firewall_wiring`, `test_commit_divergence_verdict_gate_2026_05_27`. |
| `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` | Kanoniczny ledger decyzji | writer `shadow_dispatcher`; reader panel/tools | TAK D1/R0 | Metryka musi mieć coverage/mianownik i rotation-aware reader. Bieżące 15/15 dowodzi wiring, nie prawdy. |
| Panel `app.integrations.ziomek.feed`, `services.shadow_monitor` | Czyta ledger; monitor materializuje best-effort redirect | consumer logu, display/monitor | N-D dla zmiany HARD; TAK dla widoczności H2 | N-D H1: panel nie powinien liczyć HARD. Brak konsumpcji `rule_verdict` jest luką widoczności do jawnego zaprojektowania, nie pretekstem do render-patcha w D0. |
| Panel `frontend-shared/Ops12ZiomekFeed` | Powierzchnia człowieka | consumer feed | N-D D0/H1; kandydat H2/display | N-D: D0 nie zmienia UI. Przed H2 golden E2E musi dowieść `NO+ALERT` i odróżnić no-fleet. |
| Courier API i apka Kotlin | Powierzchnia kuriera/plan | brak bezpośredniego consumera `rule_verdict`/R6 znalezionego na HEAD | N-D dla H1/H2, chyba że decyzja wymaga ekspozycji | N-D: kurier nie powinien sam klasyfikować HARD. Jeśli produkt wymaga ALERT w apce, potrzebny osobny zatwierdzony kontrakt i golden cross-repo. |

Mapa rozdziela source-of-truth od display: brak konsumenta w panelu/apce nie
oznacza, że fix należy do renderu. Najpierw D1/R0/H1 ustalają prawdę, H2 dopiero
definiuje uczciwą ekspozycję least-damage.

## 1. Problem i siła dowodu

Audyt 360 utrzymał `A360-FEAS-01` jako jedyny P1 na granicy HARD R6. Finding jest
potwierdzony na HEAD i efektywnej fladze, ale jego wejście jest predykcją, nie
fizycznym outcome. Nie daje to prawa do samodzielnej zmiany semantyki biznesowej.
`A360-FEAS-02` ma status PARTIAL/P2. `A360-FEAS-03..05` są CONFIRMED/P2 w audycie,
ale nie zostały ponownie zweryfikowane na aktualnym snapshotcie; przed użyciem w
decyzji wymagają R0.

Kanon, który karta ma zachować:

- `35 min` to normalny HARD R6;
- `40 min` to odrębny poziom alarmowy/ratunkowy i cap selekcji least-damage,
  nie drugi limit HARD ani klasa kuriera;
- SOFT scoring i display nie mogą osłabić ani ukryć HARD;
- przy istniejącej flocie i braku feasible system ma pokazać least-damage jako
  `feasibility=NO` + jawny `ALERT`, a nie udawać zwykłego feasible;
- brak wartości nie oznacza automatycznie PASS;
- stary replay jest diagnostyką/PARTIAL, nie oraclem decyzji.

## 2. Rozdzielenie warstw

| Warstwa | Rola | Czego nie wolno jej robić |
|---|---|---|
| HARD feasibility | Policzyć R6 per zlecenie na zatwierdzonej kotwicy i sklasyfikować PASS/VIOLATION/UNKNOWN/EXEMPT. Normalna granica: `value > 35` = naruszenie; `35` przechodzi. | Nie może użyć score, klasy kuriera ani always-propose do zamiany naruszenia w PASS. |
| SOFT scoring/selekcja | Uszeregować kandydatów przechodzących HARD; przy pustej puli wskazać jawny least-damage. Cap 40 ogranicza/klasyfikuje ratunek, ale nie legalizuje termiki 35–40. | Nie może przepisać `feasibility=NO` na `MAYBE`, ukryć UNKNOWN ani przedstawić ratunku jako zgodnego z HARD. |
| Display/telemetria | Pokazać status reguły, wartość, limit, kotwicę, wyjątek i tryb `ALERT/least_damage`; zachować coverage i mianownik. | Nie może zmienić decyzji, zastąpić `None` zerem/PASS ani liczyć pre-existing jako nowe naruszenie decyzji. |

Aktualny `core/invariant_firewall.py` jest obserwacyjny (`A_SHADOW`,
`enforcement=NONE`, `policy_pending=B-01/B-02`). To właściwy kierunek zbierania
dowodu, ale `SPRI-04` wskazuje, że carried/pre-existing jest dziś adnotowane jako
VIOLATION zamiast EXEMPT; dane sprzed D1 nie mogą kalibrować enforcementu.

## 3. Decyzje wymagające ACK

### B-01 — zakres normalnego HARD R6

- **B-01A — płaski HARD 35 (wariant zgodny z obecnym kanonem):** każdy
  niezwolniony order z policzalnym wiekiem termicznym `>35` ma VIOLATION,
  niezależnie od tieru i always-propose. Gold/p80 nie zmienia statusu HARD.
- **B-01B — jawny wyjątek kalibrowany:** określona kohorta może dostać EXEMPT lub
  osobny status polityki, ale nigdy cichy PASS. Wymaga fizycznego outcome,
  precyzyjnej definicji kohorty, kosztu false-pass i osobnego ACK.

Ta karta nie wybiera B-01. `FEAS-01` pokazuje konflikt; nie dowodzi, że wyjątek
jest dobry ani że należy go natychmiast usunąć.

### B-02 — semantyka UNKNOWN, carried i least-damage

- **B-02A — fail-closed klasyfikacyjnie:** `None`/brak planu/danych = UNKNOWN,
  nigdy PASS; istniejąca flota nadal dostaje least-damage `ALERT`, ale wynik nie
  jest auto-egzekwowalny.
- **B-02B — rozróżnienie odpowiedzialności decyzji:** naruszenie już istniejące
  w niesionym worku, którego nowa decyzja nie pogarsza, ma EXEMPT/PRE_EXISTING;
  pogorszenie lub nowe naruszenie ma VIOLATION. Wymaga kontrfaktyku bez nowego
  zlecenia oraz D1.
- **B-02C — brak floty:** NOT_APPLICABLE/no-fleet, bez fikcyjnego kandydata.

Ta karta nie wybiera B-02. Warianty muszą zostać rozstrzygnięte po T0, D1 i R0.

### Dokładne pytania do Adriana

**B-01 — co oznacza zatwierdzony HARD R6?**

1. Czy dla każdego niezwolnionego zlecenia gastronomicznego `35.0` jest ostatnią
   wartością PASS, a każda surowa wartość `>35.0` ma pozostać VIOLATION niezależnie
   od klasy kuriera, p80 i always-propose (**B-01A**)?
2. Czy gold≤4/p80 ma być dopuszczonym wyjątkiem (**B-01B**)? Jeśli tak: czy status
   ma być EXEMPT (jawny wyjątek), czy odrębny status; jaka dokładnie kohorta,
   źródło czasu, data wygaśnięcia i maksymalny akceptowany false-pass?
3. Czy paczki firmowe pozostają EXEMPT od termiki R6, a czasówki gastronomiczne
   podlegają tej samej granicy 35? Odpowiedź musi nazwać wyjątki, nie tylko próg.
4. Czy kotwica ma być: READY dla nieodebranego oraz faktyczny `picked_up_at` dla
   already-in-bag, a brak wymaganej kotwicy ma dawać UNKNOWN zamiast fallbacku
   fabrykującego PASS?

Konsekwencja B-01A: czystszy HARD i więcej jawnych ALERT/false-rejectów przy
pesymistycznej ETA. Konsekwencja B-01B: potencjalnie więcej uratowanych zleceń,
ale zaakceptowane ryzyko false-pass i konieczność jawnego EXEMPT/provenance.

**B-02 — jak always-propose ma działać po naruszeniu lub braku danych?**

1. Gdy flota istnieje, lecz 0 kandydatów przechodzi HARD, czy system zawsze ma
   zwrócić jeden least-damage jako `feasibility=NO` + `ALERT`, także powyżej 40,
   czy `>40` ma być odrębnym alarmem wymagającym ręcznej akcji bez zwykłej
   propozycji?
2. Czy 40 jest inkluzywnym capem ratunkowej strategii (`<=40`), a 40.1 przechodzi
   do następnego poziomu alarmu, nigdy do PASS?
3. Czy `None` dla wartości/kotwicy/planu ma zawsze dawać UNKNOWN i blokować
   automatyczne enforcement, przy zachowaniu uczciwego ALERT dla operatora?
4. Czy carried/pre-existing `>35`, którego nowa decyzja nie tworzy ani nie
   pogarsza, ma być EXEMPT, a tylko CAUSED/WORSENED ma być VIOLATION? Jaki
   minimalny wzrost (ściśle `>0`, tolerancja techniczna) rozdziela te klasy?
5. Czy strategia tier2 oznacza „poczekaj na pierwszego wolnego”; jeśli tak, jaki
   zatwierdzony limit `free_at` obowiązuje: 30, 90 czy wartość wyznaczona dopiero
   przez R0? Efektywne 90 nie może zostać uznane za decyzję przez sam fakt LIVE.
6. Czy jedynym legalnym brakiem kandydata pozostaje `no_fleet`, odrębny od
   `fleet_but_no_feasible`?

Konsekwencja B-02 „zawsze ALERT”: pełna świadomość operacyjna, ale większe ryzyko
normalizacji alarmów. Konsekwencja osobnego `>40/manual-only`: mocniejsza bariera
bezpieczeństwa, ale większe ryzyko opóźnienia i operacyjnej ślepoty, jeśli UI nie
odróżni tego od no-fleet. Konsekwencja UNKNOWN fail-closed klasyfikacyjnie:
mniej autonomii przy słabych danych, za to brak cichego kłamstwa.

## 3A. Wykonywalna specyfikacja fixture/golden — bez implementacji

Docelowy plik fixture może być JSON/JSONL, ale każdy rekord musi być czystym,
syntetycznym `WorldState` bez PII/GPS realnych osób i zawierać:

```text
case_id
now_utc
flags_snapshot                 # jawne wartości wszystkich relewantnych flag
order_event                    # typ, status, READY, package/czasowka
fleet[]                        # 0, 1 lub >=2 kandydatów; bag per order
candidate_plan                 # pickup_at, predicted_delivered_at, per_order_delivery_times
expected.hard_status           # PASS/VIOLATION/UNKNOWN/EXEMPT/NOT_APPLICABLE
expected.hard_value/limit/source/order_id
expected.selection             # feasible | least_damage_alert | no_fleet
expected.feasibility_verdict   # MAYBE albo NO
expected.rule_verdict_coverage
expected.serializer_A/B_fields
expected.panel_contract        # tylko H2: ALERT/no-fleet; bez recompute HARD
oracle_method                  # niezależne wyliczenie, nie helper produkcyjny
```

Minimalny korpus ma zawierać osobne rekordy, nie parametry ukryte w jednym teście:

- `r6_raw_34_9`, `r6_raw_35_0`, `r6_raw_35_1`;
- `rescue_39_9`, `rescue_40_0`, `rescue_40_1`;
- `missing_ready_not_picked`, `missing_plan`, `missing_per_order_value`;
- `two_orders_20_20`, `two_orders_20_35_1`;
- `ready_35_1_inbag_5_not_picked` oraz bliźniaczy `already_picked`;
- `fleet_zero`, `fleet_present_zero_feasible`, `fleet_mixed_feasible`;
- `preexisting_40_unchanged`, `preexisting_40_worsened`;
- `gold4_quantile_off/on` na identycznym świecie;
- `tier2_free_29_9/30_0/30_1` i `89_9/90_0/90_1` z jawnym snapshotem flag.

### Kryteria falsyfikacji

Specyfikacja lub przyszły wariant odpada, jeżeli choć jedno zachodzi:

- 35.1 dostaje zwykły PASS bez jawnego, zatwierdzonego EXEMPT;
- 40/40.1 zmienia status HARD z VIOLATION na PASS;
- `None` staje się 0/PASS albo znika z mianownika;
- suma/średnia worka ukrywa pojedyncze przekroczenie per-order;
- feasibility i plan_recheck różnie klasyfikują ten sam frozen plan/kotwicę;
- SOFT score readmituje `feasibility=NO` jako zwykłego feasible;
- istniejąca flota + 0 feasible kończy się cichym brakiem zamiast jawnego trybu;
- `no_fleet` i `fleet_but_no_feasible` mają ten sam reason/status;
- serializer A/B gubi value/limit/source/status albo panel usuwa ALERT;
- EXEMPT pre-existing jest liczony jako nowe VIOLATION decyzji;
- paired OFF/ON nie raportuje efektywnej flagi lub OFF/OFF jest niestabilne na
  polach krytycznych.

### Obowiązkowe mutation probes

1. HARD: `>`↔`>=` na 35 oraz 35→40.
2. Rescue: `<=40`↔`<40`, usunięcie capu i 40→35.
3. Missing: `None→0`, `None→safe`, usunięcie missing_reason.
4. Agregacja: per-order/max→sum/average.
5. Kotwica: READY→plan pickup/in-bag oraz picked_up→READY.
6. Layering: pominięcie `_assert_feasibility_first` lub NO→MAYBE po selection.
7. Always-propose: usunięcie `ALERT`, `feasibility=NO` albo rozróżnienia no-fleet.
8. D1: PRE_EXISTING EXEMPT→VIOLATION i CAUSED VIOLATION→EXEMPT.
9. Serializery: usunięcie pola z A albo B oraz zamiana `rule_verdict` na string.
10. Carrier: wymuszenie fallbacku 30 przy efektywnym 90 i odwrotnie.

Każda mutacja ma zaczerwienić co najmniej jeden test behawioralny; test wyłącznie
tekstowy nie wystarcza. Probe wykonuje się dopiero po T0 i po commitowaniu
implementacji właściwego etapu, z pełnym odtworzeniem czystego drzewa po próbie.

## 4. Macierz golden cases

Legenda oczekiwań: `P`=PASS, `V`=VIOLATION, `U`=UNKNOWN, `E`=EXEMPT,
`NA`=NOT_APPLICABLE. „Aktualnie” oznacza zachowanie potwierdzone kodem/testem;
gdy finding nie był reverified, wpis mówi to jawnie.

| ID / przypadek | Aktualne zachowanie potwierdzone w kodzie/testach | Wariant B-01/B-02 do decyzji | Oczekiwany golden | Skutek biznesowy | Koszt uboczny | Wymagany oracle i metryka | Mutation tripwire | Kill-switch / rollback | Zależność |
|---|---|---|---|---|---|---|---|---|---|
| FEAS-01: 34.9 | `value > 35` jest warunkiem naruszenia; 34.9 przechodzi. | B-01A/B | HARD `P`; brak ALERT z samej R6. | Legalna trasa nie jest fałszywie blokowana. | Błąd ETA blisko progu może dać false-pass. | Frozen plan + niezależne ręczne wyliczenie wieku; `hard_pass_rate`, outcome breach i coverage. | Mutacja `>`→`>=` musi zaczerwienić 35.0, nie 34.9. | H1 default-OFF; OFF przywraca obecne zachowanie; revert osobnego commita. | T0→D1/R0→H1 |
| FEAS-01: 35.0 | Test firewalla potwierdza brak violation przy dokładnie 35. | B-01A/B | HARD `P` (granica inkluzywna). | Stabilna, jednoznaczna granica. | Zaokrąglenie może ukryć 35.001; firewall używa raw predicted elapsed. | Golden raw 35.000 oraz serializer bez zaokrąglenia decyzyjnego. | `>`→`>=` musi zabić test. | Jak wyżej. | T0→D1/R0→H1 |
| FEAS-01: 35.1 | Kodowy dial 35 daje violation; efektywna furtka gold≤4 może jednak kalibrować wartość bramki p80. | B-01A: zawsze `V`; B-01B: tylko jawny EXEMPT, nigdy cichy P. | `V`; jeżeli zatwierdzony wyjątek, osobny `E` z provenance. | Chroni świeżość i uczciwość HARD. | Możliwy false-reject przy pesymistycznej ETA; always-propose nadal pokazuje ratunek. | R0 na tym samym world record OFF/ON + fizyczny pickup/delivery outcome; false-pass/false-reject, net R6, coverage. | Neutralizacja furtki lub polaryzacji musi zmienić golden; OFF↔OFF control. | Istniejąca furtka ma własny kill-switch; H1 nie tworzy drugiej flagi bez potrzeby. | T0→D1/R0→ACK→H1 |
| FEAS-01/02: 39.9 | Termicznie >35, więc nie-feasible; 40 jest tylko capem eskalacji. | B-01A + B-02A/B. | HARD `V`; przy 0 feasible i flocie: least-damage `ALERT`, `feasibility=NO`. | Koordynator widzi ratunek bez kłamstwa o HARD. | Więcej ALERT-ów; możliwe zmęczenie operatora. | Known-answer z co najmniej 2 kandydatami; precision ALERT, rate least-damage, outcome regret. | Usunięcie tagu ALERT albo zmiana NO→MAYBE musi zaczerwienić. | H2 default-OFF; rollback nie wyłącza always-propose, tylko nową klasyfikację/cap. | T0→D1/R0→H1→PLAN→H2 |
| FEAS-02: 40.0 | `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` jest odrębny i luźniejszy od 35; test pilnuje rozdzielenia. | B-02 musi określić inkluzywność capu. | HARD `V`; ratunkowy cap akceptuje dokładnie 40 tylko jako `ALERT/least_damage`. | Maksymalny jawnie dopuszczony ratunek. | Ryzyko normalizacji alarmu jako zwykłego wyniku. | Golden graniczny z raw 40.000; metryka `alert_at_cap`, outcome breach/regret. | `<=40`→`<40` musi odróżnić 40.0; 40 nie może zmienić HARD na P. | H2 kill-switch OFF; revert H2. | T0→D1/R0→ACK→H2 |
| FEAS-02: 40.1 | FEAS-02 opisuje fail-open/fallback bez capu jako PARTIAL; ścieżka pozostaje jawnie NO/ALERT, ale pełny kontrakt wymaga R0. | B-02A: UNKNOWN lub least-damage poza capem z osobnym alarmem; zakaz PASS. | HARD `V`; nigdy zwykły feasible. Ostateczny typ ratunku wymaga ACK. | Nie ukrywa ekstremalnego naruszenia; system nadal nie milczy przy flocie. | Brak „akceptowalnego” kandydata może wymagać eskalacji ręcznej. | R0 z kandydatem ≤40 i >40; metryka `over40_alert_rate`, realizowany worst-R6 i brak silent no-candidate. | Usunięcie capu/fallback raw bez tagu musi zaczerwienić. | H2 OFF przywraca obecne jawne best-effort; nie wyłączać globalnie always-propose. | T0→D1/R0→ACK→H2 |
| FEAS-02: `None` per-order | Firewall daje UNKNOWN/PARTIAL przy braku metadanych/planu; finding wskazuje historyczne fail-open best-effort. | B-02A. | `U`, coverage PARTIAL/NONE; nigdy P ani zero. Przy flocie można pokazać ALERT „brak danych”. | Uczciwa widoczność luki danych. | Więcej ręcznych decyzji; mniej automatyzacji. | Golden z brakującym POD/predicted/metadata; `unknown_rate`, reason coverage, input-miss osobno od decision-diff. | `None→0`, `None→safe`, pominięcie missing_reason muszą zabić test. | Instrumentacja D1 revertowalna; enforcement pozostaje OFF. | T0→D1→R0→H1/H2 |
| FEAS-02: per-order 20+20 vs „suma 40” | Firewall i feasibility operują per-order/max; sama suma worka nie jest wiekiem termicznym pojedynczego jedzenia. FEAS-02 wskazuje rozjazd proxy sum≠per-order. | B-01A: HARD na per-order; suma wyłącznie osobna metryka obciążenia. | Oba ordery `P`; suma 40 nie daje R6 V. | Nie odrzuca legalnego worka przez błędne dodawanie niezależnych wieków. | Może nie uchwycić wspólnego ryzyka pojemności — to osobna reguła, nie R6. | Plan z dwoma znanymi kotwicami; porównanie wektora per-order, max i sum; `per_order_coverage=100%`. | Zamiana `max`/per-order na `sum` musi zaczerwienić. | H1 OFF; wspólna stała 35, bez nowego progu sumy. | T0→D1/R0→H1 |
| FEAS-02: per-order 20+35.1 | Jeden order przekracza 35 mimo sumy/średniej. | B-01A. | Jeden `V`, drugi `P`; final status V; order_id wskazuje winowajcę. | Precyzyjna diagnoza i właściwy least-damage. | Alert może dotyczyć carried, a nie skutku nowego orderu — D1 musi rozdzielić E/V. | Golden wektorowy + kontrfaktyk bez nowego orderu; violation count per order. | Uśrednienie lub agregat bez order_id musi zaczerwienić. | D1/H1 osobne rollbacki. | T0→D1/R0→H1 |
| FEAS-04: READY 35.1, in-bag 5 | Feasibility używa kotwicy READY; trzy re-sekwencery mają ścieżkę in-bag przy `ENABLE_CARRIED_FIRST_RELAX_READY_ANCHOR=OFF`. Finding nie był reverified; test potwierdza, że flaga zmienia decyzję i `None` ready spada do in-bag. | B-01 musi zatwierdzić jedną kotwicę; kanon wskazuje READY dla nieodebranego i picked_up dla carried. | Nieodebrany READY: `V`; już odebrany: wiek od rzeczywistego picked_up. Ta sama trasa nie może zmienić klasy między bliźniakami. | Koniec ukrywania wieku przez odroczenie odbioru. | READY może być niepewnym/proxy czasem; potrzebna jakość źródła. | R0 ten sam world w feasibility i 3 re-sekwencerach; twin divergence=0, source coverage. | Wyłączenie ready-anchor w jednym z 3 bliźniaków musi zabić parity test. | Preferować jeden istniejący kill-switch; rollback OFF + revert wspólnego commita. | T0→D1/R0→H1→PLAN→H2 |
| FEAS-04: READY `None`, in-bag znane | Kod testowy pokazuje fallback do in-bag niezależnie od flagi; brak READY nie może być traktowany jako gotowe teraz. | B-02A/B. | Dla already-picked: policz od picked_up; dla not-picked bez READY: `U`. | Zachowuje carried truth, nie fabrykuje wieku nowego orderu. | Więcej UNKNOWN dla słabego ingestu. | Dwa goldeny rozdzielające status; unknown/source coverage. | `None ready→now` lub `0` musi zaczerwienić. | D1/H1 OFF. | T0→D1/R0→H1 |
| FEAS-03/05: istnieje flota, 0 feasible | Efektywne always-propose jest ON. Kod wybiera best-effort z `feasibility=NO`; firewall test dowodzi, że always-propose nie ukrywa violation. FEAS-03/05 wymagają ponownej weryfikacji progów/tier2 w R0. | B-02A/B; osobno ACK dla semantyki strategii 2/3. | Zawsze jeden jawny least-damage `ALERT`, status reguły V/U/E zachowany; brak zwykłego P. | Brak operacyjnej ślepoty „brak kandydatów”. | Ryzyko, że operator potraktuje ALERT jak rekomendację normalną; potrzebny framing. | Golden z ≥1 pracującym kandydatem i 0 feasible; `silent_no_candidate=0`, ALERT coverage=100%, realized regret. | Usunięcie ALERT, ukrycie NO, wybór kandydata gorszego wg zatwierdzonego lex musi zaczerwienić. | H2 OFF/revert; globalnego always-propose nie flipować w D0. | T0→D1/R0→H1→PLAN→ACK→H2 |
| FEAS-03: brak floty | ADR-003 dopuszcza jedyny legalny brak przy 0 floty; firewall bez selected plan daje NA/coverage NONE zależnie od reason. | B-02C. | `NA/no_fleet`, bez fikcyjnego kandydata i bez least-damage. | Panel może uczciwie zablokować zamówienie. | Utrata zamówienia jest jawna, ale nie ma bezpiecznej alternatywy. | Golden fleet=[] vs fleet istnieje lecz 0 feasible; metryki muszą rozdzielać mianowniki. | Zlanie `no_fleet` z `no_feasible` musi zabić test. | Brak flipa; H2 rollback nie zmienia no-fleet. | T0→D1/R0→H2 |
| FEAS-05: tier2 free-at 29.9/30/30.1 oraz historyczne 90 | Kodowy default na tym HEAD wynosi 30, natomiast finding mówi o efektywnym 90 i nie był reverified; nie wolno przyjąć żadnej wartości bez provenance procesu. | B-02: określić, czy tier2 to defer do pierwszego wolnego i jaki ma mieć limit; nie mieszać z R6 40. | Do R0: oczekiwanie UNKNOWN/PENDING. Golden ma przypiąć 29.9/30/30.1 i osobno 89.9/90/90.1 na efektywnej konfiguracji. | Rozróżnia realny defer od saturacji telemetrii. | Zbyt niski próg częściej wpada w alarm; zbyt wysoki czyni tier2 tautologią. | Efektywna wartość z procesu + frozen cases; rozkład tier1/2/3 nie może być 100% jednej klasy bez uzasadnienia. | 30↔90 i `<`↔`<=` muszą zmienić znane przypadki. | Istniejący klucz, jeśli jest pełnym carrierem; H2 nie tworzy duplikatu. | T0→R0→D1→ACK→H2 |

## 5. Minimalny kontrakt oracle R0/D1

R0 i D1 muszą dostarczyć łącznie:

1. zamrożony `WorldState` z jawnym `now`, efektywnymi flagami i provenance;
2. pełny wektor per-order: status, READY, picked_up, predicted delivery, typ/paczka;
3. niezależne ręczne/brute wyliczenie wieku termicznego, nie przez ten sam helper;
4. oddzielne klasy `input_miss`, `OSRM_miss`, `soft_diff`, `hard_diff`;
5. mianownik, coverage, freshness i listę reasonów UNKNOWN/EXEMPT;
6. kontrfaktyk bez nowego orderu dla rozdzielenia PRE_EXISTING od CAUSED/WORSENED;
7. parę OFF/ON na tym samym rekordzie, w obu kolejnościach, plus OFF/OFF control;
8. mutation probes: polaryzacja 35, inkluzywność 40, `None→PASS`, per-order→sum,
   READY→in-bag, usunięcie ALERT i zlanie no-fleet z no-feasible;
9. fizyczny outcome dla oceny false-pass/false-reject; klik może być tylko jawnie
   oznaczonym proxy;
10. dane po D1, ponieważ rekordy sprzed poprawnego EXEMPT/VIOLATION nie mogą być
    podstawą kalibracji enforcementu.

## 6. Metryki bramkujące przyszłą decyzję

- `r6_rule_coverage` i `unknown_rate` z mianownikiem wszystkich wybranych planów;
- `r6_violation_rate` osobno dla new/READY, carried/pre-existing i least-damage;
- `exempt_rate` z reasonami oraz `caused_or_worsened_rate`;
- `alert_coverage` dla `feasibility=NO` (cel 100% widoczności, nie cel liczby ALERT);
- `silent_no_candidate_rate` rozdzielone na `no_fleet` i `fleet_but_no_feasible`;
- false-pass/false-reject względem zatwierdzonego physical/proxy oracle;
- Pareto: świeżość/R6 kontra pickup delay, liczba uratowanych zleceń i regret;
- twin divergence feasibility↔selection↔plan_recheck oraz serializer A↔B.

Żadna pojedyncza liczba ze starego replayu nie spełnia tego kontraktu.

## 7. Kolejność i bramki

1. **T0 TEST-TRUTH:** pełny default i STRICT 0 failed, syntetyczne źródła flag/state.
2. **D1 FIREWALL-EXEMPT-TRUTH:** EXEMPT ≠ VIOLATION; obserwacja bez enforcementu.
3. **R0 REPLAY-TRUTH:** oracle z §5 i metryki z §6.
4. **Decyzja B-01/B-02 + jawny ACK Adriana.** Bez niej STOP.
5. **H1 R6-HARD:** wyłącznie zatwierdzona semantyka, default-OFF kill-switch,
   golden 34.9/35/35.1 i 39.9/40/40.1, pełna regresja, ON/OFF i 2 dni obserwacji.
6. **PLAN-INTEGRITY:** przed H2, aby selekcja nie opierała się na rozjechanym planie.
7. **H2 BEST-EFFORT:** zachować always-propose jako jawny ALERT/least-damage;
   osobny commit, kill-switch, replay, ACK i rollback.

## 8. Rollback przyszłych etapów

D0 nie wykonuje rollbacku live, bo niczego nie zmienia. Dla przyszłych etapów
minimalny kontrakt to: kill-switch OFF → potwierdzenie fingerprintu → revert
wyłącznie zatwierdzonego commita H1 albo H2 → jeden kontrolowany restart za ACK →
PID/NRestarts/health/log/smoke. H1 i H2 muszą mieć oddzielne commity i flagi lub
jedną istniejącą flagę o udowodnionym, jednoznacznym zakresie; nie wolno wyłączać
always-propose tylko po to, by wycofać klasyfikację ALERT.

## 9. Werdykt D0

Karta jest gotowa do wypełnienia dowodami T0/D1/R0, ale **nie jest gotowa do
wyboru wariantu B-01/B-02**. Aktualny stan uzasadnia utrzymanie rozdziału:
`35 HARD` kontra `40 ALERT/least-damage`; nie uzasadnia implementacji, enforcementu,
flipa ani zmiany istniejącej semantyki bez oracle i jawnego ACK.

## 10. Wykonanie i dowody D0

- Bootstrap i ETAP 0 wykonane read-only; aktualny handoff z 11.07 10:06 UTC
  został uwzględniony.
- Kod/testy/runtime czytano bez zmian. Nie wykonano flipa, migracji, deployu,
  restartu ani zapisu danych live.
- Celowany zestaw mapy po jej uzupełnieniu: **67 passed w 6.14 s**:
  dial 35/40, wspólna kotwica R6, firewall+wiring, feasibility-first,
  always-propose, best-effort redirect, READY/in-bag oraz serializer A/B.
- `tools/flag_lifecycle_check.py`: **504 flagi, kuracja 504/504, 0 błędów**.
- Wcześniejsza pełna suita base: **4849 passed, 1 failed, 24 skipped,
  10 xfailed**; jedyny TEST-11 należy do T0 i pozostaje blockerem H1/H2.
- `git diff --check`: bez błędów.
- Jedyny plik zmieniony przez D0 to ten raport. Wspólny backlog, pamięć, kanon,
  handoff oraz chroniony `CLAIM_LEDGER_HARD_GATE_CARD.md` pozostały nietknięte.
- Rollback D0: revert pojedynczego commita dokumentacyjnego; brak rollbacku
  runtime, ponieważ runtime nie zmieniono.
