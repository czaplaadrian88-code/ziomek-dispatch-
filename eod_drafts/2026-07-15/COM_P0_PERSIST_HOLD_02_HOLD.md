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

## Subsequent CTO gate — v1.1

Owner przekazał exact scope v1.1/production zgodny z powyższymi ośmioma
punktami. Niezależny syntetyczny probe potwierdził, że przygotowanie symlinka
`/dev/null` w katalogu unitu i same-filesystem atomic rename daje stan
`masked`, a failure-rollback przez zweryfikowany restore staging przywraca
identyczny hash, tryb, uid/gid i `enabled`.

CTO wydał `ACK_PERSIST_HOLD_V1_1` z warunkami: fresh exact preflight;
single-executor lock; backup w prywatnym katalogu; weryfikacja symlinka przed
rename; automatyczny pełny restore przy każdym błędzie; brak `rm`, startu,
restartu i zmian poza exact scope. Przy wydaniu gate v1.1 była `ACK_READY`, ale
jeszcze nie zastosowana na produkcji.
