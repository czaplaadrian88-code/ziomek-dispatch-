"""A-6/G6 (K1) — verifier waliduje SCHEMAT każdego czytanego roota tożsamości.

Bramka: engine.a6-verifier-complete-roots-epoch (K1; K2 = bezprzedmiotowa na
masterze — nie ma epoki poświadczeń w verifierze, patrz REPORT.md).

DEFEKT na masterze (RED tu, GREEN na kandydacie):
  ``new_courier_pairing.verify_courier_wired`` sprawdzał wyłącznie OBECNOŚĆ
  wpisu (``str(cid) in tiers``, ``alias in full``, ``alias in set(piny.values())``),
  nigdy KSZTAŁTU rekordu. Zepsuty rekord — ``True`` / lista / ``"017"`` /
  wartość złego typu — przechodził bez echa i verifier meldował „✓ podpięty",
  choć kanoniczny czytelnik (``identity.roster``, ``courier_info``,
  ``courier_availability``, ``pin_auth``) tego rekordu NIE widzi. Werdykt tego
  narzędzia jest ostatnią bramką auto-parowania (``_auto_wire``) i treścią DM do
  właściciela, więc cichy fałszywy sukces utrwalał niewidzialnego kuriera.
  Osobno: zepsuta WARTOŚĆ w ``kurier_piny`` (lista) wywalała cały skan
  ``TypeError: unhashable`` — nieczytelny root nie może być nierozróżnialny od
  roota zdrowego ani wywracać skanu.

KONTRAKT po naprawie (rekomendacja CTO z reskope, przyjęta):
  * rekord zepsuty DOTYCZĄCY audytowanej tożsamości  → check ✗ (fail-closed),
  * rekord zepsuty NIEZWIĄZANY                        → dodatkowa linia
    ``⚠ malformed w <root>: N``; ``all_ok`` BEZ zmiany (narzędzie diagnostyczne
    operatora — nie blokuje wpięcia kuriera A z powodu wpisu kuriera B),
  * kontrakt publiczny ``(all_ok, checklist_lines)`` i fail-soft introspekcji
    reverse-check — bez zmian,
  * na ZDROWYCH plikach linie checklisty są IDENTYCZNE z masterem (parytet).

ITER2 (finding F1 blind review, CONFIRMED_DEFECT):
  Czytelnicy rootów dzielą się na DWIE klasy i model „liczę check na rekordach
  zdrowych" jest poprawny tylko dla jednej z nich:
    * czytelnik PER REKORD (``identity.roster`` G3, ``courier_tiers``,
      ``kurier_piny``, ``kurier_full_names``) pomija zepsuty wiersz POJEDYNCZO —
      filtrowanie magazynu przed checkiem może werdykt tylko zaostrzyć,
    * czytelnik KONKURENCYJNY (``shift_notifications.worker.resolve_cid`` nad
      ``kurier_ids``) rozstrzyga po WSZYSTKICH wierszach (exact →
      case-insensitive → score, remis ⇒ ``None``), a jego loader nie waliduje
      niczego. Zepsuty wiersz NIE jest dla niego niewidzialny: może WYGRAĆ
      score-fallback albo wywołać remis. Usunięcie go przed checkiem POLUZOWUJE
      werdykt względem produkcji (fałszywe ✓ nad kurierem, którego dyspozytornia
      resolwuje na cudzy/śmieciowy cid albo wcale).
  Dlatego check dyspozytorni liczy się na SUROWYM magazynie — tym, który widzi
  czytelnik — a wynik walidacji schematu zamyka bramkę wyłącznie przez
  ``identity_broken``. Testy tego pliku NIE stubują ``resolve_cid``: stub z iter1
  trafiał tylko dokładnym kluczem, więc score-fallback (jedyne miejsce, gdzie
  filtrowanie zmienia odpowiedź) nie był pokryty żadnym oraclem.

Ten plik pokrywa: (1) parytet zdrowej ścieżki bajt-w-bajt; (2) oracle negatywny
per KAŻDY z 6 rootów w obu wariantach (dotyczy tożsamości ✗ / niezwiązany ⚠);
(3) root nieczytelny/nie-mapa; (4) `_meta` w courier_tiers nie jest defektem;
(5) RATCHET: każdy czytany root ma walidację delegowaną do ``identity.schema``
(usunięcie walidacji jednego roota albo kopia inline zamiast delegacji = RED)
+ RATCHET czytelnika konkurencyjnego (powrót checku na ``kids.healthy`` = RED);
(6) czytelnik konkurencyjny: audyt nigdy nie jest bardziej zielony niż produkcyjny
``resolve_cid`` (oracle F1 — S1 „obcy zepsuty wygrywa", S2 „remis w pełnym
magazynie", S3 parytet wartości int).

Hermetyczny: WSZYSTKIE 6 stałych ścieżek zmonkeypatchowane na tmp_path, zero I/O
do żywego dispatch_state.
"""
from __future__ import annotations

