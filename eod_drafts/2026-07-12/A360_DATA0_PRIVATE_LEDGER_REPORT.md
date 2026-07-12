# A360-DATA0 PRIVATE-LEDGER-RETENTION

Status: **SOURCE/PREP — NOT MERGED, NOT DEPLOYED, NOT INSTALLED**
Branch: `privacy/a360-data0-ledger-retention`
Worktree: `/root/a360_data0_wt/dispatch_v2`
Frozen base: `1cf6ae4bdc52223ff0accafdea5fdadd593c70cf`

## Wynik fazy

Powstał wspólny kontrakt źródłowy dla `shadow_decisions` i `world_record`:

- domyślny `compat` deleguje do starego writera i zachowuje bajtowy payload;
- `mirror` jest jawnym HOLD i failuje przed pierwszym zapisem, dopóki nie
  powstanie retry-safe outbox/transakcja dual-write;
- `private` zapisuje tylko pseudonimizowany artefakt 0600;
- każdy błąd konfiguracji, klucza, ścieżki lub zapisu w aktywnym non-compat jest
  fail-loud dla producenta; minimalny status bez identyfikatorów jest jedynie
  dodatkową telemetrią, nie substytutem propagacji;
- rekursywna klasyfikacja obejmuje identyfikatory, adres/lokalizację/GPS,
  nazwy, free text, dynamiczne klucze i sprzężone kontenery replay;
- stabilny pseudonim to HMAC-SHA256 z jawnym `scope` i zewnętrznym providerem;
  w repo, testach i raporcie nie ma rzeczywistego klucza;
- wersjonowana koperta ma uwierzytelnienie rekordu; rozpoznany private record bez
  klucza, z błędnym auth albo malformed/truncated JSON jest błędem wejścia;
- rotacja używa rename/reopen pod tym samym stabilnym lockiem co append;
  `copytruncate` nie jest zaimplementowany;
- retencja ma wyłącznie tryb `would-delete` po metadanych. Nie istnieje opcja
  apply ani kod kasowania.

Źródło świadomie nie udaje szyfrowania. Pseudonimizowany corpus zachowuje
kontrolowany replay bezpiecznej projekcji decyzji. Pełny replay wrażliwych
koordynatów wymaga osobno zatwierdzonego sealera/key-management i pozostaje
bramką przed `private`; nie wolno odtwarzać go przez zachowanie raw GPS.

## Dowód problemu bez odczytu PII

Potwierdzono wyłącznie metadane live: legacy artefakty i katalog mają zbyt
szerokie prawa (pliki 0644, katalog 0755), a obecna rotacja legacy shadow używa
`copytruncate`. Rozmiary są rzędu dziesiątek/setek MB zgodnie z wejściowym
dowodem sprintu. Nie otwierano treści live ledgera ani world records.

Chroniona próba carriera została zablokowana przez PreToolUse; klasa próby
została zatrzymana i nie nastąpił odczyt. Próby nie ponawiano innym narzędziem
ani ścieżką.

## ETAP 0 i kolizje

- worktree/branch/base potwierdzone przed planem;
- tmux 71 = SEC0 w osobnym worktree, tmux 72 = E0 w osobnym worktree,
  tmux 73 = DATA0 (ten sprint); nie wykryto wspólnego write-setu;
- procesy shadow, watcher i courier-api były active/running, bez restartów;
  parser był healthy; jedyny znany werdykt to at-214 na 2026-07-13 12:15 UTC;
- nie odczytywano efektywnych sekretów ani środowiska procesu;
- nie zmieniono live plików, praw, flag, unitów ani danych.

Incydent narzędziowy C32: raz użyto niedozwolonego widoku pełnego argv
procesów. Widoczny zakres obejmował wyłącznie proces testowy i blokadę; output
nie zawierał sekretów ani PII. Metoda została natychmiast wycofana. Dalsza
kontrola procesu/locka jest ograniczona do PID, `comm`, cgroup, cwd i `fuser`,
bez argv oraz bez `/proc/*/cmdline` i `/proc/*/environ`.

Drugi incydent narzędziowy: wzorzec read-only `rg` zawierał niepoprawnie
cytowany backtick, przez co shell spróbował uruchomić literalną nazwę polecenia.
Polecenie nie istniało, nie nastąpiła mutacja ani odczyt danych. Dalsze komendy
nie używają backticków w argumentach shella.

## Mapa kompletności

