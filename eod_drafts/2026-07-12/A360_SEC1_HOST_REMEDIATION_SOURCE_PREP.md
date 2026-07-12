# A360-SEC1 HOST-REMEDIATION — raport SOURCE/PREP

- **Stan:** SOURCE/PREP, **NOT APPLIED / HOLD**
- **Worktree:** `/root/a360_sec1_wt/dispatch_v2`
- **Branch:** `security/a360-sec1-host-remediation`
- **Frozen base:** `0891b06e9e894d88d6bd8a8b9dd9f837cf12a1e0`
- **Integrator / FLIPMASTER:** root, tmux58
**Wykonawca:** tmux75

Ten sprint nie zmienił bindu, firewalla, routingu, provider firewall, kontenera,
unitu, `/etc`, poświadczenia, flags, live state ani procesu produkcyjnego. Nie
wykonał daemon-reloadu, restartu, recreate, deployu, rotacji, chmod carriera ani
zewnętrznego skanu. Powstały wyłącznie source contracts, testy i runbook.

## 1. Werdykt

Host nadal jest niezabezpieczony w rozumieniu A360-SEC1 i prawidłowy werdykt to
`HOLD`.

Read-only auditor uruchomiony na frozen base o `2026-07-12T08:42:33Z`, po
zmianach source oraz finalnie po drugim review o `10:20:26Z`, za każdym razem
zwrócił exit 2, osiem tych samych findingów oraz
`mutations_performed=false`:

- `PUBLIC_V4_BIND_8767`, owner `courier-api.service`;
- `PUBLIC_V4_BIND_9222` i `PUBLIC_V6_BIND_9222`, owner
  `openclaw-browser`;
- brak docelowego deny dla INPUT v4/v6;
- brak docelowego deny dla DOCKER-USER v4/v6;
- `PROVIDER_FIREWALL_UNKNOWN`.

UFW pozostaje `INACTIVE`. Tool nie emituje PID, surowych adresów, image, argv,
Environment, EnvironmentFile, credentialu ani raw ruleset. Po rozszerzeniu
dodatkowo pokazuje sześć braków koperty remediation; nie zmniejsza to ani nie
zastępuje ośmiu findingów granicy.

## 2. ETAP 0 i baseline

- worktree, branch i HEAD dokładnie zgodne ze zleceniem; startowy status clean;
- tmux75 jest ownerem tego worktree, tmux58 jedynym integratorem/FLIPMASTEREM;
- tmux74 V214 i tmux76 E1 mają osobne worktree oraz rozłączne write-sety;
- `atq`: tylko job 214 na `2026-07-13 12:15 UTC`;
- `courier-api.service`: active/running, MainPID obecny, `NRestarts=0`;
- live unit ma nadal owner `0:0`, mode `0644`, size 553 B i mtime
  `2026-04-16T16:17:02.283810331Z`; treści nie czytano;
- worktree carrier `/root/a360_sec1_wt/flags.json`: regular, root:root, `0444`;
  nie czytano ani nie zmieniano jego treści;
- wspólny full-test lock był używany przez jeden runner tmux75; owner
  potwierdzono wyłącznie PID/comm/cwd, bez argv.

Baseline przed edycją, oba biegi pod
`/tmp/ziomek_full_regression.lock` i z wymaganym
`ZIOMEK_SCRIPTS_ROOT=/root/a360_sec1_wt`,
`PYTHONPATH=/root/a360_sec1_wt`, `DISPATCH_UNDER_PYTEST=1`:

| wariant | UTC start–end | wynik | czas | load start→end |
|---|---|---|---:|---|
| DEFAULT | `08:52:20Z–08:57:03Z` | 5155 pass / 24 skip / 8 xfail / 0 fail / 0 XPASS | 281.03 s | 2.00/1.58/1.31 → 1.72/1.80/1.47 |
| STRICT | `08:57:03Z–09:01:27Z` | 5105 pass / 74 skip / 8 xfail / 0 fail / 0 XPASS | 261.57 s | 1.72/1.80/1.47 → 2.29/1.88/1.58 |

