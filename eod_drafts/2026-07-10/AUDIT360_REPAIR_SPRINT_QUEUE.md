# Audyt 360 — kolejka napraw i bezkolizyjne sprinty — 2026-07-10

Status: Wave 3 code/test/read-only jest zamknieta na trzech clean/pushed
branchach; D1 merge pozostaje HOLD do at-214, DR1B pozostaje HOLD, a OPS0 nie
daje GO do tuningu live. S0 API-OWNERSHIP pozostaje LIVE. Nie daje to ACK na
flage, HARD/SOFT, credential, dane, siec ani kolejne operacje live.

Fala pierwsza zostala uruchomiona 2026-07-11 o 10:04 UTC w tmux 57/59/61 i
zamknieta przez integratora tego samego dnia. T0 oraz D0 sa zintegrowane, a S0
zostal zmergowany, wdrozony i zweryfikowany po jednym kontrolowanym restarcie
`courier-api`.

## 1. Reguły kwalifikacji

- `CONFIRMED` trafia do naprawy albo jawnego `VERIFY-CLOSE`.
- `PARTIAL` i `PLAUSIBLE` najpierw dostają sprint dowodowy; nie zakładamy z góry
  implementacji wskazanej przez pierwotny raport.
- 52 `UNVERIFIED` pozostają hipotezami z reprodukcją w
  `audits/2026-07-10/full-system-360/25_REPRODUCTION_INDEX.md`. Nie są bugami i
  nie dostają sprintów naprawczych.
- `REFUTED` pozostaje zamknięte, żeby fałszywy alarm nie wrócił.
- Używamy prefiksu `A360-`, bo historyczna pamięć ma inne zadanie o nazwie
  `FEAS-01`.

Priorytet ma dwie osie:

1. biznesowo najpilniejsze są bezpieczeństwo credentialu i `A360-FEAS-01`
   (jedyny utrzymany P1, granica HARD R6);
2. technicznie pierwszym mergem musi być hermetyczny baseline TEST-11/12, bo
   czerwony i live-zależny oracle unieważnia dowody kolejnych zmian.

## 2. Bramka G0 — stan historyczny przed uruchomieniem kodu

Integrator najpierw rozstrzyga właścicieli i bazę każdej gałęzi. Stan wykryty
10.07 około 20:17 UTC:

- `/root/sprint1_wt/dispatch_v2` ma cudze dirty m.in. w `core/candidates.py`,
  `dispatch_pipeline.py`, `panel_watcher.py`, `plan_manager.py` i
  `plan_recheck.py`; nie wolno tych zmian stage'ować, kopiować ani nadpisywać;
- Sprint 3 `85b9dc7` nie jest scalony i dotyka m.in. `common.py`,
  `core/candidates.py`, `dispatch_pipeline.py`, `route_simulator_v2.py`, OSRM i
  rejestru flag; przed lane'ami EVIDENCE/ENGINE trzeba go scalić albo jawnie
  odrzucić i dopiero wtedy przestawić bazę;
- tmux50 jest właścicielem cudzego WIP grafiku w głównym worktree panelu
  Nadajesz; plan/CAS pracuje wyłącznie w osobnym worktree i nie dotyka tych
  plików ani wspólnego `core/flags.py`;
