"""Daily Accounting — entrypoint.

Flow:
  1. Flag check (ENABLE_DAILY_ACCOUNTING) — False → exit(0) (unless --dry-run)
  2. Compute date bucket (Warsaw TZ)
  3. Load kurier_ids + kurier_full_names → canonical (cid, alias, full_name) list
  4. Panel login
  5. For each cid: scrape main + eljot → record z H
  6. Fetch sheet grid (A, B, C)
  7. Idempotent check (name, target_date)
  8. Car lookup (A:B last-match-wins)
  9. Batch write rows (real) or JSON dump (dry-run)
 10. Free-rows alert if < MIN_FREE_ROWS_ALERT
 11. Telegram success report

CLI:
  --dry-run          Force dry-run (no writes, JSON dump do /tmp)
  --target-date YYYY-MM-DD  Override today's date (testy, manual rerun)
"""
import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("daily_accounting.main")


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _load_kurier_ids() -> Dict[str, int]:
    path = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
    with open(path) as f:
        return json.load(f)


def _load_full_names() -> Dict[str, str]:
    path = Path(__file__).parent / "kurier_full_names.json"
    with open(path) as f:
        return json.load(f)


def _try_alert(text: str) -> bool:
    """Send Telegram, NIE throw (report atomicity)."""
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        return send_admin_alert(text)
    except Exception as e:
        log.error(f"Telegram send exception: {e}")
        return False