import ast
import inspect
import json
import textwrap

import pytest

from dispatch_v2 import new_courier_pairing as ncp
from dispatch_v2.identity import schema as identity_schema
from dispatch_v2.shift_notifications import state as sn_state
from dispatch_v2.shift_notifications.worker import resolve_cid as real_resolve_cid


CID = 9001
NAME = "Jan Kowalski"
ALIAS = "Jan Ko"          # derive_alias(NAME)
OTHER_CID = 9002
OTHER_NAME = "Ola Zielona"
OTHER_ALIAS = "Ola Zi"

#: Linie checklisty MASTERA (c91b7ede8) na zdrowych plikach — zamrożone bajt-w-bajt.
#: Zmiana etykiety albo kolejności = zmiana kontraktu konsumentów (DM Telegram
#: ``_auto_wire`` + ``telegram_approver`` /nowy) → RED.
MASTER_LINES = [
    "✓ dyspozytornia (resolve_cid)",
    "✓ scoring (courier_tiers, tier=new)",
    "✓ apka kuriera (PIN login)",
    "✓ liczenie COD (kurier_full_names)",
    "✓ dispatch reverse (cid->imię->grafik)",
]

#: root -> stała ścieżki w ``new_courier_pairing``.
ROOT_CONSTS = {
    "kurier_ids": "KURIER_IDS",
    "kurier_piny": "KURIER_PINY",
    "courier_tiers": "COURIER_TIERS",
    "kurier_full_names": "KURIER_FULL_NAMES",
    "courier_names": "COURIER_NAMES",
    "grafik_full_names": "GRAFIK_FULL_NAMES",
}

#: root -> etykieta checku, który MUSI zejść na ✗ gdy zepsuty rekord dotyczy
#: audytowanej tożsamości.
ROOT_CHECK = {
    "kurier_ids": "dyspozytornia (resolve_cid)",
    "kurier_piny": "apka kuriera (PIN login)",
    "courier_tiers": "scoring (courier_tiers, tier=new)",
    "kurier_full_names": "liczenie COD (kurier_full_names)",
    "courier_names": "dispatch reverse (cid->imię->grafik)",
    "grafik_full_names": "dispatch reverse (cid->imię->grafik)",
}


def _healthy() -> dict:
    """Zdrowa generacja 6 rootów: audytowany kurier + jeden obcy (kontrola)."""
    return {
        "kurier_ids": {ALIAS: CID, NAME: CID, OTHER_ALIAS: OTHER_CID},
        "kurier_piny": {"1234": ALIAS, "5678": OTHER_ALIAS},
        "courier_tiers": {str(CID): {"tier": "new"},
                          str(OTHER_CID): {"tier": "std"}},
        "kurier_full_names": {ALIAS: NAME, OTHER_ALIAS: OTHER_NAME},
        "courier_names": {str(CID): NAME, str(OTHER_CID): OTHER_ALIAS},
        "grafik_full_names": {NAME: CID, OTHER_NAME: OTHER_CID},
    }


@pytest.fixture
def verify(tmp_path, monkeypatch):
    """Zasiej 6 rootów w tmp i zwróć wywoływacz ``verify_courier_wired``.

    ``resolve_cid`` NIE jest stubowany (iter2). Stub z iter1 trafiał wyłącznie
    dokładnym kluczem, więc omijał score-fallback ``worker.resolve_cid`` — czyli
    jedyne miejsce, w którym rekord CUDZY zmienia odpowiedź czytelnika dla
    audytowanego imienia. Testy mierzą tu produkcyjny resolver (luka dowodowa
    F1 z blind review).
    """
    def _run(roots=None, *, cid=CID, full_name=NAME):
        data = _healthy()
        data.update(roots or {})
        for root, const in ROOT_CONSTS.items():
            path = tmp_path / f"{root}.json"
            payload = data[root]
            if isinstance(payload, str):       # surowy tekst (uszkodzony JSON)
                path.write_text(payload, encoding="utf-8")
            else:
                path.write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
            monkeypatch.setattr(ncp, const, str(path))
        # resolve_cid loguje remis/rozstrzygnięcie do courier_match_debug.jsonl
        # (stała modułowa wskazująca ŻYWY dispatch_state) — przekieruj do tmp,
        # żeby prawdziwy resolver nie zależał od write-guarda hermetyzacji.
        monkeypatch.setattr(sn_state, "MATCH_DEBUG_LOG",
                            tmp_path / "courier_match_debug.jsonl")
        return ncp.verify_courier_wired(cid, full_name)
    return _run


