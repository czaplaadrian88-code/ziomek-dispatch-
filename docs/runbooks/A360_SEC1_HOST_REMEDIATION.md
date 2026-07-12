# A360-SEC1 HOST-REMEDIATION — runbook maintenance

- **Status:** SOURCE/PREP, NOT APPLIED, host verdict `HOLD`
- **Właściciel przyszłej operacji:** integrator / FLIPMASTER
- **Zakres tego artefaktu:** kontrakt, kolejność, dowody i rollback; zero zmian live

Ten runbook nie jest ACK na provider firewall, reguły hosta, bind, `/etc`,
rotację poświadczenia, recreate kontenera, restart ani deploy. Wykonanie dowolnej
z tych operacji wymaga jawnego ACK Adriana w bieżącej sesji maintenance.

## 1. Stan wejściowy i granica twierdzenia

Read-only auditor z `2026-07-12T08:42:33Z` potwierdził:

- `8767`: `PUBLIC_WILDCARD_V4`, owner `courier-api.service`;
- `9222`: `PUBLIC_WILDCARD_V4` i `PUBLIC_WILDCARD_V6`, owner
  `openclaw-browser`;
- INPUT v4/v6 i DOCKER-USER v4/v6: brak docelowego deny;
- UFW inactive;
- provider firewall: `UNKNOWN`, brak zewnętrznego proof;
- wynik `HOLD`, osiem findingów, `mutations_performed=false`.

Source/PREP nie zmienia tego stanu. Nawet kompletny template nie jest dowodem
instalacji. Host może zostać nazwany zweryfikowanym dopiero wtedy, gdy po
zatwierdzonym maintenance są równocześnie: bezpieczne bindy, trwałe reguły obu
rodzin, provider proof, zgodny receipt rulesetu oraz niezależne allowed/denied
probes, exact immutable browser postimage i świeży receipt postimage Courier
API związany z bieżącym PID/unitem. Brak trasy IPv6 nie jest dowodem deny.

## 2. Dokładny stan docelowy

| granica | oczekiwany owner | bind/publish | ruch bezpośredni | zatwierdzona ścieżka |
|---|---|---|---|---|
| `8767/tcp` | `courier-api.service` | `127.0.0.1:8767` | DENIED v4 i v6 | lokalny proxy/HTTPS |
| `9222/tcp` | `openclaw-browser` | `127.0.0.1:9222` | DENIED v4 i v6 | tunel administracyjny |
| INPUT | wersjonowany provisioner | dedykowany chain, anchor 1 | deny 8767+9222 | loopback nieblokowany |
| DOCKER-USER | ten sam provisioner | dedykowany chain, anchor 1 | deny 9222 | loopback nieblokowany |
| provider | udowodniony ruleset przypięty do hosta | n/d | deny 8767+9222 v4/v6 | SSH/proxy według osobnego allow |
| credential API | systemd `LoadCredential` | nowa root-only rewizja | stara rewizja REJECTED | nowa rewizja PASS |

Loopback IPv6 nie jest domyślnie publikowany. Można go dodać tylko po wskazaniu
konkretnego konsumenta i rozszerzeniu kontraktu/testów; wildcard v4/v6 i każdy
non-loopback pozostają fail-closed.

## 3. Artefakty kontraktu

- `ops/security/A360_SEC1_CONTAINER_MANIFEST.schema.json` — source manifest,
  immutable digest, loopback publish, wolumeny/sieci/capabilities, health i
  restart policy bez raw argv ani wartości środowiska.
- `ops/security/A360_SEC1_CONTAINER_MANIFEST.template.json` — celowo niepełny;
  obecne `null` są blockerami recreate, nie domyślnymi wartościami.
- `ops/security/A360_SEC1_HOST_FIREWALL_PLAN.json` — symetryczny v4/v6 plan
  dedicated-chain; nie zawiera funkcji apply i nie jest zainstalowany.
- `ops/security/A360_SEC1_PROVIDER_PROOF.schema.json` oraz template — proof
  providera z attachment state, hashem eksportu i exact denied ports v4/v6.
- `ops/security/A360_SEC1_COURIER_API_DEPLOYMENT_RECEIPT.schema.json` oraz
  template — provenance wersjonowanego deployera, tracked postimage i
  allowlisted snapshot bieżącego unitu/PID.