Listy skip/xfail odpowiadają wersjonowanemu N0 i znanej kwarantannie STRICT.
To +1 pass wobec ostatniego SEC0 po zintegrowanym follow-up N0, bez nowego
nodeidu, skipa, xfaila lub faila.

## 3. Root cause i ownership

### 8767

Auditor koreluje listener z `courier-api.service`. Source należy do osobnego
repo `/root/.openclaw/workspace/scripts/courier_api`, które pozostało clean na
`master@fa249e678aa3e15641e6440b10a972df830010f5`. Bezpieczne targeted lookupy
samych nazw plików dały:

- bind consumer: `main.py`;
- definicja `COURIER_ADMIN_PASS`: `config.py`;
- `ADMIN_PASS` consumers: `config.py`, `routes/admin.py` oraz test hardening.

Nie wypisano linii, wartości ani całego configu. Preimage tracked kodu pozostaje
zgodny z SEC0: `config.py`
`16e0c8d6cbf05f8a0a618f994c05d8b2ae5a494b9548c5c3255ade0ca7c9d887`,
`main.py` `9bfe43878634414c9932e34b7a9afb1b2d1ade24f745b9be62263f542d392e16`.

Realny patch należy do nowego worktree repo `courier_api`, przydzielonego przez
integratora. Ta sesja tylko definiuje dokładny plan: loopback-only startup gate,
`LoadCredential`, zero process fallbacku, fail-closed owner/mode/symlink/nlink,
redakcja i pozytywna/negatywna próba rewizji.

### 9222

Owner runtime jest `openclaw-browser`, ale source manifest, repo owner i
immutable image digest nadal są `UNKNOWN`. Nie wykonano `docker inspect`, raw
config dumpu, odczytu argv/env ani recreate. Obecna nazwa/tag runtime nie jest
source of truth. Recreate pozostaje zablokowany do manifestu zgodnego z
`A360_SEC1_CONTAINER_MANIFEST.schema.json`.

### Host/provider

Brak trwałego ownera docelowych reguł jest przyczyną, dla której historyczna
deklaracja ochrony nie stanowi dowodu. Source plan używa pojedynczego jumpu na
pozycji 1 i dedykowanych chainów, symetrycznie dla IPv4/IPv6 i
INPUT/DOCKER-USER. Provider nie może być zazieleniony z danych lokalnych;
schema wymaga attachment do bieżącego hosta, świeżego export hash i exact deny
`[8767,9222]` dla obu rodzin.

### Credential

SEC0 zarejestrował wyłącznie metadane wcześniejszego carriera: root:root,
`0644`, 221 B i mtime `2026-07-05T19:02:05Z`. SEC1 nie otwierał, nie hashował i
nie rediscoverował tego pliku przez EnvironmentFile ani process environment.
Ponieważ dozwolone źródła nie dają aktualnej ścieżki carriera, bieżące metadane
pozostają w tym lane `UNKNOWN/HOLD`; nie odziedziczono ich jako świeżego proof.

Docelowy receipt dotyczy wyłącznie nowej rewizji: regular file, nofollow,
root:root, 0600, nlink 1, size >0, mtime obecne; nowa rewizja PASS, poprzednia
REJECTED. Treść i hash credentialu są zabronione. Rollback zawsze tworzy
następną nową rewizję.

## 4. Co zmieniono w source

### Auditor

`tools/host_boundary_audit.py` zachowuje dotychczasowy `--live` i dodaje:

- ręcznie walidowany `a360.sec1.evidence-bundle.v2`;
- root-only loader exact filename `A360_SEC1_EVIDENCE.json`, O_NOFOLLOW,
  inode parity, owner/mode/nlink/size cap i bez surowych błędów;
