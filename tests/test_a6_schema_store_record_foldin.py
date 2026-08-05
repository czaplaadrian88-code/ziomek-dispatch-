"""A-6 — FOLD-IN: store-walidatory rootów delegują kształt rekordu do ``*_record_issue``.

Bramka: engine.a6-schema-validators-fold-in (dług zapisany przy merge G6, due 12.08).

DEFEKT na masterze ``06e4d5c39`` (RED tu, GREEN na kandydacie):
  po merge G5 (walidator per STORE) i G6 (walidator per REKORD) ``identity/schema.py``
  trzyma DWIE kopie tej samej polityki kształtu rekordu — ``validate_kurier_piny_root``
  i ``piny_record_issue`` (analogicznie tiers / courier_names / kurier_full_names /
  kurier_ids) sprawdzają dokładnie te same warunki, każdy własnym ``if``. Dwa źródła
  jednej prawdy rozjeżdżają się cicho: zaostrzenie reguły w warstwie per-rekord
  (verifier ``new_courier_pairing``) NIE zmienia werdyktu ścieżki pisarza
  (``identity.journal`` — zapis generacji i recovery po crashu) i odwrotnie, więc
  rekord odrzucony przez audyt może zostać utrwalony przez transakcję.

KONTRAKT po naprawie:
  * kanonicznym ownerem reguły kształtu POJEDYNCZEGO rekordu jest ``*_record_issue``,
  * store-walidator = iteracja po magazynie + reguły poziomu generacji (typ
    kontenera; dla ``kurier_ids`` dodatkowo kontrakt autoryzacji
    ``validate_courier_ids_store``) + fail-closed ``ValueError``,
  * ⛔ ``validate_courier_ids_store`` (kontrakt autoryzacji ``courier_info``) zostaje
    NIETKNIĘTY — jego zaostrzenie to osobna decyzja ownera,
  * PARYTET: żaden przypadek, który przechodził przed fold-inem, nie zaczyna padać
    (ani odwrotnie) — sekcja (2) to korpus zapisany jako test, zielony PRZED i PO.

Pokrycie tego pliku:
  (1) ORACLE MUTACYJNY — podmiana ``*_record_issue`` MUSI zmieniać werdykt
      store-walidatora w OBIE strony (zaostrzenie i poluzowanie). Na masterze-przed
      te testy są czerwone (store-walidator ma własną kopię reguły).
  (2) PARYTET — korpus poprawnych i WSZYSTKICH klas zepsucia per root.
  (3) RATCHET — store-walidator nie zawiera inline polityki kształtu rekordu
      (powrót ``isdigit`` / ``canonical_numeric_cid`` / literału ``"_meta"`` = RED)
      i każdy root z ``ROOT_VALIDATORS`` woła swój walidator rekordu.
  (4) GRANICE — ``validate_courier_ids_store`` bez zmian semantyki, a
      ``validate_kurier_ids_root`` nadal go woła PONAD delegacją.

Hermetyczny: czysty unit na literałach, zero I/O do ``dispatch_state``.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from dispatch_v2.identity import schema as SCH

# --------------------------------------------------------------------------- #
# Mapa fold-inu: root -> (store-walidator, nazwa walidatora rekordu)
# --------------------------------------------------------------------------- #
FOLDED = {
    "kurier_piny": ("validate_kurier_piny_root", "piny_record_issue"),
    "courier_tiers": ("validate_courier_tiers_root", "tiers_record_issue"),
    "courier_names": ("validate_courier_names_root", "courier_names_record_issue"),
    "kurier_full_names": ("validate_kurier_full_names_root", "full_names_record_issue"),
    "kurier_ids": ("validate_kurier_ids_root", "ids_record_issue"),
}

#: Magazyn ZDROWY per root — po fold-inie musi przejść, a po zaostrzeniu reguły
#: per-rekord musi zacząć padać (oracle mutacyjny M1).
HEALTHY = {
    "kurier_piny": {"1234": "Jan Ko"},
    "courier_tiers": {"900": {"tier": "gold"}, "_meta": {"generated_at": "x"}},
    "courier_names": {"900": "Jan Ko"},
    "kurier_full_names": {"Jan Ko": "Jan Kowalski"},
    "kurier_ids": {"Jan Ko": 900, "Jan Kowalski": "900"},
}

#: Magazyn zepsuty WYŁĄCZNIE regułą per-rekord (nie regułą poziomu generacji) —
#: po poluzowaniu walidatora rekordu musi przejść (oracle mutacyjny M2).
#: Dla ``kurier_ids`` celowo forma, którą odrzuca TYLKO reguła liczbowa
#: (``validate_courier_ids_store`` przepuszcza ``"017"`` — i ma tak zostać).
RECORD_ONLY_BROKEN = {
    "kurier_piny": {"12345": "Jan Ko"},
    "courier_tiers": {"017": {"tier": "gold"}},
    "courier_names": {"017": "Jan Ko"},
    "kurier_full_names": {"Jan Ko": "   "},
    "kurier_ids": {"Jan Ko": "017"},
}


def _store_validator(root):
    return getattr(SCH, FOLDED[root][0])


# --------------------------------------------------------------------------- #
# (1) ORACLE MUTACYJNY — walidator rekordu STERUJE werdyktem store-walidatora
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("root", sorted(FOLDED))
def test_M1_tightening_record_rule_changes_store_verdict(root, monkeypatch):
    """Zaostrzenie reguły per-rekord MUSI odrzucić magazyn ZDROWY.

    RED przed fold-inem: store-walidator ma własną kopię reguły, więc podmiana
    ``*_record_issue`` nie zmienia niczego i zdrowy magazyn dalej przechodzi —
    dokładnie to znaczy „dwie definicje tej samej prawdy".
    """
    monkeypatch.setattr(SCH, FOLDED[root][1], lambda *_a: "mutacja: rekord odrzucony")
    with pytest.raises(ValueError):
        _store_validator(root)(HEALTHY[root])


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_M2_relaxing_record_rule_changes_store_verdict(root, monkeypatch):
    """Poluzowanie reguły per-rekord MUSI przepuścić magazyn zepsuty tą regułą.

    Druga strona tej samej mutacji: dowodzi, że store-walidator NIE trzyma już
    własnej, równoległej kopii warunku (gdyby trzymał, dalej by odrzucał).
    """
    monkeypatch.setattr(SCH, FOLDED[root][1], lambda *_a: None)
    assert _store_validator(root)(RECORD_ONLY_BROKEN[root]) == RECORD_ONLY_BROKEN[root]


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_M3_record_validator_is_called_for_every_record(root, monkeypatch):
    """Delegacja obejmuje KAŻDY rekord magazynu, nie tylko pierwszy."""
    seen = []
    real = getattr(SCH, FOLDED[root][1])

    def spy(key, value):
        seen.append(key)
        return real(key, value)

    monkeypatch.setattr(SCH, FOLDED[root][1], spy)
    store = HEALTHY[root]
    _store_validator(root)(store)
    assert seen == list(store), f"{root}: walidator rekordu nie zobaczył wszystkich wierszy"


# --------------------------------------------------------------------------- #
# (2) PARYTET — korpus werdyktów, identyczny PRZED i PO fold-inie
# --------------------------------------------------------------------------- #
# ``True`` = magazyn zdrowy (walidator zwraca store), ``False`` = ``ValueError``.
# Korpus powstał na masterze ``06e4d5c39`` (przed zmianą) i jest tam ZIELONY —
# jest więc dowodem parytetu, a nie opisem nowego zachowania.
CORPUS = [
    # --- poziom generacji: kontener ---
    ("kurier_piny", "nie-mapa-lista", [], False),
    ("kurier_piny", "nie-mapa-str", "1234", False),
    ("courier_tiers", "nie-mapa-lista", [("900", {})], False),
    ("courier_names", "nie-mapa-none", None, False),
    ("kurier_full_names", "nie-mapa-int", 7, False),
    ("kurier_ids", "nie-mapa-lista-par", [("Jan Ko", 900)], False),
    # --- magazyn pusty = zdrowy (onboarding pierwszego kuriera) ---
    ("kurier_piny", "pusty", {}, True),
    ("courier_tiers", "pusty", {}, True),
    ("courier_names", "pusty", {}, True),
    ("kurier_full_names", "pusty", {}, True),
    ("kurier_ids", "pusty", {}, True),
    # --- kurier_piny: klucz PIN ---
    ("kurier_piny", "ok", {"1234": "Jan Ko"}, True),
    ("kurier_piny", "ok-wiele", {"1234": "Jan Ko", "9999": "Piotr No"}, True),
    ("kurier_piny", "pin-3-cyfry", {"123": "Jan Ko"}, False),
    ("kurier_piny", "pin-5-cyfr", {"12345": "Jan Ko"}, False),
    ("kurier_piny", "pin-litera", {"12a4": "Jan Ko"}, False),
    ("kurier_piny", "pin-spacja", {"12 4": "Jan Ko"}, False),
    ("kurier_piny", "pin-unicode-cyfry", {"١٢٣٤": "Jan Ko"}, False),
    ("kurier_piny", "pin-int", {1234: "Jan Ko"}, False),
    ("kurier_piny", "pin-pusty", {"": "Jan Ko"}, False),
    ("kurier_piny", "alias-pusty", {"1234": ""}, False),
    ("kurier_piny", "alias-spacje", {"1234": "   "}, False),
    ("kurier_piny", "alias-nie-str", {"1234": 900}, False),
    ("kurier_piny", "alias-none", {"1234": None}, False),
    ("kurier_piny", "alias-lista", {"1234": ["Jan Ko"]}, False),
    ("kurier_piny", "drugi-rekord-zepsuty", {"1234": "Jan Ko", "999": "Piotr No"}, False),
    # --- courier_tiers: klucz CID + wiersz + _meta ---
    ("courier_tiers", "ok", {"900": {"tier": "gold"}}, True),
    ("courier_tiers", "ok-meta", {"_meta": {"x": 1}, "900": {"tier": "gold"}}, True),
    ("courier_tiers", "ok-wiersz-pusty-dict", {"900": {}}, True),
    ("courier_tiers", "meta-nie-obiekt", {"_meta": ["x"]}, False),
    ("courier_tiers", "meta-none", {"_meta": None}, False),
    ("courier_tiers", "cid-lead-zero", {"017": {}}, False),
    ("courier_tiers", "cid-spacje", {" 900 ": {}}, False),
    ("courier_tiers", "cid-ujemny", {-5: {}}, False),
    ("courier_tiers", "cid-zero", {"0": {}}, False),
    ("courier_tiers", "cid-bool", {True: {}}, False),
    ("courier_tiers", "cid-float", {1.5: {}}, False),
    ("courier_tiers", "cid-podkreslnik", {"9_0_0": {}}, False),
    ("courier_tiers", "cid-pusty", {"": {}}, False),
    ("courier_tiers", "wiersz-nie-obiekt", {"900": "gold"}, False),
    ("courier_tiers", "wiersz-lista", {"900": [1]}, False),
    ("courier_tiers", "wiersz-none", {"900": None}, False),
    # --- courier_names ---
    ("courier_names", "ok", {"900": "Jan Ko"}, True),
    ("courier_names", "cid-lead-zero", {"017": "Jan Ko"}, False),
    ("courier_names", "cid-spacje", {" 900 ": "Jan Ko"}, False),
    ("courier_names", "cid-ujemny", {-5: "Jan Ko"}, False),
    ("courier_names", "cid-zero", {0: "Jan Ko"}, False),
    ("courier_names", "cid-bool", {True: "Jan Ko"}, False),
    ("courier_names", "cid-float", {1.5: "Jan Ko"}, False),
    ("courier_names", "nazwa-pusta", {"900": ""}, False),
    ("courier_names", "nazwa-spacje", {"900": "   "}, False),
    ("courier_names", "nazwa-nie-str", {"900": 17}, False),
    ("courier_names", "nazwa-none", {"900": None}, False),
    ("courier_names", "meta-nie-jest-wyjatkiem", {"_meta": {"x": 1}}, False),
    # --- kurier_full_names ---
    ("kurier_full_names", "ok", {"Jan Ko": "Jan Kowalski"}, True),
    ("kurier_full_names", "alias-pusty", {"": "Jan Kowalski"}, False),
    ("kurier_full_names", "alias-spacje", {"   ": "Jan Kowalski"}, False),
    ("kurier_full_names", "alias-nie-str", {900: "Jan Kowalski"}, False),
    ("kurier_full_names", "alias-bool", {True: "Jan Kowalski"}, False),
    ("kurier_full_names", "imie-puste", {"Jan Ko": ""}, False),
    ("kurier_full_names", "imie-spacje", {"Jan Ko": "  "}, False),
    ("kurier_full_names", "imie-nie-str", {"Jan Ko": 900}, False),
    ("kurier_full_names", "imie-none", {"Jan Ko": None}, False),
    ("kurier_full_names", "imie-lista", {"Jan Ko": ["Jan"]}, False),
    # --- kurier_ids (delegacja + kontrakt autoryzacji PONAD nią) ---
    ("kurier_ids", "ok-str", {"Jan Ko": "900"}, True),
    ("kurier_ids", "ok-int", {"Jan Ko": 900}, True),
    ("kurier_ids", "ok-mieszane", {"Jan Ko": 900, "Jan Kowalski": "900"}, True),
    ("kurier_ids", "nazwa-pusta", {"": 900}, False),
    ("kurier_ids", "nazwa-spacje", {"   ": 900}, False),
    ("kurier_ids", "nazwa-nie-str", {900: 900}, False),
    ("kurier_ids", "cid-lead-zero", {"Jan Ko": "017"}, False),
    ("kurier_ids", "cid-spacje", {"Jan Ko": " 900 "}, False),
    ("kurier_ids", "cid-ujemny", {"Jan Ko": -5}, False),
    ("kurier_ids", "cid-zero", {"Jan Ko": 0}, False),
    ("kurier_ids", "cid-plus", {"Jan Ko": "+17"}, False),
    ("kurier_ids", "cid-podkreslnik", {"Jan Ko": "9_0_0"}, False),
    ("kurier_ids", "cid-bool", {"Jan Ko": True}, False),
    ("kurier_ids", "cid-float", {"Jan Ko": 1.5}, False),
    ("kurier_ids", "cid-none", {"Jan Ko": None}, False),
    ("kurier_ids", "cid-lista", {"Jan Ko": [900]}, False),
    ("kurier_ids", "cid-pusty-str", {"Jan Ko": ""}, False),
    ("kurier_ids", "drugi-rekord-zepsuty", {"Jan Ko": 900, "Zly Ktos": "017"}, False),
]


@pytest.mark.parametrize(
    "root,store,healthy",
    [pytest.param(r, s, ok, id=f"{r}-{label}") for r, label, s, ok in CORPUS],
)
def test_parity_store_validator_verdicts(root, store, healthy):
    """Werdykt store-walidatora na korpusie — identyczny przed i po fold-inie."""
    validator = _store_validator(root)
    if healthy:
        assert validator(store) is store
    else:
        with pytest.raises(ValueError):
            validator(store)


@pytest.mark.parametrize("root,label,store,healthy",
                         [pytest.param(*c, id=f"{c[0]}-{c[1]}") for c in CORPUS])
def test_parity_record_validator_agrees_with_store_validator(root, label, store, healthy):
    """Ten sam korpus przez ``validate_root`` + zgodność z warstwą per-rekord.

    Dla magazynów będących mapami: store-walidator jest zdrowy dokładnie wtedy,
    gdy KAŻDY rekord jest zdrowy wg ``*_record_issue`` — z jedynym udokumentowanym
    wyjątkiem ``kurier_ids``, gdzie PONAD delegacją stoi kontrakt autoryzacji
    ``validate_courier_ids_store`` (nietykalny w tej bramce).
    """
    issue = getattr(SCH, FOLDED[root][1])
    if not isinstance(store, dict):
        with pytest.raises(ValueError):
            SCH.validate_root(root, store)
        return
    all_records_ok = all(issue(k, v) is None for k, v in store.items())
    if healthy:
        assert all_records_ok, f"{root}-{label}: store zdrowy, a rekord odrzucony"
        assert SCH.validate_root(root, store) is store
    else:
        with pytest.raises(ValueError):
            SCH.validate_root(root, store)


# --------------------------------------------------------------------------- #
# (3) RATCHET — jeden owner reguły kształtu rekordu
# --------------------------------------------------------------------------- #
#: Nazwy/literały, które w store-walidatorze oznaczają POWRÓT drugiej kopii
#: polityki kształtu rekordu (formę CID/PIN i wyjątek ``_meta`` trzyma wyłącznie
#: warstwa ``*_record_issue``).
BANNED_IN_STORE_VALIDATOR = ("isdigit", "isascii", "canonical_numeric_cid",
                             "canonical_courier_id", "isinstance", "PIN_LENGTH",
                             "TIERS_META_KEY")


def _code_ast(fn) -> ast.Module:
    """AST funkcji BEZ docstringu — komentarz/dokumentacja opisuje regułę, kod ją
    stanowi; ratchet ma pilnować kodu."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    func = tree.body[0]
    if (func.body and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)
            and isinstance(func.body[0].value.value, str)):
        func.body = func.body[1:]
    return tree


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_ratchet_store_validator_has_no_inline_record_policy(root):
    """RATCHET: store-walidator nie powtarza reguły kształtu rekordu."""
    tree = _code_ast(_store_validator(root))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for banned in BANNED_IN_STORE_VALIDATOR:
        assert banned not in names and banned not in attrs, (
            f"{root}: inline polityka kształtu rekordu ({banned}) wróciła do "
            f"store-walidatora — owner tej reguły to {FOLDED[root][1]}"
        )
    assert SCH.TIERS_META_KEY not in literals, (
        f"{root}: literał '_meta' w store-walidatorze = druga kopia wyjątku _meta"
    )


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_ratchet_store_validator_references_its_record_validator(root):
    """RATCHET: store-walidator wprost odwołuje się do swojego ``*_record_issue``."""
    src = textwrap.dedent(inspect.getsource(_store_validator(root)))
    names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
    assert FOLDED[root][1] in names, (
        f"{root}: store-walidator nie deleguje do {FOLDED[root][1]}"
    )


