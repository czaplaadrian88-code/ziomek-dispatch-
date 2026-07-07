"""T2.4 inkrement 6 — efekty trybu S3 (kanon-krytyczne, advisory Tura 2).

Testuje pure logikę efektów: R6 cap wg trybu (S3→40, relaks TYLKO w S3), R27 okno,
priority-shed. KANON: jedzenie NIGDY >40 (twardy sufit niezależny od wstrzykniętego
alarm_cap). Kanon WSTRZYKIWANY → brak duplikatu stałej (A1 ratchet czysty).
"""
from __future__ import annotations

from dispatch_v2 import mode_layer as M


def test_r6_cap_relax_only_in_s3():
    base = 35.0  # kanon C.BAG_TIME_HARD_MAX_MIN (wstrzyknięty)
    assert M.mode_r6_cap_min(M.S1, base) == 35.0
    assert M.mode_r6_cap_min(M.S2, base) == 35.0      # S2 NIE relaksuje R6
    assert M.mode_r6_cap_min(M.S3, base, 40.0) == 40.0  # S3 = alarm 40


def test_food_never_over_40_hard_ceiling():
    # nawet gdy silnik wstrzyknie bledny alarm_cap>40, sufit trzyma 40 (jedzenie NIGDY>40)
    assert M.mode_r6_cap_min(M.S3, 35.0, 45.0) == 40.0
    assert M.mode_r6_cap_min(M.S3, 35.0, 99.0) == 40.0
    assert M.mode_r6_cap_min(M.S1, 50.0) == 40.0       # nawet base>40 przycięte


def test_r27_window_relax_only_s3():
    assert M.mode_r27_window_min(M.S1, 5.0) == 5.0
    assert M.mode_r27_window_min(M.S2, 5.0) == 5.0
    assert M.mode_r27_window_min(M.S3, 5.0, 10.0) == 10.0


def test_priority_shed_protects_czasowka_and_oldest():
    orders = [
        {"oid": "cz", "is_czasowka": True, "age_min": 1},
        {"oid": "old1", "is_czasowka": False, "age_min": 90},
        {"oid": "old2", "is_czasowka": False, "age_min": 80},
        {"oid": "new", "is_czasowka": False, "age_min": 5},
    ]
    prot = M.priority_shed(orders, M.S3, protect_oldest_n=2)
    assert "cz" in prot                    # czasówka ZAWSZE chroniona
    assert "old1" in prot and "old2" in prot   # 2 najstarsze
    assert "new" not in prot               # świeże niechronione (może defer/best-effort)


def test_priority_shed_non_s3_protects_all():
    orders = [{"oid": "a", "is_czasowka": False, "age_min": 5},
              {"oid": "b", "is_czasowka": False, "age_min": 3}]
    assert M.priority_shed(orders, M.S1) == {"a", "b"}   # poza S3 brak shed
    assert M.priority_shed(orders, M.S2) == {"a", "b"}
    assert M.priority_shed([], M.S3) == set()