- allowlistę pól każdej sekcji i detekcję pól zabronionych;
- source contract: exact owners/binds, Courier API, immutable browser manifest,
  host plan, credential plan i rollback policy;
- provider proof, niezależne probes, host receipt skorelowany z fingerprintem
  bieżących reguł, credential receipt i rollback preconditions;
- wspólny `observation_id` oraz politykę czasu v1: max-age 900 s,
  future-skew 30 s i mutual-skew 300 s dla provider/probes/receipts/rollback;
- source contract v2 związany z tym samym observation ID;
- exact porównanie immutable browser image z live Docker output wyłącznie
  in-memory; image ma `repr=False` i nigdy nie jest emitowane;
- świeży `a360.sec1.courier-api-deployment-receipt.v1`: provenance
  wersjonowanego provisionera, exact source commit/config/main hashes oraz
  bieżący PID/unit/stany porównywane in-memory;
- stabilne reason codes; raw wartości bundle nigdy nie trafiają do outputu;
- `--validate-evidence` oraz `--live --evidence`.

Sam schema PASS nie zabezpiecza hosta. `--live --evidence` może zwrócić PASS
wyłącznie przy loopback listenerach, zgodnych ownerach, widocznych deny,
receipt hash zgodnym z bieżącym rulesetem, provider proof, pełnych independent
v4/v6 denied oraz allowed HTTPS/API/tunnel, nowej rewizji i bezpiecznym
rollbacku, exact live image oraz zgodnym świeżym postimage API.

### Ops/source templates

- `A360_SEC1_CONTAINER_MANIFEST.schema.json` + template;
- `A360_SEC1_PROVIDER_PROOF.schema.json` + template;
- `A360_SEC1_COURIER_API_DEPLOYMENT_RECEIPT.schema.json` + template;
- `A360_SEC1_HOST_FIREWALL_PLAN.json`;
- `A360_SEC1_EVIDENCE.template.json`.

Wszystkie template’y są celowo `SOURCE_PREP_NOT_INSTALLED` lub zawierają
`null/UNKNOWN/NOT_RUN`. Brakujące wartości są reason codes, nie fallbackiem.
Host firewall plan nie ma funkcji apply, timera ani live ownera.

### Runbook

`docs/runbooks/A360_SEC1_HOST_REMEDIATION.md` definiuje:

- exact desired state i ownerów;
- osobny cross-repo patch Courier API;
- immutable manifest przed recreate;
- single-jump idempotency i kolejność v4/v6;
- drugą sesję administracyjną i safe tunnel;
- backup bez treści/hasha credentialu;
- provider/host/bind/credential kolejność;
- allowed+denied matrix, gdzie IPv6 `NO_ROUTE` jest fail;
- rollback utrzymujący provider/host deny i loopback, nigdy starą rewizję.

## 5. Mapa kompletności