def _line_for(lines, label):
    matching = [ln for ln in lines if ln.endswith(label)]
    assert matching, f"brak linii checklisty {label!r} w {lines!r}"
    return matching[0]


def _malformed_lines(lines):
    return [ln for ln in lines if ln.startswith("⚠ malformed w ")]


# --------------------------------------------------------------------------- #
# (1) Parytet zdrowej ścieżki — bajt-w-bajt jak master
# --------------------------------------------------------------------------- #

def test_healthy_generation_lines_identical_to_master(verify):
    """Na zdrowych plikach kontrakt wyjściowy jest DOKŁADNIE masterowy: te same
    5 linii, ta sama kolejność, żadnej linii ⚠, ``all_ok`` True."""
    ok, lines = verify()
    assert ok is True
    assert lines == MASTER_LINES


def test_healthy_generation_public_contract_shape(verify):
    """``(bool, list[str])`` — konsumenci (``_auto_wire``, /nowy) rozpakowują krotkę."""
    result = verify()
    assert isinstance(result, tuple) and len(result) == 2
    ok, lines = result
    assert isinstance(ok, bool)
    assert isinstance(lines, list) and all(isinstance(ln, str) for ln in lines)


def test_meta_block_in_courier_tiers_is_not_malformed(verify):
    """``courier_tiers._meta`` to kontrakt żywego roota (schema_version, ground
    truth tierów) — NIE wolno go raportować jako zepsutego rekordu, bo verifier
    krzyczałby ⚠ przy KAŻDYM wpięciu na produkcji."""
    tiers = _healthy()["courier_tiers"]
    tiers["_meta"] = {"schema_version": "v1", "source": "ground_truth"}
    ok, lines = verify({"courier_tiers": tiers})
    assert ok is True
    assert lines == MASTER_LINES


# --------------------------------------------------------------------------- #
# (2a) Oracle negatywny: rekord zepsuty DOTYCZĄCY audytowanej tożsamości → ✗
# --------------------------------------------------------------------------- #

BROKEN_IDENTITY = [
    # (root, payload roota, id przypadku)
    pytest.param("kurier_ids", {ALIAS: True, NAME: CID, OTHER_ALIAS: OTHER_CID},
                 id="ids-alias-bool"),
    pytest.param("kurier_ids", {ALIAS: ["9001"], NAME: CID},
                 id="ids-alias-list"),
    pytest.param("kurier_ids", {ALIAS: "0" + str(CID), NAME: CID},
                 id="ids-alias-leading-zero"),
    pytest.param("kurier_ids", {ALIAS: "+" + str(CID), NAME: CID},
                 id="ids-alias-plus-sign"),
    pytest.param("kurier_piny", {"12A4": ALIAS, "5678": OTHER_ALIAS},
                 id="piny-key-not-4-digits"),
    pytest.param("kurier_piny", {"01234": ALIAS, "5678": OTHER_ALIAS},
                 id="piny-key-too-long"),
    pytest.param("courier_tiers", {str(CID): True, str(OTHER_CID): {"tier": "std"}},
                 id="tiers-row-not-object"),
    pytest.param("courier_tiers", {str(CID): {"tier": "new"},
                                   "0" + str(CID): {"tier": "gold"}},
                 id="tiers-noncanonical-duplicate-key"),
    pytest.param("kurier_full_names", {ALIAS: 123, OTHER_ALIAS: OTHER_NAME},
                 id="full-value-not-string"),
    pytest.param("kurier_full_names", {ALIAS: "   ", OTHER_ALIAS: OTHER_NAME},
                 id="full-value-blank"),
    pytest.param("courier_names", {"0" + str(CID): NAME,
                                   str(OTHER_CID): OTHER_ALIAS},
                 id="names-noncanonical-cid-key"),
    pytest.param("courier_names", {str(CID): 123, str(OTHER_CID): OTHER_ALIAS},
                 id="names-value-not-string"),
    pytest.param("grafik_full_names", {NAME: "0" + str(CID), OTHER_NAME: OTHER_CID},
                 id="grafik-noncanonical-cid"),
    pytest.param("grafik_full_names", {NAME: True, OTHER_NAME: OTHER_CID},
                 id="grafik-cid-bool"),
    pytest.param("grafik_full_names", {NAME: [CID], OTHER_NAME: OTHER_CID},
                 id="grafik-cid-list"),
]


