"""Mockup v2 (2026-05-07) — operator-friendly Telegram propozycja redesign.

8 testów pokrywających: body layout, confidence bucket (3 wariants), best_effort
banner, GPS markers (5 wariants), bag emoji bucketing, reason composer (4
ścieżki + v326_rationale priority), keyboard 4-button top row + safety net,
flag OFF regression guard (legacy format zachowany).

Manual stdlib runner — pytest available but matching project convention.
"""
import os
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import telegram_approver as ta


# ----- helpers -----

class _FlagPatch:
    """Context manager: monkey-patch ta.flag() na test duration."""
    def __init__(self, value: bool):
        self.value = value
        self._orig = None

    def __enter__(self):
        self._orig = ta.flag

        def fake(name, default=False):
            if name == "PROPOSAL_FORMAT_V2":
                return self.value
            return self._orig(name, default)

        ta.flag = fake
        return self

    def __exit__(self, *exc):
        ta.flag = self._orig


def _mk_decision(
    auto_route="ACK",
    best_effort=False,
    best_pos_source="gps",
    best_bag=0,
    best_free_at=0.0,
    best_travel=15.0,
    best_eta_pickup_hhmm="21:49",
    alt2=None,
    alt3=None,
    rationale=None,
    pickup_in_min=15.0,
):
    """Fixture builder — mockup v2 #471167 stylized data."""
    from datetime import datetime, timezone, timedelta
    pickup_iso = (datetime.now(timezone.utc) + timedelta(minutes=pickup_in_min)).isoformat()
    best = {
        "courier_id": "470",
        "name": "Piotr Zaw",
        "score": 80.78,
        "pos_source": best_pos_source,
        "r6_bag_size": best_bag,
        "free_at_min": best_free_at,
        "travel_min": best_travel,
        "eta_pickup_hhmm": best_eta_pickup_hhmm,
        "best_effort": best_effort,
    }
    if rationale is not None:
        best["v326_rationale"] = rationale
    decision = {
        "order_id": "471167",
        "restaurant": "Restauracja Kumar's",
        "delivery_address": "Rzemieślnicza 40/44",
        "best": best,
        "alternatives": [],
        "auto_route": auto_route,
        "pool_total_count": 5,
        "pool_feasible_count": 3,
        "pickup_ready_at": pickup_iso,
    }
    if alt2 is not None:
        decision["alternatives"].append(alt2)
    if alt3 is not None:
        decision["alternatives"].append(alt3)
    return decision


# ----- tests -----

def test_v2_body_happy_path():
    """Full mockup v2 layout obecny: header, ⏱️ Odbiór, conf line, 👥 Kandydaci,
    💡 reason, 🗺 Trasa. Plus WYBRANY marker przy top1."""
    alt2 = {"courier_id": "370", "name": "Jakub OL", "pos_source": "gps",
            "r6_bag_size": 0, "travel_min": 16.0, "eta_pickup_hhmm": "21:49"}
    alt3 = {"courier_id": "515", "name": "Szymon P", "pos_source": "last_pickup",
            "r6_bag_size": 1, "travel_min": 35.0, "eta_pickup_hhmm": "22:09"}
    d = _mk_decision(alt2=alt2, alt3=alt3)
    out = ta._format_proposal_v2(d)
    assert out.startswith("🚖 Piotr Zaw (K-470) → Restauracja Kumar's → Rzemieślnicza 40/44 ("), \
        f"header malformed: {out[:120]!r}"
    assert "👥 Kandydaci:" in out
    assert "← WYBRANY" in out, "top1 brak WYBRANY marker"
    # Top1 ma WYBRANY, alt2/alt3 nie
    lines = out.split("\n")
    cand_lines = [ln for ln in lines if ln.startswith(("1.", "2.", "3."))]
    assert len(cand_lines) == 3, f"oczekiwane 3 candidate lines, got {len(cand_lines)}"
    assert "← WYBRANY" in cand_lines[0]
    assert "← WYBRANY" not in cand_lines[1]
    assert "← WYBRANY" not in cand_lines[2]
    assert "🗺 Trasa:" in out
    assert "— start" in out
    assert "(odbiór)" in out


