"""Daily Accounting — entrypoint.

Flow:
  1. Flag check (ENABLE_DAILY_ACCOUNTING) — False → exit(0) (unless --dry-run)
  2. Compute date bucket (Warsaw TZ)
  3. Load known courier IDs + full names; exclude only system identities
  4. Panel login
  5. For each cid: scrape main + eljot → record z H
  6. Fetch sheet grid (A, C, H, P, S)
  7. Reconcile by stable CID+period key; legacy name/date rows are checked H/P
  8. Car lookup (A:B last-match-wins)
  9. Batch write rows (real) or JSON dump (dry-run); full read-back verification
 10. Free-rows alert if < MIN_FREE_ROWS_ALERT
 11. Telegram success report

CLI:
  --dry-run          Force dry-run (no writes, JSON dump do /tmp)
  --target-date YYYY-MM-DD  Override today's date (testy, manual rerun)
  --reconcile-legacy Allow controlled H/P/S correction of a legacy mismatch
  --apply-reconciliation Required with --reconcile-legacy to write; otherwise preview
"""
import argparse
import json
import logging
import os
import sys
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


def _try_alert(text: str) -> bool:
    """Send Telegram, NIE throw (report atomicity)."""
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        return send_admin_alert(text)
    except Exception as e:
        log.error(f"Telegram send exception: {e}")
        return False


def _alert_if_real(dry_run: bool, text: str) -> bool:
    """A preview may read sources, but must not send an operational alert."""
    if dry_run:
        log.info("DRY-RUN alert suppressed")
        return False
    return _try_alert(text)


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

    def safe_row(row: Dict) -> Dict:
        meta = row.get("_meta") if isinstance(row.get("_meta"), dict) else {}
        return {
            "row": row.get("row"),
            "cid": meta.get("cid", row.get("cid")),
            "action": meta.get("action", row.get("action", "NEW")),
        }

    return {
        "run_date": run_date.isoformat(),
        "target_date": target.isoformat(),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "weekday": run_date.strftime("%A"),
        "is_weekend_bucket": date_from != date_to,
        "panel_calls": len(rows_to_write) + len(skipped),
        "qualified_couriers": qualified,
        # Report is an operational preview, not a second financial ledger.
        # Keep it useful for auditing while never writing names or amounts to /tmp.
        "rows_to_write": [safe_row(row) for row in rows_to_write],
        "skipped": [safe_row(row) for row in skipped],
        "free_rows_after": free_rows_after,
    }


