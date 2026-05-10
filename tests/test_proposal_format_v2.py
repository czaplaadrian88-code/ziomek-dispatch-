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
    # Route emoji redesign 2026-05-08: 🍕 odbiór / 📍 dostawa, no #oid, ← TA preserved.
    assert "🍕" in out and "Restauracja Kumar's ← TA" in out
    assert "#471167" not in out


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


def test_v2_gps_markers_full_live_distribution():
    """Pełna live pos_source distribution (8 unique values w shadow_decisions.jsonl
    audit 2026-05-07 hotfix post #471182). Operational PL labels."""
    # 3 z mockup (Adrian spec)
    assert ta._gps_marker_v2("gps") == "📍GPS"
    assert ta._gps_marker_v2(None) == "📍GPS"  # default live GPS
    assert ta._gps_marker_v2("no_gps") == "❌brak GPS"
    assert ta._gps_marker_v2("pre_shift") == "🆔pre-shift"
    # 5 dodatkowych z live data (hotfix 2026-05-07)
    assert ta._gps_marker_v2("last_assigned_pickup") == "📍przy restauracji"
    assert ta._gps_marker_v2("last_picked_up_delivery") == "📍w trasie"
    assert ta._gps_marker_v2("last_picked_up_recent") == "📍w trasie"
    assert ta._gps_marker_v2("last_delivered") == "📍po dostawie"
    assert ta._gps_marker_v2("post_wave") == "📍po fali"
    # Legacy alias z mockup spec
    assert ta._gps_marker_v2("last_pickup") == "📍last-pickup"
    assert ta._gps_marker_v2("last-pickup") == "📍last-pickup"
    # Unknown → fallback
    assert ta._gps_marker_v2("synthetic_BIALYSTOK_CENTER") == "❔?"


def test_v2_bag_emoji_buckets():
    """Bag count → emoji bucket (Adrian 2026-05-08): 0-1=🟢, 2-3=🟡, 4+=🔴."""
    assert ta._bag_emoji_v2(0) == "🟢"
    assert ta._bag_emoji_v2(1) == "🟢"
    assert ta._bag_emoji_v2(2) == "🟡"
    assert ta._bag_emoji_v2(3) == "🟡"
    assert ta._bag_emoji_v2(4) == "🔴"
    assert ta._bag_emoji_v2(5) == "🔴"
    assert ta._bag_emoji_v2(-1) == "🟢"  # defensive


def test_v2_reason_composer_paths():
    """3 ścieżki rule-based composer + v326_rationale ZIGNOROWANY (hotfix
    2026-05-07 post #471182: rationale zwracał scoring breakdown 'bliskość/
    timing/przewaga' co łamie regułę feedback_rules.md 'operational logic,
    NIE scoring')."""
    # Path A: free + ETA == pickup_ready → "dokładnie na gotowe danie"
    best_a = {"r6_bag_size": 0, "free_at_min": 0.0}
    out_a = ta._reason_text_v2(best=best_a, alts=[], restaurant="Kumar's",
                                pickup_in_min=15.0, top1_eta_min=15.0)
    assert "Wolny od ręki" in out_a and "dokładnie na gotowe danie z Kumar's" in out_a, out_a

    # Path B: bag>0 → "Z N dowoz... w torbie"
    best_b = {"r6_bag_size": 2, "free_at_min": 5.0}
    out_b = ta._reason_text_v2(best=best_b, alts=[], restaurant="Kumar's",
                                pickup_in_min=15.0, top1_eta_min=22.0)
    assert "Z 2 dowozami w torbie" in out_b and "dotrze za 7 min" in out_b, out_b

    # Path C: contrast vs alt z bag>0 i delay >=10 min
    best_c = {"r6_bag_size": 0, "free_at_min": 0.0}
    alt_slow = {"courier_id": "515", "name": "Szymon P", "r6_bag_size": 1,
                "travel_min": 35.0}
    out_c = ta._reason_text_v2(best=best_c, alts=[alt_slow], restaurant="Kumar's",
                                pickup_in_min=15.0, top1_eta_min=15.0)
    assert "Wolny od ręki" in out_c
    assert "Szymon P ma już 1 dowóz w torbie" in out_c, out_c
    assert "spóźni się 20 min" in out_c, out_c

    # Path D (REGRESSION GUARD): v326_rationale.dlaczego MUSI być ignored
    # (poprzednio Priorytet 1, hotfix usunął — łamie regułę "Zero słów: score").
    best_rat = {"courier_id": "470", "r6_bag_size": 0, "free_at_min": 0.0,
                "v326_rationale": {"dlaczego": "bliskość -11, timing +5, przewaga +122"}}
    out_rat = ta._reason_text_v2(best=best_rat, alts=[], restaurant="Kumar's",
                                  pickup_in_min=15.0, top1_eta_min=15.0)
    assert "bliskość" not in out_rat, f"v326_rationale leaked do reason: {out_rat!r}"
    assert "przewaga" not in out_rat, f"scoring breakdown leaked: {out_rat!r}"
    assert "Wolny od ręki" in out_rat, "rule-based template powinno działać mimo rationale"