| miejsce | rola | writer / consumer | TAK / N-D | powód | test/dowód |
|---|---|---|---|---|---|
| `tools/host_boundary_audit.py` | verdict + evidence | safe collectors / FLIPMASTER | TAK | source addytywny | live HOLD, golden bundle, runtime binding, redaction |
| `tests/test_host_boundary_audit.py` | oracle | pytest/N0 | TAK | te same 11 nodeidów | v4/v6, owner, order, provider, postimage, symlink/mode, rollback |
| container schema/template | immutable source contract | manifest owner / FLIPMASTER | TAK | contract gotowy, dane realne brak | mutable tag i wildcard fail |
| browser live postimage | runtime image binding | Docker collector / auditor | TAK | exact in-memory, zero emisji | wrong image przy tej samej nazwie/porcie fail |
| provider schema/template | external proof | provider owner / auditor | TAK | lokalnie zawsze UNKNOWN | missing v4/v6/attachment/time fail |
| Courier API deployment receipt | source→runtime postimage | versioned deployer / auditor | TAK contract, N-D live | producer/patch/deploy brak | stale/missing/source/PID/unit mismatch fail |
| INPUT v4/v6 | host boundary | provisioner / kernel | TAK source, N-D live | brak ACK/instalacji | exact order validator; live NO_TARGET_DENY |
| DOCKER-USER v4/v6 | container boundary | provisioner / Docker path | TAK source, N-D live | brak ACK/instalacji | exact order validator; live NO_TARGET_DENY |
| `courier_api` config/main/routes | bind + auth | inne repo / systemd/API | N-D | brak przydzielonego worktree | preimage + exact patch/deployment-receipt plan |
| `courier-api.service` | process owner | `/etc` / API | N-D | live i zabronione | allowlist properties + stat only |
| `openclaw-browser` manifest | port owner | obce/nieudowodnione repo | N-D | source UNKNOWN | schema + HOLD recreate |
| provider firewall | zewnętrzna granica | provider | N-D | API/zmiana zabronione | proof schema, status UNKNOWN |
| nowy credential | auth boundary | przyszły `LoadCredential` | TAK contract, N-D live | rotacja zabroniona | metadata-only/symlink/mode/revision tests |
| allowed/denied probes | efekt | independent vantage | N-D | maintenance/ACK brak | schema; syntetyczne NO_ROUTE fail |
| rollback | recovery | FLIPMASTER | TAK plan, N-D drill | brak live | unsafe preconditions fail |
| flags/HARD/SOFT/runtime state | poza zakresem | dispatch | N-D | jawny zakaz | final diff/smoke |

## 6. Testy i review

Stan po drugim review i finalnym freeze:

- py_compile/import: PASS;
- targeted DEFAULT `11/11`, targeted STRICT `11/11`, klaster
  auditor+N0+hermetic guard `36/36`;
- malicious synthetics: wildcard/non-loopback v4/v6, unexpected owner,
  conditional/late deny, zła kolejność IPv6, malformed rules, provider unknown,
  missing IPv4/IPv6, `NO_ROUTE`, runtime rules receipt mismatch, mutable source
  image, **wrong live image przy tej samej nazwie/porcie**, brak/stary/future lub
  source/PID/unit-mismatched deployment receipt API, unknown manifest owner,
  arbitrary/prohibited field, stdout redaction, evidence symlink/mode,
  credential symlink/mode i unsafe rollback;
- time controls: stale/future dla provider/probes/host/API deployment/
  credential/rollback, mutual skew 301 s, observation mismatch i poprawne
  granice 900/30/300 s;
- golden proof: pełny bundle v2 + loopback + exact runtime image + zgodny świeży
  API receipt i pozostałe receipts daje PASS; każda powyższa mutacja daje HOLD;
- raw live image, source path/commit/hash i PID są używane wyłącznie in-memory;
  testy potwierdzają, że nie trafiają do JSON ani repr;
- wszystkie JSON artefakty parsują się bez duplikatów kluczy;
- sensitive-output scan: brak key block, URL userinfo i przypisań sekretów;
- `git diff --check`: PASS.

Pierwsza regresja po initial freeze, oba biegi pod tym samym flockiem:

| wariant | UTC start–end | wynik | czas | load start→end |
|---|---|---|---:|---|
| DEFAULT | `09:28:10Z–09:32:47Z` | 5155 pass / 24 skip / 8 xfail / 0 fail / 0 XPASS | 275.61 s | 0.49/0.50/0.71 → 1.61/1.16/0.95 |
| STRICT | `09:32:47Z–09:37:10Z` | 5105 pass / 74 skip / 8 xfail / 0 fail / 0 XPASS | 261.14 s | 1.61/1.16/0.95 → 1.67/1.50/1.15 |

DEFAULT i STRICT były dokładnie zgodne z baseline, ale **nie są finalnym dowodem
kodu**: review integratora po tym biegu wykrył brak świeżości i wzajemnego
wiązania czasów external probes, host receipt i credential receipt; provider
window także nie wiązał capture z bieżącą obserwacją. Zgodnie z C44 oba biegi są
`SUPERSEDED` i pozostają wyłącznie host-load do sensitivity at-214.

