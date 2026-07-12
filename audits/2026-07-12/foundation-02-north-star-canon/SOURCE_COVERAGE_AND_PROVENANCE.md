# Pokrycie źródeł i provenance

Wygenerowano w izolowanym worktree 2026-07-12. Pokrycie jest pełne dla centralnych źródeł North Star i wskazanych konfliktów, lecz nie oznacza masowego przeczytania wszystkich testów, EOD, archiwów ani danych runtime.

## Preflight Promptu 01

- Worktree źródłowy: `/root/codex_audit_prompt01_20260712T140957Z`.
- Branch: `codex/audit-prompt-01-20260712T140957Z`.
- Commit artefaktów: `14e7a5e32346edea719eb33261c97bd06c01cd16`, czas 2026-07-12T15:02:29Z.
- Parent: dokładnie `c7de9f29127851a59507fac92d14f328336afe61`.
- Worktree był clean; commit zawierał wyłącznie osiem artefaktów Promptu 01.
- `BASELINE_MANIFEST.json` parsował się; `KNOWLEDGE_SOURCE_REGISTER.jsonl` parsował się jako 82 unikalne rekordy `KSP-001..KSP-082`.
- Werdykt: `PARTIAL`; brama: `READY` wyłącznie dla read-only rekonstrukcji North Star/kanonu.

| Artefakt | SHA-256 zweryfikowany w preflight |
|---|---|
| `BASELINE_REPORT.md` | `330a1e5e306b5ad684936c8e7a6a91984eaf3b84ced0d7cb9df7560fadf5ae49` |
| `BASELINE_MANIFEST.json` | `46f5a89359d72fc2327fe6ef3567a942b939fa53014be5f6476c667936f5f438` |
| `KNOWLEDGE_SOURCE_REGISTER.md` | `97fa9570d9a36847b7028a4929bdff931470bfc8da119acc7e284341e2b39b7b` |
| `KNOWLEDGE_SOURCE_REGISTER.jsonl` | `2ab2c886834ba7c987d543af0d9f7c4a1bafe99e3522bb8ff238695fcd171701` |
| `COMMAND_AND_SIDE_EFFECT_REGISTER.md` | `4d5bb0f0c6286ba67dba2ad97a59fb2b7e7b4fa4f92451f446a470b6eabe909a` |
| `ASSET_AND_DATA_REGISTER.md` | `a4431d9a2d8a8cbff1e21a743c5585ef7be1016419f4699053a46760894988b5` |
| `OPEN_RISKS_AND_UNKNOWNS.md` | `3dee4fff46dcb66b13802127af6ded50720e5db0f4adc78e3cb24dc7fe7cee9d` |
| `PROMPT_02_ENTRY_GATE.md` | `cc1fc0ca030cd48e00ee5c6b43dd301cc480d9dcf3741f06a7d6933565290be5` |

## Osie czasu

| Oś | SHA / czas | Klasyfikacja i użycie |
|---|---|---|
| Frozen code/docs | `c7de9f29127851a59507fac92d14f328336afe61`, 2026-07-12T14:00:15Z | Jedyna oś `IMPLEMENTED_AT_BASELINE`. |
| Późniejszy commit docs | `c1b576e96365fc6895c0d8b1f3d35a8185a78870`, 2026-07-12T14:08:54Z | `POST_BASELINE`; zmienia tylko backlog i kartę GRF-02/near-miss, nie został włączony do baseline. |
| Master w preflight Promptu 02 | `b96c480b8491d5a7e33e6e1d7e7f5522fe900f54`, 2026-07-12T15:17:52Z | `POST_BASELINE`; dalszy docs/live-deploy zapis GRF-02, nie źródło North Star. |
| Refactor branch docs | `a359e909aa99e18634db97336a2c36213bdaf09b`, 2026-07-06T22:01:42Z | Jawne branch-scoped źródło architektury/refaktoru; fakty sprawdzane na c7. |
| Memory snapshot | `ca55742b969404663dff80e4ec2aadfe3c64bdbf`, 2026-07-12T14:11:49Z | Oś intencji/pamięci; nieatomowa z kodem. |
| Memory HEAD w preflight | `d4b2d333905b58b1a29e1a9fdc85e73f0e5b6086`, 2026-07-12 po snapshotcie | `POST_BASELINE`; diff dotyczył bieżących GRF-02/C55 handoffów/protokołu, nie został automatycznie uznany za intencję. |
| Runtime Prompt 01 | 2026-07-12 14:09–14:45 UTC, artefakty `14e7a5e` | Wyłącznie `EFFECTIVE_LIVE_AT_TIME`; selektywne, nie pełny fingerprint wszystkich reguł. |

