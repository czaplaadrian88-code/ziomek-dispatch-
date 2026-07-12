# A360-SEC0 HOST-BOUNDARY-CREDENTIAL — raport SOURCE/PREP

**Stan:** SOURCE/PREP, **NOT APPLIED**
**Data dowodu runtime:** 2026-07-11 UTC
**Worktree:** `/root/a360_sec0_wt/dispatch_v2`
**Branch:** `security/a360-sec0-host-boundary-truth`
**Frozen base:** `1cf6ae4bdc52223ff0accafdea5fdadd593c70cf`

Ten sprint nie zmienił bindu, firewalla, routingu, provider firewall, kontenera,
unitu, poświadczenia, flags, live state ani procesu produkcyjnego. Nie wykonał
daemon-reloadu, restartu, deployu ani zewnętrznego skanu.

## 1. Werdykt

Problem nadal istnieje. Finalny read-only audytor o 2026-07-12T00:00:02Z zwrócił
kontrolowany exit 2 / `HOLD` i osiem ustaleń:

- publiczny wildcard IPv4 na 8767, owner `courier-api.service`;
- publiczny wildcard IPv4 oraz IPv6 na 9222, owner `openclaw-browser`;
- brak docelowego deny w INPUT dla IPv4 i IPv6;
- brak docelowego deny w DOCKER-USER dla IPv4 i IPv6;
- provider firewall `UNKNOWN` z powodu braku świeżego zewnętrznego proof.

UFW jest nieaktywne, bazowa polityka INPUT jest ACCEPT. Lokalny status nie może
zazielenić providera. Ochrona hosta opisana w historycznym runbooku nie jest
obecna: bieżące łańcuchy nie mają reguł docelowych, a wpis odtwarzający reguły
po starcie jest nieobecny. Host nie rebootował po dacie tamtej deklaracji, więc
to nie jest wyjaśnienie utraty. Wniosek: poprzednia kontrola była nietrwała albo
nie miała skutecznego ownera; nie wolno traktować jej jako LIVE.

## 2. ETAP 0 i brak kolizji

- start: branch i worktree zgodne ze zleceniem, HEAD równy frozen base, clean;
- tmux71 jest ownerem tego worktree; aktywne tory E0 i DATA0 pracują w osobnych,
  czystych worktree; integrator ma osobną sesję;
- dozwolony write-set używa unikalnych nazw i nie pokrywa się z plikami torów;
- `atq`: tylko sensitivity job 214; pełne testy tego sprintu są serializowane
  przez `/tmp/ziomek_full_regression.lock` i ich dokładne okna UTC są niżej;
- `dispatch-shadow` i `dispatch-panel-watcher` były active/running, po zero
  restartów procesu; parser health był `healthy`, bez anomalii;
- carrier flags w worktree pozostał kopią `0444`, SHA-256
  `568436f3de693d048a73bf1a2ba5c23e65191a350ef2e566fa5b6f34ace848bf`;
  nie był otwierany do edycji ani modyfikowany.

Wspólny baseline integratora na frozen base:

`DEFAULT 2026-07-11T23:05:55Z..23:10:40Z — 5143 passed, 24 skipped,
8 xfailed, 0 failed/XPASS, 147 warnings, 281.88 s`.

Różnicę +3 pass/-3 skip wobec wcześniejszego biegu stanowi znany zegarowy
self-skip `test_preshift_window`; ocena końcowa używa listy, nie samej liczby.

## 3. Ownerzy i proweniencja

### 8767 / courier API

- cgroup i MainPID wiążą listener z `courier-api.service`;
- unit: active/running, PID 925329, `NRestarts=0` w odczycie ETAP 0;
- source repo `/root/.openclaw/workspace/scripts/courier_api`: clean, HEAD
  `fa249e678aa3e15641e6440b10a972df830010f5`;
- `config.py`: owner `0:0`, mode `0644`, SHA-256
  `16e0c8d6cbf05f8a0a618f994c05d8b2ae5a494b9548c5c3255ade0ca7c9d887`;
- `main.py`: owner `0:0`, mode `0644`, SHA-256
  `9bfe43878634414c9932e34b7a9afb1b2d1ade24f745b9be62263f542d392e16`;
- live unit: existence confirmed; owner `0:0`, mode `0644`, size 553 B,
  mtime 2026-04-16T16:17:02Z; hash nie jest dopuszczonym proof;
- celowany extractor nazw pól wskazał konsumentów w `config.py`,
  `routes/admin.py` i teście hardening. Żadnych wartości nie wypisał.

Kanon aplikacji korzysta z pośredniej ścieżki HTTPS, a lokalny smoke tej ścieżki
zwrócił status 200 bez body. Source config nginx nie został udowodniony: guard
zablokował szerszy odczyt i nie był obchodzony. Dlatego nginx source proof jest
otwartą bramką, nie domysłem.