| Miejsce / rodzina | Rola | Writer / consumer | Status | Dowód / test |
|---|---|---|---|---|
| `shadow_dispatcher._serialize_candidate` | serializer A | producer | TAK | oba serializery dochodzą do wspólnego `_append_decision`; serializer regression |
| `shadow_dispatcher._serialize_result` + pola dokładane przed append | serializer B | producer | TAK | wspólny source boundary; OFF byte parity |
| `shadow_dispatcher._append_decision` | final shadow write | producer | TAK | policy router; błąd non-compat propaguje |
| `world_record._json_safe`, `_capture`, `around_assess` | world serializer/capture | producer | TAK | policy router; błąd propaguje; private nie uruchamia legacy GC |
| hooki `dispatch_pipeline` i `osrm_client` | źródła world capture | producer | N-D | payload/decision bez zmian; redakcja na jednym finalnym boundary |
| `core/jsonl_appender` | legacy O_APPEND/flock | writer | TAK/N-D | pozostaje kanonem wyłącznie `compat`; byte parity |
| `privacy/private_ledger` | policy/redactor/writer/reader/rotate | wspólny boundary | TAK | permission, race, auth, corruption, rotation, crash tests |
| `tools/ledger_io` + `_rotated_logs` raw lines | shadow canonical read | consumer | TAK | old/new, auth/key/corruption fail-loud; legacy malformed nadal skip |
| `tools/world_replay` | world canonical replay | consumer | TAK | old/new; plan+lock redirect sprzężony fail-closed |
| `tools/world_replay_gate`, `paired_flag_replay` | canonical downstream | consumer | TAK | regresje gate/parity przez poprawiony reader |
| dashboard/alert/brief: `daily_briefing`, `daily_stats_sheets`, `observability/data_alerts`, `observability/koord_cascade_monitor`, `tools/health_scoreboard`, `tools/latency_alarm` | direct/indirect legacy analytics | consumer | N-D w PREP | `compat` zachowuje wejście; wymagają migracji na canonical reader przed flipem `private` |
| replay/calibration family: `tools/*replay*`, `tools/decision_outcome_join`, `tools/shadow_outcome_enricher`, `tools/weight_calibration`, `ml_data_prep/online_shadow_parity` | replay/ML | consumer | N-D w PREP | zinwentaryzowane; żadnego private flipu przed reader migration matrix |
| Telegram/panel paths: `telegram_approver`, `panel_watcher`, `auto_assign_executor`, `czasowka_scheduler` | bieżące decyzje/alerty | consumer | N-D | nie czytają nowego private artefaktu w tej fazie; semantyka decyzji OFF bez zmian |
| `world_record` schema v1 + `private_ledger.v1` | schema | writer/reader | TAK | wersjonowana koperta, stary i nowy reader |
| `/etc/logrotate.d/dispatch-v2` | legacy shadow rotation | external writer | N-D / BLOCKED | istniejący `copytruncate` wykryty, lecz live config niezmieniony; przyszły rollout używa template rename/reopen |
| world daily file + legacy `_gc` | daily rotation/retention | writer | TAK | private blokuje legacy GC; brak delete |
| `deploy/private-ledger/*` | manual unit/runbook | deploy template | TAK | source-only, nic nie zainstalowano; brak timerów i harmonogramu |
| live backup job obejmujący state/logs | backup | consumer | N-D / ACK | zinwentaryzowany; nie uruchamiano i nie zmieniano; polityka backup retention wymaga B-05 |
| at-214 / corpus do 13.07 | sensitivity/verdict | consumer | HOLD | zero live change przed werdyktem |
| feasibility/scoring/selection/event/FSM | logika decyzji | poza zakresem | N-D | niezmienione; OFF parity i pełna regresja |

Repozytoryjny scan objął również historyczne `eod_drafts` i archiwa. Są
nieaktywne i pozostają N-D. Aktywacja `private` jest zabroniona, dopóki każdy
aktywny direct reader z powyższych rodzin nie przejdzie na wersjonowany decoder.

## Near-missy wykryte i zamknięte przed commitem

1. Pierwsza wersja mogła zwrócić ignorowany `degraded` po dowolnym wyjątku
   writera. Teraz non-compat zawsze propaguje; mirror zachowuje legacy, ale też
   głośno zgłasza private failure. Testy obejmują oba producery i invalid mode.
2. `O_NOFOLLOW` tylko na leaf nie chronił przed symlinkiem ancestora. Directory
   walk jest teraz komponentowy przez przypięte dirfd; test ancestor-symlink
   jest negatywnym oracle.
