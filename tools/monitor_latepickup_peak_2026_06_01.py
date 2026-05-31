#!/usr/bin/env python3
"""Monitor pierwszego weekday lunch-peaku po 4 zmianach selekcji 2026-05-31.

Waliduje na REALNYM peaku (pon 01.06, 11-14 Warsaw = 09-12 UTC) efekt:
  - Opcja B late-pickup tiering score-first (late_pickup_shadow)
  - fix #5 last_picked_up_pickup→INFORMED (w late_pickup_shadow)
  - fix #6 R6 danger zone (r6_danger_shadow + bonus_r6_soft_pen vs _legacy)
  - fix #7 v3273 wait steepen (bonus_v3273_wait_courier vs _legacy) + pre_shift bucket

READ-ONLY: czyta TYLKO shadow_decisions.jsonl (pola shadow już tam są — zero
rekomputacji, zero wpływu na prod). Digest → Telegram (send_admin_alert) + raport md.

Niedzielne dane były niereprezentatywne (lekki ruch, mult 1.0). Ten monitor łapie
pierwszy peak z obłożonymi kurierami = realny konflikt tier-0/tier-1 + near-limit bundle.
"""
import argparse
import json
import sys
from collections import Counter

SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
REPORT_DEFAULT = (
    "/root/.openclaw/workspace/scripts/dispatch_v2/"
    "eod_drafts/2026-06-01/latepickup_monitor_peak.md"
)


def _f(v, nd=1):
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


BLIND = {"no_gps", "pre_shift", "none", None}


def _classify_lp(lps, cid_pos):
    """Klasyfikacja rozjazdu Opcji B (logika przeglądu CC wbudowana w monitor).

    OK_REJECT_BLIND: stary zwycięzca blind/pre_shift (fake-low synthetic r6) → Opcja B
        słusznie wzięła realnego kuriera (wyższy r6 = PRAWDZIWY czas, nie regresja).
    SUSPECT_WORSE: stary informed (realny GPS) ORAZ nowy r6 > stary r6 +2min → Opcja B
        wzięła GORSZY dowóz od realnego kuriera = potencjalna regresja → DO OCZU.
    OK: nowy r6 ≤ stary (lepszy/równy dowóz) lub stary informed ale dowóz nie gorszy.
    """
    old_pos = cid_pos.get(str(lps.get("old_winner_cid")))
    old_r6 = lps.get("old_winner_r6_max_bag_time_min")
    new_r6 = lps.get("new_winner_r6_max_bag_time_min")
    if old_pos in BLIND:
        return "OK_REJECT_BLIND", old_pos
    try:
        if new_r6 is not None and old_r6 is not None and float(new_r6) > float(old_r6) + 2.0:
            return "SUSPECT_WORSE", old_pos
    except (TypeError, ValueError):
        pass
    return "OK", old_pos