def run(
    dry_run: bool,
    target_date_override: Optional[date] = None,
    reconcile_legacy: bool = False,
) -> int:
    """Full orchestration. Returns exit code (0 OK, >0 error)."""
    from dispatch_v2.daily_accounting.bucket_logic import compute_bucket, today_warsaw
    from dispatch_v2.daily_accounting.config import (
        MIN_FREE_ROWS_ALERT,
        NON_SETTLEMENT_CIDS,
    )
    from dispatch_v2.daily_accounting.panel_scraper import (
        build_courier_iteration_list,
        scrape_courier,
    )
    from dispatch_v2.daily_accounting.sheets_writer import (
        DATE_FMT,
        batch_write_rows,
        build_batch_data,
        count_free_rows_after,
        ensure_grid_capacity,
        fetch_grid,
        first_empty_row,
        reconcile_existing_row,
        settlement_key,
        snapshot_written_cells,
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
    iteration = build_courier_iteration_list(kids, NON_SETTLEMENT_CIDS)
    log.info(f"Couriers to scrape: {len(iteration)} (after NON_SETTLEMENT_CIDS)")
    from dispatch_v2.identity.registry import build_registry
    identity = build_registry()

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
        full_name = identity.accounting_name(cid)
        if full_name is None:
            log.warning(f"cid={cid} has no canonical accounting name — skip")
            _alert_if_real(dry_run,
                f"⚠️ Daily Accounting: HOLD, cid={cid} nie ma kanonicznej nazwy "
                "rozliczeniowej w rejestrze identity. Nie zapisuję częściowego wyniku."
            )
            scrape_errors.append({"cid": cid, "error": "no_canonical_accounting_name"})
            continue
        try:
            rec = scrape_courier(opener, cid, alias, date_from, date_to)
            rec["full_name"] = full_name
            scrape_results.append(rec)
            log.info(
                f"cid={cid}: orders={rec['ilosc_zlecen']} "
                f"eljot_adjustment={rec['eljot_pobrania'] != 0 or rec['eljot_cena'] != 0}"
            )
        except Exception as e:
            log.error(f"cid={cid} scrape fail final: {e}")
            _alert_if_real(dry_run,
                f"⚠️ Daily Accounting: nie udało się pobrać danych kuriera "
                f"cid={cid} za {date_from}..{date_to}\n"
                f"Błąd: {e}\n\n"
                f"Co robię: pominąłem tego kuriera, reszta leci dalej. "
                f"Jeśli to się powtórzy następnego dnia → sprawdź czy panel "
                f"nie zmienił API albo czy kurier nie został usunięty."
            )
            scrape_errors.append({"cid": cid, "alias": alias, "error": str(e)})

    # Incomplete source data must never become a partial "OK" settlement.
    if scrape_errors:
        log.error(f"Aborting before sheet write: {len(scrape_errors)} scrape error(s)")
        _alert_if_real(dry_run,
            f"❌ Daily Accounting HOLD za {date_from}..{date_to}\n"
            f"Nie zapisuję częściowego rozliczenia: błędy pobrania danych = "
            f"{len(scrape_errors)}."
        )
        return 1

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
    col_h = grid["col_h"]
    col_p = grid["col_p"]
    col_s = grid["col_s"]
    next_row = first_empty_row(col_a)

    rows_to_write: List[Dict] = []
    skipped: List[Dict] = []
    reconciliation_conflicts: List[Dict] = []
    row_cursor = next_row
    for rec in qualified:
        emp = rec["full_name"]
        source_key = settlement_key(rec["cid"], date_from, date_to)
        existing = reconcile_existing_row(
            key=source_key,
            employee_name=emp,
            legacy_names=identity.accounting_names(rec["cid"]),
            target_date=target_c,
            expected_h=rec["H_computed"],
            expected_p=rec["ilosc_zlecen"],
            col_a=col_a,
            col_c=col_c,
            col_h=col_h,
            col_p=col_p,
            col_s=col_s,
        )
        status = existing["status"]
        if status in ("MACHINE_MATCH", "LEGACY_MATCH"):
            skipped.append({
                "cid": rec["cid"], "alias": rec["alias"], "full_name": emp,
                "target_date": target_c.strftime(DATE_FMT),
                "action": status,
                "H_computed": rec["H_computed"],
            })
            continue
        if status == "LEGACY_MISMATCH" and reconcile_legacy:
            rows_to_write.append({
                "row": existing["row"],
                "H": rec["H_computed"],
                "P": rec["ilosc_zlecen"],
                "S": source_key,
                "_meta": {
                    "cid": rec["cid"],
                    "alias": rec["alias"],
                    "action": "RECONCILE_LEGACY",
                },
            })
            continue
        if status != "NEW":
            reconciliation_conflicts.append({
                "cid": rec["cid"],
                "alias": rec["alias"],
                "full_name": emp,
                "target_date": target_c.strftime(DATE_FMT),
                "action": status,
                "rows": existing.get("rows") or [existing.get("row")],
            })
            continue
        # B (samochód firmowy/prywatny) + F (płatność kartą) — stop-write 2026-05-14
        # per Adrian: tylko ogólne pobrania (H) + liczba zleceń (P). find_car skipped
        # żeby brak wpisu kol. B dla nowego kuriera nie strzelał błędem.
        rows_to_write.append({
            "row": row_cursor,
            "A": emp,
            "C": target_c.strftime(DATE_FMT),
            "H": rec["H_computed"],
            "P": rec["ilosc_zlecen"],
            "S": source_key,
            "_meta": {
                "cid": rec["cid"],
                "alias": rec["alias"],
                "pobrania_total": rec["suma_pobran_total"],
                "eljot_pobrania": rec["eljot_pobrania"],
                "eljot_cena": rec["eljot_cena"],
            },
        })
        row_cursor += 1

    # A conflicting reconciliation is a hard hold.  Check it before capacity
    # growth: inserting blank rows is itself a Sheet mutation and must not happen
    # when this run is deliberately refusing partial settlement.
    if reconciliation_conflicts and not dry_run:
        log.error(
            f"Reconciliation HOLD: {len(reconciliation_conflicts)} conflicting row(s); "
            "no sheet write performed"
        )
        _alert_if_real(dry_run,
            f"❌ Daily Accounting HOLD za {target_c.strftime(DATE_FMT)}\n"
            f"Wykryto {len(reconciliation_conflicts)} istniejących wierszy z "
            "innym H/P lub niejednoznacznym kluczem. Nie zapisuję częściowo."
        )
        return 1

    # Auto-grow grid PRZED policzeniem wolnych wierszy i zapisem (tylko real-path),
    # żeby free_after odzwierciedlał rozszerzoną pojemność (nie odpalał mylnego
    # alertu "dodaj wiersze ręcznie" po auto-grow). batch_write_rows też to robi
    # (defense-in-depth), tu robimy wcześniej dla spójności free_after.
    if not dry_run and rows_to_write:
        ensure_grid_capacity(ws, row_cursor - 1)

    free_after = count_free_rows_after(ws, row_cursor - 1)

    # Strip _meta before batch write (used in both dry-run preview and real write)
    clean_rows = [{k: v for k, v in r.items() if k != "_meta"} for r in rows_to_write]

    dry_run_skips = (
        skipped
        + [{**u, "action": "SKIP_ZERO_ORDERS"} for u in unqualified]
        + reconciliation_conflicts
    )

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
            log.info(f"  range={d['range']!r}")

        report = _build_dry_run_report(
            run_date, bucket, len(qualified), rows_to_write,
            dry_run_skips,
            free_after,
        )
        report["reconciliation_conflict_count"] = len(reconciliation_conflicts)
        report["sample_ranges"] = [d["range"] for d in sample_data[:6]]
        out_path = Path(f"/tmp/daily_accounting_dryrun_{run_date.isoformat()}.json")
        fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        log.info(f"DRY-RUN report: {out_path}")
        log.info(
            f"Summary: qualified={len(qualified)}, to_write={len(rows_to_write)}, "
            f"skipped={len(skipped)}, conflicts={len(reconciliation_conflicts)}, "
            f"skip_zero={len(unqualified)}, free_rows_after={free_after}"
        )
        return 2 if reconciliation_conflicts else 0

    # Real write
    log.info(f"Writing {len(rows_to_write)} rows (real)")
    rollback_rows = snapshot_written_cells(ws, clean_rows)

    def rollback_after_failed_write(reason: str) -> bool:
        """Compensate the current batch from its in-memory preimage only."""
        if not rollback_rows:
            return True
        try:
            restore_result = batch_write_rows(ws, rollback_rows)
            restored = restore_result.get("api_success", False)
            if restored:
                restored = not verify_writes(ws, rollback_rows)["mismatches"]
        except Exception:
            log.exception("WRITE ROLLBACK raised: reason=%s rows=%s", reason, len(rollback_rows))
            restored = False
        log.error(
            "WRITE ROLLBACK: reason=%s rows=%s restored=%s",
            reason,
            len(rollback_rows),
            restored,
        )
        return restored

    try:
        write_result = batch_write_rows(ws, clean_rows)
    except Exception:
        rolled_back = rollback_after_failed_write("api_exception")
        log.exception("WRITE EXCEPTION")
        _alert_if_real(dry_run,
            f"❌ Daily Accounting nie potwierdził zapisu za "
            f"{target_c.strftime(DATE_FMT)}. Nie raportuję częściowego sukcesu.\n"
            f"Rollback bieżącej partii: {'OK' if rolled_back else 'NIEPOTWIERDZONY'}"
        )
        return 1
    log.info(f"Wrote: {write_result}")

    # DIFF A: check API response — exit !=0 + alert na fail
    if not write_result.get("api_success", False):
        rolled_back = rollback_after_failed_write("api_response")
        log.error(
            f"WRITE FAIL: api_updated={write_result.get('api_total_updated_cells')} "
            f"expected={write_result.get('api_expected_cells')}"
        )
        _alert_if_real(dry_run,
            f"❌ Daily Accounting nie zapisał arkusza za "
            f"{target_c.strftime(DATE_FMT)}\n"
            f"Próbowałem zapisać {len(rows_to_write)} wierszy, ale Google Sheets "
            f"API potwierdziło tylko {write_result.get('api_total_updated_cells')} "
            f"z {write_result.get('api_expected_cells')}. To znaczy że albo arkusz "
            f"Controlling/Obliczenia ma błędne uprawnienia, albo struktura kolumn "
            f"się rozjechała.\n\n"
            f"Co Ty masz zrobić:\n"
            f"1) Otwórz arkusz Controlling → Obliczenia, sprawdź czy zakładka "
            f"istnieje i wiersze za {target_c.strftime(DATE_FMT)} nie są zablokowane\n"
            f"2) Sprawdź uprawnienia konta serwisowego (musi być Editor)\n"
            f"3) Po fixie odpal ręcznie: "
            f"`python3 -m dispatch_v2.daily_accounting.main "
            f"--target-date {target_c.strftime('%Y-%m-%d')}`\n"
            f"Rollback bieżącej partii: {'OK' if rolled_back else 'NIEPOTWIERDZONY'}"
        )
        return 1

    # DIFF B: post-write read-back verify (defense-in-depth — łapie wrong-sheet
    # routing nawet gdy API zwróci api_success=True).
    if clean_rows:
        verify_result = verify_writes(ws, clean_rows)
        log.info(f"Verify: {verify_result['verified']}/{len(clean_rows)} rzędów zgodnych")
        if verify_result["mismatches"]:
            rolled_back = rollback_after_failed_write("readback")
            first = verify_result["mismatches"][0]
            log.error(
                f"VERIFY FAIL: {len(verify_result['mismatches'])} z {len(clean_rows)} "
                f"rozjazdów. Pierwszy: row={first['row']} fields={first['fields']!r}"
            )
            _alert_if_real(dry_run,
                f"⚠️ Daily Accounting: dane mogły trafić do złej zakładki "
                f"({target_c.strftime(DATE_FMT)})\n"
                f"Google Sheets API zwróciło sukces zapisu, ale gdy odczytałem "
                f"wiersze z powrotem żeby zweryfikować — "
                f"{len(verify_result['mismatches'])} z {len(clean_rows)} się nie "
                f"zgadza.\n"
                f"Pierwszy rozjazd: wiersz {first['row']}, pola={first['fields']}\n\n"
                f"Co Ty masz zrobić: pilnie sprawdź czy dane nie wpadły do innej "
                f"zakładki (np. domyślnej zamiast Obliczenia). Jeśli tak → revert "
                f"ręcznie i odpal jeszcze raz po fixie.\n"
                f"Rollback bieżącej partii: {'OK' if rolled_back else 'NIEPOTWIERDZONY'}"
            )
            return 1

    # Alert if low free rows
    if free_after < MIN_FREE_ROWS_ALERT:
        _alert_if_real(dry_run,
            f"⚠️ Daily Accounting: w arkuszu Controlling/Obliczenia "
            f"kończy się miejsce\n"
            f"Zapisałem {write_result['written']} wierszy za "
            f"{target_c.strftime(DATE_FMT)}, ale poniżej zostało tylko "
            f"{free_after} wolnych wierszy.\n\n"
            f"Co Ty masz zrobić: dodaj ~200 pustych wierszy w arkuszu "
            f"(zaznacz wiersz → Insert rows above × 200), żeby kolejne dni "
            f"miały gdzie się zapisywać."
        )

    # Success report (DIFF A: czytamy z write_result, nie len(rows_to_write))
    _alert_if_real(dry_run,
        f"✅ Daily Accounting OK za {target_c.strftime(DATE_FMT)}\n"
        f"Zapisano: {write_result['written']} wierszy\n"
        f"Pominięto zgodne wiersze: {len(skipped)}\n"
        f"Pominięto kurierów z 0 zleceń: {len(unqualified)}\n"
        f"Błędy pobrania danych: {len(scrape_errors)}\n"
        f"Wolnych wierszy w arkuszu: {free_after}"
    )
    return 0


def main() -> int:
    _setup_logging()
    from dispatch_v2.common import ENABLE_DAILY_ACCOUNTING

    ap = argparse.ArgumentParser(description="Daily Accounting runner")
    ap.add_argument("--dry-run", action="store_true", help="No writes, JSON to /tmp")
    ap.add_argument("--target-date", help="YYYY-MM-DD override (default=today Warsaw)")
    ap.add_argument(
        "--reconcile-legacy",
        action="store_true",
        help="Preview or apply a legacy row correction after H/P comparison",
    )
    ap.add_argument(
        "--apply-reconciliation",
        action="store_true",
        help="Permit write only together with --reconcile-legacy",
    )
    args = ap.parse_args()

    if args.apply_reconciliation and not args.reconcile_legacy:
        ap.error("--apply-reconciliation requires --reconcile-legacy")

    target_override = _parse_date_arg(args.target_date) if args.target_date else None

    # Flag check — force-skip real run even if --dry-run jest dozwolone
    if not ENABLE_DAILY_ACCOUNTING and not args.dry_run:
        log.info("ENABLE_DAILY_ACCOUNTING=False, skipping run")
        return 0

    preview_reconciliation = args.reconcile_legacy and not args.apply_reconciliation
    return run(
        dry_run=args.dry_run or preview_reconciliation or not ENABLE_DAILY_ACCOUNTING,
        target_date_override=target_override,
        reconcile_legacy=args.reconcile_legacy,
    )


if __name__ == "__main__":
    sys.exit(main())
