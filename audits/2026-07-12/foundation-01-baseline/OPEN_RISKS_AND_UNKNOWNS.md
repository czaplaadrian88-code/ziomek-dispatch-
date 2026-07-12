# Otwarte ryzyka i niewiadome

Skala wpływu: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`. Pewność dotyczy ustalenia, nie przyszłego skutku. W tym etapie nie wdrażano remediacji.

## Rejestr priorytetowy

| ID | Typ | Wpływ / pilność | Pewność | Odwracalność | Ryzyko lub niewiadoma | Dowód | Wymagana późniejsza decyzja |
|---|---|---|---|---|---|---|---|
| P01-REP-001 | FAKT+WNIOSEK | CRITICAL / przed zmianą decyzji | wysoka | niska bez provenance | HEAD nie zawiera całego zachowania: config, flags, wagi, primary model i state są zewnętrzne | `common.py:12-17,65-105`; `dispatch_pipeline.py:67-93`; `ml_inference.py:37-47`; `plan_manager.py:32-33` | techniczna: release manifest i provenance |
| P01-RUN-001 | NIEWIADOMA | HIGH / przed deployem | wysoka co do braku dowodu | średnia | brak dokładnego build ID/dirty hash załadowanego przez każdy proces | CMD-006 exit 0 w oknie 14:09–14:45Z; `systemd/README.md:3-12`; `/proc` tylko cwd/exe; brak embedded ID | techniczna |
| P01-DR-001 | FAKT | HIGH / przed ryzykowną migracją | wysoka | niska w awarii | joby backupu są zielone, ale realny restore/RTO/RPO pozostają HOLD; live/source backup script się różnią | CMD-006 exit 0; SHA `5f5615…e9e8` vs `565df5…007d`; sentinel `df2c91…8deb`; karty DR1A/DR1B | techniczna + operacyjny ACK |
| P01-DATA-001 | FAKT | HIGH / przed replayem historycznym | wysoka | niska | whole-map GPS/GT jest niewersjonowane; retencja eventów/logów ogranicza rekonstrukcję | `docs/eta/06_ground_truth_contract.md:40-49,89-112`; metadata world record/events | produktowa dla KPI, techniczna dla lineage |
| P01-SEC-001 | FAKT | HIGH / pilne | wysoka | średnia | wiele live aktywów i DB z klasami danych GPS/sesji/ID ma mode `0644` | CMD-002 metadata-only i CMD-011 schema-only, exit 0; agregaty w `ASSET_AND_DATA_REGISTER.md` | techniczna + privacy |
| P01-NET-001 | FAKT+NIEWIADOMA | HIGH / pilne | wysoka dla listenerów, niska dla ekspozycji zewnętrznej | średnia | listenery na `0.0.0.0`/`::`; host INPUT accept i provider firewall niezweryfikowany; port debug 9222 byłby szczególnie krytyczny przy braku filtra | CMD-008 exit 0; bez zewnętrznego skanu | infrastrukturalna |
| P01-DEP-001 | FAKT | HIGH / przed rebuildem | wysoka | średnia | brak lockfile i pełnych manifestów; SBOM błędnie nazywa wszystko spoza manifestu transitive | manifesty; `tools/dependency_inventory.py:127-135` | techniczna |
| P01-MODEL-001 | FAKT | HIGH / przed replayem ML | wysoka | niska dla historii | primary LGBM v1.1 i residual ETA są poza Git; część modeli/encoderów tracked, pickle wymaga zaufanego provenance | `ml_inference.py:43-57,173-176,684-689`; metadata zewnętrznych katalogów | techniczna |
| P01-KNOW-001 | FAKT | HIGH / Prompt 02 | wysoka | wysoka | dokumenty R6/core/flags/current logic są sprzeczne lub historyczne; zasada 35 normalnie/40 alarm jest rozstrzygnięta, drift nie | ADR-001, ADR-008, mapy, flag inventory, aktualna instrukcja | produktowa tylko dla trigger/exit/precedencji alarmu; techniczna dla driftu |
| P01-TOOL-001 | FAKT | HIGH / przed uruchamianiem narzędzi | wysoka | średnia | `offline`, `read-only`, `dry-run` bywają fałszywymi etykietami | `tools/llm_triage.py:1-7,55-89,176-183,217-297`; legacy migration | techniczna |
| P01-TEST-001 | FAKT | MEDIUM / przed kolejną pełną regresją | wysoka | wysoka | hermetic guard nie blokuje globalnie sieci/chmury, subprocess fail-open; DEFAULT ma live-read quarantine | `conftest.py:18-21,40-81`; `docs/HERMETIC_TESTS.md:22-38` | techniczna |
| P01-GIT-001 | FAKT | MEDIUM / teraz | wysoka | wysoka | równoległa sesja przesunęła source `master` po zamrożeniu | Git HEAD/timestamp, tmux | koordynacja sesji |
| P01-INFRA-001 | FAKT | HIGH / przed rebuildem hosta | wysoka | niska | repo nie ma pełnego IaC/unit mirror; kontenery używają również mutable tags | `systemd/README.md:3-12`; `docker ps/inspect` metadata | infrastrukturalna |
| P01-EVENT-001 | FAKT | MEDIUM / przed uznaniem DLQ za recovery | wysoka | średnia | retry schema live, ale auto-retry OFF i brak workera; requeue ręczny | `event_retry.py:27,314-320`; `replay_dead_letter.py:8,106-112` | techniczna/operacyjna |
| P01-PII-001 | FAKT | HIGH / pilne | wysoka | średnia | tracked fixtures/źródła i historyczne materiały zawierają klasy danych osobowych; nie cytowano ich | nazwy ścieżek i schema, bez treści | privacy + repo hygiene |
| P01-PRIV-001 | FAKT | HIGH / pilne | wysoka | średnia | większość głównych usług dispatch/panel/API działa jako root | `systemctl show -p User -p Group` | infrastrukturalna / least privilege |
| P01-AUTO-001 | FAKT | MEDIUM / koordynacja sesji | wysoka | średnia | 134 enabled timers i 31 aktywnych wpisów crona działają niezależnie; cron zawiera godzinny push i historyczny skrypt EOD | `systemctl list-unit-files`, zredagowane `crontab -l` | operacyjna / ownership |
| P01-SECRETS-001 | FAKT+NIEWIADOMA | MEDIUM / pilne | wysoka dla katalogu, brak dla plików | średnia | katalog magazynu sekretów ma mode `0755`; dowodzi widoczności nazw/metadanych lokalnie, nie odczytu wartości | dozwolony metadata-only `stat`; głębsza inspekcja zablokowana | security / permissions |

## Niewiadome bez rozstrzygnięcia w Prompt 01

1. Który dokładnie commit i dirty state działa w pamięci każdego procesu.
2. Czy provider firewall skutecznie ogranicza publiczną ekspozycję listenerów.
3. Czy najnowszy backup da się odtworzyć w zadanym RTO/RPO i spójnie z WAL/config/modelami.
4. Jak odtworzyć historyczny zestaw flag, env, rule weights i external models dla danej decyzji.
5. Czy istnieje poza odkrytymi lokalizacjami archiwum pełnej historii GPS/decyzji z legalnym provenance.
6. Które zdarzenie ma być kanonicznym KPI pickup i delivery handoff.
7. Jak brzmi ostateczny kanon R6 normal/alarm w każdym bliźniaku kodu.
8. Czy wszystkie ręczne unity/drop-iny mają właściciela, źródło i rollback.
9. Jak uruchomić całą suitę z twardą blokadą sieci i bez jakiegokolwiek live-read.
10. Pełny supply-chain status CVE/EOL; istniejący raport ma wartości `UNKNOWN`.

## Czego nie należy wnioskować

- Zielony backup timer nie oznacza udanego restore’u.
- `pip check` nie oznacza kompletnej deklaracji zależności.
- `healthy` parser nie oznacza zdrowia całego systemu.
- Tag rollout nie identyfikuje wszystkich procesów i danych.
- `mode=ro` SQLite chroni przed zapisem, ale schema nadal ujawnia klasę danych; dlatego raport zawiera tylko nazwy funkcjonalne.
- `dry-run` nie jest klasą bezpieczeństwa bez inspekcji implementacji.
- Kod pokazuje zachowanie zaimplementowane, nie rozstrzyga intencji produktu.
