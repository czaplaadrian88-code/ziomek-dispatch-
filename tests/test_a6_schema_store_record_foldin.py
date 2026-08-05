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
  (3) RATCHET STRUKTURALNY — ciało store-walidatora (i wspólnej instalacji, przez
      którą on przechodzi) MUSI mieć DOKŁADNIE kształt delegacji; każda dodatkowa
      instrukcja lub wywołanie = RED. Plus: każdy root z ``ROOT_VALIDATORS`` ma
      opisany fold-in.
  (4) GRANICE — ``validate_courier_ids_store`` bez zmian semantyki, a
      ``validate_kurier_ids_root`` nadal go woła PONAD delegacją.
  (5) ORACLE RÓWNOWAŻNOŚCI — na GENEROWANYM korpusie: werdykt store-walidatora
      ⟺ werdykt warstwy per-rekord (poza regułami poziomu generacji). Łapie
      dodatkową restrykcję niezależnie od tego, w którym module ona mieszka.

Sekcje (3) i (5) powstały w iter2, po blind-review sesji 297: pierwotny ratchet
był czarną listą NAZW w ciele funkcji, a oracle M1/M2 był przywiązany do dwóch
stałych magazynów — recenzent zmierzył, że jeden helper module-level (w tym samym
albo w innym module) przywraca drugą kopię polityki przy 192 zielonych testach.
Szczegóły i dowody: ``RAPORT_ITER2.md``.

