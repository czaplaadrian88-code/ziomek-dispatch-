# Rejestr źródeł wiedzy

Baseline: `c7de9f29127851a59507fac92d14f328336afe61`. Markdown zawiera klasyfikację rodzin i pojedynczych źródeł o wysokim znaczeniu. Towarzyszący `KNOWLEDGE_SOURCE_REGISTER.jsonl` daje path-level rejestr faktycznie wybranych istotnych źródeł; każda linia jest osobnym JSON-em. Pokrycie pozostaje `PARTIAL`: nie oznacza przeczytania ani indywidualnej klasyfikacji każdego z 843 historycznych plików `eod_drafts/`, każdego unitu/testu ani treści wrażliwych.

Statusy: `CURRENT_CANDIDATE`, `PARTIALLY_CURRENT`, `HISTORICAL`, `CONFLICTING`, `GENERATED`, `UNKNOWN`.

## Źródła nadrzędne i bieżące

| ID | Ścieżka / typ | Temat, zakres i pochodzenie | Ostatnia istotna zmiana | Powiązane moduły / kandydat prawdy | Status; zaufanie i uzasadnienie | Konflikt / sensitive | Rekomendacja |
|---|---|---|---|---|---|---|---|
| KS-001 | `/root/.codex/AGENTS.md`, instrukcja hosta | Globalny protokół Ziomka, bezpieczeństwo, #0, bootstrap | poza repo; jawnie dostarczone w sesji | wszystkie projekty; instrukcja nadrzędna | `CURRENT_CANDIDATE`; najwyższe po aktualnym poleceniu właściciela produktu | opsec: średnie; konflikt push/handoff rozstrzygnięty aktualnym Prompt 01 | zachować, weryfikować aktualne polecenie |
| KS-002 | `/root/AGENTS.md`, pointer | Punkt wejścia do KS-001 | poza repo | wszystkie projekty | `CURRENT_CANDIDATE`; wysokie | brak treści produktowej | zachować |
| KS-003 | `/root/.openclaw/workspace/AGENTS.md`, instrukcja nested | Inna rola i ścieżki środowiska `/home/node` | poza repo | workspace OpenClaw | `CONFLICTING`; niskie dla Ziomka | koliduje z `/root/.codex`; opsec średnie | ograniczyć scope/pointer później |
| KS-004 | `CLAUDE.md:1-36`, nawigacja | Mapy, #0, ostrzeżenie o historycznym body | `8b5af8a`, 2026-07-05 | całe repo; kandydat nawigacji | `CURRENT_CANDIDATE`; wysokie dla głowy | sensitive wysokie | zachować jako punkt wejścia |
| KS-005 | `CLAUDE.md:38+`, snapshot | Body poniżej sekcji STATUS: historyczne statusy, flagi i numery linii | snapshot 2026-05-10 | dawna architektura | `HISTORICAL`; niskie dla LIVE | pojedyncze evergreen facts nadal wymagają niezależnej weryfikacji | zachować jako historię |
| KS-006 | `docs/CODEMAP.md` | Mapa katalogów, wejść, pułapek i runtime roots | `3c43573`, 2026-07-10 | całe repo; nawigacja | `CURRENT_CANDIDATE`; wysokie dla ścieżek | claims LIVE wymagają runtime; opsec średnie | zachować i aktualizować po zmianach struktury |
| KS-007 | `docs/ARCHITECTURE.md` | Przepływ 10 warstw, core, state, integracje | `3c43573`, 2026-07-10 | pipeline/core/consumers | `CURRENT_CANDIDATE`; wysokie dla mapy | R6/ADR drift; opsec średnie | zachować, zweryfikować semantykę |
| KS-008 | `ZIOMEK_ARCHITECTURE.md` | Konstrukcja i rejestr kruchości | `e07cfb4`, 2026-07-06 | core/feasibility/plan | `PARTIALLY_CURRENT`; średnio-wysokie | „35/40 tier-aware” kontra kanon; sensitive średnie | zweryfikować/scalić później |
| KS-009 | `ZIOMEK_INVARIANTS.md` | Inwarianty i strażnicy | `62f54bd`, 2026-07-08 | testy/checkery/harness | `PARTIALLY_CURRENT`; wysokie dla nazw kontraktów | statusy muszą być potwierdzone testami | zachować |
| KS-010 | `ZIOMEK_DEFINITION_OF_DONE.md` | Normatywne DoD | `542dfa1`, 2026-07-03 | proces wydania | `CURRENT_CANDIDATE`; wysokie | liczbowe baseline’y historyczne | zachować, generować liczby z runu |
| KS-011 | `ZIOMEK_BACKLOG.md` | Repozytoryjny punkt wykonawczy | frozen `c7de9f2`; post-baseline `c1b576e`, 2026-07-12 | sprinty, live gates | `CURRENT_CANDIDATE`; wysokie przy jawnej wersji SHA | równoległy docs drift; sensitive średnie | manifestować commit; nie mieszać wersji |
| KS-012 | `ZIOMEK_LOGIC_REFERENCE.md` | Konsolidacja logiki od 21.06 | `1cf6ae4`, 2026-07-11 | pipeline/reguły/flag states | `CONFLICTING`; średnie | mówi „current/not committed”, choć tracked; część treści jest nadal użyteczna | weryfikować sekcja po sekcji |