3. Redirect `PLANS_FILE` i `LOCK_FILE` w replay był pod broad `except`. Przy
   obecnym snapshot `plans` oba są sprzężone i każda porażka propaguje, aby
   replay nigdy nie wrócił do live locka.
4. Legacy JSON corruption i private corruption były zbyt łatwe do zlania.
   Legacy malformed zachowuje historyczny skip; rozpoznany/private-file
   malformed/truncated/auth/key miss jest fail-loud i nie zmniejsza mianownika.
5. Pierwszy szeroki targeted ujawnił, że replay przekierowywał `PLANS_FILE`,
   ale nie jego lock; HERMETIC-GUARD słusznie zatrzymał live-path access. Fix
   przenosi oba sprzężone pola razem, bez osłabienia guarda.
6. Carrier klucza miał początkowo `O_NOFOLLOW` tylko na leaf. Teraz używa tego
   samego component-wise dirfd walku i sprawdza inode przed i po odczycie.
7. Prywatny reader używał zwykłego `open`. Teraz private path wymaga 0600,
   current owner, nlink=1, bez symlinka leaf/ancestor; legacy reader zachowuje
   dotychczasową zgodność z 0644.
8. Redactor przepuszczał liczbowe wartości pól wrażliwych i warianty
   `pickup_lat`/`*_lng`/`*_lon`. Klasyfikacja działa teraz przed typem wartości,
   a malicious fixtures i mutation probe obejmują te przypadki.
9. Lock inode był sprawdzany przed, lecz nie po `flock`. Ponowna walidacja jest
   teraz obowiązkowa w append i rotate; race replacement failuje przed zapisem.
10. Dual-write mirror miał partial-write/retry duplicate risk. Zamiast udawać
    idempotencję, `mirror` jest HOLD; dwie próby nie tworzą żadnego artefaktu.
11. Component-wise create ujawnił w teście współbieżnym race dwóch pierwszych
    writerów. Create-or-open ponawia bezpieczny openat po `FileExistsError` i
    nadal waliduje typ/owner/mode; pełny klaster po fixie jest zielony.
12. Side-effect order w `world_record` wymagał osobnego oracla dla mirror HOLD.
    `legacy_gc_allowed` jest true wyłącznie dla `compat`, `private_mode_active`
    nie raportuje mirror, a test starego syntetycznego pliku potwierdza zero
    unlink, niezmienione mtime i zero nowych artefaktów przed fail-loud.
13. Reader i carrier korzystały z create-capable directory walku. Oba wywołują
    teraz jawny `create=False`; typo path failuje bez utworzenia katalogu.
14. Alfabetczny PII w dynamicznym kluczu mapy mógł przejść walidację nazwy
    pola. Jawny rejestr kontenerów dynamicznych pseudonimizuje ich klucze, lecz
    zachowuje bezpieczne nazwy pól schematu; malicious `courier_times` i mutation
    probe dowodzą obu stron kontraktu.
15. Pierwszy create pliku nie utrwalał directory entry. Writer rozróżnia
    istniejący open od `O_EXCL` create i po data fsync wykonuje fsync dirfd;
    hook testowy potwierdza kolejność file przed directory.
16. CLI rotacji domyślnie mutował dowolną ścieżkę, a template narzucał
    niezatwierdzony harmonogram. Domyślnie jest teraz `would-rotate`, apply jest
    tylko sandboxowy z odmową known-live, a timer został usunięty.
17. Migrator appendował częściowy cel i nie był retry-safe. Apply jest teraz
    jawnym HOLD; dry-run wymaga nieistniejącego celu, jawnej syntetycznej fixture
    i carriera 0600 wewnątrz sandboxu. Powtórzone dry-run/apply-HOLD nie zmieniają
    filesystemu.

Mutation probes obejmują: zmianę mode-at-create/0600, ujawnienie unknown string
przez redactor, brak reopen po rename/crash, próbę delete w dry-run, zmianę
private error na `continue` (mianownik 1→0) oraz odpięcie plan lock redirect.

## Testy i koszt

Baseline integratora (frozen base): UTC 2026-07-11 23:05:55–23:10:40,
5143 passed, 24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings, 281.88 s.

Własny baseline pre-implementation: UTC 2026-07-11 23:16:16–23:21:03,
5143 passed, 24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings, 284.58 s.