Diff `c7de9f2..c1b576e` i `c1b576e..b96c480` został sprawdzony ścieżkowo i sklasyfikowany przed syntezą. Żadnego materiału `POST_BASELINE` nie przeniesiono po cichu do frozen implementation.

## Katalog roboczy Promptu 02

- Worktree: `/root/codex_audit_prompt02_20260712T153736Z`.
- Branch: `codex/audit-prompt-02-20260712T153736Z`.
- Base/HEAD przy utworzeniu: `c7de9f29127851a59507fac92d14f328336afe61`.
- Katalog wyjściowy: `audits/2026-07-12/foundation-02-north-star-canon/`.
- Oryginalny repo przed pracą: master `b96c480...`, zastany dirty tylko `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`; nie dotknięto.
- Repo pamięci przed pracą: HEAD `d4b2d333...`, clean; nie dotknięto.
- Istniejące tmux/worktree potraktowano jako cudzą własność; Prompt 01 użyto wyłącznie read-only.

## Rejestr źródeł użyty przez ledger

| Source ID | Dokładne źródło / snapshot | Ostatnia istotna zmiana znana | Typ prawdy i sposób użycia |
|---|---|---|---|
| `SRC-OWNER-P02` | bieżący Prompt 02, 2026-07-12 | session current | Najwyższa intencja właściciela i granica zadania. |
| `SRC-P01` | komplet artefaktów Promptu 01, commit `14e7a5e` | 2026-07-12T15:02:29Z | Preflight, register, risk/asset/side-effect evidence. |
| `SRC-P01-RUNTIME` | report/manifest Promptu 01 `14e7a5e` | obserwacja 14:09–14:45Z | Tylko selektywny `EFFECTIVE_LIVE_AT_TIME`. |
| `SRC-MEM-CANON` | `memory:ZIOMEK_REGULY_KANON.md@ca55742b` | content commit `83912f913336d6d1ccb01cfc3aed6b41cedc959c`, 2026-07-03T13:01:57Z | Główne źródło utrwalonych owner verdicts. |
| `SRC-MEM-OVERVIEW` | `memory:project_overview.md@ca55742b` | `dd2971cd0f99c68f83484a792871f4da3add569a`, 2026-05-31T11:28:04Z | `HISTORICAL_INTENT`, nigdy status live. |
| `SRC-MEM-PRIORITIES` | `memory:priorytety-stabilnosc-jakosc-skala-2026-07-03.md@ca55742b` | `02cb28e7597279325bc5372051a1f2185d8dbae1`, 2026-07-04T09:48:55Z | Owner directive o hierarchii jakości/stabilności/skali. |
| `SRC-MEM-FEEDBACK` | `memory:feedback_rules.md@ca55742b` + tematyczne feedback files | główny plik `34b5772a81622b29248318bfd477076b9f6f14fc`, 2026-07-05T19:37:57Z | Korekty właściciela; tylko z datą/scope i krzyżowym sprawdzeniem. |
| `SRC-MEM-LESSONS` | `memory:lessons.md@ca55742b` | `c2f958fdae72fc0f872f5bc3fb5d2545c53c9441`, 2026-07-08T07:46:29Z | Chronologia korekt learning/outcome oraz owner-correction R6 ready-anchor; nie samodzielny runtime proof. |
| `SRC-MEM-PROTOCOL` | `memory:ziomek-change-protocol.md@ca55742b` | `b835905bfe576b54a474d642ad40cca8ff0d7a03`, 2026-07-12T14:10:43Z | Normatywny protokół zmiany, utrwalania klas błędów i zapis ready-anchor, który pozostaje w konflikcie z innym sformułowaniem kanonu. |
| `SRC-DOD` | `ZIOMEK_DEFINITION_OF_DONE.md@c7de9f2` | `542dfa16d4078ee845e742adc9e8ea8ca092f8a7`, 2026-07-03T12:55:45Z | Normatywne kryteria engineering evidence/rollback. |
| `SRC-ARCH` | `ZIOMEK_ARCHITECTURE.md@c7de9f2` | `e07cfb47e98293bd1adc91214c56446395dc5ca7`, 2026-07-06T21:29:33Z | Zatwierdzona konstrukcja docelowa; stale R6 facts odseparowane. |
| `SRC-INVAR` | `ZIOMEK_INVARIANTS.md@c7de9f2` | `62f54bd9cd4d6613da6c9e681cf8f0b0d0b33651`, 2026-07-08T17:37:09Z | Normatywne invariant slots i jawne statusy luk. |
| `SRC-ADR-001` | `docs/decisions/ADR-001-pipeline-hard-przed-soft.md@c7de9f2` | ADR family commit `651244e068c4510f02d3dd2e327334aafde1982b`, 2026-07-03T12:28:20Z | HARD/SOFT intencja; fraza R6 35/40 oznaczona stale. |
| `SRC-ADR-002` | `docs/decisions/ADR-002-shadow-first-flip-za-ack.md@c7de9f2` | `651244e068c4510f02d3dd2e327334aafde1982b`, 2026-07-03T12:28:20Z | Shadow/measurement/ACK; używany jako składnik syntezy bezpiecznego awansu. |
| `SRC-ADR-003` | `docs/decisions/ADR-003-always-propose.md@c7de9f2` | `651244e068c4510f02d3dd2e327334aafde1982b`, 2026-07-03T12:28:20Z | Normatywny Always-propose, sprawdzony z kanonem/kodem. |
| `SRC-ADR-004` | `docs/decisions/ADR-004-flagi-trzy-swiaty.md@c7de9f2` | `651244e068c4510f02d3dd2e327334aafde1982b`, 2026-07-03T12:28:20Z | Normatywny kontrakt rozdzielenia registry/config/process effective. |
| `SRC-ADR-008` | `docs/decisions/ADR-008-rdzen-nie-przenoszony.md@c7de9f2` | `651244e068c4510f02d3dd2e327334aafde1982b`, 2026-07-03T12:28:20Z | Time-scoped historical intent, nie bieżący fakt layout. |
| `SRC-FLAGREG` | `tools/flag_lifecycle_registry.json@c7de9f2` | `a860c538126d0dcb960e17ebcbe3cf31ba77fa9d`, 2026-07-11T10:31:03Z | Machine registry/snapshot; nigdy process-effective bez P01. |
| `SRC-CODE-C7` | selektywne code/tests na dokładnym `c7de9f2` | frozen 2026-07-12T14:00:15Z | Wyłącznie `IMPLEMENTED_AT_BASELINE`. |
| `SRC-REFACTOR` | 21 plików `docs/refaktor/**@a359e909` | 2026-07-06T22:01:42Z | Branch-scoped ADR/diagnoza/raport; implementacja sprawdzana na c7. |
| `SRC-ETA-01-05` | `docs/eta/01..05@c7de9f2` | wspólny content commit `971c40a37fe8aff5e8fb3eb2f85e639a8a3f194e`, 2026-07-08T21:02:46Z | Jedna zależna lineage badań/projektu/walidacji, częściowo historyczna. |
| `SRC-ETA-06` | `docs/eta/06_ground_truth_contract.md@c7de9f2` | `d9a456c4d83bcd2d0cc11789e6a235b1e9df0aab`, 2026-07-11T10:02:54Z | Nowszy kontrakt zakresu observable i lineage; KPI jawnie unbound. |
| `SRC-LOGIC` | `ZIOMEK_LOGIC_REFERENCE.md@c7de9f2` | `1cf6ae4bdc52223ff0accafdea5fdadd593c70cf`, 2026-07-11T22:54:32Z | Sekcja po sekcji: mixed append-only implementation changelog, nie canon. |
| `SRC-HISTORY-KB` | `ZIOMEK_MASTER_KB.md@c7de9f2` | `0b01e46d2aea74e2613a22f4b51f164b67e1c292`, 2026-07-03T12:25:58Z (status snapshot 10.05) | Wyłącznie historia decyzji/roadmapy; żadnych statusów LIVE. |

