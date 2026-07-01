"""L0.4 — czerwone-na-start SLOT-y inwariantów z ZIOMEK_INVARIANTS.md (audyt spójności).

Mechanizm RATCHET (`@pytest.mark.xfail(strict=True)`):
  • Inwariant jest DZIŚ łamany u źródła → test CZERWONY → pytest raportuje XFAIL,
    suita kończy exit 0 (baseline zielony, dług NAZWANY nie ukryty).
  • Gdy właściwa fala naprawi źródło → test zacznie PRZECHODZIĆ → XPASS(strict)
    = FAIL → ratchet ZMUSZA autora naprawy do zdjęcia znacznika xfail (i tym samym
    przypięcia inwariantu jako żywego strażnika 🟢 TEST).
Precedens w repo: tests/test_demote_tier_bucket_p4.py:62-68.

KAŻDY slot poniżej został URUCHOMIONY i zweryfikowany jako deterministycznie XFAILED
(nie XPASS, nie zwykły FAIL/ERROR) — patrz raport L0.4.

⚠ Warunki testów NIE opierają się na flags.json (conftest `_isolate_flags_json`
wycina flagi decyzyjne) — stan ustawiany monkeypatchem stałych/funkcji modułów.
Import bez efektów ubocznych: moduły dispatch_v2 importują się czysto (pure).
"""
import ast
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as D
from dispatch_v2 import objm_lexr6 as OL
from dispatch_v2 import plan_manager as PM


class _Cand:
    """Lekki kandydat (wzór z test_demote_tier_bucket_p4._Cand): .courier_id/.score/.metrics."""

    def __init__(self, cid, score, metrics):
        self.courier_id = cid
        self.score = score
        self.metrics = dict(metrics)


# ─────────────────────────────────────────────────────────────────────────────
# SLOT 1 — INV-SRC-EQUAL-TREATMENT (Kontrakt ①, ZIOMEK_INVARIANTS.md l.21)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(
    strict=True,
    reason="INV-SRC-EQUAL-TREATMENT [SLOT L0.4, kontrakt ①/l.21]: pre_shift NIE ma "
    "identycznego traktowania we wszystkich bliźniakach — kanon `_selection_bucket` "
    "(equal-treatment ON) daje bucket 0 (równy, po score), a bliźniak "
    "`tools/reassignment_forward_shadow._SYNTH_POS` (l.64) trzyma 'pre_shift' w koszyku "
    "fikcji-pozycji (=„zgadnięta”, szum powiadomień). Rozjazd łatany >=4x. "
    "Zdejmij xfail po fali unifikacji 8 bliźniaków ① (usunięcie 'pre_shift' z _SYNTH_POS).",
)
def test_inv_src_equal_treatment_pre_shift_twin_parity():
    """Kontrakt ①: „brak GPS / pre_shift = identyczny bucket we WSZYSTKICH 8 bliźniakach".

    Sprawdzamy RELACJĘ SPÓJNOŚCI między kanonem selekcji a bliźniakiem
    reassignment_forward_shadow: pod równym traktowaniem (`_equal_bucket_on` ON) kanon
    klasyfikuje pre_shift jako bucket 0 (konkuruje po score, NIE fikcja). Wierny bliźniak
    MUSI mieć tę samą klasę — pre_shift NIE może siedzieć w zbiorze pozycji-fikcji.
    Dziś siedzi → niespójność → CZERWONY.

    Kruchość: zależy od nazw `dispatch_pipeline._selection_bucket`/`_equal_bucket_on`
    i stałej `reassignment_forward_shadow._SYNTH_POS`. Rename któregokolwiek złamie test
    niewinnie.
    """
    # bliźniak przerzutu — import lokalny (limit blast-radius; moduł importuje się czysto)
    from dispatch_v2.tools import reassignment_forward_shadow as RFS

    pre = _Cand("PRE", 97.0, {"pos_source": "pre_shift", "r6_bag_size": 0})

    # Kanon: równe traktowanie ON (monkeypatch funkcji, NIE flags.json) → pre_shift bucket 0.
    _orig = D._equal_bucket_on
    try:
        D._equal_bucket_on = lambda: True
        canon_treats_equal = (D._selection_bucket(pre) == 0)
    finally:
        D._equal_bucket_on = _orig

    # Bliźniak: pre_shift traktowane RÓWNO ⇔ NIE jest w koszyku fikcji `_SYNTH_POS`.
    twin_treats_equal = ("pre_shift" not in RFS._SYNTH_POS)

    assert canon_treats_equal == twin_treats_equal, (
        "INV-SRC-EQUAL-TREATMENT: kanon i bliźniak przerzutu NIEspójnie traktują "
        f"pre_shift (kanon_równo={canon_treats_equal}, bliźniak_równo={twin_treats_equal}); "
        f"_SYNTH_POS={sorted(RFS._SYNTH_POS)!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SLOT 2 — INV-LIFE-LOADPLAN-PURE (Kontrakt ⑦, ZIOMEK_INVARIANTS.md l.55)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def _isolated_pm(tmp_path, monkeypatch):
    """Izolacja plan_manager na tmp (wzór z test_loadplan_pure_read_2026_06_29.isolated_pm)."""
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    return PM


def _seed_mismatch_plan(pm, cid="999"):
    """Plan z dropoffami {A, B}; bieżący worek = {A} (B wypadł = mismatch z rzeczywistością)."""
    body = {
        "start_pos": {"lat": 53.13, "lng": 23.16, "source": "test",
                      "source_ts": "2026-06-29T09:00:00+00:00"},
        "start_ts": "2026-06-29T09:00:00+00:00",
        "stops": [
            {"order_id": "A", "type": "pickup", "coords": {"lat": 53.13, "lng": 23.16},
             "predicted_at": "2026-06-29T09:05:00+00:00"},
            {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.14, "lng": 23.15},
             "predicted_at": "2026-06-29T09:12:00+00:00"},
            {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.15, "lng": 23.12},
             "predicted_at": "2026-06-29T09:20:00+00:00"},
        ],
        "optimization_method": "incremental",
    }
    pm.save_plan(cid, body)