- `ops/security/A360_SEC1_EVIDENCE.template.json` — wspólna koperta source,
  provider, probes, host receipt, deployment receipt Courier API, credential
  receipt i rollback preconditions.
- `tools/host_boundary_audit.py` — read-only audit plus walidacja koperty 0600.

Template skopiowany do change record musi nazywać się dokładnie
`A360_SEC1_EVIDENCE.json`, być regularnym plikiem root:root, mode 0400 albo 0600,
`nlink=1` i nie może być symlinkiem. Auditor czyta go z `O_NOFOLLOW`, nie emituje
wartości pól i przy każdym braku zwraca stabilny reason code.

Koperta v2 ma jeden wspólny `observation_id` także w source contract i we
wszystkich proof/receiptach oraz
wersjonowaną politykę `a360.sec1.evidence-time-policy.v1`:

- max age względem bieżącego `observed_at_utc`: 900 s;
- max future skew: 30 s;
- max wzajemny skew provider/probes/host/API deployment/credential/rollback:
  300 s.

Wartości są częścią kontraktu, nie ustawieniami operatora. Ich zwiększenie albo
brak wspólnego observation ID jest HOLD i wymaga nowej wersji polityki/review.

## 4. Source lanes przed maintenance

### 4.1 Courier API — osobne repo i worktree

Aktualny udowodniony preimage repo `courier_api` to
`fa249e678aa3e15641e6440b10a972df830010f5`. Pliki `config.py` i `main.py`
mają odpowiednio preimage SHA-256
`16e0c8d6cbf05f8a0a618f994c05d8b2ae5a494b9548c5c3255ade0ca7c9d887` i
`9bfe43878634414c9932e34b7a9afb1b2d1ade24f745b9be62263f542d392e16`.
SEC1 nie edytuje tego repo bez osobnego worktree przydzielonego przez integratora.

Patch w tym przyszłym worktree ma:

1. Ustawić loopback IPv4 jako jedyny dozwolony bind i odrzucać przy starcie
   wildcard v4/v6 oraz non-loopback; bez cichego fallbacku.
2. Zachować port 8767 i kontrakt HTTPS; zmiana portu nie należy do sprintu.
3. Czytać nową rewizję logiczną `COURIER_ADMIN_PASS` wyłącznie przez
   `CREDENTIALS_DIRECTORY`/`LoadCredential`.
4. Zatrzymać start przy braku, pustym pliku, symlinku, `nlink!=1`, ownerze innym
   niż root:root lub mode innym niż 0600.
5. Usunąć fallback do wartości procesu. Log, repr, exception i health nie mogą
   zawierać wartości ani ścieżki carriera.
6. Mieć test pozytywny nowej rewizji i negatywny poprzedniej wyłącznie kodami
   statusu; żadnego porównywania lub logowania treści.
7. Wersjonowany deploy provisioner ma po udanym starcie utworzyć świeży receipt
   `a360.sec1.courier-api-deployment-receipt.v1`: własny commit/hash artefaktu,
   postimage commit oraz SHA-256 tracked `config.py`/`main.py`, a także
   allowlisted Id/stany/MainPID odczytane po starcie. Receipt musi odpowiadać
   policy w source contract i wspólnemu observation ID; ręcznie przepisana
   deklaracja bez udowodnionego provisionera jest HOLD.

Unit/drop-in i daemon-reload są osobną operacją live za ACK. EnvironmentFile,
pełne Environment i `/proc/*/environ` nie są źródłami dowodowymi.

### 4.2 `openclaw-browser` — manifest przed recreate

Obecny owner runtime jest znany, lecz source manifest, repo owner i immutable
digest pozostają nieudowodnione. Zabronione jest rekonstruowanie `docker run`
z pamięci, raw argv, `docker inspect` dumpu lub bieżącego mutowalnego tagu.

Owner ma dostarczyć manifest zgodny z
`A360_SEC1_CONTAINER_MANIFEST.schema.json`. Kontrakt wymaga:

- source repository/path/commit/hash;
- image reference `name@sha256:<64 hex>`;
- dokładnie jeden publish `127.0.0.1:9222 -> 9222/tcp`;
- jawne logiczne wolumeny, sieci, capabilities, `no_new_privileges`,
  read-only-rootfs decision, healthcheck ID i restart policy;