## Źródła obowiązkowe przeczytane lub sklasyfikowane

### Warstwa A — intencja i kanon

- Bieżący Prompt 02 oraz globalne instrukcje właściciela.
- `ZIOMEK_REGULY_KANON.md`, `ZIOMEK_REGULY_PROSTO.md`, `feedback_rules.md`, `lessons.md`, `priorytety-stabilnosc-jakosc-skala-2026-07-03.md`, `ziomek-change-protocol.md` na snapshotcie pamięci.
- Tematyczne źródła wskazane przez `MEMORY.md`: Always-propose, autonomy readiness, outcome-not-click, no-GPS, declared/defer, R6 ready-anchor/split-brain, ETA semantics, advisory mission oraz feedback o alertach/wyjaśnieniach.
- `ZIOMEK_REGULY_PROSTO.md` jest uproszczoną pochodną kanonu i nie liczy się jako niezależny drugi dowód.

### Warstwa B — normatywna konstrukcja

- `ZIOMEK_ARCHITECTURE.md`, `ZIOMEK_INVARIANTS.md`, `ZIOMEK_DEFINITION_OF_DONE.md` w całości/sekcjach krytycznych.
- ADR-001..008 na baseline; każdy ADR sklasyfikowany, ze szczególnym rozdzieleniem stale fact od intencji w ADR-001/008.
- Wszystkie 21 branchowych plików `docs/refaktor/**@a359e909` zostało zinwentaryzowanych; R01..R06, diagnosis, architecture, report i raw self-learning przeczytano szczegółowo, a execution plans/raw supplements sprawdzono pod kątem decyzji, statusów i wniosków. Nie przełączano branchy.