def test_v2_keyboard_2x2_grid_strict_4_buttons():
    """Mockup v2 strict 4-button only (Adrian post visual check 2026-05-07:
    'Tylko cztery przyciski, resztę usuń' — 2×2 grid mobile-friendly,
    NO safety net INNY/KOORD pod spodem)."""
    candidates = [
        {"courier_id": "470", "name": "Piotr Zaw", "travel_min": 15.0},
        {"courier_id": "370", "name": "Jakub OL", "travel_min": 16.0},
        {"courier_id": "515", "name": "Szymon P", "travel_min": 35.0},
    ]
    with _FlagPatch(True):
        kb = ta.build_keyboard("471167", candidates=candidates, pickup_ready_at=None)
    rows = kb["inline_keyboard"]
    # 2×2 grid: 2 rows × 2 buttons each
    assert len(rows) == 2, f"2×2 grid: 2 rows, got {len(rows)}"
    assert len(rows[0]) == 2, f"row1 powinien mieć 2 buttony, got {len(rows[0])}"
    assert len(rows[1]) == 2, f"row2 powinien mieć 2 buttony, got {len(rows[1])}"
    # Layout 1:1 z mockup
    assert rows[0][0]["text"] == "✅ Akceptuj"
    assert rows[0][1]["text"] == "🥈 Weź #2"
    assert rows[1][0]["text"] == "🥉 Weź #3"
    assert rows[1][1]["text"] == "⏰ +10 min"
    # Callbacks: ASSIGN compat + INNY:postpone_10min
    assert rows[0][0]["callback_data"].startswith("ASSIGN:471167:470:")
    assert rows[0][1]["callback_data"].startswith("ASSIGN:471167:370:")
    assert rows[1][0]["callback_data"].startswith("ASSIGN:471167:515:")
    assert rows[1][1]["callback_data"] == "INNY:postpone_10min:471167"
    # Strict — ZERO safety net rows
    total_buttons = sum(len(r) for r in rows)
    assert total_buttons == 4, f"strict 4-button only, got {total_buttons}"
    flat_callbacks = [b["callback_data"] for row in rows for b in row]
    assert not any(cb.startswith("KOORD:") for cb in flat_callbacks), \
        "KOORD safety net obecny — usunięty po visual check"
    inny_count = sum(1 for cb in flat_callbacks
                     if cb.startswith("INNY:") and not cb.startswith("INNY:postpone_10min"))
    assert inny_count == 0, f"INNY safety net obecny ({inny_count} buttonów) — usunięty"


def test_v2_keyboard_grid_with_2_candidates():
    """Edge case: tylko 2 kandydatów (np. ALERT pool=2). Layout zachowuje
    [⏰ +10 min] w prawym-dolnym — slot 🥉 nie ma kandydata, postpone fallback."""
    candidates = [
        {"courier_id": "470", "name": "Piotr Zaw", "travel_min": 15.0},
        {"courier_id": "370", "name": "Jakub OL", "travel_min": 16.0},
    ]
    with _FlagPatch(True):
        kb = ta.build_keyboard("471167", candidates=candidates, pickup_ready_at=None)
    rows = kb["inline_keyboard"]
    # row1 = [Akceptuj, Weź #2], row2 = [⏰ +10 min] (single button — slot 🥉 pominięty)
    flat = [b["text"] for row in rows for b in row]
    assert "✅ Akceptuj" in flat
    assert "🥈 Weź #2" in flat
    assert "🥉 Weź #3" not in flat
    assert "⏰ +10 min" in flat
    assert sum(len(r) for r in rows) == 3


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


# ----- 2026-05-07 extension+route sprint: pickup (+N min) + wave-aware trasa -----