@pytest.mark.xfail(
    strict=True,
    reason="INV-LIFE-LOADPLAN-PURE [SLOT L0.4, kontrakt ⑦/l.55]: `load_plan` u źródła "
    "NIE jest pure-read — DEFAULT `invalidate_on_mismatch=True` (plan_manager.py:124) "
    "= read-with-side-effect: przy mismatch worka PERSYSTUJE invalidację "
    "(ORDER_DELIVERED_ALL) na dysku (żywy leak: podglądy `_soon_free_probe`/base_sequence "
    "darły plan co tick). Zdejmij xfail po fali L (⑦: load_plan default→pure-read).",
)
def test_inv_life_loadplan_pure_default(_isolated_pm):
    """Kontrakt ⑦: „load_plan = pure-read u źródła (dziś read-with-side-effect)".

    Wołanie load_plan BEZ parametru pure-read (domyślne), na planie z mismatch worka:
    plik NIE może zostać zmutowany (invalidated_at pozostaje None). To ODWROTNOŚĆ
    istniejącego `test_default_is_legacy` (który asertuje że default PERSYSTUJE) — więc
    dziś PADA.

    Kruchość: zależy od domyślnej wartości parametru `invalidate_on_mismatch` i pola
    rekordu `invalidated_at`.
    """
    pm = _isolated_pm
    _seed_mismatch_plan(pm)
    out = pm.load_plan("999", active_bag_oids={"A"})   # BEZ param → DEFAULT
    assert out is None                                 # nadal „nie używaj dla tego worka"
    raw = pm._read_raw()["999"]
    assert raw.get("invalidated_at") is None, (
        "INV-LIFE-LOADPLAN-PURE: domyślny load_plan zmutował plan przy odczycie "
        f"(invalidated_at={raw.get('invalidated_at')!r}, reason={raw.get('invalidation_reason')!r}) "
        "— read-with-side-effect zamiast pure-read"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SLOT 3 — INV-SRC-LEXQUAL (Kontrakt ①, ZIOMEK_INVARIANTS.md l.20)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(
    strict=True,
    reason="INV-SRC-LEXQUAL [SLOT L0.4, kontrakt ①/l.20]: 3 kopie klucza jakości NIE dają "
    "identycznego rankingu. Kanon `objm_lexr6.lex_qual` przy ENABLE_POST_SHIFT_OVERRUN_PENALTY "
    "prependuje WIODĄCY `post_shift_overrun_penalty` (4-krotka), a ZAMROŻONA kopia inline w "
    "`dispatch_pipeline._objm_lexr6_shadow` (l.1135) trzyma 3-krotkę bez tego termu → wybiera "
    "innego zwycięzcę grupy. Zdejmij xfail po unifikacji objm-lexr6 "
    "(repięcie _objm_lexr6_shadow na objm_lexr6.lex_qual, bramka 03.07).",
)
def test_inv_src_lexqual_shadow_vs_canon_ranking(monkeypatch):
    """Kontrakt ①: „3 kopie lex_qual dają identyczny ranking (parytet)".

    Korpus 2 kandydatów tej samej grupy (tier 0 × bucket 0 = informed/gps), gdzie
    `post_shift_overrun_penalty` jest termem decydującym. Przy fladze ON:
      • KANON (objm_lexr6.lex_qual, 4-krotka post-shift-first) → zwycięzca = A (post 0 < 50)
      • ZAMROŻONY CIEŃ (_objm_lexr6_shadow, inline 3-krotka R6-first) → zwycięzca = B (r6 5 < 10)
    Porównujemy RANKING w punkcie decyzji (głowa: kogo wybiera każda kopia). Cień eksponuje
    tylko zwycięzcę (`objm_lexr6_best_cid`). Dziś A≠B → CZERWONY.

    (Kopia #3 — `_best_effort_objm_pick` — używa kanonu `objm_lexr6.lex_qual`, więc jest
    zgodna z kanonem; łamie parytet WYŁĄCZNIE zamrożony cień.)

    Kruchość: zależy od nazw `_objm_lexr6_shadow`/metryki `objm_lexr6_best_cid` i od tego,
    że cień pozostaje zamrożony bez `post_shift_overrun_penalty`.
    """
    # ENABLE_POST_SHIFT_OVERRUN_PENALTY ON — monkeypatch decision_flag (NIE flags.json).
    monkeypatch.setattr(
        C, "decision_flag",
        lambda name: name == "ENABLE_POST_SHIFT_OVERRUN_PENALTY",
    )

    base = {"pos_source": "gps", "r6_bag_size": 1,
            "late_pickup_committed_max": 0.0, "new_pickup_late_min": 0.0}
    cA = _Cand("A", 100.0, {**base, "objm_r6_breach_max_min": 10.0,
                            "post_shift_overrun_penalty": 0.0})   # kończy w oknie zmiany
    cB = _Cand("B", 90.0, {**base, "objm_r6_breach_max_min": 5.0,
                           "post_shift_overrun_penalty": 50.0})   # kończy DŁUGO po zmianie

    # sanity: obaj w tej samej grupie (tier, bucket) co score-zwycięzca cA
    assert D._late_pickup_tier(cA) == D._late_pickup_tier(cB) == 0
    assert D._selection_bucket(cA) == D._selection_bucket(cB) == 0

    # KANON (kopia #1): głowa rankingu leksykograficznego
    canon_best = min([cA, cB], key=OL.lex_qual).courier_id
    assert len(OL.lex_qual(cA)) == 4, "sanity: kanon = 4-krotka przy post-shift ON"

    # CIEŃ (kopia #2): uruchom realny selektor, odczytaj jego wybór z metryk
    top, feasible = [cA, cB], [cA, cB]
    D._objm_lexr6_shadow(top, feasible, order_id="L04_LEXQUAL")
    shadow_best = cA.metrics.get("objm_lexr6_best_cid")
    assert cA.metrics.get("objm_lexr6_group_size") == 2, "sanity: cień widzi całą grupę"

    assert shadow_best == canon_best, (
        "INV-SRC-LEXQUAL: ranking rozjechany — kanon(objm_lexr6.lex_qual) wybiera "
        f"{canon_best!r}, zamrożony cień(_objm_lexr6_shadow) wybiera {shadow_best!r} "
        "(cień bez wiodącego post_shift_overrun_penalty)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SLOT 4 — INV-COH-R-DECLARED (Kontrakt ⑧, ZIOMEK_INVARIANTS.md l.61)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(
    strict=True,
    reason="INV-COH-R-DECLARED [SLOT L0.4, kontrakt ⑧/l.61]: brak tripwire "
    "`czas_kuriera ≥ czas_odbioru_timestamp` (R-DECLARED-TIME). Chokepoint committed-time "
    "`dispatch_pipeline._bag_dict_to_order_in_bag_raw` (l.3056) liczy pickup_time z "
    "czas_kuriera_warsaw/czas_odbioru_timestamp, ale NIC nie wykrywa/loguje gdy ck < odbiór "
    "(grep strażników = 0). Brak siostrzanego strażnika `_assert_r_declared_time` obok "
    "istniejącego `_assert_feasibility_first`. Zdejmij xfail po fali L (⑧: tripwire R-DECLARED).",
)
def test_inv_coh_r_declared_tripwire_exists():
    """Kontrakt ⑧: „tripwire czas_kuriera ≥ czas_odbioru_timestamp (R-DECLARED-TIME) zawsze".

    Repo zna konwencję strażnika-inwariantu `_assert_<inwariant>` (dziś jedyny:
    `_assert_feasibility_first`). R-DECLARED-TIME nie ma swojego strażnika — żaden
    chokepoint committed-time nie flaguje ck < czas_odbioru. Test wymaga istnienia
    strażnika I (gdy powstanie) jego zadziałania na naruszającym rekordzie. Dziś strażnik
    NIE istnieje → CZERWONY.

    Znalezisko (recon L0.4): sprawdzone — ŻADEN mechanizm dziś tego nie łapie (grep po
    dispatch_pipeline/panel_client/state_machine/common = 0 walidatorów), więc to prawdziwy
    SLOT (nie XPASS-niespodzianka). late_pickup_committed_* mierzy ODWROTNY kierunek
    (kurier spóźniony na committed odbiór), nie ck < odbiór.

    Kruchość: XPASS-flip wymaga, by fala R-DECLARED nazwała strażnik `_assert_r_declared_time`
    (konwencja `_assert_feasibility_first`) albo zaktualizowała ten test. To świadomy,
    nazwany dług — strażnik NIE istnieje pod żadną oczywistą nazwą dziś.
    """
    guard = getattr(D, "_assert_r_declared_time", None)
    assert callable(guard), (
        "INV-COH-R-DECLARED: brak tripwire R-DECLARED-TIME "
        "(oczekiwany siostrzany strażnik dispatch_pipeline._assert_r_declared_time "
        "obok _assert_feasibility_first) — ck < czas_odbioru przechodzi niewykryte"
    )
    # Kontrakt behawioralny na przyszłość: gdy strażnik powstanie, MUSI oflagować
    # naruszenie (czas_kuriera wcześniejszy niż czas_odbioru_timestamp). Rekord niesie
    # oba pola; strażnik powinien ustawić marker naruszenia w metrykach.
    rec = {
        "order_id": "L04RD",
        "czas_kuriera_warsaw": "2026-07-01T11:00:00",       # deklarowany dojazd 11:00
        "czas_odbioru_timestamp": "2026-07-01T11:30:00",    # odbiór gotowy 11:30 (ck < odbiór)
        "metrics": {},
    }
    guard(rec)  # nie może rzucić
    assert rec["metrics"].get("r_declared_violation") is True, (
        "INV-COH-R-DECLARED: strażnik istnieje, ale NIE oflagował ck<odbiór"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SLOT 5 — INV-LAYER-HARD-BEFORE-SOFT (pełny) (Kontrakt ②, ZIOMEK_INVARIANTS.md l.26)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(
    strict=True,
    reason="INV-LAYER-HARD-BEFORE-SOFT [SLOT L0.4, kontrakt ②/l.26]: `_assert_feasibility_first` "
    "istnieje TYLKO na 1 call-site (dispatch_pipeline.py:5995), PRZED mutacją selekcji "
    "FEAS_CARRY_READMIT (l.6323-6358), która promuje kandydata verdict=NO na top[0] i ręcznie "
    "ustawia verdict→MAYBE. Brak re-assertu na EMIT PO readmit (wzorzec #10) → SOFT może obejść "
    "HARD bez złapania. Zdejmij xfail po fali L (re-assert _assert_feasibility_first na EMIT po readmit).",
)
def test_inv_layer_hard_before_soft_reassert_after_readmit():
    """Kontrakt ②: `_assert_feasibility_first` musi być RE-ASSERTOWANY na EMIT po mutacjach
    FEAS_CARRY_READMIT (dziś 1 call-site przed readmit → strażnik globalny brak).

    Test STRUKTURALNY (AST) — parsuje ŻYWE źródło dispatch_pipeline.py, znajduje wszystkie
    call-site'y `_assert_feasibility_first` i linię bramki `ENABLE_FEAS_CARRY_READMIT`
    (ostatnia mutacja selekcji). Wymaga co najmniej jednego wywołania strażnika PO tej mutacji.
    Dziś jedyne wołanie (l.5995) jest PRZED readmit (l.~6323) → CZERWONY.

    Kruchość: test strukturalny — zależy od nazwy funkcji `_assert_feasibility_first` i
    literału flagi `ENABLE_FEAS_CARRY_READMIT`. Rename któregokolwiek złamie test niewinnie.
    """
    src_path = D.__file__
    with open(src_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    assert_call_lines = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_assert_feasibility_first"
    ]
    readmit_flag_lines = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == "ENABLE_FEAS_CARRY_READMIT"
    ]

    # sanity: struktura, na której opiera się inwariant, nadal istnieje
    assert assert_call_lines, "sanity: brak call-site _assert_feasibility_first (rename?)"
    assert readmit_flag_lines, "sanity: brak bramki ENABLE_FEAS_CARRY_READMIT (rename?)"
    readmit_line = min(readmit_flag_lines)

    reassert_after = [ln for ln in assert_call_lines if ln > readmit_line]
    assert reassert_after, (
        "INV-LAYER-HARD-BEFORE-SOFT: brak re-assertu _assert_feasibility_first PO mutacji "
        f"FEAS_CARRY_READMIT (call-sites={assert_call_lines}, readmit@{readmit_line}); "
        "promocja verdict=NO→MAYBE na top[0] nie jest re-strzeżona na EMIT"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-rx"]))