@pytest.mark.parametrize("root,payload", BROKEN_IDENTITY)
def test_malformed_record_of_audited_identity_fails_closed(verify, root, payload):
    """Zepsuty rekord DOTYCZĄCY audytowanej tożsamości = check tego roota ✗.

    Kanoniczny czytelnik takiego rekordu nie przyjmuje (``canonical_numeric_cid``
    w ``identity.roster`` pomija go, ``validate_courier_ids_store`` wywraca całą
    generację, ``pin_auth`` nie trafi klucza PIN), więc „✓ podpięty" byłoby
    fałszywym meldunkiem — nie ma prawa być ani ✓, ani ciszą.
    """
    ok, lines = verify({root: payload})
    assert ok is False, f"{root}: zepsuty rekord tożsamości przeszedł jako sukces"
    assert _line_for(lines, ROOT_CHECK[root]).startswith("✗")


# --------------------------------------------------------------------------- #
# (2b) Oracle negatywny: rekord zepsuty NIEZWIĄZANY → linia ⚠, all_ok bez zmiany
# --------------------------------------------------------------------------- #

BROKEN_UNRELATED = [
    pytest.param("kurier_ids", {ALIAS: CID, NAME: CID, OTHER_ALIAS: ["9002"]},
                 id="ids-foreign-list"),
    pytest.param("kurier_ids", {ALIAS: CID, NAME: CID, OTHER_ALIAS: "0" + str(OTHER_CID)},
                 id="ids-foreign-leading-zero"),
    pytest.param("kurier_piny", {"1234": ALIAS, "9999": [OTHER_ALIAS]},
                 id="piny-foreign-value-list"),
    pytest.param("kurier_piny", {"1234": ALIAS, "99": OTHER_ALIAS},
                 id="piny-foreign-key-short"),
    pytest.param("courier_tiers", {str(CID): {"tier": "new"}, str(OTHER_CID): "std"},
                 id="tiers-foreign-row-not-object"),
    pytest.param("kurier_full_names", {ALIAS: NAME, OTHER_ALIAS: None},
                 id="full-foreign-value-null"),
    pytest.param("courier_names", {str(CID): NAME, "0" + str(OTHER_CID): OTHER_ALIAS},
                 id="names-foreign-noncanonical-key"),
    pytest.param("grafik_full_names", {NAME: CID, OTHER_NAME: [OTHER_CID]},
                 id="grafik-foreign-cid-list"),
]


@pytest.mark.parametrize("root,payload", BROKEN_UNRELATED)
def test_malformed_unrelated_record_is_reported_not_silent(verify, root, payload):
    """Zepsuty rekord CUDZEJ tożsamości: jawna linia ``⚠ malformed w <root>: N``.

    ``all_ok`` się NIE zmienia — to narzędzie diagnostyczne operatora i nie wolno
    mu blokować wpięcia kuriera A dlatego, że rekord kuriera B jest zepsuty
    (rekomendacja CTO z reskope). Cisza jednak też jest niedopuszczalna: dziś
    verifier mówi „wszystko ✓" nad rejestrem, którego ``courier_info`` nie
    przyjmie.
    """
    ok, lines = verify({root: payload})
    assert ok is True, f"{root}: obcy zepsuty rekord nie ma prawa blokować all_ok"
    assert lines[:len(MASTER_LINES)] == MASTER_LINES
    assert f"⚠ malformed w {root}: 1" in lines


def test_malformed_unrelated_counts_are_per_root(verify):
    """Liczniki są PER ROOT i tylko dla rootów z defektem (żadnych zer-szumu)."""
    ok, lines = verify({
        "kurier_ids": {ALIAS: CID, NAME: CID, OTHER_ALIAS: ["9002"], "Zenon Zz": True},
        "courier_tiers": {str(CID): {"tier": "new"}, str(OTHER_CID): "std"},
    })
    assert ok is True
    assert _malformed_lines(lines) == [
        "⚠ malformed w kurier_ids: 2",
        "⚠ malformed w courier_tiers: 1",
    ]


