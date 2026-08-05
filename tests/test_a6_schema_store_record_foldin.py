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
  (3) RATCHET STRUKTURALNY — ciało store-walidatora (i całej instalacji, przez
      którą przechodzi ścieżka pisarza) MUSI mieć DOKŁADNIE pinowany kształt;
      każda dodatkowa instrukcja lub wywołanie = RED. Pinowana jest też TOŻSAMOŚĆ
      obiektu (własna, nieowinięta funkcja modułu), bo ratchet czyta ŹRÓDŁO, a
      dekorator uruchamia inny kod, niż źródło pokazuje. Plus: każdy root
      z ``ROOT_VALIDATORS`` ma opisany fold-in.
  (4) GRANICE — ``validate_courier_ids_store`` bez zmian semantyki, a
      ``validate_kurier_ids_root`` nadal go woła PONAD delegacją.
  (5) ORACLE RÓWNOWAŻNOŚCI — na GENEROWANYM korpusie: werdykt ⟺ werdykt warstwy
      per-rekord (poza regułami poziomu generacji), sprawdzany na OBU wejściach:
      store-walidatorze i ``validate_root`` (jedyne wejście PISARZA). Łapie
      dodatkową restrykcję niezależnie od tego, w którym module ona mieszka.

Sekcje (3) i (5) powstały w iter2, po blind-review sesji 297: pierwotny ratchet
był czarną listą NAZW w ciele funkcji, a oracle M1/M2 był przywiązany do dwóch
stałych magazynów — recenzent zmierzył, że jeden helper module-level (w tym samym
albo w innym module) przywraca drugą kopię polityki przy 192 zielonych testach.
Szczegóły i dowody: ``RAPORT_ITER2.md``.

Iter3 domyka trzy dziury TRWAŁOŚCI zmierzone w delta-blind #2 (dowody:
``RAPORT_ITER3.md``): bramka mierzyła tylko wejście ``_store_validator``, choć
pisarz wchodzi przez ``validate_root`` (F1-R2); ratchet dawał się oślepić
wrapperem, bo ``inspect.getsource`` podąża za ``__wrapped__`` (F2-R2); progi
bramki na jakość korpusu liczyły się z tych samych pul, które miały chronić
(F3-R2). Kod produkcyjny nie zmienił się ani o bajt — dziury były w bramce.

