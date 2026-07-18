"""Sprint D3-gold TODO (2026-07-18) — strażnik kalibracji speed-mult per tier.

Pomiar composition-clean (eod_drafts/2026-07-18/gold_speed_mult_measure.py)
OBALIŁ tabelę 0.78/0.82 z 26.06 i dał zmierzone mediany; flip ŚWIADOMIE
zaniechany (zysk ~3% MAE << kalibrator per-kurier −20%). Pinujemy:
  1. flaga OFF (stan live) → mult 1.0 dla KAŻDEGO tieru (inert, zero decyzji),
  2. tabela == wartości ZMIERZONE 18.07 (anti-drift: nikt nie „wskrzesi" 0.78
     ani nie dopisze wartości bez nowego pomiaru — zmiana tu MUSI iść z nowym
     pomiarem i aktualizacją komentarza w common),
  3. ON → funkcja czyta tabelę (mechanizm żywy na przyszły flip za ACK).
"""
import dispatch_v2.common as C

MEASURED_2026_07_18 = {'gold': 0.96, 'std+': 1.06, 'std': 0.86,
                       'slow': 1.0, 'new': 0.95}


def test_off_is_inert_for_every_tier(monkeypatch):
    monkeypatch.setattr(C, "flag", lambda n, d=False: False
                        if n == "ENABLE_DRIVE_SPEED_TIER_CORRECTION" else d)
    for tier in list(MEASURED_2026_07_18) + [None, "nieznany"]:
        assert C.speed_mult_for_tier(tier) == 1.0


def test_table_matches_measurement_2026_07_18():
    assert C.DRIVE_SPEED_MULT_BY_TIER == MEASURED_2026_07_18


def test_on_reads_table(monkeypatch):
    monkeypatch.setattr(C, "flag", lambda n, d=False: True
                        if n == "ENABLE_DRIVE_SPEED_TIER_CORRECTION" else d)
    assert C.speed_mult_for_tier("gold") == 0.96
    assert C.speed_mult_for_tier(None) == 1.0
