"""B-PRECEDENCE-CARRIED (audyt 2026-06-24, spec odporności §6.B): JEDEN snapshot
CAŁEJ hierarchii kolejności trasy w `plan_recheck._apply_canon_order_invariants`
(choke kanonu). Fragmenty behawioralne istnieją osobno (`test_carried_first_relax`,
`test_lex_committed_window`, `test_canon_order_invariants`) — TU pinujemy PEŁNĄ
PRECEDENCJĘ warstw, by przypadkowa inwersja (np. okno-committed PONAD carried-first,
albo min-jazda PONAD niesione) padła testem.

Hierarchia (każda warstwa działa na wyniku poprzedniej):
  L1 carried-first front        (BEZWARUNKOWE — szkielet: niesione dropoffy na przód)
  L2 committed-pickup sort       (BEZWARUNKOWE — odbiory wg czas_kuriera rosnąco)
  L3 no-return                   (ENABLE_NO_RETURN_TO_DEPARTED_PICKUP)
  L4 carried-first relax         (ENABLE_CARRIED_FIRST_RELAX — „odbierz po drodze")
  L5 lex committed-window        (ENABLE_LEX_COMMITTED_WINDOW — P-1, anchored NA relax)
  L6 min-drive non-carried       (ENABLE_NONCARRIED_DROPOFF_REORDER — Fix M, ostatnie)

Inwersje, które to łapie: L5 nad L4 (okno regresowałoby carried), L6 nad L1
(min-jazda przestawiłaby niesione), zgubienie warstwy, dodanie nowej bez miejsca
w hierarchii.
"""
import inspect

import dispatch_v2.plan_recheck as PR

_CANON_SRC = inspect.getsource(PR._apply_canon_order_invariants)

# Uporządkowana hierarchia: (id, marker-w-źródle, flaga|None). Kolejność listy =
# OCZEKIWANA precedencja (rosnące indeksy w źródle choke).
PRECEDENCE = [
    ("L1_carried_first_front",   "seq = front + rest",                  None),
    ("L2_committed_pickup_sort", "sorted(pickup_steps",                 None),
    ("L3_no_return",             "_detect_departed_pickup_revisit",     "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP"),
    ("L4_carried_first_relax",   "_relax_carried_first(seq",            "ENABLE_CARRIED_FIRST_RELAX"),
    ("L5_lex_committed_window",  "_lex_committed_window_reorder(seq",   "ENABLE_LEX_COMMITTED_WINDOW"),
    ("L6_min_drive_noncarried",  "_reorder_noncarried_min_drive(seq",   "ENABLE_NONCARRIED_DROPOFF_REORDER"),
]


def _marker_index(marker):
    lines = _CANON_SRC.splitlines()
    for i, ln in enumerate(lines):
        if marker in ln:
            return i
    return -1


def test_all_layers_present():
    """Każda warstwa hierarchii istnieje w choke (zgubienie warstwy → fail)."""
    missing = [gid for gid, marker, _ in PRECEDENCE if _marker_index(marker) < 0]
    assert not missing, (
        f"warstwy precedencji ZNIKNĘŁY z _apply_canon_order_invariants: {missing} "
        f"— jeśli przeniesione/przemianowane, zaktualizuj PRECEDENCE świadomie")


def test_layer_order_is_monotonic():
    """PEŁNA precedencja: warstwy w DOKŁADNIE tej kolejności w źródle choke.
    Inwersja (np. lex-window przed relax, min-drive przed carried-first) → fail."""
    idxs = [(gid, _marker_index(marker)) for gid, marker, _ in PRECEDENCE]
    order = [gid for gid, _ in idxs]
    by_pos = [gid for gid, _ in sorted(idxs, key=lambda t: t[1])]
    assert order == by_pos, (
        f"INWERSJA hierarchii kolejności trasy.\n"
        f"oczekiwane: {order}\nw kodzie:   {by_pos}\n"
        f"carried-first MUSI poprzedzać relax→lex-window→min-drive "
        f"(P-1 anchored na relax; min-jazda tylko non-carried na końcu)")


def test_gated_layers_reference_their_flag():
    """L3–L6 są flag-gated — flaga MUSI być w bloku warstwy (okno wokół markera;
    guard `if FLAG:` bywa tuż NAD wywołaniem [relax/min-drive] albo tuż POD
    [no-return/lex, gdzie detekcja jest pierwsza]). Inaczej warstwa stałaby się
    bezwarunkowa (utrata kill-switcha). Okno ciaśniejsze niż odstęp warstw (~12
    linii) → brak cross-bleedu między różnymi flagami."""
    lines = _CANON_SRC.splitlines()
    for gid, marker, flag in PRECEDENCE:
        if flag is None:
            continue
        mi = _marker_index(marker)
        window = "\n".join(lines[max(0, mi - 4):mi + 7])
        assert flag in window, (
            f"{gid}: flaga {flag} nie występuje w bloku warstwy "
            f"(±okno wokół '{marker}') — warstwa straciła kill-switch")


def test_unconditional_base_precedes_gated_reorders():
    """L1+L2 (carried-first + committed-sort) = BEZWARUNKOWY szkielet — MUSZĄ być
    PRZED pierwszą flag-gated warstwą reorderu. Inaczej szkielet stałby się
    opcjonalny (relax/okno mogłyby zadziałać zanim niesione trafią na przód)."""
    base_max = max(_marker_index(PRECEDENCE[0][1]), _marker_index(PRECEDENCE[1][1]))
    first_gated = min(
        _marker_index(marker) for _, marker, flag in PRECEDENCE if flag is not None)
    assert base_max < first_gated, (
        "bezwarunkowy szkielet (carried-first front + committed-sort) NIE jest "
        "przed flag-gated reorderami — szkielet stał się opcjonalny")


def test_choke_applied_on_both_build_and_retime():
    """`_apply_canon_order_invariants` to JEDYNY choke — wołany i przy BUDOWIE
    (`_gen_one_bag_plan`) i przy RE-CZASOWANIU (`_retime_one_bag_plan`). Żadna
    ścieżka zapisu planu nie omija hierarchii (symetria jak P-5 recanon-on-write)."""
    for fn_name in ("_gen_one_bag_plan", "_retime_one_bag_plan"):
        src = inspect.getsource(getattr(PR, fn_name))
        assert "_apply_canon_order_invariants(" in src, (
            f"{fn_name} NIE woła _apply_canon_order_invariants — ścieżka omija "
            f"hierarchię precedencji (trasa bez kanonu)")