def main():
    ap = argparse.ArgumentParser(description="late-pickup/R6/wait peak monitor")
    ap.add_argument("--since-iso", default="2026-06-01T09:00:00+00:00")
    ap.add_argument("--until-iso", default="2026-06-01T12:00:00+00:00")
    ap.add_argument("--report", default=REPORT_DEFAULT)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    since, until = args.since_iso, args.until_iso
    tot = 0
    lp_changed, r6_changed = [], []
    r6_danger_cands = 0           # kandydatów w strefie danger (32-35) wśród best
    wait_steeper_best = 0         # best gdzie nowa kara wait > legacy
    rr_best = rr_demoted = 0
    spreads, r6s = [], []         # best deliv_spread / r6 (czy maleją)
    tier_dist = Counter()
    pos_best = Counter()
    lp_class = Counter()          # klasyfikacja rozjazdów Opcji B (przegląd wbudowany)
    lp_suspects = []              # ⚠ potencjalne regresje (do oczu)

    with open(LOG) as f:
        for line in f:
            if '"verdict": "PROPOSE"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts", "")
            if not (since <= ts < until):
                continue
            if "_NEW_ORDER_first" not in d.get("event_id", ""):
                continue
            b = d.get("best")
            if not b:
                continue
            tot += 1
            pos_best[b.get("pos_source")] += 1
            sp, r6 = _f(b.get("deliv_spread_km"), 2), _f(b.get("r6_max_bag_time_min"), 1)
            if sp is not None:
                spreads.append(sp)
            if r6 is not None:
                r6s.append(r6)
                if r6 > 32.0:
                    r6_danger_cands += 1

            # cid → pos_source z kandydatów tej propozycji (do klasyfikacji starego zwycięzcy)
            cid_pos = {str(c.get("courier_id")): c.get("pos_source")
                       for c in [b] + d.get("alternatives", [])}

            lps = d.get("late_pickup_shadow") or {}
            if lps.get("changed"):
                cls, old_pos = _classify_lp(lps, cid_pos)
                lp_class[cls] += 1
                lp_changed.append((ts[11:16], d["order_id"], d.get("restaurant", "")[:14], lps, cls, old_pos))
                tier_dist[lps.get("new_winner_tier")] += 1
                if cls == "SUSPECT_WORSE":
                    lp_suspects.append((ts[11:16], d["order_id"], d.get("restaurant", "")[:14], lps, old_pos))

            r6s_ = d.get("r6_danger_shadow") or {}
            if r6s_.get("changed"):
                r6_changed.append((ts[11:16], d["order_id"], d.get("restaurant", "")[:14], r6s_))

            # fix #7 wait: best gdzie nowa kara v3273 ostrzejsza niż legacy
            wn, wl = b.get("bonus_v3273_wait_courier"), b.get("bonus_v3273_wait_courier_legacy")
            if isinstance(wn, (int, float)) and isinstance(wl, (int, float)) and wn < wl - 0.01:
                wait_steeper_best += 1

            if b.get("return_to_restaurant"):
                rr_best += 1
            for c in d.get("alternatives", []):
                if c.get("return_to_restaurant"):
                    rr_demoted += 1
                    break

    med = lambda xs: round(sorted(xs)[len(xs) // 2], 2) if xs else None
    digest_lines = [
        "📊 PEAK MONITOR 01.06 lunch (11-14) — 4 zmiany selekcji 31.05",
        f"PROPOSE w oknie: {tot}",
        f"🔀 Opcja B przestawiła zwycięzcę: {len(lp_changed)} — przegląd: {dict(lp_class)}",
        f"   (OK_REJECT_BLIND=słusznie odrzucił blind/pre_shift fake-low | "
        f"SUSPECT_WORSE=⚠realny kurier, gorszy dowóz | OK=lepszy/równy)",
        f"🔀 R6-danger przestawił zwycięzcę: {len(r6_changed)}",
        f"⏱ best w strefie danger r6>32: {r6_danger_cands}/{tot}",
        f"⏳ best z ostrzejszą karą wait (fix#7): {wait_steeper_best}",
        f"↩ powrót: demoted={rr_demoted} / wygrał(only/infeasible)={rr_best}",
        f"📦 best deliv_spread mediana={med(spreads)}km | r6 mediana={med(r6s)}min",
        f"pos_source best: {dict(pos_best)}",
    ]
    if lp_suspects:
        digest_lines.append(f"⚠️ DO OCZU — {len(lp_suspects)} potencjalnych regresji (realny kurier→gorszy r6):")
        for ts, oid, rest, lps, old_pos in lp_suspects[:6]:
            digest_lines.append(
                f"  {ts} {oid} {rest}: {lps.get('old_winner_name')}[{old_pos}]"
                f"(sc {lps.get('old_winner_score')},r6 {lps.get('old_winner_r6_max_bag_time_min')})→"
                f"{lps.get('new_winner_name')}(sc {lps.get('new_winner_score')},"
                f"r6 {lps.get('new_winner_r6_max_bag_time_min')},tier {lps.get('new_winner_tier')})")
    else:
        digest_lines.append("✅ Brak SUSPECT_WORSE — żaden rozjazd nie wziął gorszego dowozu od realnego kuriera.")
    digest = "\n".join(digest_lines)
    print(digest)

    with open(args.report, "w") as rf:
        rf.write(f"# Late-pickup/R6/wait peak monitor — 01.06 lunch\n\n")
        rf.write(f"Okno: {since} → {until} | PROPOSE: {tot}\n\n")
        rf.write(digest + "\n\n")
        rf.write("## Opcja B rozjazdy (wszystkie, z klasyfikacją)\n\n")
        for ts, oid, rest, lps, cls, old_pos in lp_changed:
            mark = "⚠️" if cls == "SUSPECT_WORSE" else ("✅" if cls == "OK_REJECT_BLIND" else "·")
            rf.write(
                f"- {mark} [{cls}] {ts} {oid} {rest}: stary={lps.get('old_winner_name')}[{old_pos}] "
                f"(sc {lps.get('old_winner_score')},tier {lps.get('old_winner_tier')},"
                f"spread {lps.get('old_winner_deliv_spread_km')},r6 {lps.get('old_winner_r6_max_bag_time_min')}) "
                f"→ nowy={lps.get('new_winner_name')} (sc {lps.get('new_winner_score')},"
                f"tier {lps.get('new_winner_tier')},spread {lps.get('new_winner_deliv_spread_km')},"
                f"r6 {lps.get('new_winner_r6_max_bag_time_min')})\n")
        rf.write("\n## R6-danger rozjazdy\n\n")
        for ts, oid, rest, r6s_ in r6_changed:
            rf.write(
                f"- {ts} {oid} {rest}: stary={r6s_.get('old_winner_name')}"
                f"(r6 {r6s_.get('old_winner_r6_max_bag_time_min')}) → "
                f"nowy={r6s_.get('new_winner_name')}(r6 {r6s_.get('new_winner_r6_max_bag_time_min')})\n")
        rf.write("\n---\nREAD-ONLY (tylko shadow_decisions.jsonl). Bez wpływu na prod.\n")

    if not args.no_telegram:
        try:
            from dispatch_v2 import telegram_utils as T
            ok = T.send_admin_alert(digest)
            print(f"\n[telegram] send_admin_alert ok={ok}")
        except Exception as e:  # noqa: BLE001
            print(f"\n[telegram] FAIL {type(e).__name__}: {e}")
    print(f"\n[report] {args.report}")


if __name__ == "__main__":
    main()
