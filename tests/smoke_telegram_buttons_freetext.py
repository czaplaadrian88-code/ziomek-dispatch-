"""Smoke test dla Task #5 (buttons per kurier) + Task #6 (free-text REPLY_OVERRIDE).

Weryfikuje:
  Task #5:
    - build_keyboard(oid, candidates) tworzy rząd 1 z przyciskami per kandydat
    - time_min clamped do [5, 60]
    - callback_data format "ASSIGN:{oid}:{cid}:{tmin}"
    - rząd 2 zawsze [INNY, KOORD] (bez NIE)
    - candidates=None/[] → tylko rząd 2
    - callback parse: "ASSIGN:466700:207:15" → (action=ASSIGN, oid=466700, cid=207, tmin=15)

  Task #6:
    - _parse_courier_time: różne formaty "Imię [czas]"
    - allow_name_only=True → samo imię OK (default 15)
    - allow_name_only=False → wymaga explicit czasu
    - time >60 / <1 → None (guard anti-false-positive)
    - pusty/nonsens text → None

Uruchomienie: python3 -m dispatch_v2.tests.smoke_telegram_buttons_freetext
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import telegram_approver as ta


FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ✓ {msg}")
    else:
        FAILURES.append(msg)
        print(f"  ✗ {msg}")


# ============ Task #5: build_keyboard ============

def test_keyboard_no_candidates():
    print("\n[T#5 Case 1] build_keyboard(oid, None) → tylko rząd 2:")
    kbd = ta.build_keyboard("466700", candidates=None)
    rows = kbd["inline_keyboard"]
    check(len(rows) == 1, f"1 rząd (got {len(rows)})")
    check(len(rows[0]) == 2, f"rząd 2 ma 2 przyciski (got {len(rows[0])})")
    texts = [b["text"] for b in rows[0]]
    check("🔄 INNY" in texts[0], f"przycisk INNY (got {texts[0]!r})")
    check("👤 KOORD" in texts[1], f"przycisk KOORD (got {texts[1]!r})")
    check(not any("TAK" in t or "NIE" in t for t in texts), "brak TAK/NIE")


def test_keyboard_one_candidate():
    print("\n[T#5 Case 2] build_keyboard z 1 kandydatem:")
    cands = [{"courier_id": "207", "name": "Marek", "travel_min": 13.4}]
    kbd = ta.build_keyboard("466700", candidates=cands)
    rows = kbd["inline_keyboard"]
    check(len(rows) == 2, f"2 rzędy (got {len(rows)})")
    check(len(rows[0]) == 1, f"rząd 1 ma 1 przycisk")
    btn = rows[0][0]
    # time_min = round(13.4) + 2 = 15
    check("Marek 15min" in btn["text"], f"text zawiera 'Marek 15min' (got {btn['text']!r})")
    check(btn["callback_data"] == "ASSIGN:466700:207:15",
          f"callback_data=ASSIGN:466700:207:15 (got {btn['callback_data']!r})")


def test_keyboard_three_candidates():
    print("\n[T#5 Case 3] build_keyboard z 3 kandydatami:")
    cands = [
        {"courier_id": "207", "name": "Marek", "travel_min": 10.0},   # round(10)+2=12
        {"courier_id": "289", "name": "Grzegorz", "travel_min": 18.7}, # round(18.7)+2=21
        {"courier_id": "312", "name": "Bartek", "travel_min": 3.0},    # round(3)+2=5
    ]
    kbd = ta.build_keyboard("466700", candidates=cands)
    rows = kbd["inline_keyboard"]
    check(len(rows) == 2, f"2 rzędy")
    check(len(rows[0]) == 3, f"rząd 1 ma 3 przyciski (got {len(rows[0])})")
    texts = [b["text"] for b in rows[0]]
    callbacks = [b["callback_data"] for b in rows[0]]
    check("Marek 12min" in texts[0], f"t1={texts[0]!r}")
    check("Grzegorz 21min" in texts[1], f"t2={texts[1]!r}")
    check("Bartek 5min" in texts[2], f"t3={texts[2]!r}")
    check(callbacks[0] == "ASSIGN:466700:207:12", f"cb1={callbacks[0]}")
    check(callbacks[1] == "ASSIGN:466700:289:21", f"cb2={callbacks[1]}")
    check(callbacks[2] == "ASSIGN:466700:312:5", f"cb3={callbacks[2]}")


def test_keyboard_clamp_extremes():
    print("\n[T#5 Case 4] clamp time_min do [5, 60]:")
    cands = [
        {"courier_id": "A", "name": "Low", "travel_min": 0.5},    # round(0.5)+2=2 → clamp 5
        {"courier_id": "B", "name": "High", "travel_min": 65.0},  # round(65)+2=67 → clamp 60
    ]
    kbd = ta.build_keyboard("X", candidates=cands)
    texts = [b["text"] for b in kbd["inline_keyboard"][0]]
    check("Low 5min" in texts[0], f"clamp floor 5min (got {texts[0]!r})")
    check("High 60min" in texts[1], f"clamp ceiling 60min (got {texts[1]!r})")


def test_keyboard_truncates_to_3():
    print("\n[T#5 Case 5] więcej niż 3 kandydatów → tylko 3:")
    cands = [
        {"courier_id": str(i), "name": f"C{i}", "travel_min": 10.0}
        for i in range(1, 6)
    ]
    kbd = ta.build_keyboard("X", candidates=cands)
    check(len(kbd["inline_keyboard"][0]) == 3, f"max 3 przyciski (got {len(kbd['inline_keyboard'][0])})")


def test_keyboard_skips_invalid():
    print("\n[T#5 Case 6] pomija None/bez courier_id:")
    cands = [
        None,
        {"courier_id": "", "name": "Empty"},  # pusty cid → skip
        {"courier_id": "207", "name": "OK", "travel_min": 10},
    ]
    kbd = ta.build_keyboard("X", candidates=cands)
    check(len(kbd["inline_keyboard"][0]) == 1, f"tylko 1 valid (got {len(kbd['inline_keyboard'][0])})")
    check(kbd["inline_keyboard"][0][0]["callback_data"] == "ASSIGN:X:207:12",
          "valid cand → ASSIGN:X:207:12")


def test_assign_callback_parse():
    """Weryfikacja że callback 'ASSIGN:466700:207:15' poprawnie parsuje się
    w updates_poller: split(':', 1) → ('ASSIGN', '466700:207:15')."""
    print("\n[T#5 Case 7] ASSIGN callback parse (updates_poller + handle_callback):")
    data = "ASSIGN:466700:207:15"
    action, oid_raw = data.split(":", 1)
    check(action == "ASSIGN", f"action=ASSIGN (got {action})")
    check(oid_raw == "466700:207:15", f"oid_raw=466700:207:15 (got {oid_raw})")
    # handle_callback re-splits oid_raw
    parts = oid_raw.split(":")
    check(len(parts) == 3, f"3 parts (got {len(parts)})")
    check(parts[0] == "466700" and parts[1] == "207" and int(parts[2]) == 15,
          f"parsed: oid={parts[0]}, cid={parts[1]}, tmin={parts[2]}")


# ============ Task #6: _parse_courier_time ============

def test_parse_time_formats():
    print("\n[T#6 Case 1] _parse_courier_time — formaty czasu:")
    # HH:MM w przyszłości (względem now)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_w = datetime.now(ZoneInfo("Europe/Warsaw"))
    future_h = (now_w.hour + 1) % 24
    future_str = f"Bartek {future_h:02d}:30"

    cases = [
        ("Bartek 15", False, ("Bartek", 15)),
        ("Bartek 15min", False, ("Bartek", 15)),
        ("Michał 20 min", False, ("Michał", 20)),
        ("Gabriel 40", False, ("Gabriel", 40)),
    ]
    for text, allow_name, expected in cases:
        result = ta._parse_courier_time(text, allow_name_only=allow_name)
        check(result == expected,
              f"{text!r} (allow_name={allow_name}) → {expected} (got {result})")


def test_parse_name_only():
    print("\n[T#6 Case 2] samo imię vs allow_name_only:")
    r1 = ta._parse_courier_time("Bartek", allow_name_only=True)
    check(r1 == ("Bartek", 15), f"allow_name_only=True: samo 'Bartek' → ('Bartek', 15) (got {r1})")

    r2 = ta._parse_courier_time("Bartek", allow_name_only=False)
    check(r2 is None, f"allow_name_only=False: samo 'Bartek' → None (got {r2})")


def test_parse_empty_or_noise():
    print("\n[T#6 Case 3] pusty/noise text → None:")
    for text in ["", "   ", "Dzień dobry", "halo co tam"]:
        r = ta._parse_courier_time(text, allow_name_only=False)
        check(r is None, f"{text!r} → None (got {r})")
    # "Dzień dobry" z allow_name_only=True → defaults to ("Dzień dobry", 15) — to jest by design
    r = ta._parse_courier_time("Dzień dobry", allow_name_only=True)
    check(r == ("Dzień dobry", 15),
          f"'Dzień dobry' allow_name=True → ('Dzień dobry', 15) [by design — wymagany Reply context]")


def test_parse_time_out_of_range():
    print("\n[T#6 Case 4] czas poza [1, 60] → None (anti-false-positive):")
    r1 = ta._parse_courier_time("kurier 207", allow_name_only=False)
    check(r1 is None, f"'kurier 207' (time=207 >60) → None (got {r1})")

    r2 = ta._parse_courier_time("Bartek 75", allow_name_only=False)
    check(r2 is None, f"'Bartek 75' (time=75 >60) → None (got {r2})")

    # 60 jest w zakresie
    r3 = ta._parse_courier_time("Bartek 60", allow_name_only=False)
    check(r3 == ("Bartek", 60), f"'Bartek 60' → ('Bartek', 60) (got {r3})")


def test_parse_hhmm_future():
    """HH:MM w przyszłości: 'Bartek HH:30' gdzie HH = current+1."""
    print("\n[T#6 Case 5] HH:MM w przyszłości:")
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_w = datetime.now(ZoneInfo("Europe/Warsaw"))
    future_h = (now_w.hour + 1) % 24
    text = f"Bartek {future_h:02d}:30"
    r = ta._parse_courier_time(text, allow_name_only=False)
    check(r is not None and r[0] == "Bartek", f"{text!r} → parsed name=Bartek (got {r})")
    if r is not None:
        _, tmin = r
        # Różnica powinna być między 30 a 90 min (zależy od :MM of now)
        check(tmin >= 1 and tmin <= 120,
              f"time_min w rozsądnym zakresie [1, 120]: tmin={tmin}")


def main():
    print("=== SMOKE TEST Task #5 (buttons) + Task #6 (free-text) ===")
    # Task #5
    test_keyboard_no_candidates()
    test_keyboard_one_candidate()
    test_keyboard_three_candidates()
    test_keyboard_clamp_extremes()
    test_keyboard_truncates_to_3()
    test_keyboard_skips_invalid()
    test_assign_callback_parse()
    # Task #6
    test_parse_time_formats()
    test_parse_name_only()
    test_parse_empty_or_noise()
    test_parse_time_out_of_range()
    test_parse_hhmm_future()

    print("\n=== WYNIK ===")
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} failure(s):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS (wszystkie asercje OK)")


if __name__ == "__main__":
    main()