Fix-forward dodaje wspólny observation ID, max-age/future-skew/mutual-skew oraz
negatywne stale/future/skew dla provider, probes, host, credential i rollback,
wraz z testami dokładnych granic 900/30/300 s.

Regresja po pierwszym review/time-fix, również pod jednym flockiem:

| wariant | UTC start–end | wynik | czas | load start→end |
|---|---|---|---:|---|
| DEFAULT | `09:48:20Z–09:53:00Z` | 5155 pass / 24 skip / 8 xfail / 0 fail / 0 XPASS | 278.68 s | 0.24/0.51/0.77 → 2.67/1.65/1.17 |
| STRICT | `09:53:00Z–09:57:23Z` | 5105 pass / 74 skip / 8 xfail / 0 fail / 0 XPASS | 260.95 s | 2.67/1.65/1.17 → 1.57/1.63/1.28 |

Drugi pełny review wykrył, że source contract obrazu i API nie były jeszcze
wiązane z faktycznym runtime postimage. Biegi `09:48–09:57` są dlatego również
`SUPERSEDED` jako dowód kodu i pozostają wyłącznie host-load. Surowego outputu
wejściowego review nie skopiowano do raportu.

Drugi fix-forward zachowuje exact image tylko in-memory i porównuje je z source
contract; API wymaga wersjonowanego, świeżego deployment receiptu z tym samym
observation ID, zgodnymi source commit/config/main hashes oraz bieżącym
PID/unitem. Brak proof, wrong image, stale/future lub każdy mismatch daje HOLD.

**Finalna regresja po drugim review**, pod jednym
`/tmp/ziomek_full_regression.lock` i wymaganym env:

| wariant | UTC start–end | wynik | czas | load start→end |
|---|---|---|---:|---|
| DEFAULT | `10:11:03Z–10:15:42Z` | 5155 pass / 24 skip / 8 xfail / 0 fail / 0 XPASS | 277.01 s | 0.48/0.77/0.91 → 1.78/1.43/1.17 |
| STRICT | `10:15:42Z–10:20:04Z` | 5105 pass / 74 skip / 8 xfail / 0 fail / 0 XPASS | 259.58 s | 1.78/1.43/1.17 → 1.66/1.73/1.37 |

Finalne sumy są dokładnie zgodne z baseline. Nie dodano nodeidów do
wersjonowanego mianownika N0; 11 testów SEC1 rozszerzono wewnątrz istniejących
nodeidów. Wszystkie pełne interwały zapisano jako host-load; zgodnie ze
zleceniem nie edytowano wspólnego registry.

## 7. Ochrona danych i near-miss guard

Nie odczytano `.env`, sekretów, EnvironmentFile, pełnego argv,
`/proc/*/environ`, container environment ani treści credentialu. Nie wykonano
hasha credentialu. Jeden łączony command próbujący zebrać wyłącznie metadane
został zablokowany przez PreToolUse przed wykonaniem; nie otworzył ani nie
wyświetlił chronionego pliku. Następne wywołania rozdzielono na dozwolone
`stat/ls` source metadata, bez obchodzenia guarda.

`ls` repo Courier API ujawnił wyłącznie nazwy i metadane plików; w tym repo nie
było `.env` na przeglądanym poziomie. Targeted `git grep -l` zwrócił tylko nazwy
plików-konsumentów, bez linii i wartości.

## 8. Operacje live

Wykonano: read-only auditor, allowlisted `systemctl show`, stat source/unit,
targeted file-name lookup i testy. Nie wykonano żadnej mutacji live.

| obszar | wykonano? |
|---|---|
| provider API/firewall | NIE |
| host firewall/network | NIE |
| bind/API patch/deploy | NIE |
| `/etc`/unit/drop-in | NIE |
| credential read/hash/chmod/rotation | NIE |
| Docker inspect/recreate/image change | NIE |
| restart/daemon-reload/deploy | NIE |
| flags/HARD/SOFT/live state | NIE |

