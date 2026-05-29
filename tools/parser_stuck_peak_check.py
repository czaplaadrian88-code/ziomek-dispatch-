#!/usr/bin/env python3
"""One-shot: po lunch peaku 2026-05-30 (09:00-12:00 UTC = 11:00-14:00 Warsaw)
przeanalizuj PARSER_STUCK_DIAG / ANOMALY PARSER_STUCK w oknie i wyślij werdykt
na Telegram. Potwierdza że fix `drop assigned_motion` (commit cce283a) zachowuje
się poprawnie przy realnym ruchu: pali/loguje TYLKO gdy new+delivered>=4 przy
zamrożonym active (realny sygnał), milczy w ciszy. Armowany przez `at`.

Uruchom: cd /root/.openclaw/workspace/scripts &&
         /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/parser_stuck_peak_check.py
"""
import re
import ast
import os

LOG = "/root/.openclaw/workspace/scripts/logs/watcher.log"
DATE = "2026-05-30"
WIN_START_H, WIN_END_H = 9, 12  # UTC (= 11-14 Warsaw)
REPORT_DIR = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-30"
REPORT_PATH = REPORT_DIR + "/parser_stuck_peak_analysis.md"

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):\d{2}")
DIAG_RE = re.compile(r"PARSER_STUCK_DIAG ")
PERCYCLE_RE = re.compile(r"per_cycle=(\[.*\])")


def in_window(date, hh):
    return date == DATE and WIN_START_H <= hh < WIN_END_H


def main():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        _send(f"🔴 PARSER_STUCK peak-check FAIL: nie mogę czytać watcher.log ({e})")
        return

    cur_in = False
    diag_lines, alert_count = [], 0
    sum_new = sum_deliv = 0
    saw_any_window_line = False

    for ln in lines:
        m = TS_RE.match(ln)
        if m:
            date, hh = m.group(1), int(m.group(2))
            cur_in = in_window(date, hh)
            if cur_in:
                saw_any_window_line = True
                # zbierz ruch z TICK
                if "panel_watcher: TICK" in ln:
                    nm = re.search(r"'new': (\d+).*?'delivered': (\d+)", ln)
                    if nm:
                        sum_new += int(nm.group(1))
                        sum_deliv += int(nm.group(2))
        # DIAG / ANOMALY dziedziczą okno z ostatniego timestampu
        if cur_in:
            if DIAG_RE.search(ln):
                diag_lines.append(ln)
            if "[ANOMALY PARSER_STUCK]" in ln:
                alert_count += 1

    if not saw_any_window_line:
        _send(
            f"⚠️ PARSER_STUCK peak-check ({DATE} 11-14 Warsaw): BRAK linii w oknie "
            f"09-12 UTC w watcher.log (rotacja? watcher martwy?). Sprawdź ręcznie."
        )
        return

    # Analiza każdego DIAG
    real_bug_suspects, legit, regression = [], [], []
    diag_infos = []
    for dl in diag_lines:
        info = _parse_diag(dl)
        if info is None:
            continue
        diag_infos.append(info)
        nd = info["sum_new"] + info["sum_deliv"]
        # closed rośnie? deliv piętrzy a closed stoi = realny bug closed_ids
        closed_vals = [c.get("closed") for c in info["pc"] if c.get("closed") is not None]
        closed_frozen = len(set(closed_vals)) <= 1 if closed_vals else True
        info["nd"] = nd
        info["closed_frozen"] = closed_frozen
        if nd < 4:
            # POST-FIX DIAG nie powinien istnieć przy new+deliv<4 (motion gated) → regresja
            regression.append(info)
        elif info["sum_deliv"] >= 4 and closed_frozen:
            real_bug_suspects.append(info)
        else:
            legit.append(info)

    # Werdykt
    head = f"🔎 PARSER_STUCK peak-check {DATE} 11-14 Warsaw (09-12 UTC)\n"
    ctx = f"Ruch w oknie: {sum_new} nowych, {sum_deliv} dostaw. Alerty: {alert_count}, DIAG: {len(diag_lines)}.\n\n"

    if regression:
        s = regression[0]
        body = (
            f"🔴 REGRESSION: {len(regression)} DIAG odpalił przy new+delivered<4 "
            f"(={s['nd']}) — fix `drop assigned_motion` NIE trzyma, FP wraca. "
            f"per_cycle={s['pc']} → pilnie sprawdź."
        )
    elif alert_count == 0 and not diag_lines:
        body = (
            "✅ FP POTWIERDZONY WYELIMINOWANY. Zero PARSER_STUCK i zero DIAG mimo "
            "realnego ruchu peaku — fix `drop assigned_motion` trzyma. Brak realnych anomalii."
        )
    elif real_bug_suspects:
        s = real_bug_suspects[0]
        body = (
            f"⚠️ {len(real_bug_suspects)} DIAG wygląda na REALNY bug (dostawy piętrzą się "
            f"a closed zamrożony → możliwy problem closed_ids/data-idkurier):\n"
            f"  stuck_active={s['stuck_active']} sum_deliv={s['sum_deliv']} closed_frozen=True\n"
            f"  per_cycle={s['pc']}\n→ wymaga analizy (NIE FP, legalne odpalenie)."
        )
    else:
        body = (
            f"✅ Fix zachowuje się POPRAWNIE. {len(diag_lines)} DIAG / {alert_count} alertów — "
            f"wszystkie przy realnym new+delivered≥4 (legalny sygnał, nie szum assigned), "
            f"closed rosło → dostawy odzwierciedlone, brak buga closed_ids. FP nie wraca."
        )
        if legit:
            i = legit[0]
            body += f"\nPrzykład: new+deliv={i['nd']}, closed_frozen={i['closed_frozen']}, stuck_active={i['stuck_active']}."

    report_path = _deep_report(lines, diag_infos, alert_count, sum_new, sum_deliv, body)
    _send(head + ctx + body + f"\n\nPełny raport: {report_path}")


