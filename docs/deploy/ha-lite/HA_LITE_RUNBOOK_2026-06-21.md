# HA-lite / Disaster Recovery — runbook (2026-06-21)

Cel: zminimalizować skutek **bus factor = 1** (jeden serwer Hetzner, jedna osoba) — ryzyko #1 wg audytu SaaS 7/7 modeli. Ten runbook NIE daje pełnego HA (to wymaga drugiego serwera — sekcja 5), ale skraca **RPO** (utrata danych) i **RTO** (czas powrotu) tak daleko, jak da się jedną maszyną.

## 0. Stan po sprincie 2026-06-21
| Metryka | Przed | Po |
|---|---|---|
| RPO (okno utraty danych) | ~24h (tylko nocny pg_dump) | ~24h dump **+ opcja PITR** (sekcja 4 — czeka na decyzję) |
| RTO (czas odbudowy) | nieokreślony, nietestowany | minuty-godziny, **przetestowany** (DR drill 2026-06-20: 0 błędów, 81 tabel/72 konta/76 lokali) + `restore_from_restic.sh` |
| Kompletność off-site | dane OK, ale brak jednostek panel/papu/courier/mailek + nginx | ✅ domknięte (`backup_restic.sh` rozszerzony 2026-06-21) |

## 1. ⚠️ NAJWAŻNIEJSZE — hasło restic
`/root/.restic_password` (45 B) jest **tylko na tym serwerze**. Jeśli serwer padnie a hasła nie ma off-machine, **cały off-site backup jest nieodszyfrowywalny = bezużyteczny**.
**DO ZROBIENIA (Adrian, jednorazowo):** zapisać treść `/root/.restic_password` w menedżerze haseł (1Password/Bitwarden) + ewentualnie u zaufanej osoby. To jest pojedynczy punkt, który decyduje czy DR w ogóle zadziała.

## 2. Co JEST w off-site (restic, Hetzner BX11, szyfrowane)
dispatch_state · modele ML v1.1 + datasets v2.0 · flags.json · 3×`.env` (workspace/ordering_app/panel) · ordering_app/media · dumpy `nadajesz_panel` + `papu` (nocne, retencja 7d) · **systemd: dispatch-/nadajesz-/papu-/courier-/mailek-** · **nginx sites-available** · logi <14d.
Timery: `dispatch-restic-backup` (03:30), `nadajesz-panel-backup` (02:15), `papu-db-backup` (02:00).

## 3. Czego NIE ma w off-site (świadomie) — odtworzyć z innych źródeł
- **`/root/.openclaw/workspace/.secrets/`** — hasła/API/tokeny. NIE wysyłane off-site (granica zaufania). → menedżer haseł off-machine. **Decyzja Adriana:** czy dołożyć do (szyfrowanego) restic, czy trzymać osobno. Bez tego odbudowa wymaga ręcznego odtworzenia sekretów.
- **Kod** — z GitHub: `ziomek-dispatch-` (Ziomek), `nadajesz` (panel), `podlaskie-papu-backend` (papu). ⚠ niezacommitowane lokalne zmiany w kodzie NIE są chronione (dispatch_v2 bywa „dirty") — commituj/pushuj regularnie.

## 4. PITR (RPO 24h → minuty) — wariant B WYBRANY, STAGED, czeka na restart nocny
Decyzja: **B (`archive_mode=on` + archive_command + base backup)**. `wal_level=replica` ✅.
**Przygotowane bezpiecznie 2026-06-21 (bez dotknięcia żywej bazy):**
- katalogi `wal_archive` + `base_backup` w wolumenie `ordering_app_papu_pgdata` (puste, inert),
- restic backupuje oba katalogi off-site; prune WAL >8d w `backup_restic.sh` (anty-zapchanie),
- `scripts/activate_pitr.sh` — jednorazowy aktywator: ustawia archive_command+archive_mode, restart, **weryfikuje że WAL faktycznie się archiwizuje**, robi `pg_basebackup` (PITR ma punkt startowy), **AUTO-ROLLBACK** jeśli weryfikacja padnie.
**ZOSTAJE (prod-affecting, OKNO NOCNE ~02-05 Warszawa, NIE peak/niedziela-dzień):**
```
/root/.openclaw/workspace/scripts/activate_pitr.sh --yes-restart-now
```
Restart `papu-postgres` = ~kilka sek blip Lokalki+panelu. ⚠ Korekta: samo archive_mode to NIE PITR — dlatego activate robi też base backup. Rollback: `ALTER SYSTEM SET archive_mode=off; docker restart papu-postgres`.

## 5. Druga instancja / failover — WYMAGA ADRIANA (nie do zrobienia z automatu)
Prawdziwe HA = drugi serwer (Hetzner/DO) + przełączenie DNS/floating IP. Kroki:
1. Adrian: provisioning VPS (≥4 vCPU/8GB), dostęp SSH, alias `bx11-storage` w `~/.ssh/config`.
2. Wpisać hasło restic (sekcja 1) → `restore_from_restic.sh --verify-only` (test dostępu).
3. `restore_from_restic.sh` → odtworzenie plików. Postawić docker `papu-postgres`.
4. `restore_from_restic.sh --load-db nadajesz_panel --force` (+ papu). 
5. Kod z GitHub, `.secrets` z menedżera, `.env` ze scratch.
6. systemd: kopia units → daemon-reload → enable --now. nginx: kopia sites → `nginx -t` → reload.
7. DNS/floating IP na nowy host.
Realny RTO przy gotowym skrypcie: ~30-60 min.

## 6. Test odtworzenia (rób co kwartał)
`./restore_from_restic.sh --verify-only` (szybkie: dostęp + integralność 5%).
Pełny drill: `./restore_from_restic.sh --load-db nadajesz_dr_test` → sanity → `dropdb nadajesz_dr_test`.

## Pliki
- Backup: `/root/.openclaw/workspace/scripts/backup_restic.sh` (backup pre-zmian: `*.bak-pre-halite-2026-06-21`)
- Restore/DR: `/root/.openclaw/workspace/scripts/restore_from_restic.sh`
- Aktywacja PITR (okno nocne): `/root/.openclaw/workspace/scripts/activate_pitr.sh --yes-restart-now`
- Strażnik backupu (świeżość+integralność): `/root/.openclaw/workspace/scripts/backup_sentinel.py` (timer `backup-sentinel.timer` codziennie 08:00 UTC; Telegram gdy dump/snapshot nieświeży >26h lub `restic check` fail; niedziela = integralność)

## 7. Monitoring backupu (dodane 2026-06-21)
OnFailure na serwisach backupu łapie tylko „serwis padł". **`backup-sentinel.timer` (08:00 UTC)** łapie ciche luki: timer nie odpalił / dump pusty / snapshot restic nieświeży / repo skorumpowane → Telegram (kanał admina). Cichy fail backupu był dotąd niewykrywalny (cron_health pusty dla restic, liveness-probe nie pokrywa backupów).
