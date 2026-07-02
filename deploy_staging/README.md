# deploy_staging — dispatch-log-rotation (gc-observability, audyt 2.0 L13)

**STATUS: STAGING. NIC nie zainstalowane. Instalacja = koordynator, za ACK Adriana, off-peak.**

Pliki:
- `etc/systemd/system/dispatch-log-rotation.service` — oneshot, `--apply`, retencja 14d, MemoryMax 200M, OnFailure→Telegram.
- `etc/systemd/system/dispatch-log-rotation.timer` — codziennie 03:00 UTC (off-peak), `OnCalendar` + `Persistent=true`.

`systemd-analyze verify` obu plików = OK (exit 0; jedyne ostrzeżenia dotyczą CUDZYCH /etc/*.service, nie tych).

## Precondycja
`observability/log_rotation.py` MUSI być scalony do kanonu
`/root/.openclaw/workspace/scripts/dispatch_v2/observability/` (ExecStart używa
`-m dispatch_v2.observability.log_rotation`). W worktree jeszcze NIE jest w kanonie.

## Instalacja (koordynator, za ACK, off-peak)
```bash
# 1. NADZOROWANY dry-run PRZED czymkolwiek (potwierdza cel: ~90 plików / ~174 MB, oldest_kept 2026-06-18):
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.log_rotation --retention-days 14

# 2. Pierwszy REALNY apply — RĘCZNIE, off-peak, z liczeniem przed/po:
find /root/.openclaw/workspace/dispatch_state/observability -maxdepth 1 -type f | wc -l   # before
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.log_rotation --apply --retention-days 14 --max-delete 500
find /root/.openclaw/workspace/dispatch_state/observability -maxdepth 1 -type f | wc -l   # after (~30)

# 3. Dopiero po udanym ręcznym apply — instaluj timer:
cp deploy_staging/etc/systemd/system/dispatch-log-rotation.service /etc/systemd/system/
cp deploy_staging/etc/systemd/system/dispatch-log-rotation.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dispatch-log-rotation.timer
systemctl list-timers dispatch-log-rotation.timer          # weryfikacja next-run
```

## Rollback
```bash
systemctl disable --now dispatch-log-rotation.timer
rm -f /etc/systemd/system/dispatch-log-rotation.service /etc/systemd/system/dispatch-log-rotation.timer
systemctl daemon-reload
```
Kod (log_rotation.py) zostaje — bez timera to martwy moduł (zero efektu). Retencja
nie działa = powrót do stanu sprzed (unbounded), ale ZERO ryzyka kasowania.

## Bezpieczeństwo (dlaczego to jest bezpieczne)
- DENYLIST > ALLOWLIST: ledgery/prawda/stan (`shadow_decisions*`, `decision_outcomes*`,
  `gps_delivery_truth*`, `sla_log*`, `orders_state*`, `courier_plans*`, `courier_last_pos*`,
  `pending_proposals*`, `*.db`, `*.py`) NIGDY nie kasowane — sprawdzane PRZED allowlistą.
- ALLOWLIST wąska: TYLKO `candidate_decisions_YYYYMMDD.jsonl` + `fleet_filter_YYYYMMDD.jsonl`.
- `--max-delete 500` = bezpiecznik przed runaway.
- Peak-agnostyczne (03:00 UTC daleko od lunch/dinner), lekki GC (<2 s).