def test_v2_conf_bucket_auto_ack_alert():
    """3 wariants confidence z decision.auto_route."""
    d_auto = _mk_decision(auto_route="AUTO")
    out_auto = ta._conf_line_v2(d_auto)
    assert "🟢 Top 30%" in out_auto and "auto poszłoby samo" in out_auto

    d_ack = _mk_decision(auto_route="ACK")
    out_ack = ta._conf_line_v2(d_ack)
    assert "🟡 Środek 40%" in out_ack and "szybki check" in out_ack

    d_alert = _mk_decision(auto_route="ALERT")
    out_alert = ta._conf_line_v2(d_alert)
    assert "🔴 Bottom 30%" in out_alert and "wymaga decyzji" in out_alert

    # None / legacy → ACK fallback
    d_legacy = _mk_decision(auto_route=None)
    d_legacy["auto_route"] = None
    out_legacy = ta._conf_line_v2(d_legacy)
    assert "🟡 Środek 40%" in out_legacy, "legacy/None auto_route powinien być ACK fallback"


def test_v2_best_effort_banner():
    """best_effort=True → prepend ⚠️ banner przed conf line."""
    d = _mk_decision(auto_route="ACK", best_effort=True)
    out = ta._conf_line_v2(d)
    assert out.startswith("⚠️ Best effort"), f"banner missing: {out!r}"
    assert "🟡 Środek 40%" in out, "conf line musi pozostać po bannerze"


def test_v2_gps_markers_all_5():
    """5 wariants pos_source mapping."""
    assert ta._gps_marker_v2("gps") == "📍GPS"
    assert ta._gps_marker_v2(None) == "📍GPS"  # default live GPS
    assert ta._gps_marker_v2("no_gps") == "❌brak GPS"
    assert ta._gps_marker_v2("last_pickup") == "📍last-pickup"
    assert ta._gps_marker_v2("last-pickup") == "📍last-pickup"  # alias
    assert ta._gps_marker_v2("pre_shift") == "🆔pre-shift"
    assert ta._gps_marker_v2("synthetic_BIALYSTOK_CENTER") == "❔?"  # fallback


def test_v2_bag_emoji_buckets():
    """Bag count → emoji bucket (0=🟢, 1=🟡, 2+=🔴)."""
    assert ta._bag_emoji_v2(0) == "🟢"
    assert ta._bag_emoji_v2(1) == "🟡"
    assert ta._bag_emoji_v2(2) == "🔴"
    assert ta._bag_emoji_v2(5) == "🔴"
    assert ta._bag_emoji_v2(-1) == "🟢"  # defensive


def test_v2_reason_composer_paths():
    """4 ścieżki composer + v326_rationale priority."""
    # Path A: v326_rationale priority (over heuristic)
    best_rat = {"courier_id": "470", "r6_bag_size": 0, "free_at_min": 0.0,
                "travel_min": 15.0, "v326_rationale": {"dlaczego": "RATIONALE OVERRIDE"}}
    out_rat = ta._reason_text_v2(best=best_rat, alts=[], restaurant="Kumar's",
                                  pickup_in_min=15.0, top1_eta_min=15.0)
    assert out_rat == "RATIONALE OVERRIDE", f"rationale priority broken: {out_rat!r}"

    # Path B: free + ETA == pickup_ready → "dokładnie na gotowe danie"
    best_b = {"r6_bag_size": 0, "free_at_min": 0.0}
    out_b = ta._reason_text_v2(best=best_b, alts=[], restaurant="Kumar's",
                                pickup_in_min=15.0, top1_eta_min=15.0)
    assert "Wolny od ręki" in out_b and "dokładnie na gotowe danie z Kumar's" in out_b, out_b

    # Path C: bag>0 → "Z N dowoz... w torbie"
    best_c = {"r6_bag_size": 2, "free_at_min": 5.0}
    out_c = ta._reason_text_v2(best=best_c, alts=[], restaurant="Kumar's",
                                pickup_in_min=15.0, top1_eta_min=22.0)
    assert "Z 2 dowozami w torbie" in out_c and "dotrze za 7 min" in out_c, out_c

    # Path D: contrast vs alt z bag>0 i delay >=10 min
    best_d = {"r6_bag_size": 0, "free_at_min": 0.0}
    alt_slow = {"courier_id": "515", "name": "Szymon P", "r6_bag_size": 1,
                "travel_min": 35.0}
    out_d = ta._reason_text_v2(best=best_d, alts=[alt_slow], restaurant="Kumar's",
                                pickup_in_min=15.0, top1_eta_min=15.0)
    assert "Wolny od ręki" in out_d
    assert "Szymon P ma już 1 dowóz w torbie" in out_d, out_d
    assert "spóźni się 20 min" in out_d, out_d