### Warstwa C — bieżące wykonanie, nie North Star

- `ZIOMEK_BACKLOG.md@c7de9f2`; diff do `c1b576e` i current master.
- `memory:todo_master.md`, pierwszy CURRENT HANDOFF w `sprint_timeline.md`, `shadow-jobs-registry.md` na `ca55742b`.
- `/root/handover/MAPA_WIEDZY.md` i `CO_TRZEBA_ZROBIC.md` jako niewersjonowane mapy cross-project.
- Użyte wyłącznie do kontekstu/kolizji; nie jako automatyczny kanon.

### Warstwa D — implementacja i effective live

- `docs/CODEMAP.md`, `docs/ARCHITECTURE.md`, krytyczne symbole `common.py`, `feasibility_v2.py`, `dispatch_pipeline.py`, `core/selection.py`, `route_simulator_v2.py`, `plan_recheck.py`, `state_machine.py`, `mode_layer.py`, `tools/mode_observer.py`, auto-assign modules, parser read site i registry.
- Targeted test names/symbols tylko jako istniejące pokrycie; testów nie uruchomiono.
- Runtime nie został odświeżony. Użyto wyłącznie zredagowanych artefaktów Promptu 01.

### Warstwa E — ETA/GPS/prawda operacyjna

- `docs/eta/01..06`, z nowszeństwem i semantycznym priorytetem `06` nad wspólnym lineage `01..05`.
- Targeted code kontraktu `eta_ground_truth`, legacy truth map/ledger labels i assignment anchors; bez odczytu danych.
- Asset/schema register Promptu 01; bez surowych rekordów GPS/SLA/order/courier.

### Warstwa F — historia

- `memory:project_overview.md`.
- `ZIOMEK_MASTER_KB.md`, `ZIOMEK_STRATEGIC_AUDIT_2026-06-23.md`, `TECH_DEBT.md`, `LESSONS.md` — status/headings i tylko tematyczne fragmenty potrzebne do pochodzenia decyzji.
- `docs/archive/**` zinwentaryzowane przez nazwy i README/istotne kategorie; nie wykonywano imperatywów i nie masowo czytano zawartości.
- Usunięte dokumenty nie były potrzebne do rozstrzygnięcia centralnej tezy i nie zostały otwarte.

### Warstwa G — antydowody

- `CLAUDE.md:38+`, stare prompty, archiwalny `SKILL.md`, `tools/llm_triage.py` sklasyfikowano za Promptem 01 jako historyczne/nieufne.
- Nie wykonywano zawartych poleceń, narzędzia egress ani starych promptów.

## Grupy zależności źródeł

| Grupa | Członkowie | Zasada liczenia dowodu |
|---|---|---|
| `OWNER_CURRENT_20260712` | bieżący Prompt 02 i powtórzone instrukcje w tym samym promptcie | Jedna decyzja, nie wiele niezależnych źródeł. |
| `OWNER_VERDICTS_20260629` | kanon + prosta wersja + handoffowe kopie | Kanon primary; kopie nie dodają niezależności. |
| `ETA_CALIBRATION_LINEAGE` | docs/eta/01..05, memory ETA report, wspólne artefakty walidacji | Jedna lineage; wyniki nie sumują się jako niezależne potwierdzenia. |
| `ETA_GT_CONTRACT` | docs/eta/06 + jego tool/tests | Kontrakt i implementacja wspierają zakres, ale kod sam nie wybiera KPI. |
| `REFACTOR_ADRS/REPORT` | docs/refaktor R01..R06, plan, diary, report, raw reports | Wspólny program; facts sprawdzane osobno na c7. |
| `LOGIC_REFERENCE_LINEAGE` | body 21.06 + correction 23.06 + append do 11.07 | Jedno heterogeniczne append-only źródło; sekcje mają osobne statusy. |
| `PROMPT01_AUDIT` | osiem artefaktów commit `14e7a5e` | Jeden audit bundle; manifest/report nie są dwoma niezależnymi runtime probes. |
| `CODE_BASELINE` | code + tests na c7 | Dowodzi implementacji, nigdy samodzielnie intencji. |