## Pamięć, handoff i kanon

| ID | Ścieżka / typ | Temat, zakres i pochodzenie | Ostatnia istotna zmiana | Powiązane moduły / kandydat prawdy | Status; zaufanie i uzasadnienie | Konflikt / sensitive | Rekomendacja |
|---|---|---|---|---|---|---|---|
| KS-020 | `memory:MEMORY.md` | Indeks pamięci i routing | `a129068`, 2026-07-12 | wszystkie tematy | `CURRENT_CANDIDATE`; wysokie jako indeks | sensitive wysokie | zachować; nie traktować streszczeń jako dowodu |
| KS-021 | `memory:todo_master.md` | Kolejka techniczna i produktowa | `b835905`, 2026-07-12 | sprinty i decyzje | `CURRENT_CANDIDATE`; wysokie dla kolejki | może wyprzedzać runtime; sensitive wysokie | czytać z handoffem/runtime |
| KS-022 | `memory:sprint_timeline.md` (pierwszy CURRENT HANDOFF) | Najnowsze przekazanie | `ca55742`, 2026-07-12 | bieżący sprint/runtime | `CURRENT_CANDIDATE`; wysokie, lecz czasowe | starsze handoffy są historyczne; sensitive wysokie | czytać tylko top jako current |
| KS-023 | `memory:shadow-jobs-registry.md` | Timery, obserwacje i przyszłe werdykty | `a129068`, 2026-07-12 | timers/atq/flags | `CURRENT_CANDIDATE`; wysokie | liczniki mogą dryfować; sensitive wysokie | krzyżować z systemd/atq |
| KS-024 | `memory:ZIOMEK_REGULY_KANON.md` | Kanon biznesowy, HARD/SOFT, R6/R27 | `83912f9`, 2026-07-03 | feasibility/scoring/selection/plan | `CURRENT_CANDIDATE`; wysokie jako utrwalona decyzja | kod ma jawne odstępstwa; biznesowo wrażliwe | główne wejście Prompt 02; potwierdzać kodem/runtime |
| KS-025 | `memory:ziomek-change-protocol.md` | Żywy, monotoniczny protokół zmian | `b835905`, 2026-07-12 | wszystkie zmiany | `CURRENT_CANDIDATE`; wysokie | opsec średnie | zachować monotonicznie |
| KS-026 | `memory:project_overview.md` | Starszy opis produktu | `dd2971c`, 2026-05-31 | product overview | `HISTORICAL`; niskie-średnie | stare źródło ground truth; część intencji może być nadal użyteczna; PII/opsec wysokie | zachować intencję, nie status LIVE |
| KS-027 | `memory:tech_debt_backlog.md` | Głęboki backlog historyczny | `d8edd65`, 2026-06-19 | dług techniczny | `PARTIALLY_CURRENT`; średnie | może dublować/konfliktować z todo | zweryfikować/scalić później |
| KS-028 | `/root/handover/MAPA_WIEDZY.md` | Topologia cross-project | update 2026-07-12 14:08; niewersjonowany | panel/API/app/host | `CURRENT_CANDIDATE`; średnie | brak Git/provenance; opsec wysokie | hashować i potwierdzać repo/runtime |
| KS-029 | `/root/handover/CO_TRZEBA_ZROBIC.md` | Przekazanie operacyjne | update 2026-07-12 | cross-project backlog | `CURRENT_CANDIDATE`; średnie | brak Git; opsec wysokie | potwierdzać przed działaniem |
| KS-030 | `/root/handover/SEKRETY_INWENTARZ.md` | Rejestr sekretów | tylko metadata mtime 2026-07-08 | security | `UNKNOWN`; treści nie otwarto | **sensitive krytyczne** | wykluczyć; osobny jawny security scope |
| KS-031 | `repo:project-memory@ca55742b969404663dff80e4ec2aadfe3c64bdbf` | Atomowy snapshot repo pamięci użyty przez audyt | obserwacja 2026-07-12T14:45:11Z, clean | wszystkie wpisy memory | `CURRENT_CANDIDATE`; wysokie dla tej chwili | snapshot concurrent/post-code-freeze, nieatomowy z `c7de9f2`; sensitive wysokie | zawsze podawać osobny SHA i czas |

