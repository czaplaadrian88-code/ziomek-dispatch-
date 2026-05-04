"""Entry point F2.1d COD Weekly — manual runs + cron timer.

Split-week support (2026-05-04): tygodnie krosujące miesiąc rozpisywane są
na 2 segmenty (kwiecień + maj etc.). Każdy segment ma osobną kolumnę payday
w arkuszu i jest przetwarzany niezależnie. Partial fail policy:
  - ≥1 segment zapisany OK → exit 0 + alert PARTIAL
  - 0 segmentów zapisanych → exit 1
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
    get_current_week_ending_sunday,
    parse_override,
)
from dispatch_v2.cod_weekly.panel_scraper import scrape_restaurant_cod
from dispatch_v2.cod_weekly.sheet_writer import (
    fetch_sheet_grid,
    find_target_cod_columns,
    validate_column_empty_ratio,
    write_cod_column_skip_filled,
    split_week_by_month,
    compute_payday,
    NoTargetColumnError,
    AmbiguousTargetError,
)
from dispatch_v2.cod_weekly.week_calculator import format_week_for_header as _fmt_week

log = logging.getLogger("cod_weekly.run")


def load_mapping() -> dict:
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _refresh_mapping() -> dict:
    """E1 — auto-rebuild mapping JSON przed --write.

    Zawsze próbuje świeżego scrape panel + sheet matchingu (eliminuje
    silent NO_MAPPING gdy nowa restauracja w arkuszu, której obecny
    JSON nie zna). Jeśli rebuild fail (panel down, sheet API timeout),
    fallback do statycznego JSON — graceful degradation.

    Returns: mapping dict (sheet_name → company_id | list[int]).
    """
    log.info("E1: auto-rebuilding mapping JSON...")
    try:
        from dispatch_v2.cod_weekly.restaurant_mapper import build_and_save
        payload = build_and_save()
        log.info(f"E1: mapping rebuilt — counts={payload['counts']}")
        return payload["mapping"]
    except Exception as e:
        log.warning(
            f"E1: auto-rebuild FAILED ({type(e).__name__}: {e}) — "
            "fallback do statycznego JSON"
        )
        return load_mapping()["mapping"]


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


def _scrape_all(restaurants, mapping, targets, opener=None):
    """Zwraca (results, errors). results[i] = {row, rest, cod_per_segment|error}.

    Argument `opener` opcjonalny — gdy None, wykonuje login() (zachowanie
    domyślne dla cmd_write). Test może wstrzyknąć mock opener.
    """
    if opener is None:
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


def _process_segment(ws, target, restaurants, mapping, ti, n_total, opener=None):
    """Process single target segment: empty check + scrape + write.

    Returns dict {idx, target, status, reason, write_result, results, errors}.
    Catches all exceptions w empty check / scrape / write — never raises.

    `opener` opcjonalny dla testów (mock).
    """
    prefix = f"[seg {ti+1}/{n_total}] " if n_total > 1 else ""
    log.info(f"{prefix}Target: {target['col_letter']} "
             f"({target['segment_start']}..{target['segment_end']})")

    row_indices = [r[0] for r in restaurants]

    # Empty check (per segment)
    try:
        empty_check = validate_column_empty_ratio(
            ws, target["col_letter"], row_indices, threshold=0.8,
        )
    except Exception as e:
        log.error(f"{prefix}Empty check exception: {e!r}")
        return {
            "idx": ti, "target": target, "status": "failed",
            "reason": f"empty_check_exception: {e!r}",
            "write_result": None, "results": [], "errors": [],
        }
    if not empty_check["ok"]:
        log.error(f"{prefix}Empty check FAIL: {empty_check}")
        return {
            "idx": ti, "target": target, "status": "failed",
            "reason": (
                f"empty_check_fail: ratio={empty_check['ratio']:.0%} "
                f"(filled_sample={empty_check['filled_sample']})"
            ),
            "write_result": None, "results": [], "errors": [],
        }
    log.info(
        f"{prefix}Empty check: {empty_check['empty_count']}/{empty_check['total']} "
        f"pustych ({empty_check['ratio']:.0%})"
    )

    # Scrape (per segment)
    try:
        results, errors = _scrape_all(restaurants, mapping, [target], opener=opener)
    except Exception as e:
        log.error(f"{prefix}Scrape exception: {e!r}")
        return {
            "idx": ti, "target": target, "status": "failed",
            "reason": f"scrape_exception: {e!r}",
            "write_result": None, "results": [], "errors": [],
        }
    n_ok = len([r for r in results
                if "cod_per_segment" in r and None not in r["cod_per_segment"]])
    log.info(f"{prefix}Scraped: {n_ok} OK, {len(errors)} errors")

    # Build row_to_value for this segment
    row_to_value = {}
    for r in results:
        if "cod_per_segment" in r and r["cod_per_segment"] and r["cod_per_segment"][0] is not None:
            row_to_value[r["row"]] = r["cod_per_segment"][0]

    # Write (per segment)
    try:
        log.info(
            f"{prefix}Writing {len(row_to_value)} values to "
            f"{target['col_letter']} (skip-already-filled)..."
        )
        write_result = write_cod_column_skip_filled(
            ws, target["col_letter"], row_to_value, dry_run=False,
        )
        log.info(
            f"{prefix}Written: {len(write_result['written_rows'])}, "
            f"Skipped: {len(write_result['skipped_filled'])}"
        )
        for sf in write_result["skipped_filled"]:
            log.info(f"{prefix}  row={sf['row']}: existing={sf['existing']!r}")
    except Exception as e:
        log.error(f"{prefix}Write exception: {e!r}")
        return {
            "idx": ti, "target": target, "status": "failed",
            "reason": f"write_exception: {e!r}",
            "write_result": None, "results": results, "errors": errors,
        }

    return {
        "idx": ti, "target": target, "status": "ok",
        "reason": None, "write_result": write_result,
        "results": results, "errors": errors,
    }


def _build_telegram_report_single(week_start, week_end, segment) -> str:
    """Backward-compat raport dla single-segment (NIE split-week)."""
    target = segment["target"]
    write_result = segment["write_result"]
    results = segment["results"]
    errors = segment["errors"]
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
    cods = [r["cod_per_segment"][0] for r in results
            if "cod_per_segment" in r and r["cod_per_segment"][0] is not None]
    sum_plus = sum(c for c in cods if c > 0)
    sum_minus = sum(c for c in cods if c < 0)
    sum_net = sum(cods)
    pairs = [(r["rest"], r["cod_per_segment"][0]) for r in results
             if "cod_per_segment" in r and r["cod_per_segment"][0] is not None]
    skipped_set = {sf["row"] for sf in write_result["skipped_filled"]}
    row_by_name = {r["rest"]: r["row"] for r in results}

    def mark(name):
        return " (skip)" if row_by_name.get(name, 0) in skipped_set else ""

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
        lines.append(f"  {i}. {n}: {c:+.2f}{mark(n)}")
    lines.append("")
    lines.append("TOP 5 minus:")
    for i, (n, c) in enumerate(top_minus, 1):
        lines.append(f"  {i}. {n}: {c:+.2f}{mark(n)}")
    if errors:
        lines.append("")
        lines.append(f"ERRORS ({error_n}):")
        for e in errors[:5]:
            lines.append(f"  {e[:100]}")
    return "\n".join(lines)


def _fmt_seg_range(target) -> str:
    """'27-30.04.2026' albo '01-03.05.2026' — zakres segmentu czytelny."""
    s = target["segment_start"]
    e = target["segment_end"]
    if s.month == e.month and s.year == e.year:
        return f"{s.day:02d}-{e.day:02d}.{e.month:02d}.{e.year}"
    return f"{s.day:02d}.{s.month:02d}-{e.day:02d}.{e.month:02d}.{e.year}"


def _build_telegram_report_multi(week_start, week_end, segments) -> str:
    """Multi-segment raport (split-week + partial-fail aware)."""
    week_hdr = _fmt_week(week_start, week_end)
    n_total = len(segments)
    n_ok = sum(1 for s in segments if s["status"] == "ok")

    if n_total == 1 and n_ok == 1:
        return _build_telegram_report_single(week_start, week_end, segments[0])

    if n_total == 1 and n_ok == 0:
        seg = segments[0]
        return (
            f"[COD WEEKLY] ❌ FAILED — tydzień {week_hdr}\n"
            f"\nKolumna: {seg['target']['col_letter']}"
            f"\nPowód: {seg['reason']}"
            f"\nAkcja: zweryfikować arkusz, ręcznie:"
            f"\n  --week {week_start.isoformat()}:{week_end.isoformat()} --write"
        )

    is_partial = 0 < n_ok < n_total
    is_total_fail = n_ok == 0

    if is_total_fail:
        title = f"[COD WEEKLY] ❌ FAILED — tydzień {week_hdr}"
    elif is_partial:
        title = f"[COD WEEKLY] ⚠️ PARTIAL — tydzień {week_hdr}"
    else:
        title = (
            f"[COD WEEKLY] Wpisano dla tygodnia {week_hdr}\n"
            f"Tryb: split-month ({n_total} segmenty)"
        )
    lines = [title, ""]

    # Per-segment summary
    for seg in segments:
        ti = seg["idx"]
        target = seg["target"]
        seg_range = _fmt_seg_range(target)
        seg_hdr = (
            f"═══ Segment {ti+1}/{n_total}: {seg_range} → kolumna "
            f"{target['col_letter']} ═══"
        )
        lines.append(seg_hdr)
        if seg["status"] == "ok":
            wr = seg["write_result"]
            results = seg["results"]
            errors = seg["errors"]
            written = len(wr["written_rows"])
            skipped = len(wr["skipped_filled"])
            cods = [r["cod_per_segment"][0] for r in results
                    if "cod_per_segment" in r and r["cod_per_segment"][0] is not None]
            seg_sum = sum(cods)
            lines.append("✅ OK")
            lines.append(
                f"Wpisano: {written}/{len(results)} wierszy | "
                f"Skip: {skipped} | Błędy: {len(errors)}"
            )
            lines.append(f"Suma segmentu: {seg_sum:+.2f} zł")
        else:
            lines.append("❌ FAILED")
            lines.append(f"Powód: {seg['reason']}")
            if is_partial:
                lines.append(
                    "Akcja: zweryfikować arkusz; po fixie odpalić ręcznie:"
                )
                lines.append(
                    f"  --week {week_start.isoformat()}:{week_end.isoformat()} --write"
                )
        lines.append("")

    # Aggregate (gdy ≥1 segment OK)
    if n_ok > 0:
        per_rest = {}
        all_errors = []
        for seg in segments:
            if seg["status"] != "ok":
                continue
            for r in seg["results"]:
                if "cod_per_segment" in r and r["cod_per_segment"] \
                        and r["cod_per_segment"][0] is not None:
                    per_rest[r["rest"]] = per_rest.get(r["rest"], 0.0) + r["cod_per_segment"][0]
            all_errors.extend(seg["errors"])
        sum_plus = sum(c for c in per_rest.values() if c > 0)
        sum_minus = sum(c for c in per_rest.values() if c < 0)
        sum_net = sum_plus + sum_minus

        lines.append(f"═══ Tydzień łącznie ({n_ok}/{n_total} segmentów) ═══")
        lines.append(f"Suma COD (+): +{sum_plus:.2f} zł")
        lines.append(f"Suma COD (-): {sum_minus:.2f} zł")
        lines.append(f"Suma netto:   {sum_net:+.2f} zł")
        lines.append("")
        plus_top = sorted(
            [(r, c) for r, c in per_rest.items() if c > 0],
            key=lambda x: -x[1],
        )[:5]
        minus_top = sorted(
            [(r, c) for r, c in per_rest.items() if c < 0],
            key=lambda x: x[1],
        )[:5]
        suffix = "  (oba segmenty zsumowane)" if n_ok > 1 else ""
        lines.append(f"TOP 5 plus:{suffix}")
        for i, (n, c) in enumerate(plus_top, 1):
            lines.append(f"  {i}. {n}: {c:+.2f}")
        lines.append("")
        lines.append(f"TOP 5 minus:{suffix}")
        for i, (n, c) in enumerate(minus_top, 1):
            lines.append(f"  {i}. {n}: {c:+.2f}")
        if all_errors:
            lines.append("")
            lines.append(f"ERRORS ({len(all_errors)}):")
            for e in all_errors[:5]:
                lines.append(f"  {e[:100]}")

    if is_partial:
        lines.append("")
        lines.append(
            f"Stan: {n_ok}/{n_total} segmentów zapisanych. "
            f"Niepełne rozliczenie."
        )
        lines.append("Zapisane segmenty NIE są cofane — tylko failed wymaga interwencji.")

    return "\n".join(lines)


def cmd_write(week_start, week_end, opener=None) -> int:
    """Real write pipeline — scrape + batch write do Sheets + Telegram raport.

    Split-week aware: pętla per segment, partial-fail tolerant.
    Exit 0 gdy ≥1 segment OK (alert PARTIAL gdy nie wszystkie).
    Exit 1 gdy wszystkie segmenty failed lub target column fail.

    `opener` opcjonalny dla testów (mock); produkcyjnie None → login() w _scrape_all.
    """
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
    except (NoTargetColumnError, AmbiguousTargetError, ValueError) as e:
        log.error(f"TARGET COLUMN FAIL: {e}")
        _try_alert(f"[COD WEEKLY ALERT] Target column fail: {e}")
        return 1

    n_segments = len(targets)
    if n_segments > 1:
        log.info(f"Split-month detected: {n_segments} segments")
    for t in targets:
        log.info(
            f"  Target {t['col_letter']}: "
            f"{t['segment_start']}..{t['segment_end']} (payday={t['payday']})"
        )

    # E1: auto-rebuild mapping przed scrape (eliminuje silent NO_MAPPING
    # dla nowych restauracji, których stary JSON nie zna). Fallback na
    # statyczny JSON gdy rebuild fail (panel down etc.).
    mapping = _refresh_mapping()

    # Process per segment — never raises (każdy try/except wewnątrz)
    segment_results = []
    for ti, target in enumerate(targets):
        seg = _process_segment(
            ws, target, restaurants, mapping, ti, n_segments, opener=opener,
        )
        segment_results.append(seg)

    # E4: separate alert dla NO_MAPPING (osobny od głównego raportu)
    no_mapping_names = _extract_no_mapping_names(segment_results)
    if no_mapping_names:
        nm_alert = _build_no_mapping_alert(week_start, week_end, no_mapping_names)
        log.warning(
            f"NO_MAPPING separate alert: {len(no_mapping_names)} restauracji"
        )
        _try_alert(nm_alert)
    else:
        log.info("NO_MAPPING check: 0 brakujących mapping (E4)")

    # Aggregate report
    msg = _build_telegram_report_multi(week_start, week_end, segment_results)
    log.info("Sending Telegram report...")
    print()
    print("=== TELEGRAM MESSAGE ===")
    print(msg)
    print("=== END ===")
    print()
    tg_ok = _try_alert(msg)
    log.info(f"Telegram: {'sent' if tg_ok else 'FAIL'}")

    # Exit code: 0 if any segment OK, 1 if all failed
    n_ok = sum(1 for s in segment_results if s["status"] == "ok")
    if n_ok == 0:
        log.error(f"ALL {n_segments} SEGMENTS FAILED — exit 1")
        return 1
    if n_ok < n_segments:
        log.warning(
            f"PARTIAL: {n_ok}/{n_segments} segments OK — exit 0 z alert PARTIAL"
        )
    return 0


def _extract_no_mapping_names(segments) -> list:
    """Wyciągnij UNIKALNE nazwy restauracji z NO_MAPPING errors per segment.

    Split-week duplikuje NO_MAPPING (raz per segment). Zwracamy sortowany
    set jako listę.
    """
    names = set()
    for seg in segments:
        for err in seg.get("errors", []):
            if not err.startswith("NO_MAPPING "):
                continue
            rest_part = err[len("NO_MAPPING "):].strip()
            # errors generated as f"NO_MAPPING {name!r}" → 'name' (single quotes)
            if len(rest_part) >= 2 and rest_part[0] == rest_part[-1] and rest_part[0] in "'\"":
                rest_part = rest_part[1:-1]
            names.add(rest_part)
    return sorted(names)


def _build_no_mapping_alert(week_start, week_end, names) -> str:
    """E4 — osobny alert 🚨 dla NO_MAPPING (oprócz głównego raportu)."""
    week_hdr = format_week_for_header(week_start, week_end)
    lines = [
        "[COD WEEKLY] 🚨 NO_MAPPING — restauracje pominięte",
        "",
        f"Tydzień: {week_hdr}",
        f"Pominięte (zero zapisu COD): {len(names)}",
        "",
    ]
    for n in names:
        lines.append(f"  - {n}")
    lines.extend([
        "",
        "Akcja: regeneruj mapping",
        "  python3 -m dispatch_v2.cod_weekly.restaurant_mapper --build",
        "",
        "Po regeneracji uruchom ponownie:",
        f"  python3 -m dispatch_v2.cod_weekly.run_weekly "
        f"--week {week_start.isoformat()}:{week_end.isoformat()} --write",
        "(skip-already-filled chroni przed duplikatami)",
    ])
    return "\n".join(lines)


def _build_preflight_instruction(week_start, week_end, segments, payday_str, error) -> str:
    """Generuj human-friendly instrukcję dla Rafała na bazie typu błędu."""
    n_expected = len(segments)
    if n_expected == 1:
        seg = segments[0]
        s, e = seg["start"], seg["end"]
        if s.month == e.month:
            range_str = f"{s.day:02d}-{e.day:02d}.{e.month:02d}.{e.year}"
        else:
            range_str = f"{s.day:02d}.{s.month:02d}-{e.day:02d}.{e.month:02d}.{e.year}"
        return (
            "Akcja Rafał:\n"
            "W arkuszu 'Wynagrodzenia Gastro' dodaj 4 kolumny po ostatnim "
            "wypełnionym bloku:\n"
            "  - Row 2 (od lewej): 'COD - Transport' / 'Korekty' / 'Wypłata' / 'Saldo do przen.'\n"
            f"  - Row 1 pos+2 (3-cia kolumna nowego bloku): '{payday_str}'   ← payday\n"
            f"  - Row 1 pos+3 (4-ta kolumna): '{range_str}'   ← zakres tygodnia\n"
            "  - Row 1 pos+0/+1: dowolne (np. 'Tydzień N' / 'wypłata z dn.')"
        )
    # split-month
    lines = [
        "Akcja Rafał:",
        "Tydzień krosuje miesiąc — wymagane DWA bloki w arkuszu "
        "'Wynagrodzenia Gastro'.",
        f"Oba bloki MUSZĄ mieć tę samą datę payday: {payday_str}",
        "Różnić się muszą TYLKO zakresem dat (Row 1 pos+3):",
        "",
    ]
    for i, seg in enumerate(segments, 1):
        s, e = seg["start"], seg["end"]
        if s.month == e.month:
            range_str = f"{s.day:02d}-{e.day:02d}.{e.month:02d}.{e.year}"
        else:
            range_str = f"{s.day:02d}.{s.month:02d}-{e.day:02d}.{e.month:02d}.{e.year}"
        lines.append(
            f"  Blok {i}/{n_expected} (miesiąc {s.month:02d}): "
            f"zakres '{range_str}', payday '{payday_str}'"
        )
    lines.extend([
        "",
        "Pozostałe komórki bloku (jak dotychczas):",
        "  Row 2: 'COD - Transport' / 'Korekty' / 'Wypłata' / 'Saldo do przen.'",
        "  Row 1 pos+0/+1: dowolne (np. 'Tydzień N' / 'wypłata z dn.')",
    ])
    return "\n".join(lines)


def cmd_preflight(week_start, week_end) -> int:
    """E5 — sprawdź czy arkusz ma kolumnę docelową bez scrape/write.

    Zwraca exit 0 gdy OK (no telegram, log only).
    Zwraca exit 1 gdy missing/ambiguous/sheet error (alert Telegram).

    Cel: niedziela 23:00 cron alertuje Rafała przed pn 08:00 odpaleniem.
    """
    week_hdr = format_week_for_header(week_start, week_end)
    log.info(f"=== PREFLIGHT MODE === tydzień {week_hdr}")
    log.info("Fetching sheet grid...")
    try:
        grid = fetch_sheet_grid()
    except Exception as e:
        msg = (
            f"[COD WEEKLY PREFLIGHT] ❌ Nie mogę otworzyć arkusza\n"
            f"\nTydzień: {week_hdr}\nBłąd: {e!r}"
            f"\n\nAkcja: sprawdzić service_account + dostęp do spreadsheet"
        )
        log.error(f"Sheet fetch error: {e!r}")
        _try_alert(msg)
        return 1

    row1 = grid["row1"]
    row2 = grid["row2"]
    restaurants = grid["restaurants"]
    log.info(
        f"Sheet OK: row1={len(row1)}, row2={len(row2)}, "
        f"restaurants={len(restaurants)}"
    )

    segments = split_week_by_month(week_start, week_end)
    n_expected = len(segments)
    payday = compute_payday(week_start, week_end)
    payday_str = payday.strftime("%d-%m-%Y")

    try:
        targets = find_target_cod_columns(row1, row2, week_start, week_end)
    except (NoTargetColumnError, AmbiguousTargetError, ValueError) as e:
        instr = _build_preflight_instruction(
            week_start, week_end, segments, payday_str, e,
        )
        msg = (
            f"[COD WEEKLY PREFLIGHT] ⚠️ Brak kolumny — tydzień {week_hdr}\n"
            f"\nTermin: jutro 08:00 Warsaw odpali się auto-rozliczenie."
            f"\nOczekiwane segmenty: {n_expected}"
            f"\nPayday: {payday_str}"
            f"\nBłąd: {type(e).__name__}: {e}"
            f"\n\n{instr}"
            f"\n\nWeryfikacja po dodaniu:"
            f"\n  python3 -m dispatch_v2.cod_weekly.run_weekly --preflight "
            f"--week {week_start.isoformat()}:{week_end.isoformat()}"
        )
        log.error(f"PREFLIGHT FAIL: {type(e).__name__}: {e}")
        _try_alert(msg)
        return 1

    log.info(f"PREFLIGHT OK: {len(targets)} target(s) found")
    for t in targets:
        log.info(
            f"  ✅ {t['col_letter']}: {t['segment_start']}..{t['segment_end']} "
            f"(payday={t['payday']})"
        )
    if len(targets) != n_expected:
        # Sanity: find_target sukces ale liczba targetów inna niż expected.
        # Nie powinno się zdarzyć (find_target sam by raise AmbiguousTargetError).
        # Defensive log + alert.
        msg = (
            f"[COD WEEKLY PREFLIGHT] ⚠️ Anomalia — tydzień {week_hdr}\n"
            f"Znaleziono {len(targets)} targets, oczekiwane {n_expected}.\n"
            f"Skontaktuj się z administratorem (manualne sprawdzenie arkusza)."
        )
        log.warning(msg)
        _try_alert(msg)
        return 1
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
        help="REAL WRITE: scrape + batch write do Sheets (skip-already-filled) + Telegram raport. "
             "Split-week aware: ≥1 segment OK → exit 0 + alert PARTIAL.",
    )
    ap.add_argument(
        "--preflight",
        action="store_true",
        help="E5: sprawdź czy arkusz ma kolumnę docelową dla nadchodzącego "
             "tygodnia (default = current week pn-niedz, override przez --week). "
             "NO scrape, NO write. Alert Telegram tylko gdy missing.",
    )
    args = ap.parse_args()

    # --preflight default uses CURRENT week (kończący się dziś), nie previous closed.
    # Inne komendy (--write, --dry-run-*) używają previous closed.
    if args.week:
        week_start, week_end = parse_override(args.week)
    elif args.preflight:
        week_start, week_end = get_current_week_ending_sunday()
    else:
        week_start, week_end = get_previous_closed_week()
    log.info(
        f"Target week: {week_start} → {week_end} "
        f"({format_week_for_header(week_start, week_end)})"
    )

    if args.preflight:
        return cmd_preflight(week_start, week_end)
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