def test_v2_pickup_extension_delta_emitted():
    """⏱️ Odbiór HH:MM (+N min) gdy Ziomek przedłużył deklarację restauracji.

    delta = pickup_ready_at − pickup_at_warsaw. Emit gdy >0, skip gdy 0/None.
    """
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc).replace(microsecond=0)
    raw = base + timedelta(minutes=15)  # restaurant declared
    extended = base + timedelta(minutes=23)  # Ziomek przedłużył o 8 min
    d = _mk_decision()
    d["pickup_ready_at"] = extended.isoformat()
    d["pickup_at_warsaw"] = raw.isoformat()
    out = ta._format_proposal_v2(d)
    assert "(+8 min)" in out, f"oczekiwane '(+8 min)' w pickup line: {out[:200]!r}"

    # Zero delta → bez nawiasu
    d2 = _mk_decision()
    d2["pickup_ready_at"] = raw.isoformat()
    d2["pickup_at_warsaw"] = raw.isoformat()
    out2 = ta._format_proposal_v2(d2)
    assert "(+" not in out2, f"delta=0 nie powinno emitować nawiasu: {out2[:200]!r}"

    # Brak pickup_at_warsaw → bez nawiasu (legacy decisions pre-sprint)
    d3 = _mk_decision()
    d3["pickup_ready_at"] = extended.isoformat()
    # pickup_at_warsaw NIE w decision
    out3 = ta._format_proposal_v2(d3)
    assert "(+" not in out3


def test_v2_route_iterates_plan_sequence_chronological():
    """Trasa = chronologiczne stopy z best.plan.pickup_at + predicted_delivered_at.

    Bag z 2 innymi orderami + obecna propozycja = 6 stopów (3 pickup + 3 drop)
    posortowanych po czasie. Każdy stop "← TA" tylko dla decision.order_id.
    """
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc).replace(microsecond=0)
    d = _mk_decision()
    d["best"]["bag_context"] = [
        {"order_id": "471180", "restaurant": "Pizzeria Roma", "delivery_address": "Lipowa 5"},
        {"order_id": "471181", "restaurant": "Sushi Kim", "delivery_address": "Mickiewicza 12"},
    ]
    d["best"]["plan"] = {
        "pickup_at": {
            "471180": (base + timedelta(minutes=5)).isoformat(),
            "471181": (base + timedelta(minutes=10)).isoformat(),
            "471167": (base + timedelta(minutes=15)).isoformat(),
        },
        "predicted_delivered_at": {
            "471180": (base + timedelta(minutes=20)).isoformat(),
            "471167": (base + timedelta(minutes=30)).isoformat(),
            "471181": (base + timedelta(minutes=40)).isoformat(),
        },
    }
    out = ta._format_proposal_v2(d)
    lines = out.split("\n")
    # Route lines after redesign 2026-05-08: start (🚖) + stops (🍕 odbiór / 📍 dostawa).
    # Filter only lines AFTER "🗺 Trasa:" marker żeby pominąć header (też zaczyna się 🚖).
    trasa_idx = next(i for i, ln in enumerate(lines) if ln.startswith("🗺 Trasa:"))
    route_lines = [ln for ln in lines[trasa_idx + 1:] if ln.startswith(("🚖 ", "🍕 ", "📍 "))]
    # 1 start + 6 stopów (3 pickup + 3 drop)
    assert len(route_lines) == 7, f"oczekiwane 7 linii (start + 6 stopów), got {len(route_lines)}: {route_lines}"
    # Sortowanie chronologiczne: pickup 471180 → pickup 471181 → pickup 471167 (← TA)
    # → drop 471180 → drop 471167 (← TA) → drop 471181
    assert route_lines[0].startswith("🚖 ") and "— start" in route_lines[0]
    assert route_lines[1].startswith("🍕 ") and "Pizzeria Roma" in route_lines[1] and "← TA" not in route_lines[1]
    assert route_lines[2].startswith("🍕 ") and "Sushi Kim" in route_lines[2] and "← TA" not in route_lines[2]
    assert route_lines[3].startswith("🍕 ") and "Restauracja Kumar's ← TA" in route_lines[3]
    assert route_lines[4].startswith("📍 ") and "Lipowa 5" in route_lines[4] and "← TA" not in route_lines[4]
    assert route_lines[5].startswith("📍 ") and "Rzemieślnicza 40/44 ← TA" in route_lines[5]
    assert route_lines[6].startswith("📍 ") and "Mickiewicza 12" in route_lines[6] and "← TA" not in route_lines[6]
    # ← TA tylko dla decision.order_id (#471167) — zachowane mimo usunięcia #oid
    ta_marks = [ln for ln in route_lines if "← TA" in ln]
    assert len(ta_marks) == 2, f"← TA powinno być dokładnie 2× (pickup+drop dla 471167): {ta_marks}"
    # Order numbers usunięte z route lines per Adrian 2026-05-08
    for ln in route_lines:
        assert "#" not in ln, f"order number nie powinien występować w route line: {ln!r}"