## Decyzje, flagi, testy i operacje

| ID | Ścieżka / typ | Temat, zakres i pochodzenie | Ostatnia istotna zmiana | Powiązane moduły / kandydat prawdy | Status; zaufanie i uzasadnienie | Konflikt / sensitive | Rekomendacja |
|---|---|---|---|---|---|---|---|
| KS-040 | `docs/decisions/ADR-001..008` | HARD/SOFT, shadow, flags, state, venv, worktree, core | `651244e`, 2026-07-03 | cała architektura | `PARTIALLY_CURRENT`; średnie | ADR-001 R6 i ADR-008 core mają drift | zachować intencję; zaktualizować fakty później |
| KS-041 | `tools/flag_lifecycle_registry.json` | Maszynowy rejestr flag | `a860c53`, 2026-07-11 | common/units/checkers | `CURRENT_CANDIDATE`; wysokie | runtime per-process nadal osobno; opsec średnie | preferować nad snapshotem human |
| KS-042 | `docs/flags/INVENTORY_2026-07-10.md`, `README.md` | Snapshot flag z 10.07 | `7d0212c`, 2026-07-10 | flags | `PARTIALLY_CURRENT`; średnie | `USE_V2_PARSER` drift już zamknięty w KS-041/live | wygenerować ponownie później |
| KS-043 | `docs/HERMETIC_TESTS.md`, `conftest.py`, quarantine | Kontrakt i implementacja izolacji | `cf3e4cb` / `4e782e8`, 10–11.07 | cała suita | kod `CURRENT_CANDIDATE`; wysokie | docs nie dowodzą blokady sieci; fail-open subprocess | zachować, zbudować network deny później |
| KS-044 | `systemd/README.md`, `systemd/**` | Mirror unitów | `adf0fae`, 2026-07-06 na frozen baseline | procesy/timery | `PARTIALLY_CURRENT`; wysokie dla źródeł, niskie dla LIVE | `/etc` jest źródłem wdrożonej konfiguracji, nie atestacją pamięci procesu; część unitów brak | zachować, zinwentaryzować source/live drift |
| KS-046 | branch `refaktor/architektura:docs/refaktor/**` | 21 branch-only dokumentów refaktoru i ADR R01–R06 | `a359e909`, 2026-07-06 | core/world record/planner/scorer | `PARTIALLY_CURRENT`; średnie-wysokie, branch-scoped | nieobecne na frozen master; wrażliwe | czytać w Prompt 02 z jawnym branch/SHA, weryfikować masterem |
| KS-047 | `eod_drafts/2026-07-10/SPRINT4_*.md` i `sprint4_artifacts/**` | Kontrakty Sprintu 4, mapy A1–A4 i review E | `70af4fa`, 2026-07-10 | identity/flags/hermetic tests | `PARTIALLY_CURRENT`; wysokie dla wdrożonych kontraktów, nie dla późniejszego runtime | sensitive wysokie | obowiązkowe źródło warunków Sprintu 4 |
| KS-045 | `docs/runbooks/**`, `docs/deploy/**` | Host boundary, backup/restore, staged deploy | 11–12.07 | infra/DR | `PARTIALLY_CURRENT`; średnio-wysokie | dokument nie jest proof; backup script source/live drift | zachować, realny restore osobno |

