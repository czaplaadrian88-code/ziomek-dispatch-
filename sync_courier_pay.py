#!/usr/bin/env python3
"""Sync płac kurierów: panel (courier_pay_profile) → Ziomek courier_pay.json.

Read-only SELECT z bazy panelu (nadajesz_panel, psycopg v3). Mapuje external_id
(cid Ziomka) → {mode, tariff_base, tariff_per_km, hourly_rate, active}. Zapis
atomowy do dispatch_state/courier_pay.json (czyta pln_objective._pay_for /
courier_labor_cost).

URL: env PANEL_DB_URL, inaczej PANEL_ASSISTANT_RO_DATABASE_URL (read-only role,
preferowane!) / PANEL_DATABASE_URL z panel backend .env (bez echo hasła).
Uruchamiać venv-em panelu (psycopg v3): cron/timer ~15 min. Fail-soft: 0 profili
lub błąd → NIE nadpisuje starego pliku (zachowuje ostatnie dobre).
"""
import json
import os
import sys
import tempfile

OUT = os.environ.get(
    "COURIER_PAY_PATH", "/root/.openclaw/workspace/dispatch_state/courier_pay.json"
)
PANEL_ENV = os.environ.get(
    "PANEL_ENV_PATH",
    "/root/.openclaw/workspace/nadajesz_clone/panel/backend/.env",
)
# RO role najpierw (twardy read-only po stronie serwera), potem zwykły.
_URL_KEYS = ("PANEL_DB_URL", "PANEL_ASSISTANT_RO_DATABASE_URL", "PANEL_DATABASE_URL", "DATABASE_URL")


def _db_url():
    for k in _URL_KEYS:
        v = os.environ.get(k)
        if v:
            return v
    try:
        env = {}
        for line in open(PANEL_ENV, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                kk, vv = line.split("=", 1)
                env[kk.strip()] = vv.strip().strip('"').strip("'")
        for k in _URL_KEYS:
            if env.get(k):
                return env[k]
    except OSError:
        pass
    return None


def main():
    url = _db_url()
    if not url:
        print(f"ERR: brak URL (probowane: {_URL_KEYS}) w env / {PANEL_ENV}", file=sys.stderr)
        return 1
    url = url.replace("postgresql+psycopg2", "postgresql").replace("postgresql+psycopg", "postgresql")
    try:
        import psycopg
    except ImportError:
        print("ERR: psycopg (v3) niedostepny — uruchom venv-em panelu", file=sys.stderr)
        return 2
    q = (
        "SELECT c.external_id, p.mode, p.tariff_base, p.tariff_per_km, "
        "p.hourly_rate, p.active "
        "FROM courier_pay_profile p JOIN courier c ON c.id = p.courier_id "
        "WHERE c.external_id IS NOT NULL"
    )
    out = {}
    conn = psycopg.connect(url, autocommit=True)
    try:
        try:
            conn.read_only = True  # belt; RO URL = suspenders
        except Exception:
            pass
        for ext, mode, tb, tpk, hr, active in conn.execute(q).fetchall():
            out[str(ext)] = {
                "mode": mode or "tariff",
                "tariff_base": float(tb or 0.0),
                "tariff_per_km": float(tpk or 0.0),
                "hourly_rate": float(hr or 0.0),
                "active": bool(active),
            }
    finally:
        conn.close()
    if not out:
        print("UWAGA: 0 profili placy — NIE nadpisuje (fail-soft)", file=sys.stderr)
        return 3
    d = os.path.dirname(OUT) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".cpay_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT)
    n_h = sum(1 for v in out.values() if v["mode"] in ("hourly", "both"))
    print(f"OK: {len(out)} kurierow ({n_h} godzinowych/mieszanych) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