- `raw_argv_recorded=false` i `environment_values_embedded=false`.

Tag-only, brak wolumenu/sieci/health, source `UNKNOWN` albo próba dodania `::`,
`0.0.0.0` czy non-loopback oznacza STOP przed recreate.

Podczas `--live --evidence` auditor zachowuje pole image wyłącznie w pamięci i
porównuje je bajt-w-bajt z `image_reference` source contract. Nazwa kontenera,
owner `docker-proxy` i poprawny port nie wystarczają. Tag-only, inny digest pod
tą samą nazwą albo brak pola image daje HOLD. Image, path i hash nie są
emitowane w JSON ani w reason code.

### 4.3 Host firewall — idempotentny kontrakt

Plan używa osobnych chainów, aby nie przepisywać całego istniejącego rulesetu.
Dla IPv4 i IPv6 kolejność jest identyczna:

1. dokładnie jeden jump z parent chain na pozycji 1;
2. `RETURN_IF_LOOPBACK`;
3. INPUT: `DROP_NEW_TCP_DPORTS_8767_9222`; DOCKER-USER:
   `DROP_NEW_TCP_DPORT_9222`;
4. końcowy `RETURN`.

Provisioner ma rekoncyliować chain i pojedynczy jump, a nie dopisywać kolejne
reguły. Zły anchor, ACCEPT/RETURN przed deny, conditional source-only deny,
brak jednej rodziny albo brak DOCKER-USER to HOLD. Jeśli host/Docker nie ma
efektywnej ścieżki IPv6 przez DOCKER-USER, nie wolno oznaczyć jej PASS-em —
wymagana jest udowodniona równoważna granica oraz niezależny probe.

Ten sprint dostarcza source plan, nie provisioner apply. Przed live plan musi
dostać wersjonowanego ownera, source commit i artifact SHA-256; dopiero jego
receipt może zostać porównany przez auditor z fingerprintem bieżących reguł.

### 4.4 Provider proof

Lokalny host nigdy nie ustala statusu providera. Dowód ma pochodzić ze świeżego
eksportu kontrolowanego przez ownera i zawierać:

- jawny provider różny od `UNKNOWN`;
- attachment `ATTACHED_TO_CURRENT_HOST`;
- SHA-256 niewrażliwego eksportu rulesetu;
- exact denied ports `[8767, 9222]` osobno dla IPv4 i IPv6;
- `captured_at_utc` i ograniczone `valid_until_utc`.

`captured_at_utc` musi dodatkowo mieścić się w tej samej polityce świeżości i
observation ID co receipt hosta, credentialu, rollback i niezależne probes.
Samo odległe `valid_until_utc` nie odświeża starego provider proof.

Nie wpisuje się publicznych adresów hosta, tokenów API, danych konta ani raw
odpowiedzi providera do evidence bundle. Provider API pozostaje poza zakresem
tej sesji.

## 5. Bramka GO/NO-GO maintenance

Wszystkie punkty muszą być spełnione jednocześnie:

1. Jawny ACK na provider, host firewall, live bind, `/etc`/unit, rotację,
   recreate, deploy i kontrolowane restarty.
2. Okno poza peakiem, brak równoległego deployu/restartu i wolny wspólny lock.
3. Dwie działające sesje administracyjne; druga osoba potwierdza nowy login i
   utrzymuje sesję do końca smoke.
4. Zielone source/targeted/full testy zamrożonych commitów.
5. Courier API postimage commit/hash, zgodny patch bez process fallbacku i
   gotowy wersjonowany producer świeżego deployment receiptu.
6. Zgodny container manifest oraz immutable obecny i poprzedni image digest.
7. Wersjonowany host provisioner z dry-run/check, single-jump reconciliation i
   persistence po restarcie/reboocie.
8. Provider proof zgodny ze schematem.
9. Lista ownerów wszystkich konsumentów credentialu, bez wartości.
10. Niezależny vantage z działającym IPv4 i IPv6; `NO_ROUTE` nie spełnia bramki.
11. Root-only change record oraz backupy z sekcji 6.