Poprawna lokalizacja źródła aplikacji to `/root/courier-app`: branch
`fix-tomtom-hero-crash-vc72`, HEAD
`740d2dd8a68d4199c8bef793711b4e697802fa36`. Tracked
`app/build.gradle.kts` ma owner `0:0`, mode `0644`, SHA-256
`60cae49d375965e200e20d68d4bcf1b63beca0e01f910c69e365b3487153c846`.
Zastany untracked katalog `docs/` nie został dotknięty. Wcześniejsza pomyłka
ścieżki w poleceniu metadata nie jest findingiem i nie jest preimage.

### 9222 / browser container

- `ss` wiąże listenery z `docker-proxy`, a bezpieczny `docker ps` z kontenerem
  `openclaw-browser`;
- kontener nie ma compose project/service/config labels;
- obraz jest wskazany mutowalnym tagiem; immutable digest i source manifest są
  `UNKNOWN`;
- lokalny CDP status zwrócił 200 dla loopback v4 i v6, bez body. Nie jest to
  test dostępności zewnętrznej ani proof providera.

Nie wykonano `docker inspect`, dumpu konfiguracji ani odczytu process argv/env.
Recreate pozostaje zabroniony do chwili dostarczenia manifestu ownera.

### Poświadczenie administracyjne

Audit360 ma otwartą rotację. Zawartość live carriera została jednokrotnie,
niedopuszczalnie odczytana przez operację hash. Nie została wyświetlona,
zinterpretowana ani powtórzona. Do dowodu dopuszczono wyłącznie istnienie,
owner `0:0`, mode `0644`, size 221 B i mtime 2026-07-05T19:02:05Z. Obliczony
hash usunięto z artefaktów i operacji nie ponowiono.
Runbook migruje źródło na root-only nową rewizję i usuwa fallback do wartości
procesu. Awaria rotacji oznacza kolejną nową rewizję, nigdy powrót do starej.

## 4. C36 — jawny zapis nadmiarowego odczytu

Przed korektą integratora wykonawca po wcześniejszym targeted search wykonał
jeden pełny odczyt tracked `courier_api/config.py`. Zakres narzędzia objął cały
plik. Narzędzie zwróciło treść do wewnętrznego transkryptu narzędziowego sesji;
nie została ona skopiowana do commentary, finalu ani artefaktów repo. Live
carrier nie został otwarty.

Osobny near-miss: w ETAP 0 policzono hash chronionego carriera. Operacja
zwróciła hash do wewnętrznego transkryptu narzędziowego, ale taki hash czyta
chronioną treść i nie jest dopuszczalnym proof. Nie jest powtórzony ani
commitowany; dla carriera pozostają wyłącznie existence/owner/mode/size/mtime.

Po C36 nie wykonano kolejnego pełnego odczytu configu. Dalsze operacje używały
wyłącznie nazw pól, listy nazw plików, hashy jawnego tracked kodu/manifestu,
metadanych chronionych plików i redagowanych klasyfikacji. Guardów
nginx/openclaw nie obchodzono. Końcowy skan nowych
artefaktów pod kątem przypisań pól, danych osobowych i surowych dumpów:
**PASS**. Wynik: zero przypisań pól auth, e-maili, URL userinfo, bloków kluczy
i zakazanych źródeł procesowych. Liczba literalnych SHA-256 odpowiada wyłącznie
flags oraz dozwolonym tracked code/manifestom; chroniony carrier i live unit nie
mają hasha w artefaktach.

## 5. Mapa kompletności

| miejsce | rola | writer / consumer | TAK / N-D | powód | test/dowód |
|---|---|---|---|---|---|
| `tools/host_boundary_audit.py` | read-only verdict | safe commands / FLIPMASTER | TAK | nowe, bez mutacji | public v4/v6, owner, guard, redakcja |
| `tests/test_host_boundary_audit.py` | negative controls | pytest | TAK | syntetyczne dane | targeted + full suite |
| ten raport | trwały evidence handoff | integrator / FLIPMASTER | TAK | unikalna ścieżka sprintu | diff, sensitive scan, review |
| `courier_api/config.py` | bind + auth source | systemd / Uvicorn/admin | N-D | inne repo; tylko patch plan | preimage hash + przyszłe testy |
| `courier-api.service` | uruchomienie | systemd / API | N-D | live i source unit poza write-set | allowlist properties + existence/owner/mode/size/mtime |
| carrier administracyjny | auth source | systemd / API | N-D | jednorazowy niedopuszczalny odczyt przez hash; live mutation zabroniona | existence/owner/mode/size/mtime, plan nowej rewizji |
| `openclaw-browser` manifest | publish 9222 | Docker / CDP | N-D | source owner nieudowodniony | safe `docker ps`; HOLD recreate |
| INPUT/DOCKER-USER v4/v6 | host boundary | kernel / ruch | N-D | live zmiana wymaga ACK | klasyfikacja `NO_TARGET_DENY_RULE`; skuteczność nieudowodniona |
| provider firewall | zewnętrzna granica | provider / ruch | N-D | brak świeżego proof | zawsze `UNKNOWN` lokalnie |
| nginx source | approved 443 path | proxy / aplikacja | N-D | guard; source proof brak | local status 200 nie wystarcza |
| runbook | przyszłe wykonanie | FLIPMASTER | TAK | backup→safe path→test→restrict→health | self-review |
| core/flags/live state | poza zakresem | dispatch runtime | N-D | jawny zakaz | końcowy diff/hash |
| entropy/flag lifecycle | guard fundamentu | tools | N-D | brak zmian silnika/flag/fundamentu | scope review |