## ETA, GPS, integracje i materiały dowodowe

| ID | Ścieżka / typ | Temat, zakres i pochodzenie | Ostatnia istotna zmiana | Powiązane moduły / kandydat prawdy | Status; zaufanie i uzasadnienie | Konflikt / sensitive | Rekomendacja |
|---|---|---|---|---|---|---|---|
| KS-050 | `docs/eta/06_ground_truth_contract.md` | Obserwowalna prawda ETA/SLA i lineage | `d9a456c`, 2026-07-11 | eta_ground_truth/GPS/SLA | `CURRENT_CANDIDATE`; wysokie dla kontraktu v1 | KPI event unbound; GPS/PII wysokie | zachować; nie nadinterpretować proxy |
| KS-051 | `docs/eta/01..05` | Badania, projekt, wdrożenie, walidacja ETA | zmiany do 2026-07-11 | eta calibration/models | `PARTIALLY_CURRENT`; średnie | starsze założenia i wyniki; sensitive wysokie | weryfikować z KS-050 i kodem |
| KS-052 | `docs/integracje/**` | Rynek, partnerzy, API, roadmapa | `971c40a`, 2026-07-08 | integracje zewnętrzne | `PARTIALLY_CURRENT`; średnie | fakty zewnętrzne niezweryfikowane; kontakty/strategia wysokie | rozdzielić research i zatwierdzone kontrakty |
| KS-053 | `audits/2026-07-10/full-system-360/**` | Formalny audyt: 110 findings, validator, negative controls | `bbacef0`, 2026-07-10 | cały system | `PARTIALLY_CURRENT`; wysokie dla metody, średnie dla statusu | baseline `70af4fa`, część wyników zdryfowała | zachować i rewalidować per finding |
| KS-054 | `docs/audyt/**` | Audyt nawigacyjny z 03.07 | `8b5af8a`, 2026-07-05 | mapa zależności/danych | `PARTIALLY_CURRENT`; średnie | część jest historyczna; liczniki i layout zdryfowały; opsec wysokie | zachować jako mapa pochodzenia |
| KS-055 | `eod_drafts/2026-07-11/**` | Karty Audit360, SBOM, DR, replay, tests | `4426606`, 2026-07-11 | aktualne dowody punktowe | `UNKNOWN`; członkowie są generated lub partially current | zależne od SHA/czasu; sensitive wysokie | używać tylko per-artefakt z provenance |
| KS-056 | `eod_drafts/2026-07-12/**` | Karty host boundary, runtime, GRF-02 | frozen `c7de9f2`; post-baseline `c1b576e` dotyka tylko backlogu i GRF-02 close | bieżące prace | `UNKNOWN`; heterogeniczna rodzina | równoległy docs commit; sensitive wysokie | oddzielić frozen/post-baseline |
| KS-057 | `eod_drafts/**` (843 pliki) | Raporty, skrypty, JSON/CSV/logi/dane | wiele dat i commitów | różne | `UNKNOWN`; heterogeniczna rodzina | bardzo wysokie ryzyko danych | klasyfikować per artefakt; nie wykonywać poleceń |
| KS-058 | `docs/archive/**` | Jawne archiwum kwiecień–czerwiec | `cda1ab7`, 2026-07-03 | historia projektu | `HISTORICAL`; wysokie dla historii, niskie dla LIVE | imperatywy i PII/opsec | zachować; nigdy automatycznie nie wykonywać |
| KS-059 | `ZIOMEK_MASTER_KB.md`, `TECH_DEBT.md`, `LESSONS.md`, strategic audit | Historyczne KB/audyty/lekcje | różne, m.in. `0b01e46`, `82c4580`, `6161d23`, `e90a42d` | historia decyzji | `HISTORICAL`; średnie | niektóre lekcje pozostają użyteczne; wcześniejsze tokeny redagowane; stare bloby nieotwarte | zachować jako historię |
| KS-060 | dwa stare prompty audytowe, `docs/archive/SKILL.md` | Polecenia dla dawnych agentów | historyczne | nie są produktem | `HISTORICAL`; niskie dla kanonu | zawierają konfliktowe imperatywy i prompt injection risk | materiał dowodowy, nie instrukcja |
| KS-061 | pięć usuniętych Markdown w historii Git | Sprint2 analysis i ETA Bug1 docs | usunięte w `cbe566f` i revert `d5f90d0` | historia | `HISTORICAL`; średnie | możliwe dane wrażliwe; treść nieprzywracana | pozostawić w historii |
| KS-062 | `/root/CLAUDE.md` | Cross-project cheat-sheet | metadata: 37 376 B, mode `0600`, mtime epoch `1783700495`, SHA-256 `33cfcc…15bc` | host/workspace | `CONFLICTING`; średnie | część może być aktualna; PII/opsec bardzo wysokie | później zredukować do pointerów |
| KS-063 | `tools/llm_triage.py` | Embedded PRIMER/system prompts, OpenRouter triage/judge | `9a77067`, 2026-06-26 | ręczne narzędzie, nie hot path | `CONFLICTING`; niskie dla kanonu | docstring przeczy sieci/zapisowi/Telegram; egress wysokie | nie wykonywać bez osobnego scope; Prompt 02 tylko jako antydowód |
| KS-064 | `memory:ZIOMEK_REGULY_PROSTO.md`, `feedback_rules.md`, `lessons.md`, `priorytety-stabilnosc-jakosc-skala-2026-07-03.md`, pozostałe tematyczne `memory/*.md` | Drugorzędne reguły, feedback, lekcje i pamięć tematyczna | różne, m.in. `bf5368d`, `34b5772`, `c2f958f`, `02cb28e` | historia decyzji i praktyk | `PARTIALLY_CURRENT`; średnie | sensitive wysokie; możliwy drift | używać przez indeks/temat, weryfikować kanonem |
| KS-065 | backupy indeksu/pamięci | Kopie i wygenerowane snapshoty | różne | recovery pamięci | `GENERATED`; niskie dla bieżącej prawdy | może zawierać stare dane | nie traktować jako current |
| KS-066 | `eod_drafts_a2/**` | Wygenerowany raport i JSON parytetu E2E | `12dbf3c`, 2026-07-08 | parity/eval | `GENERATED`; średnie przy SHA | częściowo oparte na realnych danych; sensitive wysokie | dowód czasowy, nie kanon |
| KS-067 | `runtime:effective-process/config/fingerprint/health` | Procesy, efektywne flagi i health w oknie audytu | obserwacja 2026-07-12 14:09–14:45Z | zachowanie efektywne | `CURRENT_CANDIDATE`; wysokie dla chwili obserwacji | `/etc` nie atestuje pamięci procesu; opsec bardzo wysokie | najwyższy dowód stanu, czasowo ograniczony |

