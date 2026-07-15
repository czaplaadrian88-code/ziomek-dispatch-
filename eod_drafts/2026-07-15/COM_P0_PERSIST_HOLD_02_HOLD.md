# COM-P0-PERSIST-HOLD-02 v1.0 — HOLD_SCOPE_MISMATCH

## Wynik

`HOLD_SCOPE_MISMATCH`, nie `DONE` i nie `PARTIAL_APPLY`.

Owner przekazał exact ACK v1.0/production na dwie mutacje:

1. `systemctl disable openclaw.service`;
2. `systemctl mask openclaw.service`.

CTO zatrzymał wykonanie przed pierwszą komendą. Produkcja nie została
zmodyfikowana.

## Zweryfikowany bloker

`/etc/systemd/system/openclaw.service` jest lokalnym regularnym plikiem, a nie
unitem vendorowym. Mechaniczny probe `systemctl --root` na zainstalowanym
systemd 255 potwierdził:

- `disable` usuwa wants-link i kończy się sukcesem;
- `mask` kończy się błędem `file .../openclaw.service already exists`;
- `mask --force` także nie zastępuje lokalnego regularnego pliku;
- wynik byłby tylko `disabled`, więc ręczny `systemctl start` nadal byłby
  możliwy.

Plan nie był atomowy. Zadeklarowany rollback `unmask + enable` nie odtwarzałby
treści i metadanych pliku w żadnym wariancie, który naprawdę zastąpi unit
maską.

## Finalny postcheck produkcji

O `2026-07-15T15:22:14Z` potwierdzono read-only:

| Element | Stan |
|---|---|
| unit | regular file, 0644, root:root, SHA-256 `b85e073df13ed6e00da1ea60f84ca54d2ef87100b6c7897748a27b815e307e8d` |
| wants-link | nadal wskazuje `/etc/systemd/system/openclaw.service` |
| service | loaded, enabled, inactive/dead, MainPID0, NRestarts17, no drop-ins |
| gateway i CLI | exited143 od 14:54:32Z |
| host 18789/18790 | brak listenerów |
| Telegram / WhatsApp | false / false przez wspierany CLI |
| watchdog | enabled, active/waiting; run 15:18:56Z no-op, bez restartu |
| dispatch-shadow, panel-watcher, courier-api, assistant-telegram, Mailek | active/running, NRestarts0 |

Repo OpenClaw zachowało detached `41cf93efff`, zastany
`M docker-compose.yml` i fazowy `?? docker-compose.containment.yml`. Nie
zmieniono app/config/channel/Compose/systemd, nie wykonano startu, restartu,
deployu, daemon-reloadu, commitu ani pushu.

Źródłowy prywatny handoff 0600:
`/tmp/codex_handoff_2026-07-15_1522_COM-P0-PERSIST-HOLD-02_HOLD.md`, SHA-256
`038ef03c00a434c6ae3187d06e7e6127c43fde398da1ecda76bf9cb034fd2b7a`.

## Wymagany zakres v1.1

Nowy exact owner ACK `COM-P0-PERSIST-HOLD-02 v1.1 / production` musi jawnie
autoryzować:

1. sprawdzenie SHA preimage oraz inactive/closed-port;
2. prywatny, integrity-checked backup lokalnego unitu z treścią i metadanymi;
3. usunięcie enablement linku;
4. przygotowanie maski `/dev/null` w tym samym filesystemie i atomowe
   zastąpienie ścieżki lokalnego unitu;
5. `systemctl daemon-reload` bez startu OpenClaw;
6. weryfikację `LoadState=masked`, `UnitFileState=masked`, inactive, portów,
   kontenerów, kanałów i sibling health;
7. automatyczny restore dokładnego unitu, metadanych i enablement preimage przy
   każdym niespełnionym postcondition, nadal bez startu;
8. pozostawienie recovery/OOM/Compose oraz każdego startu/restartu poza fazą.

Późniejszy rollback recovery musi odtworzyć dokładny hash i metadane unitu,
wykonać daemon-reload i enable bez startu. Każdy start pozostaje osobną bramką.