def test_malformed_line_never_leaks_names_or_pins(verify):
    """Linia ⚠ niesie WYŁĄCZNIE licznik — nigdy nazwiska ani PIN-u (parytet z
    raportami A-6: artefakty bramek nie mogą zawierać PII/sekretów)."""
    ok, lines = verify({"kurier_piny": {"1234": ALIAS, "4242": [OTHER_ALIAS]}})
    for line in _malformed_lines(lines):
        assert OTHER_ALIAS not in line and OTHER_NAME not in line
        assert "4242" not in line


# --------------------------------------------------------------------------- #
# (3) Root nieczytelny / nie-mapa — nie wywraca skanu i nie udaje sukcesu
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("root", sorted(ROOT_CONSTS))
def test_root_that_is_not_a_mapping_fails_closed(verify, root):
    """Root, który nie jest obiektem JSON (lista), to „nie umiem tego odczytać",
    a nie „nie ma tam konfliktu" — check tego roota ✗, ZERO wyjątków w górę.

    Na masterze ``kurier_piny`` jako lista wywalał ``AttributeError`` przez
    ``scan_once`` do ``main()`` (żaden nie ma except) — jeden zepsuty root
    zabijał CAŁY skan auto-parowania.
    """
    ok, lines = verify({root: ["nie", "mapa"]})
    assert ok is False
    assert _line_for(lines, ROOT_CHECK[root]).startswith("✗")


def test_unreadable_json_does_not_raise(verify):
    """Uszkodzony bajtowo plik = fail-soft I/O (jak dziś ``_read_json``): brak
    wyjątku, brak fałszywego ✓ dla rootów, które z niego wynikają."""
    ok, lines = verify({"kurier_piny": "{to nie jest json"})
    assert ok is False
    assert _line_for(lines, ROOT_CHECK["kurier_piny"]).startswith("✗")


# --------------------------------------------------------------------------- #
# (4) Przynależność zepsutego rekordu — formy, które int()-owy czytelnik zwija
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,expected", [
    (CID, True),
    (str(CID), True),
    ("0" + str(CID), True),        # leading zero — int() widzi TEN SAM CID
    ("+" + str(CID), True),        # znak — jw.
    (" " + str(CID) + " ", True),  # whitespace — jw.
    (float(CID), True),            # 9001.0 — jw.
    (True, False),                 # bool NIGDY nie jest CID-em (podklasa int)
    (OTHER_CID, False),
    ("nie-cid", False),
    ([CID], False),
    (None, False),
])
def test_refers_to_audited_cid_covers_reader_folding(value, expected):
    """Przynależność rekordu liczy się po TYCH SAMYCH formach, które zwija
    ``int()`` u czytelników (``manual_overrides``, ``courier_availability``) —
    inaczej rozdarcie własnej tożsamości ('017' vs '17') zostałoby zaraportowane
    jako cudzy rekord ⚠ zamiast ✗."""
    assert ncp._refers_to_audited_cid(value, str(CID)) is expected


def test_refers_to_audited_cid_with_non_numeric_audited_cid():
    """Audytowany CID spoza liczb: dopuszczalne wyłącznie dokładne dopasowanie —
    i ZERO wyjątku w górę (verifier nie ma prawa wywrócić skanu)."""
    assert ncp._refers_to_audited_cid("abc", "abc") is True
    assert ncp._refers_to_audited_cid(7, "abc") is False


# --------------------------------------------------------------------------- #
# (5) RATCHET — delegacja do identity.schema, komplet rootów
# --------------------------------------------------------------------------- #

def _verify_ast():
    return ast.parse(textwrap.dedent(inspect.getsource(ncp.verify_courier_wired))).body[0]


def _scan_calls():
    """Wywołania ``_scan_root`` wewnątrz ``verify_courier_wired``."""
    return [
        node for node in ast.walk(_verify_ast())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_scan_root"
    ]


def test_ratchet_every_read_root_is_validated():
    """RATCHET: verifier waliduje KOMPLET czytanych rootów.

    Usunięcie walidacji choćby jednego roota (mutacja M2) = RED. Zbiór jest
    porównywany na równość, więc dołożenie siódmego roota BEZ walidacji też
    zapala się na czerwono.
    """
    scanned = set()
    for call in _scan_calls():
        assert call.args and isinstance(call.args[0], ast.Constant), (
            "pierwszym argumentem _scan_root musi być literał id roota"
        )
        scanned.add(call.args[0].value)
    assert scanned == set(ROOT_CONSTS), (
        f"verifier waliduje {sorted(scanned)}, a czyta {sorted(ROOT_CONSTS)}"
    )