## Konflikty jawne

| ID | Strona A | Strona B | Stan |
|---|---|---|---|
| C-KH-01 | aktualna decyzja: R6 35 normalnie, 40 tylko alarm, nigdy klasa | ADR/mapy/stary prompt i miejsca kodu: 35/40 tier-aware/per-klasa | intencja rozstrzygnięta; drift dokumentacji/implementacji; otwarty trigger/exit alarmu |
| C-KH-02 | ADR-008: brak fizycznego rdzenia | `CODEMAP`/`ARCHITECTURE`: żywy `core/` po 06.07 | opis faktów ADR przestarzały |
| C-KH-03 | human flag inventory: parser drift | machine registry + runtime: drift closed, parser v2 | nowszy runtime wygrywa dla stanu efektywnego |
| C-KH-04 | Logic Reference: current/not committed | Git: tracked, historyczny rdzeń z korektami | traktować częściowo/historycznie |
| C-KH-05 | starszy project overview jako ground truth | aktualny kanon + runtime/code | starszy dokument niekanoniczny |
| C-KH-06 | archiwalne prompty/SKILL jako imperatywy | aktualne instrukcje i kanon | historia, nie instrukcje |
| C-KH-07 | ancestor `/root/.openclaw/workspace/AGENTS.md` | `/root/.codex/AGENTS.md` i aktualny prompt | ancestor jest aplikowalny, lecz na konfliktujących tematach dispatchu zostaje przebity przez nowsze jawne polecenie i kanon Codex |
| C-KH-08 | stare baseline’y testów | niedawny raportowany baseline 5152/27/8/0, niererunowany w Prompt 01 | liczby muszą mieć SHA, czas i provenance |
| C-KH-09 | handover jako aktualna mapa | brak Git/provenance | potwierdzać kodem/runtime |