def _parse_date_arg(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _build_dry_run_report(
    run_date: date,
    bucket: tuple,
    qualified: int,
    rows_to_write: List[Dict],
    skipped: List[Dict],
    free_rows_after: int,
) -> Dict:
    date_from, date_to, target = bucket
    return {
        "run_date": run_date.isoformat(),
        "target_date": target.isoformat(),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "weekday": run_date.strftime("%A"),
        "is_weekend_bucket": date_from != date_to,
        "panel_calls": len(rows_to_write) + len(skipped),
        "qualified_couriers": qualified,
        "rows_to_write": rows_to_write,
        "skipped": skipped,
        "free_rows_after": free_rows_after,
    }


def run(
    dry_run: bool,
    target_date_override: Optional[date] = None,
) -> int:
    """Full orchestration. Returns exit code (0 OK, >0 error)."""
    from dispatch_v2.daily_accounting.bucket_logic import compute_bucket, today_warsaw
    from dispatch_v2.daily_accounting.car_lookup import find_car
    from dispatch_v2.daily_accounting.config import (
        EXCLUDED_CIDS,
        MIN_FREE_ROWS_ALERT,
    )
    from dispatch_v2.daily_accounting.panel_scraper import (
        build_courier_iteration_list,
        scrape_courier,
    )
    from dispatch_v2.daily_accounting.sheets_writer import (
        DATE_FMT,
        already_written,
        batch_write_rows,
        build_batch_data,
        count_free_rows_after,
        fetch_grid,
        first_empty_row,
        verify_writes,
    )

    run_date = target_date_override or today_warsaw()
    log.info(f"Run date (Warsaw): {run_date} ({run_date.strftime('%A')})")

    bucket = compute_bucket(run_date)
    if bucket is None:
        log.warning(f"Weekend day ({run_date.strftime('%A')}), exiting 0")
        return 0
    date_from, date_to, target_c = bucket
    log.info(f"Bucket: {date_from} .. {date_to} (target_C={target_c})")

    # Load mappings
    kids = _load_kurier_ids()
    full_names = _load_full_names()
    iteration = build_courier_iteration_list(kids, EXCLUDED_CIDS)
    log.info(f"Couriers to scrape: {len(iteration)} (after EXCLUDED_CIDS)")

    # Panel login
    if dry_run and False:
        opener = None  # reserved for unit tests only
    else:
        from dispatch_v2.panel_client import login
        opener, _, _ = login()

    # Scrape loop
    scrape_results: List[Dict] = []
    scrape_errors: List[Dict] = []
    for cid, alias in iteration:
        if alias not in full_names:
            log.warning(f"cid={cid} alias={alias!r} brak w kurier_full_names.json — skip")
            _try_alert(
                f"⚠️ Daily Accounting: pominięto cid={cid} alias='{alias}' "
                f"(brak mapping w kurier_full_names.json)"
            )
            scrape_errors.append({"cid": cid, "alias": alias, "error": "no_full_name_mapping"})
            continue
        try:
            rec = scrape_courier(opener, cid, alias, date_from, date_to)
            rec["full_name"] = full_names[alias]
            scrape_results.append(rec)
            log.info(
                f"  cid={cid} {alias!r} ({full_names[alias]!r}): "
                f"ilosc={rec['ilosc_zlecen']} pobr={rec['suma_pobran_total']:.2f} "
                f"karta={rec['suma_platnosci_karta']:.2f} "
                f"eljot_p={rec['eljot_pobrania']:.2f} eljot_c={rec['eljot_cena']:.2f} "
                f"H={rec['H_computed']:.2f}"
            )
        except Exception as e:
            log.error(f"  cid={cid} {alias!r} scrape fail final: {e}")
            _try_alert(
                f"⚠️ Daily Accounting: scrape fail cid={cid} alias='{alias}' "
                f"({date_from}..{date_to}): {e}"
            )
            scrape_errors.append({"cid": cid, "alias": alias, "error": str(e)})

    # Keep only couriers with ilosc_zlecen > 0 (qualified for entry)
    qualified = [r for r in scrape_results if r["ilosc_zlecen"] > 0]
    unqualified = [r for r in scrape_results if r["ilosc_zlecen"] == 0]
    log.info(f"Qualified (ilosc_zlecen > 0): {len(qualified)} / {len(scrape_results)}")

    # Fetch sheet grid
    grid = fetch_grid()
    ws = grid["ws"]
    col_a = grid["col_a"]
    col_b = grid["col_b"]
    col_c = grid["col_c"]
    next_row = first_empty_row(col_a)

    rows_to_write: List[Dict] = []
    skipped_duplicate: List[Dict] = []
    row_cursor = next_row
    for rec in qualified:
        emp = rec["full_name"]
        if already_written(emp, target_c, col_a, col_c):
            skipped_duplicate.append({
                "cid": rec["cid"], "alias": rec["alias"], "full_name": emp,
                "target_date": target_c.strftime(DATE_FMT),
                "action": "SKIP_DUPLICATE",
                "H_computed": rec["H_computed"],
            })
            continue
        car = find_car(emp, col_a, col_b)
        rows_to_write.append({
            "row": row_cursor,
            "A": emp,
            "B": car,
            "C": target_c.strftime(DATE_FMT),
            "F": rec["suma_platnosci_karta"],
            "H": rec["H_computed"],
            "P": rec["ilosc_zlecen"],
            "_meta": {
                "cid": rec["cid"],
                "alias": rec["alias"],
                "pobrania_total": rec["suma_pobran_total"],
                "eljot_pobrania": rec["eljot_pobrania"],
                "eljot_cena": rec["eljot_cena"],
            },
        })
        row_cursor += 1

    free_after = count_free_rows_after(ws, row_cursor - 1)

    # Strip _meta before batch write (used in both dry-run preview and real write)
    clean_rows = [{k: v for k, v in r.items() if k != "_meta"} for r in rows_to_write]

    # Dry-run branch
    if dry_run:
        # DIFF C verification: pokaż jakie ranges by zbudował, żeby Adrian widział
        # że prefiks 'Obliczenia'! jest na miejscu (nie domyślny pierwszy sheet).
        sample_data = build_batch_data(ws, clean_rows)
        log.info(
            f"DRY-RUN build sample: would write {len(clean_rows)} rows, "
            f"{len(sample_data)} cells; first 6 ranges:"
        )
        for d in sample_data[:6]:
            log.info(f"  range={d['range']!r} value={d['values'][0][0]!r}")

        report = _build_dry_run_report(
            run_date, bucket, len(qualified), rows_to_write,
            skipped_duplicate + [{**u, "action": "SKIP_ZERO_ORDERS"} for u in unqualified] + scrape_errors,
            free_after,
        )
        report["sample_ranges"] = [d["range"] for d in sample_data[:6]]
        out_path = Path(f"/tmp/daily_accounting_dryrun_{run_date.isoformat()}.json")
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        log.info(f"DRY-RUN report: {out_path}")
        log.info(
            f"Summary: qualified={len(qualified)}, to_write={len(rows_to_write)}, "
            f"skip_dup={len(skipped_duplicate)}, skip_zero={len(unqualified)}, "
            f"errors={len(scrape_errors)}, free_rows_after={free_after}"
        )
        return 0

    # Real write
    log.info(f"Writing {len(rows_to_write)} rows (real)")
    write_result = batch_write_rows(ws, clean_rows)
    log.info(f"Wrote: {write_result}")

    # DIFF A: check API response — exit !=0 + alert na fail
    if not write_result.get("api_success", False):
        log.error(
            f"WRITE FAIL: api_updated={write_result.get('api_total_updated_cells')} "
            f"expected={write_result.get('api_expected_cells')}"
        )
        _try_alert(
            f"❌ Ziomek Daily Accounting {run_date.isoformat()} WRITE FAIL\n"
            f"Próba zapisu {len(rows_to_write)} wierszy za {target_c.strftime(DATE_FMT)}.\n"
            f"API zaktualizowało {write_result.get('api_total_updated_cells')} "
            f"z {write_result.get('api_expected_cells')} oczekiwanych komórek.\n"
            f"Sprawdź arkusz + permissions service account."
        )
        return 1

    # DIFF B: post-write read-back verify (defense-in-depth — łapie wrong-sheet
    # routing nawet gdy API zwróci api_success=True).
    if clean_rows:
        verify_result = verify_writes(ws, clean_rows)
        log.info(f"Verify: {verify_result['verified']}/{len(clean_rows)} rzędów zgodnych")
        if verify_result["mismatches"]:
            first = verify_result["mismatches"][0]
            log.error(
                f"VERIFY FAIL: {len(verify_result['mismatches'])} z {len(clean_rows)} "
                f"rozjazdów. Pierwszy: row={first['row']} "
                f"expected_A={first['expected_A']!r} actual_A={first['actual_A']!r} "
                f"expected_C={first['expected_C']!r} actual_C={first['actual_C']!r}"
            )
            _try_alert(
                f"⚠️ Ziomek Daily Accounting {run_date.isoformat()} VERIFY FAIL\n"
                f"API zwróciło sukces ALE read-back znalazł "
                f"{len(verify_result['mismatches'])} z {len(clean_rows)} rozjazdów.\n"
                f"Pierwszy: row={first['row']}\n"
                f"  expected: A={first['expected_A']!r} C={first['expected_C']!r}\n"
                f"  actual:   A={first['actual_A']!r} C={first['actual_C']!r}\n"
                f"Dane mogły trafić do złej zakładki — sprawdź pilnie."
            )
            return 1

    # Alert if low free rows
    if free_after < MIN_FREE_ROWS_ALERT:
        _try_alert(
            f"⚠️ Ziomek Daily Accounting\n"
            f"Zapisano {write_result['written']} wierszy za dzień "
            f"{target_c.strftime(DATE_FMT)}.\n"
            f"Zostało tylko {free_after} wolnych wierszy poniżej w "
            f"Controlling/Obliczenia.\n"
            f"Dodaj puste wiersze (~200 na raz)."
        )

    # Success report (DIFF A: czytamy z write_result, nie len(rows_to_write))
    _try_alert(
        f"✅ Ziomek Daily Accounting {run_date.isoformat()}\n"
        f"Zapisano: {write_result['written']} wierszy za {target_c.strftime(DATE_FMT)}\n"
        f"Pominięto (duplikaty): {len(skipped_duplicate)}\n"
        f"Pominięto (0 zleceń): {len(unqualified)}\n"
        f"Błędy scrape: {len(scrape_errors)}\n"
        f"Wolnych wierszy: {free_after}"
    )
    return 0


def main() -> int:
    _setup_logging()
    from dispatch_v2.common import ENABLE_DAILY_ACCOUNTING

    ap = argparse.ArgumentParser(description="Daily Accounting runner")
    ap.add_argument("--dry-run", action="store_true", help="No writes, JSON to /tmp")
    ap.add_argument("--target-date", help="YYYY-MM-DD override (default=today Warsaw)")
    args = ap.parse_args()

    target_override = _parse_date_arg(args.target_date) if args.target_date else None

    # Flag check — force-skip real run even if --dry-run jest dozwolone
    if not ENABLE_DAILY_ACCOUNTING and not args.dry_run:
        log.info("ENABLE_DAILY_ACCOUNTING=False, skipping run")
        return 0

    return run(dry_run=args.dry_run or not ENABLE_DAILY_ACCOUNTING, target_date_override=target_override)


if __name__ == "__main__":
    sys.exit(main())
