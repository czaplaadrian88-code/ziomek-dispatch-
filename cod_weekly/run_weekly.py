"""Entry point F2.1d COD Weekly — manual runs + (przyszłość) cron.

W tym commit: obsługa --dry-run-sample dla KROK 2 (subset 3 restauracji).
Auto-detekcja kolumny + write do Sheets dopiero w KROK 3+4.
"""
import argparse
import json
import logging
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.panel_client import login
from dispatch_v2.cod_weekly.config import MAPPING_PATH
from dispatch_v2.cod_weekly.week_calculator import (
    format_week_for_header,
    get_previous_closed_week,
    parse_override,
)
from dispatch_v2.cod_weekly.panel_scraper import scrape_restaurant_cod
from dispatch_v2.cod_weekly.sheet_writer import (
    fetch_sheet_grid,
    find_target_cod_columns,
    validate_column_empty_ratio,
    write_cod_column_skip_filled,
    NoTargetColumnError,
    AmbiguousTargetError,
)
from dispatch_v2.cod_weekly.week_calculator import format_week_for_header as _fmt_week

log = logging.getLogger("cod_weekly.run")


def load_mapping() -> dict:
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def cmd_dry_run_sample(names: list, week_start, week_end) -> int:
    payload = load_mapping()
    mapping = payload["mapping"]
    opener, _, _ = login()
    results = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        if name not in mapping:
            log.error(f"[MISSING MAPPING] {name!r} — nie ma w restaurant_company_mapping.json")
            results.append({"restaurant": name, "error": "missing_mapping"})
            continue
        try:
            r = scrape_restaurant_cod(opener, name, mapping[name], week_start, week_end)
            results.append(r)
            cid_fmt = r["company_ids"] if len(r["company_ids"]) > 1 else r["company_ids"][0]
            log.info(
                f"  {name} (company={cid_fmt}): "
                f"przesylki={r['przesylki']:.2f}, pobrania={r['pobrania']:.2f}, "
                f"prowizja={r['prowizja']:.2f} → COD={r['cod']:+.2f}"
            )
        except Exception as e:
            log.error(f"[SCRAPE ERROR] {name}: {e}")
            results.append({"restaurant": name, "error": str(e)})
    print(json.dumps(
        {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "week_header": format_week_for_header(week_start, week_end),
            "results": results,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_dry_run_full(week_start, week_end) -> int:
    log.info("Fetching sheet grid...")
    grid = fetch_sheet_grid()
    ws = grid["ws"]
    row1 = grid["row1"]
    row2 = grid["row2"]
    restaurants = grid["restaurants"]
    log.info(
        f"Sheet: row1={len(row1)} cells, row2={len(row2)} cells, "
        f"restaurants={len(restaurants)}"
    )

    try:
        targets = find_target_cod_columns(row1, row2, week_start, week_end)
    except (NoTargetColumnError, AmbiguousTargetError) as e:
        log.error(f"TARGET COLUMN FAIL: {e}")
        return 1
    for t in targets:
        log.info(
            f"  Target {t['col_letter']} (idx={t['col_idx']}): "
            f"{t['segment_start']} → {t['segment_end']} (payday={t['payday']})"
        )

    row_indices = [r[0] for r in restaurants]
    for t in targets:
        v = validate_column_empty_ratio(ws, t["col_letter"], row_indices, threshold=0.8)
        flag = "OK" if v["ok"] else "FAIL"
        log.info(
            f"  Empty check {t['col_letter']}: {flag} "
            f"({v['empty_count']}/{v['total']} pustych = {v['ratio']:.0%})"
        )
        if not v["ok"]:
            log.warning(f"    Próbka wypełnionych: {v['filled_sample']}")

    mapping = load_mapping()["mapping"]

    opener, _, _ = login()
    results = []
    errors = []
    log.info(f"Scraping {len(restaurants)} restauracji × {len(targets)} segment(ów)...")
    for row_idx, name in restaurants:
        if name not in mapping:
            errors.append(f"NO_MAPPING {name!r}")
            results.append({"row": row_idx, "rest": name, "error": "no_mapping"})
            continue
        cv = mapping[name]
        per_seg = []
        had_error = False
        for t in targets:
            try:
                r = scrape_restaurant_cod(
                    opener, name, cv, t["segment_start"], t["segment_end"]
                )
                per_seg.append(r["cod"])
            except Exception as e:
                per_seg.append(None)
                errors.append(f"SCRAPE_ERROR {name} {t['col_letter']}: {e}")
                had_error = True
        results.append({
            "row": row_idx, "rest": name,
            "cod_per_segment": per_seg, "had_error": had_error,
        })

    # Tabela
    print()
    header = f"{'row':<4} {'rest':<36} "
    for t in targets:
        header += f"{t['col_letter']:>12} "
    if len(targets) > 1:
        header += "   SUM"
    print(header)
    print("-" * len(header))
    for r in results:
        row_s = f"{r['row']:<4} {r['rest'][:34]:<36} "
        if "cod_per_segment" in r:
            for c in r["cod_per_segment"]:
                row_s += f"{c:>+12.2f} " if c is not None else f"{'ERR':>12} "
            if len(targets) > 1:
                total = sum(c for c in r["cod_per_segment"] if c is not None)
                row_s += f"   {total:+.2f}"
        else:
            row_s += " ".join(f"{'ERR':>12}" for _ in targets)
        print(row_s)

    # Statystyki per kolumna
    print()
    for ti, t in enumerate(targets):
        cods = [r["cod_per_segment"][ti] for r in results
                if "cod_per_segment" in r and r["cod_per_segment"][ti] is not None]
        plus = [c for c in cods if c > 0]
        minus = [c for c in cods if c < 0]
        zeros = [c for c in cods if c == 0]
        print(
            f"[{t['col_letter']} {t['segment_start']}..{t['segment_end']}] "
            f"ok={len(cods)} +={len(plus)} -={len(minus)} 0={len(zeros)} | "
            f"sum_plus={sum(plus):+.2f} sum_minus={sum(minus):+.2f} "
            f"sum_net={sum(cods):+.2f}"
        )

    # Top 5 per kolumna (gdy split — per segment)
    for ti, t in enumerate(targets):
        pairs = [
            (r["rest"], r["cod_per_segment"][ti])
            for r in results
            if "cod_per_segment" in r and r["cod_per_segment"][ti] is not None
        ]
        plus = sorted([p for p in pairs if p[1] > 0], key=lambda x: -x[1])[:5]
        minus = sorted([p for p in pairs if p[1] < 0], key=lambda x: x[1])[:5]
        print(f"\n[{t['col_letter']}] TOP 5 plus:")
        for n, c in plus:
            print(f"  {n}: {c:+.2f}")
        print(f"[{t['col_letter']}] TOP 5 minus:")
        for n, c in minus:
            print(f"  {n}: {c:+.2f}")

    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors[:20]:
            print(f"  {e}")
        if len(errors) > 20:
            print(f"  ... +{len(errors) - 20} more")
    return 0


def _scrape_all(restaurants, mapping, targets):
    """Zwraca (results, errors). results[i] = {row, rest, cod_per_segment|error}."""
    from dispatch_v2.cod_weekly.panel_scraper import scrape_restaurant_cod
    opener, _, _ = login()
    results = []
    errors = []
    for row_idx, name in restaurants:
        if name not in mapping:
            errors.append(f"NO_MAPPING {name!r}")
            results.append({"row": row_idx, "rest": name, "error": "no_mapping"})
            continue
        cv = mapping[name]
        per_seg = []
        had_error = False
        for t in targets:
            try:
                r = scrape_restaurant_cod(opener, name, cv, t["segment_start"], t["segment_end"])
                per_seg.append(r["cod"])
            except Exception as e:
                per_seg.append(None)
                errors.append(f"SCRAPE_ERROR {name} {t['col_letter']}: {e}")
                had_error = True
        results.append({"row": row_idx, "rest": name, "cod_per_segment": per_seg, "had_error": had_error})
    return results, errors


def _build_telegram_report(week_start, week_end, target, write_result, results, errors, target_idx=0):
    week_hdr = _fmt_week(week_start, week_end)
    written_n = len(write_result["written_rows"])
    skipped_n = len(write_result["skipped_filled"])
    error_n = len(errors)
    total_scanned = len(results)
    skipped_names = []
    for sf in write_result["skipped_filled"]:
        row_idx = sf["row"]
        rest_name = next((r["rest"] for r in results if r["row"] == row_idx), f"row{row_idx}")
        skipped_names.append(rest_name)
    cods = [r["cod_per_segment"][target_idx] for r in results
            if "cod_per_segment" in r and r["cod_per_segment"][target_idx] is not None]
    sum_plus = sum(c for c in cods if c > 0)
    sum_minus = sum(c for c in cods if c < 0)
    sum_net = sum(cods)
    pairs = [(r["rest"], r["cod_per_segment"][target_idx]) for r in results
             if "cod_per_segment" in r and r["cod_per_segment"][target_idx] is not None]
    skipped_set = {sf["row"] for sf in write_result["skipped_filled"]}
    def mark(name, row):
        return " (skip)" if row in skipped_set else ""
    row_by_name = {r["rest"]: r["row"] for r in results}
    top_plus = sorted([p for p in pairs if p[1] > 0], key=lambda x: -x[1])[:5]
    top_minus = sorted([p for p in pairs if p[1] < 0], key=lambda x: x[1])[:5]
    lines = [
        f"[COD WEEKLY] Wpisano dla tygodnia {week_hdr}",
        f"Kolumna: {target['col_letter']} (arkusz Wynagrodzenia Gastro)",
        f"Wpisano: {written_n}/{total_scanned} wierszy",
        f"Skip (user input): {skipped_n}" + (f" ({', '.join(skipped_names)})" if skipped_names else ""),
        f"Błędy scrape: {error_n}",
        "",
        f"Suma COD (+): +{sum_plus:.2f} zł",
        f"Suma COD (-): {sum_minus:.2f} zł",
        f"Suma netto:   {sum_net:+.2f} zł",
        "",
        "TOP 5 plus:",
    ]
    for i, (n, c) in enumerate(top_plus, 1):
        lines.append(f"  {i}. {n}: {c:+.2f}{mark(n, row_by_name.get(n, 0))}")
    lines.append("")
    lines.append("TOP 5 minus:")
    for i, (n, c) in enumerate(top_minus, 1):
        lines.append(f"  {i}. {n}: {c:+.2f}{mark(n, row_by_name.get(n, 0))}")
    if errors:
        lines.append("")
        lines.append(f"ERRORS ({error_n}):")
        for e in errors[:5]:
            lines.append(f"  {e[:100]}")
    return "\n".join(lines)


def cmd_write(week_start, week_end) -> int:
    """Real write pipeline — scrape + batch write do Sheets + Telegram raport."""
    log.info("=== REAL WRITE MODE ===")
    log.info("Fetching sheet grid...")
    grid = fetch_sheet_grid()
    ws = grid["ws"]
    row1 = grid["row1"]
    row2 = grid["row2"]
    restaurants = grid["restaurants"]
    log.info(f"Restaurants: {len(restaurants)}")

    try:
        targets = find_target_cod_columns(row1, row2, week_start, week_end)
    except (NoTargetColumnError, AmbiguousTargetError) as e:
        log.error(f"TARGET COLUMN FAIL: {e}")
        _try_alert(f"[COD WEEKLY ALERT] Target column fail: {e}")
        return 1
    if len(targets) > 1:
        log.error(f"cmd_write: split week ({len(targets)} targets) — use separate write per segment")
        _try_alert(f"[COD WEEKLY ALERT] Split week not supported yet in --write ({len(targets)} targets)")
        return 1
    target = targets[0]
    log.info(f"Target: {target['col_letter']} ({target['segment_start']}..{target['segment_end']})")

    row_indices = [r[0] for r in restaurants]
    empty_check = validate_column_empty_ratio(ws, target["col_letter"], row_indices, threshold=0.8)
    if not empty_check["ok"]:
        log.error(f"Empty check FAIL: {empty_check}")
        _try_alert(f"[COD WEEKLY ALERT] Empty check FAIL dla {target['col_letter']}: {empty_check['ratio']:.0%} pustych (threshold 80%)")
        return 1
    log.info(f"Empty check: {empty_check['empty_count']}/{empty_check['total']} pustych ({empty_check['ratio']:.0%})")

    mapping = load_mapping()["mapping"]
    results, errors = _scrape_all(restaurants, mapping, targets)
    log.info(f"Scraped: {len([r for r in results if 'cod_per_segment' in r and None not in r['cod_per_segment']])} OK, {len(errors)} errors")

    # Build row_to_value (single target — B2 scenario)
    row_to_value = {}
    for r in results:
        if "cod_per_segment" in r and r["cod_per_segment"] and r["cod_per_segment"][0] is not None:
            row_to_value[r["row"]] = r["cod_per_segment"][0]

    log.info(f"Writing {len(row_to_value)} values to {target['col_letter']} (skip-already-filled)...")
    write_result = write_cod_column_skip_filled(ws, target["col_letter"], row_to_value, dry_run=False)
    log.info(f"  Written: {len(write_result['written_rows'])}")
    log.info(f"  Skipped (user input): {len(write_result['skipped_filled'])}")
    for sf in write_result["skipped_filled"]:
        log.info(f"    row={sf['row']}: existing={sf['existing']!r}")

    msg = _build_telegram_report(week_start, week_end, target, write_result, results, errors, target_idx=0)
    log.info("Sending Telegram report...")
    print()
    print("=== TELEGRAM MESSAGE ===")
    print(msg)
    print("=== END ===")
    print()
    tg_ok = _try_alert(msg)
    log.info(f"Telegram: {'sent' if tg_ok else 'FAIL'}")
    return 0


def _try_alert(text: str) -> bool:
    """Wyślij Telegram, NIE throw na fail (żeby write był atomowy mimo Telegram down)."""
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        return send_admin_alert(text)
    except Exception as e:
        log.error(f"Telegram send exception: {e}")
        return False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description="F2.1d COD Weekly runner")
    ap.add_argument(
        "--week",
        help="Override target week 'YYYY-MM-DD:YYYY-MM-DD' (pon:niedz). "
             "Domyślnie = poprzedni zamknięty tydzień (Warsaw TZ).",
    )
    ap.add_argument(
        "--dry-run-sample",
        help="CSV nazw restauracji do scrapingu testowego (bez zapisu do Sheets)",
    )
    ap.add_argument(
        "--dry-run-full",
        action="store_true",
        help="Pełny pipeline: find target col + scrape 66 rest + print table. BEZ zapisu.",
    )
    ap.add_argument(
        "--write",
        action="store_true",
        help="REAL WRITE: scrape + batch write do Sheets (skip-already-filled) + Telegram raport.",
    )
    args = ap.parse_args()

    if args.week:
        week_start, week_end = parse_override(args.week)
    else:
        week_start, week_end = get_previous_closed_week()
    log.info(
        f"Target week: {week_start} → {week_end} "
        f"({format_week_for_header(week_start, week_end)})"
    )

    if args.dry_run_sample:
        names = [n for n in args.dry_run_sample.split(",")]
        return cmd_dry_run_sample(names, week_start, week_end)
    if args.dry_run_full:
        return cmd_dry_run_full(week_start, week_end)
    if args.write:
        return cmd_write(week_start, week_end)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