## Wyszukiwanie i wyłączenia

Skan nazw i treści tracked obejmował kategorie AGENTS/CLAUDE/cloud/memory/roadmap/plan/todo/audit/report/ADR/decision/journal/prompt/architecture/runbook/incident/postmortem/GPS/ETA/route/dispatch/score/constraint/operator/feedback/correction/learning/model/eval/benchmark/golden/test/deploy/migration/Docker/Kubernetes/Terraform/CI. Reprezentatywne źródła otwierano do poziomu pozwalającego ustalić temat, aktualność i konflikty.

Wyłączono:

- zabronione ścieżki sekretów, env, kluczy, certyfikatów i `.git/config`;
- stare bloby mogące zawierać credential-like wartości;
- raw runtime state/logi/GPS/PII i chroniony rejestr pełnych nazw;
- pickle, obrazy i PDF, gdy istniał tekstowy odpowiednik;
- masową treść JSONL/CSV/logów oraz pełną treść 843 EOD;
- fakty zewnętrzne w research integracyjnym — bez sieci i logowania.

Brak tracked `cloud*.md`, repo-local `AGENTS.md` i potwierdzonego katalogu promptów w hot path produktu. Istnieje ręczne `tools/llm_triage.py` z promptami i egress OpenRouter, lecz nie znaleziono jego harmonogramu ani dowodu wpływu na decyzję dispatchera.

## Pochodzenie i potencjalni właściciele

Właściciela zapisywano rolą, bez danych osobowych: aktualne decyzje biznesowe — właściciel produktu; kod/mapy/ADR — repo engineering; pamięć/handoff — koordynator sesji; registry/SBOM/audyt — wskazany generator i commit; runtime/systemd — operator hosta; dane GPS/ETA — producenci wskazani w `ASSET_AND_DATA_REGISTER.md`. Gdy commit, generator lub writer nie dawał bezpiecznego i jednoznacznego właściciela, pozostawiono `UNKNOWN` zamiast wnioskować z nazw lub danych osobowych.