Hermetyczny: czysty unit na literałach, zero I/O do ``dispatch_state``.
"""
from __future__ import annotations

import ast
import copy
import inspect
import os
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


#: Wspólna instalacja ścieżki pisarza: ``validate_root`` — JEDYNE wejście, przez
#: które ``identity/journal.py`` (zapis generacji i recovery po crashu) w ogóle
#: wchodzi do schematu — oraz dwa węzły, przez które przechodzi KAŻDY
#: store-walidator. Poza nimi w grafie wywołań zostają już tylko
#: ``*_record_issue`` (kanoniczny owner reguły) i nietykalny
#: ``validate_courier_ids_store``, więc polityka kształtu rekordu przeniesiona
#: „w dół" albo „w górę" tutaj też musi być RED. Kształt pinujemy strukturalnie:
#: rozdział na walidatory + fail-closed na nieznanym roocie, typ kontenera,
#: iteracja i przekład „rekord zepsuty" na ``ValueError`` — zero reguł kształtu.
#:
#: ``validate_root`` dołożył iter3: delta-blind #2 zmierzył, że druga kopia
#: polityki położona TAM zostawia całą bramkę zieloną (F1-R2) — ratchet pinował
#: tylko obiekty zwracane przez ``_store_validator``, a produkcja ich nie woła
#: bezpośrednio.
SHARED_PLUMBING = {
    "validate_root": (
        'validator = ROOT_VALIDATORS.get(root)\n'
        'if validator is None:\n'
        '    raise ValueError(f"nieznany root generacji: {root!r}")\n'
        'return validator(store)'
    ),
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


def _pinned_function(attr_name: str):
    """Obiekt, którego ciało pinuje ratchet — MUSI być własną, NIEOWINIĘTĄ funkcją
    ``identity.schema``.

    To nie jest ozdoba, tylko warunek sensowności całego ratchetu: ratchet czyta
    ŹRÓDŁO, a wrapper uruchamia kod, którego w tym źródle nie ma. Delta-blind #2
    (F2-R2) zmierzył dwa warianty:

    * (A) dekorator z ``functools.wraps`` — ``inspect.getsource`` woła
      ``inspect.unwrap``, więc ratchet parsuje źródło funkcji WEWNĘTRZNEJ i widzi
      czystą delegację, choć wywołanie rzuca; ślad: niepuste ``__wrapped__``;
    * (B) rebind ``validate_x = _tighten(validate_x)`` BEZ ``functools.wraps`` —
      ``__wrapped__`` nie powstaje, ale wrapper zdradza się tożsamością:
      ``__qualname__`` = ``_tighten.<locals>.wrapper``.

    Dlatego pinujemy tożsamość obiektu, a nie tylko jego tekst: funkcja (nie
    dowolny ``callable``), bez ``__wrapped__``, z tego modułu, o kwalifikowanej
    nazwie DOKŁADNIE równej nazwie atrybutu (to samo zamyka wariant B, bo
    ``<locals>`` nigdy się nie zgodzi) i z kodem skompilowanym z pliku tego
    modułu pod tą samą nazwą (to zamyka podmianę ``__qualname__``/``__name__``
    na wrapperze). Store-walidator i wspólna instalacja nie mają prawa być
    owinięte — nie ma legalnego powodu, dla którego ta warstwa miałaby robić
    cokolwiek poza delegacją.
    """
    fn = getattr(SCH, attr_name)
    assert inspect.isfunction(fn), (
        f"{attr_name}: nie jest funkcją modułu, tylko {type(fn).__name__} — "
        f"ratchet pinuje źródło, więc obiekt musi być tą samą rzeczą, która się wykonuje"
    )
    assert getattr(fn, "__wrapped__", None) is None, (
        f"{attr_name}: funkcja jest OWINIĘTA (__wrapped__) — dekorator wykonuje kod, "
        f"którego ratchet nie widzi (inspect.getsource podąża za __wrapped__). "
        f"Ta warstwa ma być czystą delegacją, więc wrapper jest z definicji zakazany"
    )
    assert fn.__module__ == SCH.__name__, (
        f"{attr_name}: funkcja pochodzi z modułu {fn.__module__}, nie z {SCH.__name__}"
    )
    assert fn.__qualname__ == attr_name, (
        f"{attr_name}: kwalifikowana nazwa to {fn.__qualname__!r} — pod nazwą modułową "
        f"podstawiono inny obiekt (np. wrapper z '<locals>')"
    )
    assert fn.__code__.co_name == attr_name, (
        f"{attr_name}: kod obiektu został skompilowany pod nazwą "
        f"{fn.__code__.co_name!r} — __qualname__ nie opisuje tego, co się wykonuje"
    )
    assert (os.path.realpath(fn.__code__.co_filename)
            == os.path.realpath(SCH.__file__)), (
        f"{attr_name}: kod obiektu pochodzi z pliku {fn.__code__.co_filename} — "
        f"owner reguł kształtu rootów to WYŁĄCZNIE {SCH.__file__}"
    )
    return fn


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


def _pinned_body_shape(attr_name: str) -> list:
    """Kształt ciała obiektu o SPRAWDZONEJ tożsamości — jedyna droga, którą
    ratchet czyta źródło (żeby nie dało się go nakarmić cudzym tekstem)."""
    return _body_shape(_code_ast(_pinned_function(attr_name)).body[0].body)


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_ratchet_store_validator_body_is_exactly_delegation(root):
    """RATCHET (a): ciało store-walidatora = DOKŁADNIE delegacja do warstwy rekordu.

    Nie „nie zawiera zakazanych nazw", tylko „jest dokładnie tym kształtem" —
    ratchetu nie da się oślepić helperem, bo helper trzeba z tego ciała wywołać,
    a to już jest inny kształt. Nie da się go też oślepić wrapperem: obiekt musi
    najpierw przejść ``_pinned_function`` (iter3, F2-R2).
    """
    actual = _pinned_body_shape(FOLDED[root][0])
    expected = _body_shape(ast.parse(_expected_store_body_src(root)).body)
    assert actual == expected, (
        f"{root}: ciało store-walidatora przestało być czystą delegacją do "
        f"{FOLDED[root][1]} — owner reguły kształtu rekordu jest JEDEN.\n"
        f"oczekiwany kształt:\n    {_expected_store_body_src(root)}"
    )


@pytest.mark.parametrize("helper", sorted(SHARED_PLUMBING))
def test_ratchet_shared_plumbing_body_is_exactly_pinned_shape(helper):
    """RATCHET (a2): instalacja ścieżki pisarza nie przemyca reguły kształtu rekordu.

    Bez tego duplikat polityki dałoby się schować piętro niżej (wspólny kod, przez
    który i tak przechodzi każdy store-walidator) albo piętro WYŻEJ — w
    ``validate_root``, czyli w jedynym miejscu, którym pisarz w ogóle wchodzi do
    schematu (``identity/journal.py:237``).
    """
    actual = _pinned_body_shape(helper)
    expected = _body_shape(ast.parse(SHARED_PLUMBING[helper]).body)
    assert actual == expected, (
        f"{helper}: instalacja store-walidatorów zmieniła kształt — "
        f"reguła kształtu rekordu należy WYŁĄCZNIE do warstwy *_record_issue.\n"
        f"oczekiwany kształt:\n{SHARED_PLUMBING[helper]}"
    )


def test_ratchet_every_root_validator_is_folded_in():
    """RATCHET: każdy root z ``ROOT_VALIDATORS`` ma opisany fold-in.

    Dołożenie szóstego roota bez delegacji do warstwy per-rekord = RED (ten sam
    wzorzec kompletności co ratchet ROOT_VALIDATORS w bramce G5). Wpis w mapie
    musi być tą samą, nieowiniętą funkcją, którą pinuje ratchet ciała — inaczej
    ``validate_root`` wołałby coś innego, niż bramka przeczytała.
    """
    assert set(SCH.ROOT_VALIDATORS) == set(FOLDED)
    for root, (store_name, record_name) in FOLDED.items():
        assert SCH.ROOT_VALIDATORS[root] is _pinned_function(store_name)
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

#: WYMIAR ROZMIARU GENERACJI (iter3, F2-R2). Korpus z pul ma maksymalnie 4 rekordy
#: w magazynie (``rng.randint(2, 4)``), więc restrykcja poziomu generacji typu
#: ``len(store) > 4`` — najprostsza reguła, jaką da się dołożyć OBOK warstwy
#: rekordu — leżała poza jego zasięgiem. Magazyny poniżej są ZDROWE wg warstwy
#: rekordu swojego roota (i wg kontraktu autoryzacji dla ``kurier_ids``), więc
#: każde odrzucenie ich przez store-walidatora albo przez ``validate_root`` jest
#: rozjazdem, który oracle musi zobaczyć. Rozmiary 0 i 1 są już w korpusie
#: (``{}`` + 936 magazynów jednorekordowych).
ORACLE_GENERATION_SIZES = (5, 50)

_HEALTHY_RECORD_FACTORY = {
    "kurier_piny": lambda i: (f"{1000 + i}", f"Alias {i}"),
    "courier_tiers": lambda i: (str(900 + i), {"tier": "gold"}),
    "courier_names": lambda i: (str(900 + i), f"Kurier {i}"),
    "kurier_full_names": lambda i: (f"Alias {i}", f"Imie Nazwisko {i}"),
    "kurier_ids": lambda i: (f"Kurier {i}", str(900 + i)),
}

#: Rekord zepsuty WYŁĄCZNIE regułą per-rekord, dokładany do dużego magazynu:
#: bez niego „duży" znaczyłoby w korpusie zawsze „zdrowy" i restrykcja odwrotna
#: (zgubiona delegacja dla dużych generacji) byłaby niewidoczna.
_BROKEN_RECORD = {
    "kurier_piny": ("12345", "Jan Ko"),
    "courier_tiers": ("017", {}),
    "courier_names": ("017", "Jan Ko"),
    "kurier_full_names": ("   ", "Jan Kowalski"),
    "kurier_ids": ("Zly Ktos", "017"),
}


def _sized_stores() -> list:
    """Magazyny o rozmiarach z ``ORACLE_GENERATION_SIZES`` — zdrowe i zdrowe+1
    zepsuty rekord — dla KAŻDEGO roota (korpus jest wspólny, więc każdy root
    widzi też duże magazyny pozostałych)."""
    stores = []
    for root in sorted(FOLDED):
        factory = _HEALTHY_RECORD_FACTORY[root]
        broken_key, broken_value = _BROKEN_RECORD[root]
        for size in ORACLE_GENERATION_SIZES:
            healthy = dict(factory(i) for i in range(size))
            stores.append(healthy)
            stores.append({**copy.deepcopy(healthy),
                           broken_key: copy.deepcopy(broken_value)})
    return stores


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
    cases.extend(_sized_stores())
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


def _oracle_mismatches(root: str, entry) -> list:
    """Wszystkie wejścia korpusu, na których ``entry`` nie zgadza się z warstwą
    rekordu. ``entry`` = wołalne wejście do schematu roota (store-walidator albo
    ``validate_root``) — mierzymy ZACHOWANIE, więc jest obojętne, w którym module
    i pod jaką nazwą mieszka kod, który je realizuje."""
    mismatches = []
    for store in _generated_corpus():
        probe = copy.deepcopy(store)
        expected = _expected_verdict(root, probe)
        try:
            returned = entry(probe)
        except ValueError:
            actual = "ValueError"
        except Exception as exc:  # kontrakt: fail-closed to ValueError, nic innego
            actual = f"{type(exc).__name__}: {exc}"
        else:
            actual = "ok" if returned is probe else "ok-bez-zwrotu-store"
        if actual != expected:
            mismatches.append(f"{_case_repr(probe)}: warstwa rekordu={expected}, "
                              f"wejście={actual}")
    return mismatches


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_oracle_store_verdict_equals_record_layer_on_generated_corpus(root):
    """ORACLE (b): werdykt store-walidatora ⟺ werdykt warstwy per-rekord.

    Nadmiarowa restrykcja po stronie store'a (druga kopia polityki, gdziekolwiek
    mieszka) daje ``ValueError`` tam, gdzie warstwa rekordu mówi „zdrowy" —
    i odwrotnie: zgubiona delegacja daje ``ok`` tam, gdzie rekord jest zepsuty.
    Oba kierunki są tu RED.
    """
    mismatches = _oracle_mismatches(root, _store_validator(root))
    assert not mismatches, (
        f"{root}: {len(mismatches)} rozjazdów store↔rekord na korpusie "
        f"({len(_generated_corpus())} wejść) — store-walidator ma regułę, której "
        f"nie ma {FOLDED[root][1]} (albo odwrotnie).\n  "
        + "\n  ".join(mismatches[:10])
    )


@pytest.mark.parametrize("root", sorted(FOLDED))
def test_oracle_writer_entry_verdict_equals_record_layer_on_generated_corpus(root):
    """ORACLE (b2): to samo, ale przez WEJŚCIE PISARZA — ``validate_root``.

    Iter3, F1-R2. ``identity/journal.py:237`` (``_validate_payload``, zapis
    generacji i recovery po crashu) NIE woła store-walidatora bezpośrednio: cała
    ścieżka pisarza przechodzi przez ``SCH.validate_root(root, store)``. Oracle
    mierzący wyłącznie ``_store_validator`` zostawiał więc wolne miejsce na drugą
    kopię polityki dokładnie tam, gdzie odruchowo dokłada się regułę „na poziomie
    całej generacji" — a wtedy pisarz odrzuca rekord, który verifier
    (``*_record_issue``) uznaje za zdrowy. To jest ten sam rozjazd, przeciwko
    któremu powstała ta bramka, więc jego wejście musi być mierzone wprost.
    """
    mismatches = _oracle_mismatches(root, lambda store: SCH.validate_root(root, store))
    assert not mismatches, (
        f"{root}: {len(mismatches)} rozjazdów pisarz↔rekord na korpusie "
        f"({len(_generated_corpus())} wejść) — validate_root ma regułę, której nie "
        f"ma {FOLDED[root][1]} (albo odwrotnie).\n  "
        + "\n  ".join(mismatches[:10])
    )


def _case_repr(store) -> str:
    text = repr(store)
    return text if len(text) <= 120 else text[:117] + "..."


# --------------------------------------------------------------------------- #
# Bramka na SAM KORPUS — progi STAŁE, nie liczone z pul (iter3, F3-R2)
# --------------------------------------------------------------------------- #
# Poprzednia wersja tej bramki mierzyła korpus jego własnymi pulami
# (``len(first) >= len(ORACLE_KEY_POOL) * len(ORACLE_VALUE_POOL)`` i „czy korpus
# pokrywa pule"), więc próg malał razem z zawężaną pulą, a pokrycie było prawdziwe
# dla KAŻDEJ puli. Delta-blind #2 zmierzył: usunięcie trzech kluczy z puli
# przechodziło na zielono i pozwalało — w tym samym commicie — przepchnąć
# restrykcję na usuniętej formie. Dlatego progi są teraz LITERAŁAMI (pomiar
# 2026-08-05 minus ~10% zapasu), a pokrycie sprawdzamy względem ZAMROŻONEJ listy
# form granicznych: skasowanie którejkolwiek z nich z puli jest RED niezależnie
# od tego, jak duży zostanie korpus.
MIN_CORPUS_CASES = 1130                     # zmierzone: 1264
MIN_HEALTHY_STORES = {                      # zmierzone: 88 / 27 / 331 / 86 / 47
    "courier_names": 79,
    "courier_tiers": 24,
    "kurier_full_names": 297,
    "kurier_ids": 77,
    "kurier_piny": 42,
}
MIN_BROKEN_STORES = {                       # zmierzone: 1176 / 1237 / 933 / 1178 / 1217
    "courier_names": 1058,
    "courier_tiers": 1113,
    "kurier_full_names": 839,
    "kurier_ids": 1060,
    "kurier_piny": 1095,
}

#: Formy graniczne, na których stoją reprodukcje blind-review (PIN z zerem
#: wiodącym, CID z zerem wiodącym, CID ze spacjami/podkreślnikiem/znakiem, klucz
#: ``_meta``, ``bool``/``float``/``None`` jako klucz, unicode-cyfry, formy puste).
#: Lista jest ZAMROŻONA i celowo NIE jest wyprowadzana z pul — to ona bramkuje
#: zawężanie pul, a nie odwrotnie.
FROZEN_BOUNDARY_KEYS = (
    "0123", "0000", "1234", "9999", "123", "12345", "12a4", "12 4", "١٢٣٤",
    "900", "017", "0", "00", " 900 ", "9_0_0", "+17", "-5", "1.5",
    "", "   ", "_meta", 900, 0, -5, True, False, 1.5, None,
)
FROZEN_BOUNDARY_VALUES = (
    "Jan Ko", "", "   ", "0123", "017", "900", " 900 ", "9_0_0", "+17", "1234",
    "١٢٣٤", 900, 0, -5, 1.5, True, False, None, {}, {"tier": "gold"}, [], ["Jan"],
)


def test_oracle_corpus_is_deterministic_and_non_vacuous():
    """Korpus oracle'a: powtarzalny, dostatecznie duży, dwustronny i pokrywający
    ZAMROŻONE formy graniczne oraz wymiar rozmiaru generacji.

    Zawężenie korpusu albo doprowadzenie go do stanu „same magazyny zepsute" to
    droga do cichego oślepienia oracle'a — dlatego jest ona tu bramkowana, i to
    progami niezależnymi od tego, co zostanie w pulach.
    """
    first, second = _generated_corpus(), _generated_corpus()
    assert first == second, "korpus oracle'a przestał być deterministyczny"
    assert len(first) >= MIN_CORPUS_CASES, (
        f"korpus skurczył się do {len(first)} wejść (próg stały: {MIN_CORPUS_CASES})")

    stores = [s for s in first if isinstance(s, dict)]
    covered_keys = {repr(k) for s in stores for k in s}
    covered_values = {repr(v) for s in stores for v in s.values()}
    missing_keys = sorted(repr(k) for k in FROZEN_BOUNDARY_KEYS
                          if repr(k) not in covered_keys)
    missing_values = sorted(repr(v) for v in FROZEN_BOUNDARY_VALUES
                            if repr(v) not in covered_values)
    assert not missing_keys, f"korpus stracił zamrożone formy graniczne (klucze): {missing_keys}"
    assert not missing_values, f"korpus stracił zamrożone formy graniczne (wartości): {missing_values}"

    # generator ma naprawdę emitować całe swoje pule (to inne stwierdzenie niż
    # powyższe: pilnuje generatora, nie zawartości pul)
    assert {repr(k) for k in ORACLE_KEY_POOL} <= covered_keys
    assert {repr(v) for v in ORACLE_VALUE_POOL} <= covered_values

    for root in sorted(FOLDED):
        verdicts = [_expected_verdict(root, s) for s in first]
        healthy = verdicts.count("ok")
        assert healthy >= MIN_HEALTHY_STORES[root], (
            f"{root}: korpus ma {healthy} magazynów zdrowych, próg stały "
            f"{MIN_HEALTHY_STORES[root]} — zawężenie pul oślepia oracle'a")
        assert len(verdicts) - healthy >= MIN_BROKEN_STORES[root], (
            f"{root}: korpus ma {len(verdicts) - healthy} magazynów zepsutych, "
            f"próg stały {MIN_BROKEN_STORES[root]}")
        for size in ORACLE_GENERATION_SIZES:
            assert any(len(s) >= size and _expected_verdict(root, s) == "ok"
                       for s in stores), (
                f"{root}: korpus stracił wymiar rozmiaru generacji — brak ZDROWEGO "
                f"magazynu o co najmniej {size} rekordach, więc restrykcja na "
                f"rozmiarze generacji byłaby niewidoczna")