def test_ratchet_validators_are_delegated_to_identity_schema():
    """RATCHET: walidator KAŻDEGO roota to atrybut ``identity_schema`` (mutacja
    M4: kopia reguł inline / lokalny lambda zamiast delegacji = RED)."""
    calls = _scan_calls()
    assert len(calls) == len(ROOT_CONSTS), (
        f"verifier ma {len(calls)} wywołań _scan_root, a czyta "
        f"{len(ROOT_CONSTS)} rootów"
    )
    for call in calls:
        assert len(call.args) >= 3, "_scan_root(root, store, validator, relates)"
        validator = call.args[2]
        assert isinstance(validator, ast.Attribute), (
            f"walidator roota {call.args[0].value!r} nie jest delegacją do "
            f"identity.schema (kopia inline?)"
        )
        assert isinstance(validator.value, ast.Name)
        assert validator.value.id == "identity_schema", (
            f"walidator roota {call.args[0].value!r} pochodzi z "
            f"{ast.dump(validator.value)}, a nie z identity.schema"
        )
        assert hasattr(identity_schema, validator.attr), (
            f"identity.schema nie ma walidatora {validator.attr!r}"
        )


def test_ratchet_verifier_has_no_inline_cid_form_policy():
    """RATCHET: verifier nie odtwarza polityki KSZTAŁTU CID/PIN u siebie.

    Formę kanoniczną („same cyfry ASCII", „bez zer wiodących", „4 cyfry")
    trzyma WYŁĄCZNIE ``identity.schema``. Powrót ``isdigit``/``isascii`` do
    ścieżki verifiera = powrót drugiego właściciela tej samej prawdy.
    """
    sources = [inspect.getsource(fn) for fn in (
        ncp.verify_courier_wired, ncp._scan_root, ncp._refers_to_audited_cid)]
    for src in sources:
        for banned in ("isdigit", "isascii"):
            assert banned not in src, f"inline polityka formy CID/PIN: {banned}"


def test_ratchet_dispatch_check_is_computed_on_the_whole_store():
    """RATCHET (F1): check dyspozytorni NIE liczy się na przefiltrowanym magazynie.

    ``kids.healthy`` to odpowiedź na pytanie „które wiersze zobaczy czytelnik
    PER REKORD". ``resolve_cid`` nie jest takim czytelnikiem — rozstrzyga po
    całym magazynie, więc podanie mu podzbioru zmienia odpowiedź na
    KORZYSTNIEJSZĄ niż produkcyjna (iter1: fałszywe ✓). Powrót ``kids.healthy``
    do ``verify_courier_wired`` = powrót defektu.
    """
    used = {node.attr for node in ast.walk(_verify_ast())
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "kids"}
    assert "healthy" not in used, (
        "verify_courier_wired czyta kids.healthy — check dyspozytorni musi "
        "liczyć się na magazynie, który widzi resolve_cid (kids.store)"
    )
    assert "store" in used, "check dyspozytorni nie czyta surowego magazynu"