def test_v2_route_fallback_when_no_plan_data():
    """Brak plan.pickup_at + predicted_delivered_at → fallback klasyczny 3-line
    (start + odbiór + dostawa) — zachowany dla solo orderów bez TSP planu.
    """
    d = _mk_decision()
    # bez best.plan
    out = ta._format_proposal_v2(d)
    lines = out.split("\n")
    trasa_idx = next(i for i, ln in enumerate(lines) if ln.startswith("🗺 Trasa:"))
    route_lines = [ln for ln in lines[trasa_idx + 1:] if ln.startswith(("🚖 ", "🍕 ", "📍 "))]
    # start + odbiór (drop bez predicted ETA → pominięty zgodnie z _drop_eta_hhmm_v2 None)
    assert any(ln.startswith("🚖 ") and "— start (" in ln for ln in route_lines), f"brak start line: {route_lines}"
    assert any(ln.startswith("🍕 ") and "Restauracja Kumar's ← TA" in ln for ln in route_lines), \
        f"fallback brak odbiór line z 🍕 + ← TA: {route_lines}"
    assert all("#" not in ln for ln in route_lines), f"#oid nie powinien występować: {route_lines}"


def test_v2_pickup_extension_delta_helper_unit():
    """_pickup_extension_delta_min — pure unit test."""
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc).replace(microsecond=0)
    raw = base
    extended = base + timedelta(minutes=12)
    # Both present + delta>0 → 12
    d1 = {"pickup_ready_at": extended.isoformat(), "pickup_at_warsaw": raw.isoformat()}
    assert ta._pickup_extension_delta_min(d1) == 12
    # delta == 0 → 0 (caller decides skip)
    d2 = {"pickup_ready_at": raw.isoformat(), "pickup_at_warsaw": raw.isoformat()}
    assert ta._pickup_extension_delta_min(d2) == 0
    # Brak pickup_at_warsaw → None
    d3 = {"pickup_ready_at": extended.isoformat()}
    assert ta._pickup_extension_delta_min(d3) is None
    # Invalid ISO → None
    d4 = {"pickup_ready_at": "not-iso", "pickup_at_warsaw": raw.isoformat()}
    assert ta._pickup_extension_delta_min(d4) is None


# ----- Etap 1 pickup-label tests (2026-05-08) -----

def test_v2_pickup_label_with_mins_since_creation():
    """Happy path: best.eta_pickup_hhmm + best.mins_since_creation present →
    linia "⏱️ Odbiór: {hhmm} ({N} min od złożenia)" zamiast pickup_ready_at."""
    d = _mk_decision(best_eta_pickup_hhmm="11:00")
    d["best"]["mins_since_creation"] = 40
    out = ta._format_proposal_v2(d)
    assert "⏱️ Odbiór: 11:00 (40 min od złożenia)" in out, \
        f"missing new label format: {out}"
    # NIE używamy pickup_ready_at HH:MM (powinien być now+15min, NIE 11:00)
    # i NIE używamy "+N min" extension fallback
    lines = [ln for ln in out.split("\n") if ln.startswith("⏱️")]
    assert len(lines) == 1
    assert "min od złożenia" in lines[0]


def test_v2_pickup_label_fallback_no_mins_since_creation():
    """Gdy best.eta_pickup_hhmm jest ale brak mins_since_creation (legacy
    pre-shadow-fix decision lub failed compute) → fallback do pickup_ready_at
    + opcjonalna extension delta."""
    d = _mk_decision(best_eta_pickup_hhmm="11:00")
    # brak mins_since_creation w best
    out = ta._format_proposal_v2(d)
    # pickup_ready_at = now+15min HH:MM (NIE 11:00); fallback path
    assert "min od złożenia" not in out, "should use fallback when no mins_since_creation"
    assert "⏱️ Odbiór:" in out


