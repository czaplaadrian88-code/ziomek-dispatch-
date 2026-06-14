#!/usr/bin/env python3
"""Sync płac kurierów: panel (courier_pay_profile) → Ziomek courier_pay.json.

Read-only SELECT z bazy panelu (nadajesz_panel). Mapuje external_id (cid Ziomka)
→ {mode, tariff_base, tariff_per_km, hourly_rate, active}. Zapis atomowy do
dispatch_state/courier_pay.json (czyta pln_objective._pay_for / courier_labor_cost).

Źródło URL: env PANEL_DB_URL, inaczej DATABASE_URL z panel backend .env (bez echo).
Uruchamiać venv-em panelu (psycopg2): cron/timer co ~15 min. Fail-soft:
błąd połączenia/zapytania → NIE nadpisuje starego pliku (zachowuje ostatnie dobre).

Wymaga: psycopg2 (jest w venv panelu: nadajesz_clone/panel/backend/.venv).
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


def _db_url():
    u = os.environ.get("PANEL_DB_URL")
    if u:
        return u
    try:
        for line in open(PANEL_ENV, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def main():
    url = _db_url()
    if not url:
        print("ERR: brak PANEL_DB_URL / DATABASE_URL w panel .env", file=sys.stderr)
        return 1
    # SQLAlchemy URL (postgresql+psycopg...) → czysty dla psycopg2
    url = url.replace("postgresql+psycopg2", "postgresql").replace("postgresql+psycopg", "postgresql")
    try:
        import psycopg2
    except ImportError:
        print("ERR: psycopg2 niedostępny — uruchom venv-em panelu", file=sys.stderr)
        return 2
    q = (
        "SELECT c.external_id, p.mode, p.tariff_base, p.tariff_per_km, "
        "p.hourly_rate, p.active "
        "FROM courier_pay_profile p JOIN courier c ON c.id = p.courier_id "
        "WHERE c.external_id IS NOT NULL"
    )
    out = {}
    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True, autocommit=True)  # twardo read-only
        cur = conn.cursor()
        cur.execute(q)
        for ext, mode, tb, tpk, hr, active in cur.fetchall():
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
        print("UWAGA: 0 profili płac — NIE nadpisuję (fail-soft)", file=sys.stderr)
        return 3
    d = os.path.dirname(OUT) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".cpay_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT)
    n_h = sum(1 for v in out.values() if v["mode"] in ("hourly", "both"))
    print(f"OK: {len(out)} kurierów ({n_h} godzinowych/mieszanych) → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
