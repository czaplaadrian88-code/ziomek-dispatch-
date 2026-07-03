# systemd/ — jednostki w repo (mapa; audyt-porządki 2026-07-03)

**WDROŻONE naprawdę = `/etc/systemd/system/`** — zawsze weryfikuj `systemctl cat <unit>` (część unitów w /etc to kopie, część mogła powstać ręcznie). Repo trzyma ŹRÓDŁA:

| Miejsce | Co |
|---|---|
| `systemd/*.service`, `*.timer`, `*.d/` | główny mirror jednostek dispatch-* (historycznie instalowane stąd) |
| `systemd/reconciliation/` | jednostki workera rekoncyliacji (przeniesione z `reconciliation/systemd/` 03.07; instrukcja instalacji: `reconciliation/README.md`) |
| `systemd/shift_notifications/` | jednostki workera powiadomień o zmianach (przeniesione z `shift_notifications/systemd/` 03.07) |
| `deploy/` (korzeń repo) | staged kit: checkpoint-tz-shadow (kolektor disabled 27.06), reassignment-shadow |
| `deploy_staging/` | staged kit: bundle-calib-shadow (README_INSTALL.md) |
| `docs/deploy/ha-lite/` | kit HA-lite 21.06 — ⚠ źródło ŻYWEGO `backup-sentinel.service` (deployowany jako `scripts/backup_sentinel.py` poza repo) |

Zasada: nowa jednostka → tutaj (`systemd/` lub `systemd/<moduł>/`), nie nowy katalog. Drop-iny env per-serwis: patrz `docs/decisions/ADR-004` (flagi 3 światy).