def test_v2_pickup_label_fallback_no_best_eta():
    """Gdy brak best.eta_pickup_hhmm (rare: brak GPS+brak shift) → fallback do
    pickup_ready_at (pełny legacy path), nawet jeśli mins_since_creation ustawione."""
    d = _mk_decision(best_eta_pickup_hhmm=None)
    d["best"]["mins_since_creation"] = 40  # nawet z mins, bez eta_hhmm fallback
    out = ta._format_proposal_v2(d)
    assert "min od złożenia" not in out, \
        "without best.eta_pickup_hhmm must NOT show new format"
    assert "⏱️ Odbiór:" in out


# ----- ETAP 2 route start clamp tests (2026-05-08) -----

def test_v2_route_start_uses_effective_start_at():
    """V3.28 ETAP 2: best.effective_start_at = shift_start (UTC ISO) → trasa
    "start" line pokazuje shift_start Warsaw zamiast real now."""
    d = _mk_decision()
    # 09:00 UTC = 11:00 Warsaw (CEST)
    d["best"]["effective_start_at"] = "2026-05-08T09:00:00+00:00"
    d["best"]["pos_source"] = "pre_shift"
    out = ta._format_proposal_v2(d)
    # Trasa MUSI mieć "11:00 — start" (shift_start), NIE now_hhmm
    route_section = out.split("🗺 Trasa:")[1] if "🗺 Trasa:" in out else ""
    assert "11:00 — start" in route_section, \
        f"trasa start ≠ 11:00 (effective_start_at): {route_section[:200]}"


def test_v2_route_start_falls_back_to_now():
    """Gdy brak best.effective_start_at (gps kurier, post-shift, legacy) →
    trasa "start" line używa now_hhmm (backward compat)."""
    d = _mk_decision()
    # No effective_start_at field
    out = ta._format_proposal_v2(d)
    route_section = out.split("🗺 Trasa:")[1] if "🗺 Trasa:" in out else ""
    assert "— start" in route_section
    # NIE pokazuje 11:00 (bo brak effective_start_at) — używa now_hhmm dynamicznie
    # Konkretna wartość zależna od now() at runtime, więc wystarczy że jakiś hh:mm jest
    import re
    assert re.search(r"\d{2}:\d{2} — start", route_section), \
        f"start line nie ma HH:MM: {route_section[:200]}"


# ----- runner -----

def main():
    tests = [
        ('v2_body_happy_path', test_v2_body_happy_path),
        ('v2_conf_bucket_auto_ack_alert', test_v2_conf_bucket_auto_ack_alert),
        ('v2_best_effort_banner', test_v2_best_effort_banner),
        ('v2_gps_markers_full_live_distribution', test_v2_gps_markers_full_live_distribution),
        ('v2_bag_emoji_buckets', test_v2_bag_emoji_buckets),
        ('v2_reason_composer_paths', test_v2_reason_composer_paths),
        ('v2_keyboard_2x2_grid_strict_4_buttons',
         test_v2_keyboard_2x2_grid_strict_4_buttons),
        ('v2_keyboard_grid_with_2_candidates',
         test_v2_keyboard_grid_with_2_candidates),
        ('v2_flag_off_returns_legacy_format', test_v2_flag_off_returns_legacy_format),
        ('v2_flag_on_uses_v2_path', test_v2_flag_on_uses_v2_path),
        ('v2_pickup_extension_delta_emitted', test_v2_pickup_extension_delta_emitted),
        ('v2_route_iterates_plan_sequence_chronological',
         test_v2_route_iterates_plan_sequence_chronological),
        ('v2_route_fallback_when_no_plan_data', test_v2_route_fallback_when_no_plan_data),
        ('v2_pickup_extension_delta_helper_unit', test_v2_pickup_extension_delta_helper_unit),
        ('v2_pickup_label_with_mins_since_creation',
         test_v2_pickup_label_with_mins_since_creation),
        ('v2_pickup_label_fallback_no_mins_since_creation',
         test_v2_pickup_label_fallback_no_mins_since_creation),
        ('v2_pickup_label_fallback_no_best_eta',
         test_v2_pickup_label_fallback_no_best_eta),
        ('v2_route_start_uses_effective_start_at',
         test_v2_route_start_uses_effective_start_at),
        ('v2_route_start_falls_back_to_now',
         test_v2_route_start_falls_back_to_now),
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
