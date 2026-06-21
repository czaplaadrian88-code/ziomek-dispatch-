# HA-lite / Disaster Recovery — snapshoty skryptów + runbook (2026-06-21)

⚠️ **To są SNAPSHOTY skryptów operacyjnych, nie kanon wykonawczy.** Żywe wersje biegną
spod swoich ścieżek (poniżej) i są wpięte w systemd. Edytuj ŻYWE, potem odśwież ten snapshot.
Powód trzymania kopii tu (zgodnie z konwencją `docs/deploy/` w tym repo): off-machine
durability procedury DR (bus factor = 1, audyt SaaS 2026-06-18).

## Kanon (żywe ścieżki) + wpięcie systemd
| Plik (snapshot) | Żywa ścieżka | Wpięcie |
|---|---|---|
| `backup_restic.sh` | `/root/.openclaw/workspace/scripts/backup_restic.sh` | `dispatch-restic-backup.timer` (03:30) — off-site Hetzner BX11 |
| `restore_from_restic.sh` | `…/scripts/restore_from_restic.sh` | ręczny DR (`--verify-only` / `--load-db` / `--force`) |
| `activate_pitr.sh` | `…/scripts/activate_pitr.sh` | jednorazowy `pitr-activate-oneshot.timer` (22.06 01:00 UTC) |
| `pitr_verify.sh` | `…/scripts/pitr_verify.sh` | jednorazowy `pitr-verify-oneshot.timer` (22.06 05:00 UTC) → Telegram |
| `HA_LITE_RUNBOOK_2026-06-21.md` | `/root/HA_LITE_RUNBOOK_2026-06-21.md` | dokument DR (kanon roboczy w /root) |

## Co dodano w sprincie HA-lite 2026-06-21
- **Kompletność off-site:** `backup_restic.sh` rozszerzony o systemd `nadajesz-/papu-/courier-/mailek-*` + `nginx/sites-available` (wcześniej tylko `dispatch-*` → panel/courier/ordering/mailek były POZA backupem). `.secrets/` świadomie POZA off-site.
- **RTO:** `restore_from_restic.sh` (bezpieczny, scratch domyślnie, `--force` do prod) — `--verify-only` przeszedł.
- **RPO/PITR:** archive_mode + WAL archive + base backup — staged, aktywacja przez `activate_pitr.sh` w oknie nocnym (auto-rollback + Telegram OnFailure).

## ⚠️ Wymaga człowieka (poza automatem)
1. Hasło restic `/root/.restic_password` → menedżer haseł **off-machine** (inaczej off-site nieodszyfrowywalny).
2. Drugi serwer + DNS = prawdziwe HA (bring-up w runbooku).
3. Decyzja czy `.secrets/` dokładać do (szyfrowanego) restic.