def test_v2_keyboard_top_row_4_buttons_plus_safety_net():
    """v2 flag ON → top row 4 buttony (Akceptuj/Weź#2/Weź#3/+10min) +
    INNY 8-grid + KOORD safety net pod spodem."""
    candidates = [
        {"courier_id": "470", "name": "Piotr Zaw", "travel_min": 15.0},
        {"courier_id": "370", "name": "Jakub OL", "travel_min": 16.0},
        {"courier_id": "515", "name": "Szymon P", "travel_min": 35.0},
    ]
    with _FlagPatch(True):
        kb = ta.build_keyboard("471167", candidates=candidates, pickup_ready_at=None)
    rows = kb["inline_keyboard"]
    # Row 1 = mockup v2 top row (4 buttons)
    assert len(rows[0]) == 4, f"top row ma {len(rows[0])} buttonów, oczekiwano 4"
    labels = [b["text"] for b in rows[0]]
    assert labels[0] == "✅ Akceptuj"
    assert labels[1] == "🥈 Weź #2"
    assert labels[2] == "🥉 Weź #3"
    assert labels[3] == "⏰ +10 min"
    # Callback compat: Akceptuj→ASSIGN, +10min→INNY:postpone_10min
    cbs = [b["callback_data"] for b in rows[0]]
    assert cbs[0].startswith("ASSIGN:471167:470:")
    assert cbs[1].startswith("ASSIGN:471167:370:")
    assert cbs[2].startswith("ASSIGN:471167:515:")
    assert cbs[3] == "INNY:postpone_10min:471167"
    # Safety net obecny: INNY 8-grid (4 rows × 2) + KOORD row
    flat_callbacks = [b["callback_data"] for row in rows for b in row]
    assert any(cb.startswith("KOORD:471167") for cb in flat_callbacks), \
        "KOORD safety net brak — Adrian explicit zostawić"
    inny_count = sum(1 for cb in flat_callbacks
                     if cb.startswith("INNY:") and not cb.startswith("INNY:postpone_10min"))
    assert inny_count == 8, f"oczekiwane 8 INNY reason buttons (safety net), got {inny_count}"


def test_v2_flag_off_returns_legacy_format():
    """Regression guard: gdy flag OFF, format_proposal() używa legacy path
    (zawiera '[PROPOZYCJA]' + 'TAK / INNY (powód) / KOORD' tekst)."""
    d = _mk_decision()
    with _FlagPatch(False):
        out = ta.format_proposal(d)
    assert "[PROPOZYCJA]" in out, "legacy header [PROPOZYCJA] brak — flag off path broken"
    assert "TAK / INNY (powód) / KOORD" in out, "legacy footer brak"
    # v2 layout markery NIEOBECNE
    assert "🚖 Piotr Zaw (K-470)" not in out
    assert "👥 Kandydaci:" not in out
    assert "🟡 Środek 40%" not in out


def test_v2_flag_on_uses_v2_path():
    """Regression guard counterpart: flag ON → v2 layout (no legacy markers)."""
    d = _mk_decision()
    with _FlagPatch(True):
        out = ta.format_proposal(d)
    assert "🚖 Piotr Zaw (K-470)" in out
    assert "👥 Kandydaci:" in out
    assert "🗺 Trasa:" in out
    assert "[PROPOZYCJA]" not in out, "legacy header obecny w v2 path — dispatcher broken"
    assert "TAK / INNY (powód) / KOORD" not in out


# ----- runner -----

def main():
    tests = [
        ('v2_body_happy_path', test_v2_body_happy_path),
        ('v2_conf_bucket_auto_ack_alert', test_v2_conf_bucket_auto_ack_alert),
        ('v2_best_effort_banner', test_v2_best_effort_banner),
        ('v2_gps_markers_all_5', test_v2_gps_markers_all_5),
        ('v2_bag_emoji_buckets', test_v2_bag_emoji_buckets),
        ('v2_reason_composer_paths', test_v2_reason_composer_paths),
        ('v2_keyboard_top_row_4_buttons_plus_safety_net',
         test_v2_keyboard_top_row_4_buttons_plus_safety_net),
        ('v2_flag_off_returns_legacy_format', test_v2_flag_off_returns_legacy_format),
        ('v2_flag_on_uses_v2_path', test_v2_flag_on_uses_v2_path),
    ]
    print('=' * 60)
    print('Mockup v2 — operator-friendly Telegram propozycja redesign')
    print('=' * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS {name}')
            passed += 1
        except AssertionError as e:
            print(f'  FAIL {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  FAIL {name}: UNEXPECTED {type(e).__name__}: {e}')
            failed.append(name)
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