def test_ratchet_every_root_validator_is_folded_in():
    """RATCHET: każdy root z ``ROOT_VALIDATORS`` ma opisany fold-in.

    Dołożenie szóstego roota bez delegacji do warstwy per-rekord = RED (ten sam
    wzorzec kompletności co ratchet ROOT_VALIDATORS w bramce G5).
    """
    assert set(SCH.ROOT_VALIDATORS) == set(FOLDED)
    for root, (store_name, record_name) in FOLDED.items():
        assert SCH.ROOT_VALIDATORS[root] is getattr(SCH, store_name)
        assert callable(getattr(SCH, record_name))


# --------------------------------------------------------------------------- #
# (4) GRANICE — czego fold-in NIE rusza
# --------------------------------------------------------------------------- #
def test_courier_ids_store_authorization_contract_untouched():
    """``validate_courier_ids_store`` (autoryzacja ``courier_info``) bez zmian:
    dalej luźny ``canonical_courier_id`` — ``"017"`` / ``" 900 "`` / ``-5`` przechodzą.

    Zaostrzenie tego kontraktu = osobna decyzja ownera (poza tą bramką).
    """
    for lax in ({"Jan Ko": "017"}, {"Jan Ko": " 900 "}, {"Jan Ko": -5},
                {"Jan Ko": "9_0_0"}):
        assert SCH.validate_courier_ids_store(lax) is lax
    for broken in ({"": 900}, {"Jan Ko": None}, {"Jan Ko": True}):
        with pytest.raises(ValueError):
            SCH.validate_courier_ids_store(broken)
    with pytest.raises(ValueError):
        SCH.validate_courier_ids_store([("Jan Ko", 900)])


def test_kurier_ids_root_still_calls_authorization_contract(monkeypatch):
    """``validate_kurier_ids_root`` woła kontrakt autoryzacji PONAD delegacją.

    Nawet gdy warstwa per-rekord przepuszcza wszystko, rekord odrzucony przez
    ``validate_courier_ids_store`` (nazwa pusta) dalej wywraca cały root.
    """
    monkeypatch.setattr(SCH, "ids_record_issue", lambda *_a: None)
    with pytest.raises(ValueError):
        SCH.validate_kurier_ids_root({"": 900})
