"""Companion COD Weekly → panel Pobrania (FIN-08).

Ten sam scraper co arkusz „Wynagrodzenia Gastro" (panel_scraper.scrape_restaurant_cod),
ale wynik wysyła do PANELU operatora (zakładka Pobrania) zamiast/oprócz Google Sheets.
NIE dotyka crona księgowego (dispatch-cod-weekly) — to osobny, niezależny writer.
Cel: Ziomek wpisuje tygodniowe COD w panelu (docelowo jedyne miejsce), nie na dysku.

Uruchom:
  python3 -m dispatch_v2.cod_weekly.panel_cod_ingest                 # poprzedni zamknięty tydzień
  python3 -m dispatch_v2.cod_weekly.panel_cod_ingest --week 2026-06-01:2026-06-07
  python3 -m dispatch_v2.cod_weekly.panel_cod_ingest --dry-run       # bez POST, tylko wydruk

Token: env PANEL_INTERNAL_TOKEN, w przeciwnym razie czytany z .env backendu panelu.
Partial policy: błąd pojedynczej restauracji = skip + log (nie wywala całości);
≥1 wiersz → POST + exit 0. Zero wierszy / błąd POST → exit 1 (OnFailure Telegram).
"""
import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path

from dispatch_v2.cod_weekly.config import MAPPING_PATH
from dispatch_v2.cod_weekly.panel_scraper import scrape_restaurant_cod
from dispatch_v2.cod_weekly.week_calculator import get_previous_closed_week, parse_override
from dispatch_v2.panel_client import login

log = logging.getLogger("cod_weekly.panel_ingest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PANEL_URL = "http://127.0.0.1:8000/api/finance/operator/weekly-cod/ingest"
PANEL_ENV = Path("/root/.openclaw/workspace/nadajesz_clone/panel/backend/.env")


def _load_mapping() -> dict:
    data = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    return data.get("mapping", data)


def _internal_token() -> str:
    import os
    tok = os.environ.get("PANEL_INTERNAL_TOKEN")
    if tok:
        return tok.strip()
    # fallback: odczyt z .env backendu panelu (ten sam host)
    if PANEL_ENV.exists():
        for line in PANEL_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("PANEL_INTERNAL_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("brak PANEL_INTERNAL_TOKEN (env ani .env panelu)")


def _post(payload: dict, token: str) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PANEL_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="COD Weekly → panel Pobrania")
    ap.add_argument("--week", help="YYYY-MM-DD:YYYY-MM-DD (pon-niedz); domyślnie poprzedni zamknięty")
    ap.add_argument("--dry-run", action="store_true", help="nie wysyłaj do panelu — tylko wydruk")
    ap.add_argument("--tenant-id", type=int, default=1)
    args = ap.parse_args()

    if args.week:
        week_start, week_end = parse_override(args.week)
    else:
        week_start, week_end = get_previous_closed_week()
    log.info(f"Tydzień {week_start}..{week_end}")

    mapping = _load_mapping()
    log.info(f"Restauracji w mapowaniu: {len(mapping)}")

    opener, _, _ = login()
    rows, errors = [], []
    for name, cid in mapping.items():
        try:
            r = scrape_restaurant_cod(opener, name, cid, week_start, week_end)
            rows.append({
                "restaurant": r["restaurant"],
                "company_ids": r["company_ids"],
                "przesylki": r["przesylki"],
                "pobrania": r["pobrania"],
                "prowizja": r["prowizja"],
                "cod": r["cod"],
            })
        except Exception as e:  # noqa: BLE001 — partial policy: skip jedną, leć dalej
            log.warning(f"[SKIP] {name!r}: {e}")
            errors.append({"restaurant": name, "error": str(e)})

    total_cod = round(sum(x["cod"] for x in rows), 2)
    log.info(f"Zescrape'owano {len(rows)} restauracji (Σ COD={total_cod} zł), błędów={len(errors)}")

    if not rows:
        log.error("Zero wierszy — nic do wysłania.")
        return 1

    payload = {
        "week_start": week_start.isoformat(), "week_end": week_end.isoformat(),
        "source": "ziomek", "tenant_id": args.tenant_id, "rows": rows,
    }
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        log.info("DRY-RUN — nie wysłano.")
        return 0

    try:
        res = _post(payload, _internal_token())
    except Exception as e:  # noqa: BLE001
        log.error(f"POST do panelu nieudany: {e}")
        return 1
    log.info(f"Panel: upserted={res.get('upserted')} tydzień={res.get('week_start')}..{res.get('week_end')}")
    if errors:
        log.warning(f"PARTIAL — {len(errors)} restauracji pominiętych: "
                    + ", ".join(e['restaurant'] for e in errors[:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