def test_ratchet_schema_record_validators_delegate_cid_decision():
    """RATCHET: walidatory rekordów w ``identity.schema`` rozstrzygają CID przez
    ``canonical_numeric_cid`` — jeden owner formy CID dla writerów (G4/G7),
    rostera (G3) i verifiera (G6)."""
    cid_validators = ("ids_record_issue", "tiers_record_issue",
                      "courier_names_record_issue", "grafik_record_issue")
    for fname in cid_validators:
        fn = getattr(identity_schema, fname)
        src = textwrap.dedent(inspect.getsource(fn))
        calls = {
            node.func.id
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "canonical_numeric_cid" in calls, (
            f"{fname} nie deleguje decyzji o CID do canonical_numeric_cid"
        )


# --------------------------------------------------------------------------- #
# (6) Czytelnik KONKURENCYJNY (kurier_ids -> resolve_cid) — oracle F1
# --------------------------------------------------------------------------- #
# Verdykt blind review: check dyspozytorni liczony na PRZEFILTROWANYM magazynie
# melduje ✓ tam, gdzie PRODUKCYJNY resolve_cid resolwuje audytowane imię na
# cudzy cid albo na nic. Poniższe scenariusze to oracle negatywny (S1/S2),
# parytet (S3) i niezmiennik kierunkowy „audyt nigdy nie zieleńszy niż czytelnik".

SHADOW_KEY = "Jan Kow"      # score 30 ('Kowalski'.startswith('Kow')) > ALIAS 'Jan Ko' = 20
SHADOW_CID = -5             # zepsuta wartość (canonical_numeric_cid -> None)

#: S1 — obcy ZEPSUTY rekord wygrywa score-fallback nad zdrowym rekordem ofiary.
S1_SHADOW_WINS = {ALIAS: str(CID), SHADOW_KEY: SHADOW_CID}
#: S2 — obcy ZEPSUTY rekord tworzy REMIS w pełnym magazynie (czytelnik -> None).
S2_TIE = {SHADOW_KEY: str(CID), SHADOW_KEY + " ": SHADOW_CID}
#: S3 — ten sam kształt co S1, ale wartości int (dzisiejszy typ w żywym pliku).
S3_INT_VALUES = {ALIAS: CID, SHADOW_KEY: SHADOW_CID}
#: Obcy zepsuty rekord, który NIE zmienia odpowiedzi czytelnika (exact match).
S4_HARMLESS = {ALIAS: CID, NAME: CID, OTHER_ALIAS: ["9002"]}


def _reader_sees(store, full_name=NAME):
    """Odpowiedź PRODUKCYJNEGO czytelnika name->cid na tym samym magazynie.

    Odtwarza dokładnie parę ``_load_kurier_ids`` (``{str(k): str(v)}``, zero
    walidacji) + ``resolve_cid`` z ``shift_notifications.worker``. To jest
    definicja „widoczny dla dyspozytorni", względem której mierzymy audyt.
    """
    coerced = ({str(k): str(v) for k, v in store.items()}
               if isinstance(store, dict) else {})
    return real_resolve_cid(full_name, coerced)


def _dispatch_line(lines):
    return _line_for(lines, ROOT_CHECK["kurier_ids"])


def test_reader_ground_truth_malformed_foreign_row_is_not_invisible(monkeypatch,
                                                                    tmp_path):
    """FAKT O PRODUKCJI (fundament F1): dla ``resolve_cid`` zepsuty wiersz CUDZY
    nie jest niewidzialny — wygrywa score-fallback (S1) albo tworzy remis (S2).

    Ten test nie mierzy verifiera. Zamraża zachowanie czytelnika, na którym
    opiera się cały oracle: gdyby ``resolve_cid`` kiedyś zaczął pomijać wiersze
    POJEDYNCZO, poniższe asercje zapalą się i trzeba przemyśleć kontrakt audytu.
    """
    monkeypatch.setattr(sn_state, "MATCH_DEBUG_LOG",
                        tmp_path / "courier_match_debug.jsonl")
    assert _reader_sees({ALIAS: str(CID)}) == str(CID)      # sam zdrowy wiersz
    assert _reader_sees(S1_SHADOW_WINS) == str(SHADOW_CID)  # zepsuty obcy WYGRYWA
    assert _reader_sees(S2_TIE) is None                     # remis -> niewidzialny
    assert _reader_sees(S3_INT_VALUES) == str(SHADOW_CID)
    assert _reader_sees(S4_HARMLESS) == str(CID)            # exact match nietknięty


def test_foreign_malformed_record_hijacking_resolve_cid_fails_closed(verify):
    """S1: obcy zepsuty rekord WYGRYWA score-fallback → check dyspozytorni ✗.

    ``resolve_cid`` rozstrzyga po CAŁYM magazynie, więc odfiltrowanie zepsutego
    wiersza przed checkiem daje odpowiedź KORZYSTNIEJSZĄ niż produkcyjna: audyt
    mówi „✓ dyspozytornia", a dyspozytornia resolwuje to imię na ``-5``.
    Fałszywe ✓ jest ostatnią bramką ``_auto_wire`` — kończy się DM „kurier
    wpięty, widoczny w:" nad tożsamością, której realny czytelnik nie widzi.
    """
    ok, lines = verify({"kurier_ids": S1_SHADOW_WINS})
    assert _reader_sees(S1_SHADOW_WINS) != str(CID), "scenariusz stracił sens"
    assert _dispatch_line(lines).startswith("✗")
    assert ok is False
    # Rekord jest nadal RAPORTOWANY jako zepsuty-obcy (licznik bez nazwisk).
    assert "⚠ malformed w kurier_ids: 1" in lines


def test_foreign_malformed_record_causing_tie_fails_closed(verify):
    """S2: obcy zepsuty rekord tworzy REMIS w pełnym magazynie → check ✗.

    Remis w ``resolve_cid`` = ``None`` = kurier NIEWIDZIALNY dla dyspozytorni.
    W przefiltrowanym magazynie remisu nie ma (zepsuty wiersz zniknął), więc
    audyt widział jednoznacznego zwycięzcę i meldował ✓.
    """
    ok, lines = verify({"kurier_ids": S2_TIE})
    assert _reader_sees(S2_TIE) is None, "scenariusz stracił sens"
    assert _dispatch_line(lines).startswith("✗")
    assert ok is False
    assert "⚠ malformed w kurier_ids: 1" in lines


def test_foreign_malformed_record_that_changes_nothing_keeps_ok(verify):
    """Kontrola kierunku: obcy zepsuty rekord, który NIE zmienia odpowiedzi
    czytelnika (tu: exact match na pełnym imieniu), nie blokuje wpięcia —
    zostaje sama linia ⚠ (kontrakt „narzędzie diagnostyczne", reskope CTO).

    Bez tego testu naprawa F1 mogłaby zdegenerować się do „każdy ⚠ zeruje
    all_ok", czyli do zmiany kontraktu, na którą nikt nie dał ACK.
    """
    ok, lines = verify({"kurier_ids": S4_HARMLESS})
    assert _reader_sees(S4_HARMLESS) == str(CID)
    assert lines[:len(MASTER_LINES)] == MASTER_LINES
    assert ok is True
    assert "⚠ malformed w kurier_ids: 1" in lines


def test_int_valued_store_with_shadow_row_matches_master(verify):
    """S3 (parytet, nie regresja): ten sam kształt co S1, ale wartości int —
    czyli dzisiejszy typ w żywym ``kurier_ids.json`` (125/125 int).

    Tu master, iter1 i iter2 dają TO SAMO ✗: ramię ``resolve_cid(...) == cid_s``
    porównuje surową wartość (int) ze stringiem, więc na danych int trzyma się
    wyłącznie ramię ``store.get(full_name) == cid`` (klucz pełnego imienia).
    Zachowanie odziedziczone po masterze (obserwacja recenzenta, nie finding) —
    kierunek fail-CLOSED, więc iter2 go NIE zmienia; test zamraża ten stan, żeby
    ewentualna przyszła zmiana ramienia była świadoma, a nie uboczna.
    """
    ok, lines = verify({"kurier_ids": S3_INT_VALUES})
    assert _dispatch_line(lines).startswith("✗")
    assert ok is False


#: Magazyny kurier_ids do niezmiennika kierunkowego (zdrowe, zepsute, graniczne).
READER_PARITY_STORES = [
    pytest.param({ALIAS: str(CID)}, id="healthy-alias-str"),
    pytest.param({ALIAS: CID, NAME: CID}, id="healthy-alias-and-full-int"),
    pytest.param({NAME: str(CID)}, id="healthy-full-str"),
    pytest.param(S1_SHADOW_WINS, id="S1-shadow-malformed-wins"),
    pytest.param(S2_TIE, id="S2-tie-in-full-store"),
    pytest.param(S3_INT_VALUES, id="S3-shadow-int-values"),
    pytest.param(S4_HARMLESS, id="S4-foreign-malformed-harmless"),
    pytest.param({ALIAS: "0" + str(CID), NAME: CID}, id="own-record-noncanonical"),
    pytest.param({ALIAS: str(CID), "Ktos Inny": "0" + str(CID)},
                 id="foreign-row-folding-to-own-cid"),
    pytest.param({}, id="empty-store"),
    pytest.param(["nie", "mapa"], id="not-a-mapping"),
]


@pytest.mark.parametrize("store", READER_PARITY_STORES)
def test_dispatch_check_is_never_greener_than_the_reader(verify, store):
    """NIEZMIENNIK F1: ✓ dyspozytornia ⇒ produkcyjny ``resolve_cid`` NAPRAWDĘ
    resolwuje audytowane imię na audytowany cid.

    Implikacja jest jednokierunkowa świadomie: audyt WOLNO mieć ostrzejszy
    (fail-closed przy zepsutym rekordzie własnej tożsamości albo przy int-owym
    ramieniu odziedziczonym po masterze), nie wolno mu być ŁAGODNIEJSZY. To jest
    dokładnie ta własność, którą łamał iter1 — i której nie mierzył żaden test,
    bo stub ``resolve_cid`` omijał score-fallback.
    """
    _ok, lines = verify({"kurier_ids": store})
    if _dispatch_line(lines).startswith("✓"):
        assert _reader_sees(store) == str(CID), (
            f"audyt melduje ✓ dyspozytornia, a czytelnik widzi "
            f"{_reader_sees(store)!r} zamiast {str(CID)!r}"
        )