Hermetyczny: czysty unit na literałach, zero I/O do ``dispatch_state``.
"""
from __future__ import annotations

import ast
import copy
import inspect
import random
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
# (3) RATCHET STRUKTURALNY — ciało store-walidatora JEST delegacją, nic więcej
# --------------------------------------------------------------------------- #
# Ten ratchet był wcześniej CZARNĄ LISTĄ NAZW zakazanych w ciele store-walidatora
# (``isdigit`` / ``canonical_numeric_cid`` / ``PIN_LENGTH`` / …). Blind-review
# sesji 297 zmierzył, że taką listę oślepia się JEDNYM prywatnym helperem
# module-level — w tym samym module albo w innym: nazwa spoza listy przechodzi,
# druga kopia polityki kształtu wraca, a cały plik bramki zostaje zielony (192
# passed przy żywym duplikacie). Czarna lista jest wyliczeniowa, więc obiecuje
# więcej, niż egzekwuje.
#
# Dlatego ratchet nie mówi już, czego BYĆ NIE MOŻE — stwierdza STRUKTURALNIE, co
# ciało MUSI być: dokładnie delegacja do ``_validate_records`` z walidatorem
# rekordu tego roota (dla ``kurier_ids`` poprzedzona WYŁĄCZNIE kontraktem
# autoryzacji). Porównujemy kształt AST, więc każda dodatkowa instrukcja i każde
# dodatkowe wywołanie — także wywołanie helpera, obojętne w którym module on
# mieszka — zmienia kształt i jest RED. Oczekiwany kształt jest WYPROWADZANY
# z ``FOLDED``, nie wpisany ręcznie per root: dołożenie roota nie da się
# „przepchnąć" przez zapomnienie wpisu.
def _expected_store_body_src(root: str) -> str:
    delegation = f'return _validate_records("{root}", store, {FOLDED[root][1]})'
    if root == "kurier_ids":
        # kontrakt autoryzacji ``courier_info`` stoi PONAD delegacją (sekcja 4)
        return f'validate_courier_ids_store(_as_mapping(store, "{root}"))\n{delegation}'
    return delegation


#: Wspólna instalacja, przez którą przechodzi KAŻDY store-walidator. To jedyne
#: pozostałe węzły ich grafu wywołań (poza ``*_record_issue`` — kanonicznym
#: ownerem reguły — i nietykalnym ``validate_courier_ids_store``), więc polityka
#: kształtu rekordu przeniesiona „w dół" tutaj też musi być RED. Kształt pinujemy
#: tak samo strukturalnie: typ kontenera + iteracja + przekład „rekord zepsuty"
#: na fail-closed ``ValueError``, zero reguł kształtu.
SHARED_PLUMBING = {
    "_as_mapping": (
        'if not isinstance(store, dict):\n'
        '    raise ValueError(f"{root} nie jest mapą")\n'
        'return store'
    ),
    "_validate_records": (
        'for key, value in _as_mapping(store, root).items():\n'
        '    issue = record_issue(key, value)\n'
        '    if issue is not None:\n'
        '        raise ValueError(f"{root}: {issue}")\n'
        'return store'
    ),
}


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


def _body_shape(stmts) -> list:
    """Kształt ciała: zrzut AST instrukcji bez pozycji w pliku (formatowanie,
    wcięcia i numery linii nie mają znaczenia — struktura ma)."""
    return [ast.dump(node) for node in stmts]


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_ratchet_store_validator_body_is_exactly_delegation(root):
    """RATCHET (a): ciało store-walidatora = DOKŁADNIE delegacja do warstwy rekordu.

    Nie „nie zawiera zakazanych nazw", tylko „jest dokładnie tym kształtem" —
    ratchetu nie da się oślepić helperem, bo helper trzeba z tego ciała wywołać,
    a to już jest inny kształt.
    """
    actual = _body_shape(_code_ast(_store_validator(root)).body[0].body)
    expected = _body_shape(ast.parse(_expected_store_body_src(root)).body)
    assert actual == expected, (
        f"{root}: ciało store-walidatora przestało być czystą delegacją do "
        f"{FOLDED[root][1]} — owner reguły kształtu rekordu jest JEDEN.\n"
        f"oczekiwany kształt:\n    {_expected_store_body_src(root)}"
    )


@pytest.mark.parametrize("helper", sorted(SHARED_PLUMBING))
def test_ratchet_shared_plumbing_body_is_exactly_iteration(helper):
    """RATCHET (a2): wspólna instalacja nie przemyca reguły kształtu rekordu.

    Bez tego duplikat polityki dałoby się schować o piętro niżej — w kodzie, przez
    który i tak przechodzi każdy store-walidator.
    """
    actual = _body_shape(_code_ast(getattr(SCH, helper)).body[0].body)
    expected = _body_shape(ast.parse(SHARED_PLUMBING[helper]).body)
    assert actual == expected, (
        f"{helper}: wspólna instalacja store-walidatorów zmieniła kształt — "
        f"reguła kształtu rekordu należy WYŁĄCZNIE do warstwy *_record_issue.\n"
        f"oczekiwany kształt:\n{SHARED_PLUMBING[helper]}"
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


# --------------------------------------------------------------------------- #
# (5) ORACLE RÓWNOWAŻNOŚCI — własnościowy, na GENEROWANYM korpusie
# --------------------------------------------------------------------------- #
# Druga noga naprawy po blind-review 297. Oracle mutacyjny M1/M2 jest przywiązany
# do DWÓCH STAŁYCH magazynów na root (``HEALTHY`` / ``RECORD_ONLY_BROKEN``), więc
# duplikat polityki, którego dodatkowa restrykcja leży poza tymi dwoma wejściami,
# nie rusza ani M1, ani M2 (zmierzone: helper odrzucający PIN-y z zerem wiodącym
# przechodził przy 192 zielonych testach). Tu wyrażamy własność OGÓLNĄ, prawdziwą
# dla każdego wejścia z korpusu i niezależną od tego, gdzie kod mieszka:
#
#   store_validator(store) rzuca ValueError
#     ⟺  store nie jest mapą                                    [poziom generacji]
#     ∨  root == kurier_ids ∧ validate_courier_ids_store rzuca  [granica, sekcja 4]
#     ∨  ∃ (k, v) ∈ store: <root>_record_issue(k, v) is not None
#
# Prawa strona wymienia JEDYNE dozwolone reguły poziomu generacji. Każda inna
# restrykcja — inline, w helperze tego modułu, w innym module, we wspólnej
# instalacji — łamie równoważność na korpusie i jest RED, bez wyliczania nazw.
_ORACLE_SEED = 20260805  # korpus deterministyczny: ta sama lista przy każdym biegu
_ORACLE_MULTI_RECORD_CASES = 300

#: Klucze: aliasy/nazwy, CID-y w formach kanonicznych i granicznych, PIN-y (także
#: z zerem wiodącym — forma, na której polegał repro recenzenta), klucze specjalne.
ORACLE_KEY_POOL = (
    "Jan Ko", "Jan Kowalski", "   ", "", "ą Ł", "x" * 120,
    "900", "017", "0", "00", " 900 ", "9_0_0", "+17", "-5", "1.5", "9" * 40,
    900, 0, -5, True, False, 1.5, None,
    "1234", "0123", "0000", "9999", "123", "12345", "12a4", "12 4", "١٢٣٤",
    "_meta", "__meta__", "meta", "_META",
)

ORACLE_VALUE_POOL = (
    "Jan Ko", "Jan Kowalski", "", "   ", "0123", "017", "900", " 900 ",
    "9_0_0", "+17", "1234", "١٢٣٤", "x" * 120,
    900, 0, -5, 1.5, True, False, None,
    {}, {"tier": "gold"}, {"generated_at": "x"}, [], ["Jan"], ("Jan",),
)

ORACLE_NON_DICT_STORES = ([], "1234", None, 7, [("Jan Ko", 900)], set(), (("a", 1),))


def _generated_corpus() -> list:
    """Korpus wejść wspólny dla wszystkich rootów (każdy root widzi każdy kształt).

    Deterministyczny: stały seed i stałe pule, więc bieg jest powtarzalny, a
    zawężenie korpusu (droga do oślepienia oracle'a) jest widoczne jako zmiana
    liczb w ``test_oracle_corpus_is_deterministic_and_non_vacuous``.
    """
    cases = [copy.deepcopy(store) for store in ORACLE_NON_DICT_STORES]
    cases.append({})
    for key in ORACLE_KEY_POOL:
        for value in ORACLE_VALUE_POOL:
            cases.append({key: copy.deepcopy(value)})
    rng = random.Random(_ORACLE_SEED)
    for _ in range(_ORACLE_MULTI_RECORD_CASES):
        store = {}
        for _ in range(rng.randint(2, 4)):
            store[rng.choice(ORACLE_KEY_POOL)] = copy.deepcopy(rng.choice(ORACLE_VALUE_POOL))
        cases.append(store)
    return cases


def _expected_verdict(root: str, store) -> str:
    """Werdykt WYPROWADZONY z warstwy rekordu + dozwolonych reguł poziomu generacji."""
    if not isinstance(store, dict):
        return "ValueError"
    if root == "kurier_ids":
        try:
            SCH.validate_courier_ids_store(store)
        except ValueError:
            return "ValueError"
    record_issue = getattr(SCH, FOLDED[root][1])
    if any(record_issue(k, v) is not None for k, v in store.items()):
        return "ValueError"
    return "ok"


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_oracle_store_verdict_equals_record_layer_on_generated_corpus(root):
    """ORACLE (b): werdykt store-walidatora ⟺ werdykt warstwy per-rekord.

    Nadmiarowa restrykcja po stronie store'a (druga kopia polityki, gdziekolwiek
    mieszka) daje ``ValueError`` tam, gdzie warstwa rekordu mówi „zdrowy" —
    i odwrotnie: zgubiona delegacja daje ``ok`` tam, gdzie rekord jest zepsuty.
    Oba kierunki są tu RED.
    """
    validator = _store_validator(root)
    mismatches = []
    for store in _generated_corpus():
        probe = copy.deepcopy(store)
        expected = _expected_verdict(root, probe)
        try:
            returned = validator(probe)
        except ValueError:
            actual = "ValueError"
        except Exception as exc:  # kontrakt: fail-closed to ValueError, nic innego
            actual = f"{type(exc).__name__}: {exc}"
        else:
            actual = "ok" if returned is probe else "ok-bez-zwrotu-store"
        if actual != expected:
            mismatches.append(f"{_case_repr(probe)}: warstwa rekordu={expected}, "
                              f"store-walidator={actual}")
    assert not mismatches, (
        f"{root}: {len(mismatches)} rozjazdów store↔rekord na korpusie "
        f"({len(_generated_corpus())} wejść) — store-walidator ma regułę, której "
        f"nie ma {FOLDED[root][1]} (albo odwrotnie).\n  "
        + "\n  ".join(mismatches[:10])
    )


def _case_repr(store) -> str:
    text = repr(store)
    return text if len(text) <= 120 else text[:117] + "..."


def test_oracle_corpus_is_deterministic_and_non_vacuous():
    """Korpus oracle'a: powtarzalny, pokrywający obie pule i NIE jednostronny.

    Zawężenie korpusu albo doprowadzenie go do stanu „same magazyny zepsute" to
    droga do cichego oślepienia oracle'a — dlatego jest ona tu bramkowana.
    """
    first, second = _generated_corpus(), _generated_corpus()
    assert first == second, "korpus oracle'a przestał być deterministyczny"
    assert len(first) >= len(ORACLE_KEY_POOL) * len(ORACLE_VALUE_POOL)

    covered_keys = {repr(k) for s in first if isinstance(s, dict) for k in s}
    covered_values = {repr(v) for s in first if isinstance(s, dict) for v in s.values()}
    assert {repr(k) for k in ORACLE_KEY_POOL} <= covered_keys
    assert {repr(v) for v in ORACLE_VALUE_POOL} <= covered_values

    for root in sorted(FOLDED):
        verdicts = [_expected_verdict(root, s) for s in first]
        healthy = verdicts.count("ok")
        assert healthy >= 20, f"{root}: korpus prawie bez magazynów zdrowych ({healthy})"
        assert len(verdicts) - healthy >= 20, f"{root}: korpus bez magazynów zepsutych"