Jeden brak oznacza STOP i `SOURCE/PREP / HOLD`.

## 6. Backup bez starego credentialu

W root-only katalogu change record zapisać:

- branch/commit/postimage hash Courier API i manifestu kontenera;
- niewrażliwy eksport reguł hosta v4/v6 i provider proof;
- allowlistę properties unitów: Id/LoadState/ActiveState/SubState/MainPID/
  NRestarts oraz metadane źródłowego unitu;
- immutable bieżący i poprzedni image digest oraz source manifest;
- pre/post audit JSON i wszystkie czasy UTC;
- dla starego i nowego carriera wyłącznie existence, regular/symlink, uid/gid,
  mode, nlink, size i mtime.

Nie wolno backupować, hashować ani wyświetlać zawartości starego credentialu.
Nie wolno zapisywać argv, Environment, EnvironmentFile, container environment,
tokenów providera ani surowych danych konta.

## 7. Kolejność zatwierdzonej operacji live

1. **Freeze:** potwierdź identity worktree/commit, ownerów, brak kolizji i
   wszystkie bramki. Uruchom pre-audit; oczekiwany wynik przed zmianą to HOLD.
2. **Backup:** utwórz change record z sekcji 6 i zwaliduj jego odczyt bez
   dotykania starej wartości.
3. **Safe path:** druga sesja potwierdza nowy SSH login. Przygotuj i sprawdź
   tunel do przyszłego loopback 9222. Nie zamykaj pierwszej sesji.
4. **Provider deny:** po ACK zastosuj deny 8767/9222 v4/v6 i potwierdź attachment.
   SSH/HTTPS allow pozostaje niezależne od tych portów.
5. **Host deny:** po ACK zainstaluj/reconcile dedykowane chainy obu rodzin.
   Dry-run musi przed apply pokazać dokładnie jeden jump i oczekiwaną kolejność.
6. **Courier API:** wdroż zatwierdzony commit i unit `LoadCredential`, aktywuj
   nową rewizję, wykonaj daemon-reload i jeden kontrolowany restart. Dopiero po
   starcie wersjonowany provisioner tworzy deployment receipt z aktualnym
   PID/unitem i exact tracked postimage; receipt sprzed restartu jest nieważny.
7. **Browser:** dopiero z zatwierdzonego manifestu i immutable digest wykonaj
   pojedyncze recreate do loopback-only publish.
8. **Credential:** potwierdź nową rewizję pozytywnie i poprzednią negatywnie
   kodem statusu. Starej wartości nie używaj jako rollbacku.
9. **Evidence:** zbierz host receipt, provider proof, deployment receipt Courier
   API, credential receipt i probe matrix. Skopiuj template jako
   `A360_SEC1_EVIDENCE.json`, uzupełnij bez danych wrażliwych, nadaj source
   contract i wszystkim sekcjom jeden observation ID oraz ustaw root-only mode.
   Od pierwszego do ostatniego proof nie może minąć więcej niż 300 s; inaczej
   odśwież cały zestaw, nie tylko najstarszą sekcję.
10. **Verify:** uruchom auditor z evidence. Każdy reason code lub HOLD oznacza
    niedokończoną operację; nie ogłaszaj zabezpieczenia.

`dispatch-telegram` nie należy do tego sprintu i nie jest restartowany.

## 8. Obowiązkowa macierz probe

| probe | vantage | oczekiwane | wynik niedopuszczalny |
|---|---|---|---|
| direct 8767 IPv4 | niezależna sieć v4 | DENIED | ALLOWED / ERROR / NOT_RUN |
| direct 8767 IPv6 | niezależna sieć v6 | DENIED | ALLOWED / NO_ROUTE / NOT_RUN |
| direct 9222 IPv4 | niezależna sieć v4 | DENIED | ALLOWED / ERROR / NOT_RUN |
| direct 9222 IPv6 | niezależna sieć v6 | DENIED | ALLOWED / NO_ROUTE / NOT_RUN |
| canonical HTTPS | zatwierdzony klient | ALLOWED | direct-port fallback |
| local API health | host loopback | ALLOWED | body/secrets w raporcie |
| CDP przez tunel | druga sesja | ALLOWED | bezpośredni publiczny 9222 |
| nowa rewizja | zatwierdzony klient | PASS | wartość w output/logu |
| poprzednia rewizja | zatwierdzony klient | REJECTED | stara wartość działa |