## Subsequent CTO gate — v1.1 HOLD_ROLLBACK_FAIL_OPEN

Owner przekazał exact scope v1.1/production zgodny z powyższymi ośmioma
punktami. Syntetyczny probe potwierdził, że przygotowanie symlinka `/dev/null`
w katalogu unitu i same-filesystem atomic rename daje stan `masked`, a
transakcyjny restore może przywrócić identyczny hash, tryb, uid/gid i
`enabled`.

Niezależny review wykazał jednak fail-open w regule „restore prior enablement
przy każdym failed postcondition”. Gdy failure oznacza `ActiveState=active`,
listener 18789/18790, gateway running albo channel=true, przywrócenie enabled
unitu osłabia containment. W takim drifcie maska ma zostać zachowana i faza ma
przejść w HOLD; stop wymaga osobnej contingency authority.

Zakres jest też nieprecyzyjny jako „containers stopped”: gateway i CLI są
exited143, ale `openclaw-browser` nadal działa z restart policy always, a :9222
jest publiczne jako osobny finding SEC1. Browser/:9222 muszą być jawnie N-D i
nietknięte.

CTO wydał zatem `HOLD_ROLLBACK_FAIL_OPEN`, nie ACK. Poprawiony gate wymaga
immutable runbook/manifest SHA, rozgałęzionego rollbacku, durable backup i fsync,
single-executor lock, expected `masked` mimo nonzero rc `is-enabled`, dokładnego
scope gateway+CLI oraz jednego naturalnego no-op ticku watchdoga. v1.1 nie
powinna była zostać zastosowana.

## Równoległe zastosowanie po odwołaniu ACK — HOLD_CONCURRENT_APPLY

Późniejsza weryfikacja MAIN wykazała, że non-MAIN zastosował v1.1 o
`2026-07-15T15:32:58.974Z` ze starego, skompaktowanego kontekstu, który nadal
zawierał wcześniejszy ACK i nie zawierał już obowiązującego
`HOLD_ROLLBACK_FAIL_OPEN`. To naruszenie bramki, nie zatwierdzony deploy.

Aktualny postimage jest fail-closed i dlatego nie został cofnięty:

- `/etc/systemd/system/openclaw.service` jest symlinkiem root:root do
  `/dev/null`; wants-link jest nieobecny;
- systemd: `masked`, `inactive/dead`, MainPID0, NRestarts17;
- porty 18789/18790 absent; gateway+CLI exited143; Telegram/WhatsApp false;
- naturalny watchdog tick 15:38:57Z zakończył success/no-op;
- pięć sprawdzonych sibling services jest active/running, NRestarts0;
- `openclaw-browser` i publiczne :9222 pozostają osobnym SEC1, N-D.

Preimage zachowano w prywatnym katalogu 0700 jako regularny plik 0644
root:root, 244 B, z oryginalnym mtime i SHA-256
`b85e073df13ed6e00da1ea60f84ca54d2ef87100b6c7897748a27b815e307e8d`.
Raport wykonawcy 0600:
`/tmp/codex_handoff_2026-07-15_1534_COM-P0-PERSIST-HOLD-02_v1.1.md`, SHA-256
`3a5b300e68170a2bd69bde8d956a0416f652c3a5f9360cd34e265ee629df7cc5`.
Skrypt 0700 ma SHA-256
`51dafd864bd634453d2f083882854200b01c287f976ce79afd0d756b7bc76d1e`;
jego gałąź błędu faktycznie bezwarunkowo odtwarza prior `enabled`, czyli
zawiera przyczynę CTO HOLD. Nie było startu/restartu ani zmiany Compose/app/
kanałów.

Formalny status to `HOLD_CONCURRENT_APPLY / NEEDS_RECONCILIATION`, nie DONE.
Zachować maskę; bez nowej bramki nie wykonywać rollbacku, startu, restartu ani
recovery. Następny gate musi być pobierany świeżo pod lockiem z jednego
kanonicznego artefaktu/nonce, a nie z pamięci rozmowy lub kompakcji.