Pre-fix evidence po pierwszej implementacji: UTC 2026-07-11
23:33:11–23:37:53, 5160 passed, 24 skipped, 8 xfailed, 0 failed/XPASS,
147 warnings, 279.42 s. Ten bieg nie jest finalnym dowodem po review.

Targeted po wszystkich poprawkach review: 126 passed, 1 istniejący skip.
Pośredni bieg 120/121 wykrył opisany wyżej race create-or-open i nie został
uznany za zielony dowód.
Wcześniejszy targeted po pierwszej wersji: 111 passed, 1 skip.

Final DEFAULT: UTC 2026-07-12 00:10:08–00:14:47, **5175 passed,
24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings**, 276.59 s. Host load
start 1.88/1.49/1.26, end 1.71/1.51/1.32. Względem frozen baseline jest to
dokładnie +32 nowe testy; lista 24 skipów jest niezmieniona i nie zaszedł
zegarowy wariant `test_preshift_window`.

Final STRICT: UTC 2026-07-12 00:14:55–00:19:19, **5125 passed,
74 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings**, 262.54 s. Host load
start 1.66/1.50/1.32, end 1.30/1.58/1.41. Dodatkowe 50 skipów odpowiada
jawnej `hermetic_quarantine` (live read-only, zewnętrzne zależności i testy
zegarowe); lista nie zawiera nowej kwarantanny DATA0.

Próba finalnego DEFAULT 2026-07-11 23:51:50–23:53:36 została przerwana
zewnętrznym sygnałem (RC=143) przy około 76%; nie jest liczona jako dowód i
zostanie powtórzona w całości.
Druga próba 2026-07-11 23:54:41–23:55:03 również została przerwana zewnętrznym
sygnałem (RC=143) przy około 30%; także nie jest dowodem finalnym.
Trzecia próba 2026-07-11 23:57:14–23:58:29 została przerwana zewnętrznym
sygnałem (RC=143) przy około 76%; również nie jest dowodem finalnym.

Listy skipów obu finalnych biegów oceniono po nodeid/reason, nie samej sumie.

Syntetyczny benchmark, 200 rekordów (zero live data):

| Oś | legacy | private | koszt / wynik |
|---|---:|---:|---:|
| bytes | 941090 | 1075000 | 1.142292× (+14.23%) |
| gzip bytes | 7884 | 20374 | 2.584221× |
| redaction p50 / p95 | — | 0.707 / 0.801 ms | CPU cost |
| secure append p50 / p95 | — | 0.522 / 0.731 ms | fsync cost |

Pareto: zysk to pseudonimizacja, auth, 0600 i crash-safe append/rotate kosztem
rozmiaru oraz latencji. Powtarzalny syntetyczny legacy corpus kompresuje się
wyjątkowo dobrze, więc wynik gzip jest niekorzystny i nie jest ekstrapolowany
na live. Entropy dashboard pozostał na zastanym baseline; checker flag
repo-hermetic z `--skip-external` zakończył się 0 błędów (505/505). Zewnętrznego
carriera nie odczytywano.

## Operacje live, bramki i rollback

Nie wykonano deployu, instalacji, restartu, flipu flagi, migracji, chmodu,
rotacji ani delete na live. Nie zmieniono `flags.json`, backlogu ani memory.

Otwarte bramki:

- at-214: corpus pozostaje nietknięty do werdyktu;
- B-05: liczba dni, backup semantics i ewentualne delete nadal nierozstrzygnięte;
- ACK: merge/deploy, provisioning klucza, instalacja unitów, restart i każdy
  live mirror/private/migration/chmod/delete;
- reader migration matrix dla wszystkich aktywnych direct consumers;
- retry-safe outbox/transakcja, jeśli dual-write `mirror` ma kiedykolwiek wyjść
  z HOLD;
- zatwierdzony sealer dla pełnego replay wrażliwych wejść, jeśli taki replay
  pozostaje wymagany.

Rollback źródłowy: domyślny `compat`, old/new reader oraz jawny `git revert`
commitu sprintu. Template unitów nie jest zainstalowany. Nie ma migracji ani
operacji nieodwracalnej do cofania. Przed przyszłym rolloutem rollback wymaga
powrotu do `compat` i weryfikacji legacy reader parity; nie ma nowego timera do
zatrzymania i nie wolno restartować `dispatch-telegram`.

Commit/push/HEAD=origin/clean: wykonywane po zamrożeniu tego raportu; finalny
handoff podaje SHA i weryfikację.