Probe zapisuje tylko ID, rodzinę, status i UTC. Nie zapisuje adresów, GPS, danych
użytkownika, credentialu ani body odpowiedzi.

## 9. Rollback — deny i loopback są inwariantem

Rollback wolno rozpocząć tylko gdy evidence potwierdza:

- drugą aktywną sesję;
- działające host/provider deny;
- loopback binds;
- przygotowaną następną nową rewizję credentialu;
- zakaz przywrócenia poprzedniej wartości;
- pinned poprzedni image digest.

Procedura:

- błąd API: wróć do poprzedniego **loopback-compatible** kodu, użyj następnej
  nowej rewizji, zachowaj provider/host deny;
- błąd credentialu: unieważnij wadliwą rewizję i wygeneruj następną; stara nie
  jest rollbackiem;
- błąd kontenera: wróć do poprzedniego immutable digest i manifestu, ale nadal
  publikuj tylko loopback; bez znanego digestu/repo rollback jest HOLD;
- błąd reguły: z drugiej sesji napraw dedykowany chain. Nie przywracaj całego
  starego otwartego rulesetu i nie usuwaj provider deny;
- po każdym rollbacku powtórz pełną macierz probe, PID/NRestarts/health i audit
  z nowym receipt.

Jeżeli odzyskanie usługi wymaga publicznego bindu, usunięcia deny albo starego
credentialu, STOP i eskalacja do Adriana — to nie jest dozwolony rollback.

## 10. Reason codes i redakcja

Klasy blokujące:

- `SOURCE_*`, `COURIER_API_*`, `BROWSER_*` — brak lub mutable source contract;
- `FIREWALL_PLAN_*_ORDER_INVALID` — zła rodzina/chain/anchor/kolejność;
- `PROVIDER_PROOF_*` — provider unknown, brak attachment/deny/czasu;
- `EXTERNAL_PROBE_*` — brak niezależności, IPv4/IPv6 lub allowed path;
- `HOST_RULE_RECEIPT_*` — brak persistence, order proof albo runtime mismatch;
- `COURIER_API_DEPLOYMENT_RECEIPT_*` / `COURIER_API_RUNTIME_*` — brak/stary
  receipt, niezgodny producer/postimage albo mismatch bieżącego PID/unitu;
- `BROWSER_RUNTIME_*` — live image nie jest exact immutable postimage source
  contract, nawet gdy nazwa/port/owner się zgadzają;
- `CREDENTIAL_REVISION_*` / `CREDENTIAL_RECEIPT_*` — missing, symlink, owner,
  mode, nlink, empty, brak odrzucenia starej rewizji;
- `ROLLBACK_*` — rollback mógłby otworzyć granicę lub przywrócić credential;
- `EVIDENCE_*` — zła nazwa, owner/mode, symlink, rozmiar, JSON albo pole
  zabronione, niewłaściwa wersja time policy, stale/future proof lub wzajemny
  skew ponad 300 s.

Auditor emituje tylko kody, klasy bindu, logicznych ownerów i agregaty. Surowe
adresy, PID, image reference, repo/file path, commit/hash z proof, wartości
credentialu i złośliwe pola nie trafiają do JSON.

## 11. Komendy walidacyjne dla FLIPMASTER-a

Po skopiowaniu i uzupełnieniu template w root-only change record:

```bash
/root/.openclaw/venvs/dispatch/bin/python tools/host_boundary_audit.py \
  --validate-evidence /approved/change-record/A360_SEC1_EVIDENCE.json

/root/.openclaw/venvs/dispatch/bin/python tools/host_boundary_audit.py \
  --live --evidence /approved/change-record/A360_SEC1_EVIDENCE.json
```

Pierwsza komenda dowodzi wyłącznie spójności koperty. Dopiero druga wiąże
receipty z bieżącym rulesetem, listenerami, exact live image oraz PID/unitem API.
`PASS` bez niezależnych probe i obu runtime postimage bindingów nie jest
osiągalny przez kontrakt. W tej sesji żadnej z komend evidence/live-apply nie
wykonano; aktualny host pozostaje `HOLD`.
