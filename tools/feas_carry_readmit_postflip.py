#!/usr/bin/env python3
"""Post-flip monitor B2 / #483000 (ENABLE_FEAS_CARRY_READMIT LIVE od 2026-06-27 ~22:18 UTC).

Potwierdza ŻYWE zachowanie po flipie + auto-alarm na regresję. Czyta:
  (1) journal dispatch-shadow → linie 'FEAS_CARRY_READMIT order=... redirect A→B regret=Xmin newbag=Ymin'
      = realne re-admity (LIVE redirect feasible-path).
  (2) feas_carry_blind_shadow.jsonl (shadow biegnie nadal) → would_redirect od flipa = ile POWINNO być.
  (3) journal → błędy w ścieżce ('FEAS_CARRY_READMIT live fail' / 'pick failed').

Werdykt:
  ✅ CLEAN  — re-admity występują (lub 0 gdy 0 okazji), KAŻDY z newbag≤cap (Tier-3 respektowany),
              regret>0 (carry-inclusive lepszy), ZERO błędów ścieżki.
  ⚠ ALARM  — błędy ścieżki >0  LUB newbag>cap (cap złamany)  LUB regret≤0 (zły redirect)
              → rollback hot: ENABLE_FEAS_CARRY_READMIT=false w flags.json (bez restartu).
Użycie: python3 -m dispatch_v2.tools.feas_carry_readmit_postflip [--since '2026-06-27 22:18'] [--cap 40] [--notify]
"""
import argparse
import json
import re
import subprocess
import sys

SHADOW_LOG = "/root/.openclaw/workspace/dispatch_state/feas_carry_blind_shadow.jsonl"
FLIP_DEFAULT = "2026-06-27 22:18:00"
LINE_RE = re.compile(
    r"FEAS_CARRY_READMIT order=(\S+) redirect (\S+)→(\S+) regret=([0-9.\-]+|None)min newbag=([0-9.\-]+|None)min cap=([0-9.]+)")
FAIL_RE = re.compile(r"FEAS_CARRY_READMIT (live fail|pick failed)")


def _journal(since):
    try:
        out = subprocess.run(
            ["journalctl", "-u", "dispatch-shadow", "--since", since, "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=60)
        return out.stdout
    except Exception as e:
        return f"__JOURNAL_FAIL__ {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=FLIP_DEFAULT)
    ap.add_argument("--cap", type=float, default=40.0)
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()

    jtext = _journal(args.since)
    if jtext.startswith("__JOURNAL_FAIL__"):
        print("⚠ journal niedostępny:", jtext)
        return 2

    redirects = []
    for m in LINE_RE.finditer(jtext):
        oid, frm, to, regret, newbag, cap = m.groups()
        redirects.append({
            "oid": oid, "from": frm, "to": to,
            "regret": None if regret == "None" else float(regret),
            "newbag": None if newbag == "None" else float(newbag),
            "cap": float(cap),
        })
    n_fail = len(FAIL_RE.findall(jtext))

    # shadow would_redirect od flipa (ile POWINNO) — sanity że flaga faktycznie działa
    flip_day = args.since[:10]
    would = 0
    try:
        for ln in open(SHADOW_LOG, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if (r.get("ts") or "")[:10] >= flip_day and r.get("would_redirect"):
                would += 1
    except Exception:
        would = -1

    cap_violations = [r for r in redirects if r["newbag"] is not None and r["newbag"] > args.cap]
    bad_regret = [r for r in redirects if r["regret"] is not None and r["regret"] <= 0]

    print(f"=== B2 feas-carry-readmit POST-FLIP (since {args.since}, cap {args.cap}) ===")
    print(f"LIVE re-admity (redirect):     {len(redirects)}")
    print(f"shadow would_redirect (≥flip): {would}  (ile okazji widział cień)")
    print(f"błędy ścieżki (live fail):     {n_fail}")
    if redirects:
        regs = [r['regret'] for r in redirects if r['regret'] is not None]
        nbs = [r['newbag'] for r in redirects if r['newbag'] is not None]
        print(f"regret_min: min={min(regs) if regs else '-'} max={max(regs) if regs else '-'}")
        print(f"newbag_min: max={max(nbs) if nbs else '-'} (cap {args.cap})")
        for r in redirects[:8]:
            print(f"  oid={r['oid']} {r['from']}→{r['to']} regret={r['regret']} newbag={r['newbag']}")
    print(f"cap-violations (newbag>{args.cap}): {len(cap_violations)}")
    print(f"bad-regret (≤0):               {len(bad_regret)}")

    alarm = (n_fail > 0) or bool(cap_violations) or bool(bad_regret)
    verdict = "⚠ ALARM" if alarm else "✅ CLEAN"
    detail = ""
    if alarm:
        parts = []
        if n_fail:
            parts.append(f"{n_fail} błędów ścieżki")
        if cap_violations:
            parts.append(f"{len(cap_violations)} cap-violations")
        if bad_regret:
            parts.append(f"{len(bad_regret)} bad-regret")
        detail = " (" + ", ".join(parts) + ") → ROLLBACK hot: ENABLE_FEAS_CARRY_READMIT=false"
    print(f"\nWERDYKT: {verdict}{detail}")
    if not alarm:
        print("  (re-admity capped+Pareto OK; jeśli 0 re-admitów a would_redirect>0 → sprawdź "
              "czy flaga ON w fingerprincie procesu)")

    if args.notify:
        try:
            sys.path.insert(0, "/root/.openclaw/workspace/scripts")
            from dispatch_v2.telegram_utils import send_admin_alert
            msg = (f"{verdict} B2 feas-carry-readmit post-flip: {len(redirects)} re-admitów, "
                   f"{n_fail} błędów, cap-viol {len(cap_violations)}, bad-regret {len(bad_regret)} "
                   f"(would_redirect cień {would}).{detail}")
            send_admin_alert(msg)
            print("\n[notify] wysłano na Telegram")
        except Exception as e:
            print(f"\n[notify] fail: {e!r}")
    return 1 if alarm else 0


if __name__ == "__main__":
    sys.exit(main())