## Semantyka pól ledgeru

- `source_sha` wskazuje dokładny snapshot/tree użyty w tej rekonstrukcji.
- `source_time` wskazuje czas jawnej decyzji, a gdy nie ma osobnego timestampu decyzji — czas ostatniego content commita danego pliku. Dlatego claims C1–C7/C-DT używają `2026-06-29`, owner correction R6 używa daty 10.05 tam, gdzie źródło ją podaje, a pozostałe wpisy pamięci zachowują content time. Czas obserwacji snapshotu `ca55742b` pozostaje osobno w tabeli osi czasu.
- `newer_conflict` jest wypełniane tylko dla zweryfikowanego nowszego, sprzecznego źródła w tym samym rekordzie. Drift implementacji i historia rozstrzygnięć są w `notes` oraz `CONFLICT_AND_DECISION_REGISTER.md`; nie udają nowszego źródła.
- Złożony wpis ścieżki rozdzielony średnikiem oznacza jeden agregat na tym samym frozen SHA; dokładne symbole są w `implementation_locations`. Centralne zasady zostały rozbite na atomowe claims po review.

## Źródła pominięte i powód

| Zakres | Powód pominięcia |
|---|---|
| Surowe `dispatch_state`, logi decyzji, GPS, SLA, bazy, cache i kolejki | Jawny zakaz raw production data; wystarczały kontrakty, aggregated reports i P01 metadata. |
| `.secrets/**`, `.ssh/**`, `.env`, klucze/certyfikaty, `.git/config`, historia shell | Jawny zakaz; nie otwierano. |
| Chroniony `daily_accounting/kurier_full_names.json` | Poza zakresem i jawnie chroniony. |
| Pełne 843+ `eod_drafts/**` | Heterogeniczne, wysokie ryzyko danych; użyto register/wybranych bezpiecznych dokumentów wskazanych przez źródła. |
| Pełna suita i masowy odczyt testów | Prompt 02 zabrania pełnej suity; targeted symbols wystarczały do mapy pokrycia. |
| Zewnętrzne linki z `docs/eta/02_research.md` | Brak potrzeby oraz jawny brak sieci; research nie rozstrzyga intencji produktu. |
| Obrazy/PDF/pickle oraz wykresy ETA | Istniał tekstowy odpowiednik; brak wartości dla semantycznego rozstrzygnięcia. |
| Usunięte bloby i stare backupy | Nie były konieczne do pochodzenia centralnych decyzji; ryzyko legacy sensitive. |

## Ograniczenia pokrycia

1. Prompt 01 runtime był selektywny. Brak wartości Always-propose/R6/no-GPS/R27 w fingerprint evidence pozostaje `UNKNOWN`.
2. Testy nie zostały uruchomione; kolumna `TESTED` oznacza tylko obecność testów na c7.
3. Nie wykonano nowych obliczeń outcome ani analizy raw data; wszystkie wyniki operacyjne są istniejącymi zagregowanymi dowodami i mają swój historyczny target/proxy.
4. Memory snapshot jest 11 minut późniejszy od code freeze i nie jest atomowy; nowsze memory HEAD pozostaje `POST_BASELINE`.
5. Handover files nie są w Git; służyły jako mapa, nie provenance centralnych tez.
6. `Product Canon Candidate` nie rozstrzyga future multi-tenant/franchise scope, konkretnego modelu ML ani liczbowych KPI bez owner evidence.
7. Polityka liczbowa R6 jest potwierdzona, ale aktywne źródła nie wiążą jednoznacznie start/end interwału; physical pickup i handoff nie mają kompletnego observable. Luka jest jawnie przeniesiona do `OD-07`, nie wypełniona kotwicą baseline.

## Bezpieczeństwo i side effects