def _parse_diag(line):
    try:
        m = PERCYCLE_RE.search(line)
        pc = ast.literal_eval(m.group(1)) if m else []
        sn = sum(int(c.get("new", 0) or 0) for c in pc)
        sd = sum(int(c.get("deliv", 0) or 0) for c in pc)
        sa = re.search(r"stuck_active=(\d+)", line)
        smp = re.search(r"stuck_active_sample=(\[[^\]]*\])", line)
        sample = ast.literal_eval(smp.group(1)) if smp else []
        return {"pc": pc, "sum_new": sn, "sum_deliv": sd, "sample": sample,
                "stuck_active": int(sa.group(1)) if sa else None}
    except Exception:
        return None


def _order_lifecycle(lines, oid):
    """Zwróć kluczowe zdarzenia lifecycle ordera z logu (dowód in-flight vs stuck)."""
    pats = ("NEW ", "ASSIGNED", "PICKED_UP", "PACKS_CATCHUP", "PACKS_GHOST",
            "COURIER_DELIVERED", "DELIVERED", "V3.19g1")
    hits = []
    for ln in lines:
        if oid in ln and any(p in ln for p in pats):
            # zostaw zwięzłą część po module name
            hits.append(ln.rstrip()[:140])
    return hits[-4:]  # ostatnie 4 zdarzenia wystarczą


def _deep_report(lines, diag_infos, alert_count, sum_new, sum_deliv, verdict_body):
    """Zapisz pełny raport markdown: per-okno stuck + lifecycle próbkowanych ID."""
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        out = []
        out.append(f"# PARSER_STUCK peak deep-analysis {DATE} (09-12 UTC / 11-14 Warsaw)\n")
        out.append(f"Wygenerowane przez parser_stuck_peak_check.py (job at #95).\n")
        out.append(f"\n## Werdykt\n{verdict_body}\n")
        out.append(f"\n## Ruch w oknie\nNowych: {sum_new} | Dostaw: {sum_deliv} | "
                   f"Alertów PARSER_STUCK: {alert_count} | Okien DIAG: {len(diag_infos)}\n")
        if not diag_infos:
            out.append("\n## Stuck-okna\nBrak — active set nigdy nie był zamrożony 5 cykli przy "
                       "new+delivered≥4. Przy realnym ruchu peaku to potwierdza brak FP i brak realnej anomalii.\n")
        for idx, info in enumerate(diag_infos, 1):
            out.append(f"\n## Stuck-okno #{idx}\n")
            out.append(f"stuck_active={info['stuck_active']} | sum_new={info['sum_new']} "
                       f"sum_deliv={info['sum_deliv']} | closed_frozen={info.get('closed_frozen')}\n\n")
            out.append("| cyc | active | order_ids | closed | new | deliv | assi |\n")
            out.append("|---|---|---|---|---|---|---|\n")
            for c in info["pc"]:
                out.append(f"| {c.get('cyc')} | {c.get('act')} | {c.get('ord')} | "
                           f"{c.get('closed')} | {c.get('new')} | {c.get('deliv')} | {c.get('assi')} |\n")
            # lifecycle próbki — dowód że stuck IDs to realne in-flight ordery
            out.append("\n**Lifecycle próbkowanych stuck-ID (dowód in-flight vs parser-stuck):**\n")
            for oid in (info.get("sample") or [])[:3]:
                ev = _order_lifecycle(lines, oid)
                out.append(f"\n- `{oid}`:\n")
                if ev:
                    for e in ev:
                        out.append(f"    - {e}\n")
                else:
                    out.append("    - (brak zdarzeń w logu — potencjalnie sygnał realnego buga)\n")
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.writelines(out)
        return REPORT_PATH
    except Exception as e:
        return f"(report write fail: {e})"


def _send(text):
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(text)
        print(f"sent={ok}\n---\n{text}")
    except Exception as e:
        print(f"SEND FAIL: {e}\n---\n{text}")


if __name__ == "__main__":
    main()