PID/NRestarts nie zmieniły się wskutek sprintu; enforcement pozostaje N-D, bo
nie istnieje flaga ani wdrożenie SEC1.

Finalny read-only smoke o `10:20:26Z` po regresji: `courier-api`, `dispatch-shadow` i
`dispatch-panel-watcher` active/running, każde `NRestarts=0`; parser health
HTTP 200 bez odczytu body; `atq` nadal zawiera wyłącznie job 214. Auditor nadal
HOLD z tymi samymi ośmioma findingami. Nie wykonano restartu ani health probe
z treścią odpowiedzi.

## 9. Backup i rollback plan

SOURCE rollback to jawny `git revert` przyszłych commitów tej gałęzi; ponieważ
nie było deployu, nie wymaga restartu ani operacji live. Template’y i auditor
nie mają consumera/timera.

Przyszły live rollback jest zdefiniowany przed operacją:

- provider i host deny pozostają;
- bind pozostaje loopback;
- API może wrócić tylko do loopback-compatible kodu;
- kontener tylko do poprzedniego pinned digest/manifestu z loopback publish;
- credential zawsze do następnej nowej rewizji, nigdy starej;
- naprawa rulesetu dotyczy dedykowanego chaina, nie pełnego restore otwartego
  rulesetu;
- po rollbacku pełne allowed/denied v4/v6, health, PID/NRestarts i nowy receipt.

## 10. Otwarte ACK i blocker list

Do realnego maintenance nadal brakuje:

1. osobnego ACK na provider, host firewall, bind, `/etc`/unit, rotację,
   recreate, deploy i restarty;
2. drugiej aktywnej sesji administracyjnej;
3. osobnego worktree/commita patcha `courier_api`;
4. wersjonowanego deploy provisionera Courier API i zatwierdzonej policy jego
   receiptu;
5. świeżego deployment receiptu API utworzonego po realnym starcie i zgodnego
   z postimage/PID/unitem;
6. udowodnionego source ownera, manifestu i immutable digest browsera;
7. wersjonowanego provisionera host guard z persistence ownerem;
8. świeżego provider proof przypiętego do hosta;
9. listy ownerów wszystkich konsumentów nowej rewizji;
10. approved maintenance poza peakiem;
11. niezależnego vantage z realnym IPv4 i IPv6;
12. realnych allowed/denied probes i rollback drill.

Do zamknięcia wszystkich punktów nie wolno ogłaszać hosta zabezpieczonym ani
LIVE.

## 11. Git i przekazanie

Lokalny commit initial implementation:
`27e84800b758ad5f28d0ab6462efaf15aece8175`; powstał tuż przed review
integratora i samodzielnie zawiera oba później wykryte false-PASS; nie został
wypchnięty oddzielnie. Oba blockery są domknięte fix-forward commitem
`bb8bcfc3d74e6f8903ad2c1b4f7d63044b2e45e0`, utworzonym dopiero po finalnych
targeted i pełnych DEFAULT/STRICT. Branch tip zawiera oba commity jako jedną
nierozdzielną serię; initial commit nie jest kandydatem do cherry-pick bez
fix-forward.
Commit finalnego raportu: commit zawierający ten plik; exact SHA jest
samorozwiązywalny przez `git log -1 --format=%H -- <ten plik>` i zostaje też
zapisany w snapshotcie/finalnym handoffie po commicie.
Branch jest pushowany dopiero jako komplet po commicie raportu; exact push
parity i snapshot 0600 są weryfikowane po pushu, aby zapisać finalny HEAD bez
fałszywego self-hasha.

Zgodnie ze zleceniem nie edytowano `ZIOMEK_BACKLOG.md`, memory, handover,
`flags.json`, raportów V214/E1 ani `CLAIM_LEDGER_HARD_GATE_CARD.md`.