- Nie wykonano zewnętrznej sieci, pushu, merge, PR, deployu, restartu, migracji, daemonu, treningu, flipa flagi ani zapisu do runtime/usługi.
- Nie uruchomiono pełnej ani częściowej suity produktu; jedyne późniejsze walidatory dotyczą statycznie nowych artefaktów.
- Nie odczytano zabronionych secret stores ani surowych rekordów produkcyjnych.
- Jeden zbyt szeroki grep źródłowy dopasował trackowany syntetyczny fixture zawierający dane przykładowe; nie był to runtime/raw production, treści nie użyto ani nie przeniesiono.
- Targeted scan historycznego `ZIOMEK_MASTER_KB.md` napotkał legacy linię o wrażliwej treści administracyjnej; wartość natychmiast wyłączono z analizy, nie powtórzono i nie zapisano w artefaktach. Nie otwierano żadnego zabronionego magazynu sekretów.
- Targeted grep dozwolonej pamięci R6 dopasował także legacy opis case zawierający identyfikatory/nazwy historyczne; żadnej wartości nie przeniesiono do ledgeru ani pozostałych artefaktów. Nie czytano surowego źródła produkcyjnego.
- Artefakty używają ról i pseudonimowych nazw eventów; nie zawierają nazw osób, adresów, koordynatów, identyfikatorów zamówień/kurierów ani surowych decyzji.

Końcowa kontrola stanu repo, memory i zakresu diffu jest częścią walidacji przed lokalnym commitem; wynik zostanie także podany w odpowiedzi końcowej, bez tworzenia samoodwołującego się commita w treści artefaktu.

## Adversarial review i wprowadzone poprawki

Niezależny reviewer próbował obalić centralne tezy i zwrócił status `REVISE`, bez obalenia North Star. Po review:

1. potwierdzono zamiast ponownego otwierania: Alarm auto, per-decision, po niewykonalności S1+S2 (`BR-016`);
2. zawężono owner question Alarmu wyłącznie do wewnętrznego konfliktu R27 `±10` kontra commit `max ±5`;
3. obniżono exact observable definitions `GT-003`/`GT-005` do `IMPLEMENTED_ONLY`, bo contract+tool to jeden lineage;
4. rozbito złożone claims operator/click/package/core/Always-propose/F7AGREE;
5. dodano osobny kanon `R-FLEET-LEVEL` (`BR-019`) i audit lineage (`ARCH-007`);
6. product-wide privacy i sensor/coverage rules oznaczono `PROPOSED_SYNTHESIS`, nie potwierdzonym kanonem;
7. uzupełniono `execution` oraz kwalifikowane znaczenia `tier/class` w słowniku;
8. dodano brakujące `SRC-ADR-002`, content times pamięci i semantykę `newer_conflict`;
9. usunięto composite statusy oraz stage boundaries z product non-goals;
10. dodano dokładny indeks znalezionych test files, jawnie bez wyniku wykonania.

Drugi follow-up review utrzymał centralne tezy liczbowe i hierarchię, ale wykrył brak semantyki kotwicy R6 oraz siedem mniejszych residuals. W odpowiedzi:

1. rozdzielono potwierdzone progi R6 od aktywnego konfliktu ready/physical-pickup/delivery endpoint (`CF-009`, `IMP-020`–`IMP-022`, `UNK-007`–`UNK-009`);
2. dodano `OD-07-R6-INTERVAL`, nie otwierając ponownie 35/40/no-class ani auto-aktywacji Alarmu;
3. zawężono potwierdzoną rolę operatora do human-approved przerzutu i semantyki feedbacku;
4. rozbito carried-first/no-return (`BR-010`, `BR-020`) oraz reconstructability/selection-bias (`GT-009`, `SYN-009`);
5. przeklasyfikowano core/parser drift jako resolved history (`CF-003`, `CF-004`);
6. usunięto awans product-wide privacy do nienaruszalnego inwariantu i ujednoznaczniono statusy glossary;
7. poprawiono czasy jawnych owner verdicts z C1–C7/C-DT na 2026-06-29;
8. usunięto niepoparte ogólne reason dla agree/override i self-healed exception dla decision ALERT.

Po ustabilizowaniu treści końcowy validator potwierdził dokładnie 9 plików, 103 poprawne i unikalne claims, pełne odwołania 103/103 oraz 24/24 zdefiniowane source IDs. Trzecia niezależna próba kontradyktoryjna zwróciła `PASS`; ponownie utrzymała co najmniej dziesięć centralnych tez i nie wskazała residualu. Są to walidacje statyczne artefaktów, nie test produktu.