- główny dispatch ma cudzy dirty
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`; plik jest poza zakresem;
- backlog `master` nadal ma starsze statusy. Prawidłowa kolejka jest na branchu
  audytu do czasu osobnego merge dokumentacji.

G0 kończy się tabelą `lane -> branch -> worktree -> base SHA -> owner -> file
allowlist`. Wykonawcy nie edytują wspólnych backlogów ani pamięci; robi to tylko
integrator po odbiorze lane'a.

## 2A. G0 wykonane — fala pierwsza 2026-07-11

| Lane | tmux / effort | Branch i zamrozona baza | Worktree / owner | Allowlista i granica | Status startowy |
|---|---|---|---|---|---|
| `A360-T0 TEST-TRUTH` | 57 / `high` | `review/a360-t0-test-truth` @ `f015c9f` | `/root/a360_t0_wt/dispatch_v2`, owner tmux57 | niezalezny odbior oraz fix-forward dwoch ukrytych prod-write w testach; bez `core/` | DONE; branch pushed, zintegrowany, tmux57 zamkniety |
| `A360-S0 API-OWNERSHIP` | 59 / `ultra` | `security/a360-s0-api-ownership` @ `320aa0e` | `/root/a360_s0_wt/courier_api`, owner tmux59 | wspolny guard order→CID, writery status/arrival/ground-truth/parcel, syntetyczne testy; bez pre-login UX | DONE/LIVE 11.07; API master `fa249e6`, PID 925329, health PASS, tmux59 zamkniety |
| `A360-D0 R6-DECISION-PREP` | 61 / `low` | `docs/a360-d0-r6-decision-prep` @ `c241507` | `/root/a360_d0_wt/dispatch_v2`, owner tmux61 | jedyny output `eod_drafts/2026-07-11/A360_D0_R6_DECISION_PREP.md`; kod/testy/runtime tylko read-only | CLOSED; branch pushed, whitespace gate naprawiony, tmux61 zamkniety |

Historyczny G0 zachowywal locki tmux50/tmux60 i dirty Sprintu 1. Biezacy audyt
hashy zamknal ENGINE lock jako VERIFY-CLOSE: zero unikalnego WIP i zero procesu
ownera; fizyczny worktree pozostaje nietkniety. Chroniony dirty
`CLAIM_LEDGER_HARD_GATE_CARD.md` nadal pozostaje poza zakresem. Wspolne backlogi
i pamiec aktualizuje tylko integrator po odbiorze wynikow.

## 3. Fala pierwsza — zacząć najpierw

Po G0 mogą iść równolegle poniższe tory, bo mają rozłączne repozytoria lub nie
zmieniają kodu runtime.

| Kolejność | Sprint | Kanoniczna karta | Zakres i pliki | Koniec | Bramka |
|---:|---|---|---|---|---|
| 1 — pierwszy merge | `A360-T0 TEST-TRUTH` | `Z-P0-03` + `Z-P2-07` | `tools/flag_registry.py`, lifecycle metadata, `tests/test_flag_registry_f3.py`, pięć TEST-12 i zewnętrzna kwarantanna; bez `core/` | default i pełny STRICT = 0 failed; dokładna lista skipów; syntetyczne flags+systemd/state; anty-prod negative control | najpierw disposition rejestru ze Sprintu 3; zero live |
| 1 równolegle | `A360-S0 API-OWNERSHIP` | rozszerzone `Z-P0-06` | osobny worktree repo `courier_api`: wspólny guard order→CID dla status/arrival/ground-truth/parcel oraz testy cudzej encji; katalog pre-login jako osobna decyzja UX | obcy CID ma deny we wszystkich mutacjach, właściciel nadal działa; brak wycieku przez różne odpowiedzi | kod/testy bez deployu; preferowany fix-forward; awaryjny, krótkotrwały rollback do poprzedniego API przywraca ryzyko BEZP-02 i wymaga jednego restartu + owner/foreign smoke; deploy za ACK |
| 1 równolegle | `A360-D0 R6-DECISION-PREP` | `Z-P0-01`, B-01/B-02 | wyłącznie docs-only warianty i tabela oczekiwanych golden cases dla A360-FEAS-01..05: 35 normalny HARD, 40 alarm/least-damage, `None`, per-order vs suma worka, READY vs in-bag; wykonywalne fixture dopiero po T0 | gotowa macierz wariantów, kosztów i wymagany oracle; bieżący PARTIAL replay może być tylko diagnostyką, nie dowodem decyzji | decyzja HARD dopiero po R0+D1; implementacja i flip osobno |
| pilne, osobne okno | `A360-I0 CREDENTIAL` | `INFRA-P0-01` | rotacja ujawnionego credentialu i przeniesienie do bezpiecznego carriera; w raportach tylko metadane, nigdy wartość ani wrażliwa ścieżka | stary credential unieważniony, nowe procesy działają, prawa minimalne | osobny bieżący ACK; rollback nigdy do ujawnionej wartości |

`A360-T0` i `A360-S0` mają osobne repo/worktree. `A360-D0` jest docs/read-only.
`A360-I0` ma osobnego operatora i nie dzieli release'u z żadnym sprintem kodowym.

## 4. Fala druga — dowody po odzyskaniu wiarygodnego baseline'u

### Wybrane nastepne trzy sprinty po zamknieciu fali pierwszej

1. `A360-R0 REPLAY-TRUTH` — technicznie ACCEPT @ `1b38447`, wydaniowo HOLD;
   do mastera weszly tylko docs, kod czeka na odczyt at-214.
2. `A360-DR0 RESTORE` — source/fake ACCEPT @ `d873f0b`; source zintegrowany,
   ale real verify/artifact/drill i service RTO sa DR1 HOLD / NOT DONE.
3. `A360-DEP0 SBOM` — ACCEPT/DONE @ `53730e9`; przenosny config, 6/6 unitow,
   deterministyczny SBOM; CVE/EOL uczciwie UNKNOWN.

ENGINE lock Sprintu 1 zamknieto jako VERIFY-CLOSE bez dotykania worktree:
31 blobow = aktualny master, 6 = commity historyczne, 0 unikalnych WIP i 0
procesow ownera. `A360-D1` moze ruszyc w nowym worktree, ale jego merge — tak
jak R0 — czeka na `at-214`, aby nie skazic paired replay Sprintu 3.

| Kolejność | Sprint | Kanoniczna karta | Zależność | Koniec i rollback |
|---:|---|---|---|---|
| 2 | `A360-R0 REPLAY-TRUTH` | pierwszy inkrement `Z-P1-04`, zależność `Z-P2-06` | po `A360-T0` i disposition Sprintu 3 | rozłączne input-miss/OSRM-miss/soft-diff, denominator+coverage+freshness, frozen known-answer, mutation tripwire i brak live fallbacku; tool-only revert |
| 2 | `A360-D1 FIREWALL-EXEMPT-TRUTH` | instrumentacyjny krok `Z-P0-01` | T0 DONE, ENGINE lock RELEASED; development teraz, merge po at-214 | pre-existing/carried ma `EXEMPT`, nowe naruszenie decyzji ma `VIOLATION`; golden+mutation, parity wyboru i metryka w jsonl; revert instrumentacji, zero enforcementu |
| 2 równolegle | `A360-DR0 RESTORE` | `Z-P1-10` | source/fake zintegrowany bez instalacji live | fail-closed source i 138 focused PASS; real provenance/decrypt/DB/app RTO/RPO pozostaja DR1 HOLD |
| 2 równolegle | `A360-DEP0 SBOM` | `Z-P1-08` | inwentaryzacja read-only | SBOM/constraints per proces, `pip check`, licencje i CVE triage; bez automatycznego upgrade'u |

`A360-R0` i `A360-D1` nie zmieniaja decyzji. D0 staje sie karta gotowa do
rozstrzygniecia dopiero po ich wynikach; przedtem nie wolno uzyc starego replayu
jako argumentu za wariantem biznesowym.

### Wave 3 zamknieta na branchach — 2026-07-11

1. `A360-D1 FIREWALL-EXEMPT-TRUTH` (`ultra`, tmux 65) — DONE na branchu
   `engine/a360-d1-firewall-exempt-truth` @ `e193f2a`; v2 rozdziela
   physical breach od odpowiedzialnosci decyzji, z uczciwym `UNKNOWN` bez
   baseline. Merge nadal HOLD do odczytu at-214; zero enforcementu.
2. `A360-DR1A RESTORE-PREP` (`high`, tmux 66) — SOURCE/FAKE ACCEPT na
   `ops/a360-dr1a-restore-prep` @ `b035523`; carrier/quota/app-smoke/cleanup
   gotowe syntetycznie. Realny drill to DR1B `ultra` za osobnym ACK.
3. `A360-OPS0 RUNTIME-SYSTEMD-EVIDENCE` (`high`, tmux 67) — TOOL ACCEPT na
   `ops/a360-ops0-runtime-evidence` @ `1bb4699`; mapa 10 uslug jest PROVEN,
   lecz reprezentatywny profil i bezpieczne limity pozostaja UNKNOWN.

Fazy developerskie sa plikowo rozlaczne. Ciezki real DR1B i reprezentatywne
okno OPS0 nie moga biec jednoczesnie, bo skazilyby pomiar. H1 nadal czeka na
at-214, integracje R0, D1 oraz decyzje B-01/B-02 i osobny ACK.

Dokladny kontrakt baz, worktree, write-setow, testow i rollbacku zapisano w
`eod_drafts/2026-07-11/AUDIT360_WAVE3_LAUNCH.md`. W sobotnim oknie wszystkie
trzy lane'y maja twardy zakaz operacji live.

Pelny odbior wynikow i incydentow bezpieczenstwa:
`eod_drafts/2026-07-11/AUDIT360_WAVE3_CLOSE.md`.

## 5. Fala trzecia — jedyny P1 przed dlugim PLAN

1. `A360-H1 R6-HARD`: implementacja A360-FEAS-01 dopiero po `A360-T0`,
   `A360-D0`, `A360-D1`, wiarygodnym `A360-R0` i jawnym ACK. Golden
   34.9/35/35.1 oraz
   39.9/40/40.1, mutation polaryzacji, ON/OFF na tym samym korpusie, metryka w
   `shadow_decisions.jsonl`, pełna regresja i minimum dwa dni obserwacji. Przed
   mergem musi miec dokladny default-OFF kill-switch; preferowac istniejacy, jesli
   spelnia kontrakt, bez tworzenia drugiej flagi. Rollback live: kill-switch OFF
   → fingerprint → revert zatwierdzonego commita → jeden kontrolowany restart →
   health/smoke.
2. `A360-P0 PLAN-INTEGRITY`: dopiero po H1 i zwolnieniu dirty Sprintu 1. Jeden
   owner dla SPRI-02/03, TRAS-01/02/03/04, BLIZ-02 i DANE-01; parity
   decyzja→store→panel→apka, null-duration fail-closed, race dispatcher↔panel i
   stale reject bez lost-update. Readers-first/writer-second; rollback
   writer-first/readers-second. Nie wolno rozdzielac walidatora, stops i CAS.
3. `A360-H2 BEST-EFFORT`: A360-FEAS-02..05 w tym samym lane ENGINE, ale osobny
   sprint/commit po PLAN. Musi zachować always-propose jako jawny
   ALERT/least-damage; SOFT nie może osłabić HARD. Przed mergem osobny
   default-OFF kill-switch; rollback jak H1 z jego wlasnym commitem i flaga.
4. `A360-F0 FLAG-CARRIER`: follow-up `Z-P1-07` dla A360-FLAG-01/04/DOCS-01.
   Najpierw decyzja `retire` kontra `unify`; nie podłączać mechanicznie flagi,
   której wcześniejszy pomiar carry-chain miał wynik NO-GO. Jeżeli zostaje:
   `decision_flag`, ON różne od OFF, flaga nadal OFF, registry/fingerprint/checkery.

Po PLAN i SECURITY moze ruszyc `A360-O0 FLOW-LIVENESS-PREP` (`Z-P1-12`) na
dokladnym pathspecie observability/unit/runbook, bez zmian handlerow panelu lub
API. Instalacja/restart jest osobnym wydaniem: backup unitow → instalacja → jeden
restart uslugi → negative control + recovery; rollback unitow → restart → health.

HARD, PLAN i FLAG nie biegną równolegle: współdzielą `plan_recheck`,
`route_simulator_v2`, `core/candidates`, `dispatch_pipeline` albo rejestr flag.
W danym oknie tylko jeden FLIPMASTER.

## 6. Pełne disposition CONFIRMED/PARTIAL/PLAUSIBLE

Poniższe grupowanie gwarantuje, że potwierdzony wpis nie ginie, ale nie tworzy
drugiej równoległej listy zadań.

| Pakiet | Findings | Karta / dalszy los |
|---|---|---|
| test truth | SPRI-05, FLAG-03, TEST-01/02/11/12 | `Z-P0-03` + `Z-P2-07`, `A360-T0` |
| credential | SPRI-09, FLAG-05 | `INFRA-P0-01`, osobny ACK i bezpieczny carrier |
| API security | BEZP-02, BEZP-04 | rozszerzone `Z-P0-06`; ownership i UX katalogu rozdzielone |
| firewall truth | SPRI-04 | `Z-P0-01`, `A360-D1` przed kalibracja/enforcementem |
| R6/always-propose | FEAS-01..05 | `Z-P0-01`, najpierw D0+R0, potem H1/H2 serial |
| plan boundary | SPRI-02/03, BLIZ-02, TRAS-01/02, DANE-01 | ponownie otwarte `Z-P0-04`, jeden cross-repo lane |
| replay/core | CORE-01/02/03, TEST-03 | pierwszy inkrement `Z-P1-04`; CORE-01 najpierw verify-cause |
| flags | FLAG-01/02/04, DOCS-01 | follow-up `Z-P1-07`; FLAG-02 tylko VERIFY-CLOSE na HEAD |
| serializer decyzji | BLIZ-01 | `Z-P2-02`, po PLAN/ENGINE |
| route edge cases | TRAS-03, TRAS-04 | `Z-P0-04` w A360-P0; TRAS-04 najpierw reprodukcja, bo PLAUSIBLE |
| flow-liveness | OPS-02 | `Z-P1-12`; kod/prep osobno od restartu |
| limity runtime/systemd | OPS-03/04 | `INFRA-P1-02`; MemoryMax/OOM/Restart po pomiarze i w osobnym maintenance window |
| performance SLO | OPS-07 | `Z-P1-03`; najpierw stage evidence i disposition Sprintu 3 |
| SSH/network | OPS-01, OPS-05 | `INFRA-P1-01`; OPS-05 najpierw provider proof, potem osobne okno z drugą działającą sesją |
| retencja writer-aware | DANE-02 | `Z-P1-06`; bez `copytruncate` dla kanonicznego ledgera |
| parser/geocode | INTE-01/02 | `Z-P2-03` + `Z-P2-06`; osobny adapter lane |
| Papu/panel sync | INTE-03/06 | `Z-P2-03`; osobny cross-project lane z idempotency/read-back |
| Dr Tusz | INTE-04/05 | `Z-P2-03`; crash-safe dedup + fail-loud parser |
| epaka/cod-weekly | INTE-07/08 | osobne integracyjne lane'y; jawny consumer alertu i parser contract |
| night guard | TEST-04/05 | `Z-P2-07` + `Z-P3-03`; exact nodeid, stały denominator i hard-error visibility |
| ETA calibration | ALGO-01/02 | `Z-P1-02`; wspólny holdout, brak future-feature leakage, test istotności |
| proces audytu | AUDIT-01/02 | treść, walidator, branch, push i handoff wykonane; historyczne wpisy pozostają w indeksie |

Pozostałe 52 `UNVERIFIED` mają wyłącznie kolejkę reprodukcji. Cztery `REFUTED`
(`SPRI-01`, `BEZP-01`, `BEZP-03`, `OPS-06`) są zamknięte i nie wracają jako
sprint naprawczy.

## 7. Macierz bezkolizyjności

| Lane | Główna powierzchnia | Może równolegle | Nie może równolegle |
|---|---|---|---|
| EVIDENCE | testy, flag-registry, world_record/replay | SECURITY, docs-only D0, read-only DR/SBOM | T0 i R0 między sobą; niezintegrowany Sprint 3 |
| SECURITY | osobne repo `courier_api` | EVIDENCE, docs-only D0 | PLAN, gdy PLAN wejdzie w testy/apkę courier API |
| ENGINE | feasibility, selection, candidates, pipeline, plan_recheck | tylko rozłączne ops read-only | PLAN, FLAG, Sprint 3 i dirty Sprint 1 |
| PLAN | plan_manager, watcher, recheck, panel route, app parity | ops read-only | ENGINE, SECURITY w fazie app, drugi writer planu |
| INTEGRATIONS | osobny most/adapter per lane | inne mosty po sprawdzeniu wspólnych bibliotek | wspólny panel client/state writer bez jednego ownera |
| OPS | unit/runbook/provider proof | kod/prep read-only | jakiekolwiek równoległe restarty, sieć, credential lub deploy |
| DOCS/RELEASE | backlog, todo, timeline, registry, handoff | nic — jeden integrator | wszyscy wykonawcy lane'ów |

## 8. Wspólny Definition of Done

Każdy sprint ma: świeży ETAP 0, mapę writerów/konsumentów, pozytywny oracle i
mutation/negative control, celowane testy, pełną kanoniczną regresję względem
naprawionego baseline'u, ON/OFF albo parity, `diff --check`, niezależny review,
jawny rollback i trwały handoff. Operacja live jest osobnym krokiem po ACK; sam
zielony pytest nie daje zgody na restart, flip ani deploy.

## 9. Karty cross-project do `todo_master`

### INFRA-P0-01 — rotacja ujawnionego credentialu

- Zakres raportu obejmuje tylko ownera, mtime/prawa, status rotacji i smoke;
  nigdy wartość ani wrażliwą ścieżkę.
- DoD: nowy credential działa wyłącznie z minimalnymi prawami, stary jest
  unieważniony, procesy przeszły smoke, a transkrypt/artefakty nie zawierają
  wartości.
- Bramka: osobny bieżący ACK. Rollback nigdy do ujawnionej wartości; awaryjnie
  kolejny nowy credential/config revision.

### INFRA-P1-01 — SSH i ownership granicy sieciowej

- Najpierw provider-side proof dla OPS-05; brak dowodu pozostaje `UNKNOWN`.
- W maintenance: backup konfiguracji/reguł, druga działająca sesja, `sshd -t`,
  dodanie/test nowej ścieżki i dopiero potem rozważenie usunięcia starej.
- Rollback: przywrócić backup konfiguracji i reguł, reload/restart tylko
  zatwierdzonej usługi, potwierdzić logowanie obiema sesjami. Sieć/restart za ACK.

### INFRA-P1-02 — limity runtime i precedencja systemd

- OPS-03/04: najpierw pomiar RSS/swap/pressure oraz efektywnej precedencji
  drop-inów, bez czytania wartości sekretów.
- DoD: jeden jawny kontrakt MemoryMax/OOMScoreAdjust/Restart per usługa, test pod
  kontrolowanym obciążeniem, brak swap-thrash i brak pętli restartów.
- Wdrożenie usługa po usłudze za ACK. Rollback: backup drop-inów → przywrócenie →
  daemon-reload → jeden restart → PID/NRestarts/health.