## 6. Narzędzie i kontrakty bezpieczeństwa

`tools/host_boundary_audit.py`:

- wywołuje tylko `ss`, allowlistę `systemctl show`, bezpieczny format
  `docker ps`, status UFW oraz reguły INPUT/DOCKER-USER v4/v6;
- uruchamia subprocessy z minimalnym środowiskiem i wyciszonym stderr;
- nie czyta dowolnych ścieżek ani pliku provider proof;
- nie emituje PID, surowego adresu, obrazu, nieznanego ownera ani raw stdout;
- nie używa argv/cmdline procesów, process environment ani Docker Environment;
- `*_RULE_SEEN` i `*_POLICY_SEEN` są tylko obserwacją; kolejność, warunki i
  efektywna ścieżka pakietu pozostają nieudowodnione i nadal wymuszają `HOLD`;
- celowo nigdy nie zazielenia providera na podstawie lokalnych danych;
- zwraca znormalizowany JSON i `mutations_performed=false`.

## 7. Plan remediation i rollback

Pełna, wykonywalna kolejność jest w
`docs/runbooks/A360_SEC0_HOST_BOUNDARY_RUNBOOK.md`:

1. backup kodu, manifestów, reguł i metadanych carriera — bez jego treści;
2. druga sesja i safe tunnel/allow path;
3. isolated test patcha i nowej rewizji;
4. provider + host deny v4/v6, loopback binds, aktywacja nowej rewizji;
5. health oraz allowed/denied z niezależnej sieci dla v4/v6.

Rollback utrzymuje deny i loopback. API może wrócić do kompatybilnego kodu,
kontener do poprzedniego immutable image, ale poświadczenie zawsze przechodzi na
następną nową rewizję. Publiczny bind, pełny restore otwartego rulesetu i stara
wartość nie są dozwolonym rollbackiem.

## 8. Testy i host-load

- `py_compile` + import: PASS;
- targeted: `11 passed in 0.67s`;
- live tool 2026-07-12T00:00:02Z: exit 2 / `HOLD`, osiem findingów zgodnych z
  oracle, `mutations_performed=false`;
- finalny DEFAULT pod flock, 2026-07-12T00:00:44Z..00:05:25Z:
  `5154 passed, 24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings`, 279.42 s;
  load `1.02/0.99/1.03` → `1.39/1.39/1.19`;
- finalny STRICT pod flock, 2026-07-12T00:05:34Z..00:09:59Z:
  `5104 passed, 74 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings`, 263.12 s;
  load `1.18/1.34/1.18` → `1.70/1.44/1.24`;
- DEFAULT względem wspólnego baseline: dokładnie +11 pass z nowego pliku,
  niezmienione 24 skip/8 xfail i zero nowej regresji;
- sensitive scan: PASS;
- staged `diff --check`: PASS po finalnym docs-only truth correction;
- niezależny review integratora: wykonany; wymusił usunięcie hashy chronionych
  plików, korektę ścieżki app, `RULE_SEEN` zamiast proof i conditional-rule test;
- bounded self-review: dodatkowo wykrył niepełną korelację ownera 9222, względną
  ścieżkę CLI i brak raportu w mapie. Wszystkie findings poprawiono przed
  finalnymi testami.

Okna pełnych biegów są jawnie przeznaczone do oceny sensitivity job 214;
zgodnie ze zleceniem nie zmieniam wspólnego rejestru ani memory.

Finalny read-only smoke 2026-07-12T00:12:50Z: `courier-api.service` PID 925329,
`dispatch-shadow.service` PID 573430 i `dispatch-panel-watcher.service` PID
3659486 — wszystkie active/running, `NRestarts=0`. Direct API, kanoniczna ścieżka
HTTPS i parser health zwróciły status 200 bez body. Carrier flags nadal ma mode
`0444` i SHA zgodne z wartością z ETAP 0. Job 214 pozostaje jedyną pozycją `atq`.

## 9. Otwarte ACK i blokery

- ACK na provider firewall i zatwierdzony proof;
- ACK na host firewall v4/v6;
- ACK na patch/deploy bindu i daemon-reload/restart API;
- ACK na rotację i skoordynowanie wszystkich konsumentów;
- source manifest, immutable digest i owner `openclaw-browser`;
- ACK na recreate kontenera;
- druga sesja administracyjna;
- ACK na test z niezależnej sieci, allowed+denied v4/v6;
- source proof nginx i potwierdzenie kanonicznej ścieżki przed restrict.

Do zamknięcia tych bramek prawidłowy werdykt to **NOT APPLIED / HOLD**.
