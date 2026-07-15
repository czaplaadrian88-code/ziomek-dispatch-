# COM-P0-CONTAIN-01 v1.1 — finalny handoff fazy

## Status

`PARTIAL / FAIL_CLOSED / HOLD_OFFLINE`.

P0 jest obecnie odcięte przez zatrzymanie OpenClaw, ale planowany stan
`gateway healthy + loopback-only` nie został osiągnięty. Faza nie jest `DONE`.
Aktywny MAIN/CTO przyjął handoff w tmux `80` i niezależnie potwierdził stan
fail-closed o `2026-07-15T15:00:25Z` (`17:00:25` czasu warszawskiego).

## Authority i artefakty

- owner ACK v1.1/production oraz wcześniejszy CTO `ACK_DEPLOY` były obecne;
- po incydencie obowiązuje CTO `HOLD_OFFLINE` z
  `/tmp/cto_recovery_COM-P0-CONTAIN-01_v1.1.json`;
- finalny handoff źródłowy:
  `/tmp/codex_handoff_2026-07-15_1457_COM-P0-CONTAIN-01_FINAL.md`, SHA-256
  `ca4ed90da0658302a5a1dd4181873c99ce2ab1ef0b7cc552dc06fe7911ca095d`;
- base Compose SHA-256:
  `42ac4c242eb359ee48fc2a67ebdf5fcf763698e259c1f215d92f50745bf73134`;
- containment override SHA-256:
  `e49da43a01c1aa3638292c629a97882c01c5706801fa500f9891de78b5fcd3f2`.

## Co wykonano w fazie

- dodano `/root/openclaw/docker-compose.containment.yml`;
- zastany dirty `/root/openclaw/docker-compose.yml` pozostawiono bez zmian;
- wspieranym atomowym CLI ustawiono Telegram i WhatsApp na `enabled=false`;
- utworzono prywatny backup 0600 w
  `/root/.openclaw/backups/containment/2026-07-15T1444Z_COM-P0-CONTAIN-01_v1.1/`;
- wykonano zatwierdzony recreate tylko gatewaya z base+override;
- po V8 heap OOM i niekontrolowanych ponownych startach przywrócono fail-closed
  przez zatrzymanie `openclaw.service`.

## Zweryfikowane przyczyny i rezydua

Root writer ponownych startów został potwierdzony:

- unit: `/etc/systemd/system/openclaw.service`;
- `ExecStart=/usr/bin/docker compose up`;
- `Restart=always`;
- unit używa wyłącznie `/root/openclaw/docker-compose.yml`, więc pomija
  containment override i odtwarza `--bind lan` z publicznymi mapowaniami.

Przyczyna samego V8 OOM pozostaje niezweryfikowana. OOM zaczął się po atomowym
wyłączeniu kanałów, ale jeszcze przed recreate z override, więc nie wolno
przypisać go containmentowi ani zgadywać zmian `NODE_OPTIONS`/limitów pamięci.

## Niezależny snapshot fail-closed

O `2026-07-15T15:00:25Z` kontrola read-only potwierdziła:

| Element | Stan |
|---|---|
| `openclaw.service` | `inactive/dead`, `MainPID=0`, `NRestarts=17`, nadal `enabled` |
| gateway | exited `143` od `14:54:32Z` |
| CLI | exited `143` od `14:54:32Z` |
| porty hosta `18789/18790` | brak listenerów |
| Telegram / WhatsApp | `false / false` przez wspierany CLI |
| `gateway-mem-watchdog.timer` | `enabled`, `active/waiting` |

Watchdog wykonał bieg o `14:58:50Z`, nie odczytał pamięci zatrzymanego
kontenera, pominął restart i zakończył się sukcesem. Dispatch shadow,
panel-watcher, courier API, assistant Telegram i Mailek pozostały aktywne.

Zatrzymany kontener zachowuje base-only `--bind lan` i publiczne mapowania.
To nie jest obecna ekspozycja, bo kontener nie działa i porty są nieobecne,
ale manualny start lub reboot może ponownie otworzyć P0, ponieważ
`openclaw.service` pozostaje `enabled`.

## Git i zakres MAIN

- repo `/root/openclaw`: detached HEAD `41cf93efff` według finalnego handoffu;
- zastany `M docker-compose.yml` i fazowy `?? docker-compose.containment.yml`;
- brak commitu/pushu w repo OpenClaw;
- MAIN podczas przyjęcia handoffu wykonał wyłącznie odczyty runtime oraz
  integrację dokumentacji; nie uruchamiał OpenClaw, nie wdrażał i nie wykonywał
  rollbacku.

## Następna osobna faza recovery

1. Offline odtworzyć i ustalić root cause V8 OOM.
2. Przygotować systemd drop-in używający dokładnie base+containment override.
3. Udowodnić trwały fail-closed po reboot/manual start failure.
4. Przygotować kontrolowany test start/health bez kanałów zewnętrznych i z
   loopback-only exposure proof przed pozostawieniem procesu uruchomionego.
5. Przypiąć nowe hashe, dokładne komendy, walidację i rollback zachowujący P0.
6. Uzyskać nowy jawny CTO `ACK_RECOVERY` przed startem/deployem/restartem.

Do tego czasu obowiązuje zakaz startu, deployu, rollbacku do base/LAN,
re-enable kanałów oraz zmian `NODE_OPTIONS`/pamięci bez nowej fazy i dowodu.
