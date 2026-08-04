"""A-6/G3 (K4) — jeden walidowany owner roster CID→nazwa dla WSZYSTKICH 4 konsumentów.

Bramka: engine.a6-resolver-shared-validators

DEFEKT na masterze (RED tu, GREEN na kandydacie):
  K4 — roster {cid: nazwa} był budowany przez CZTERY bajtowo-bliźniacze kopie
       ``_load_courier_names`` (courier_resolver, courier_ranking, sla_tracker,
       telegram_approver). KAŻDA kluczowała roster surowym ``str(cid)`` BEZ
       walidacji CID → True→"True", []→"[]", " 17 " (whitespace), "017"
       (leading zero), -5, 0 trafiały jako niekanoniczne klucze CID do
       publikowanej floty. Iter1 utwardził tylko 1 z 4 (twin-path defect).

Wariant ownera (04.08): FAIL-SOFT + walidacja wartości we WSZYSTKICH 4. Budowa
roster delegowana do JEDNEGO ownera ``identity.roster.load_courier_names``,
który waliduje CID przez ``identity.schema.canonical_numeric_cid`` (parytet z
onboardingiem G4 / grafikiem G7). Niekanoniczny rekord jest POMIJANY pojedynczo,
zdrowe rekordy zostają, dostępność floty na gorącej ścieżce bez zmiany. Fail-soft
I/O plików zachowany.

Ten plik pokrywa: (1) oracle negatywny per KAŻDY z 4 konsumentów; (2) parytet
zdrowych danych per konsument; (3) fail-soft I/O; (4) flaga grafiku ON<->OFF
(tylko resolver); (5) RATCHET blokujący powrót ``str(cid)`` w którymkolwiek z 4.

Hermetyczny: stałe ścieżek zmonkeypatchowane na tmp, flag → lambda; zero I/O do
żywego dispatch_state.
"""
from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path

import pytest

from dispatch_v2 import courier_ranking as RK
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import sla_tracker as SLA
from dispatch_v2 import telegram_approver as TG
from dispatch_v2.identity import roster as ROSTER
from dispatch_v2.identity.schema import canonical_numeric_cid


# --- opis 4 konsumentów ------------------------------------------------------
# (modul, etykieta, czyta_grafik, typ_sciezki)
#   typ_sciezki: master czyta pliki różnie (open(str) vs Path.read_text) — stałe
#   monkeypatchujemy TYM SAMYM typem co oryginał, żeby oracle był RED na masterze
#   (patch str tam gdzie master robi Path.read_text ukryłby defekt = fałszywy GREEN).
CONSUMERS = [
    pytest.param(CR, "courier_resolver", True, str, id="courier_resolver"),
    pytest.param(RK, "courier_ranking", False, str, id="courier_ranking"),
    pytest.param(SLA, "sla_tracker", False, Path, id="sla_tracker"),
    pytest.param(TG, "telegram_approver", False, str, id="telegram_approver"),
]


def _write(tmp_path, fname, data, path_type):
    """Zapisz źródło do tmp (str = surowy tekst, dict = JSON). None → ścieżka
    do nieistniejącego pliku (gałąź fail-soft I/O). Zwraca ścieżkę path_type."""
    if data is None:
        return path_type(str(tmp_path / f"{fname}_absent.json"))
    p = tmp_path / fname
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path_type(str(p))


def _seed(mod, tmp_path, monkeypatch, path_type, *, kids=None, names=None,
          gfn=None, reads_grafik=False, gfn_flag=True):
    """Zmonkeypatchuj stałe ścieżek konsumenta + (resolver) flagę + reset cache."""
    monkeypatch.setattr(mod, "KURIER_IDS_PATH",
                        _write(tmp_path, "kurier_ids.json", kids, path_type))
    monkeypatch.setattr(mod, "COURIER_NAMES_PATH",
                        _write(tmp_path, "courier_names.json", names, path_type))
    if reads_grafik:
        monkeypatch.setattr(mod, "GRAFIK_FULL_NAMES_PATH",
                            _write(tmp_path, "grafik_full_names.json", gfn, str))
        monkeypatch.setattr(
            mod, "flag",
            lambda name, default=None: gfn_flag
            if name == "ENABLE_GRAFIK_FULL_NAMES_SOURCE" else default,
        )
    # telegram_approver cache'uje roster modułowo — reset przed każdym wywołaniem
    if hasattr(mod, "_courier_names_cache"):
        monkeypatch.setattr(mod, "_courier_names_cache", None)


# --- (1) ORACLE NEGATYWNY per KAŻDY z 4 konsumentów --------------------------
@pytest.mark.parametrize("mod,label,reads_grafik,path_type", CONSUMERS)
def test_noncanonical_cid_skipped_all_consumers(mod, label, reads_grafik,
                                                path_type, tmp_path, monkeypatch):
    """RED na masterze dla KAŻDEGO z 4: śmieciowe klucze CID trafiają do rostera.

    GREEN na kandydacie: każdy niekanoniczny rekord pominięty pojedynczo, zdrowe
    rekordy z kurier_ids + courier_names (+ grafik dla resolvera) zachowane.
    """
    _seed(
        mod, tmp_path, monkeypatch, path_type,
        # kurier_ids = {name: cid}; cid jako wartość
        kids={"Ghost": True, "Lister": [], "Spacey": " 17 ", "Good A": 99},
        # courier_names = {cid_str: name}; cid jako KLUCZ (JSON → zawsze string)
        names={" 17 ": "Spacey B", "017": "LeadZero", "true": "WordBool",
               "88": "Good B"},
        # grafik_full_names = {full_name: cid}; cid jako wartość (tylko resolver)
        gfn={"Bad Bool Person": True, "Neg Person": -5, "Zero Person": 0,
             "Good C": 42} if reads_grafik else None,
        reads_grafik=reads_grafik,
    )
    roster = mod._load_courier_names()

    # zdrowe rekordy ZOSTAJĄ
    assert roster.get("99") == "Good A"
    assert roster.get("88") == "Good B"
    if reads_grafik:
        assert roster.get("42") == "Good C"

    # ŻADEN niekanoniczny klucz nie trafił do floty (to jest defekt K4)
    garbage = ["True", "[]", " 17 ", "017", "true", "-5", "0", "False"]
    present = [k for k in garbage if k in roster]
    assert present == [], f"{label}: niekanoniczne klucze CID w rosterze: {present}"

    # wszystkie klucze rostera są kanonicznymi CID
    noncanon = [k for k in roster if canonical_numeric_cid(k) is None]
    assert noncanon == [], f"{label}: roster ma niekanoniczne klucze: {noncanon}"


# --- (2) PARYTET zdrowych danych per konsument ------------------------------
@pytest.mark.parametrize("mod,label,reads_grafik,path_type", CONSUMERS)
def test_parity_healthy_data_per_consumer(mod, label, reads_grafik, path_type,
                                          tmp_path, monkeypatch):
    """Zdrowe dane → roster identyczny z semantyką str(cid). Priorytet bez zmian:
    courier_names > kurier_ids (grafik nadpisuje oba, tylko resolver)."""
    _seed(
        mod, tmp_path, monkeypatch, path_type,
        kids={"Adrian": 21, "Rutcom": 23, "Bartek": 123},
        names={"123": "Bartek O.", "101": "Rafał Jabłoński"},
        gfn={"Adrian Citko": 457, "Adrian": 21} if reads_grafik else None,
        reads_grafik=reads_grafik,
    )
    roster = mod._load_courier_names()
    expected = {
        "21": "Adrian",            # kurier_ids (grafik nadpisze na resolverze — ta sama wartość)
        "23": "Rutcom",            # tylko kurier_ids
        "123": "Bartek O.",        # courier_names wygrał nad kurier_ids
        "101": "Rafał Jabłoński",  # tylko courier_names
    }
    if reads_grafik:
        expected["457"] = "Adrian Citko"  # tylko grafik
    assert roster == expected, f"{label}: parytet zdrowych danych"


# --- (3) FAIL-SOFT I/O -------------------------------------------------------
@pytest.mark.parametrize("mod,label,reads_grafik,path_type", CONSUMERS)
def test_failsoft_bad_file_partial_roster(mod, label, reads_grafik, path_type,
                                          tmp_path, monkeypatch):
    """Zepsuty (nie-JSON) plik courier_names → roster z kurier_ids (owner: zły
    plik → roster z reszty, NIE fail-closed)."""
    _seed(
        mod, tmp_path, monkeypatch, path_type,
        kids={"Adrian": 21},
        names="{ this is not valid json ]]",
        gfn={"Adrian Citko": 457} if reads_grafik else None,
        reads_grafik=reads_grafik,
    )
    roster = mod._load_courier_names()
    assert roster.get("21") == "Adrian", f"{label}: zdrowy kurier_ids przetrwał zły plik"
    assert canonical_numeric_cid  # sanity
    if reads_grafik:
        assert roster.get("457") == "Adrian Citko"


@pytest.mark.parametrize("mod,label,reads_grafik,path_type", CONSUMERS)
def test_failsoft_missing_files_empty_roster(mod, label, reads_grafik, path_type,
                                             tmp_path, monkeypatch):
    """Wszystkie źródła nieobecne → pusty roster, bez wyjątku (jak teraz)."""
    _seed(mod, tmp_path, monkeypatch, path_type,
          kids=None, names=None, gfn=None, reads_grafik=reads_grafik)
    assert mod._load_courier_names() == {}, f"{label}: brak plików → pusty roster"


# --- (4) FLAGA grafiku ON<->OFF (tylko resolver — jedyny czytający grafik) ---
def test_grafik_flag_off_source_skipped(tmp_path, monkeypatch):
    _seed(
        CR, tmp_path, monkeypatch, str,
        kids={"Adrian": 21}, names=None,
        gfn={"Adrian Citko": 457}, reads_grafik=True, gfn_flag=False,
    )
    assert CR._load_courier_names() == {"21": "Adrian"}


def test_grafik_flag_on_source_included(tmp_path, monkeypatch):
    _seed(
        CR, tmp_path, monkeypatch, str,
        kids={"Adrian": 21}, names=None,
        gfn={"Adrian Citko": 457}, reads_grafik=True, gfn_flag=True,
    )
    assert CR._load_courier_names() == {"21": "Adrian", "457": "Adrian Citko"}


# --- (5) RATCHET: wszystkie 4 delegują do ownera, żaden nie wraca do str(cid) -
def _func_ast(mod):
    src = textwrap.dedent(inspect.getsource(mod._load_courier_names))
    return ast.parse(src).body[0]


def _call_names(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


@pytest.mark.parametrize("mod,label,reads_grafik,path_type", CONSUMERS)
def test_ratchet_delegates_to_shared_owner(mod, label, reads_grafik, path_type):
    """RATCHET: każdy z 4 ``_load_courier_names`` MUSI wołać shared owner
    ``load_courier_names`` (import z identity.roster). RED gdy konsument wraca do
    własnej budowy roster."""
    fn = _func_ast(mod)
    assert "load_courier_names" in _call_names(fn), (
        f"{label}: _load_courier_names NIE deleguje do identity.roster."
        f"load_courier_names (możliwy powrót zduplikowanej budowy roster)"
    )


@pytest.mark.parametrize("mod,label,reads_grafik,path_type", CONSUMERS)
def test_ratchet_no_raw_strcid_roster_key(mod, label, reads_grafik, path_type):
    """RATCHET: żaden z 4 konsumentów nie może reintrodukować klucza rostera
    ``merged[str(cid)] = ...`` (surowy str(cid) bez walidacji = defekt K4)."""
    fn = _func_ast(mod)
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Store):
            idx = sub.slice
            bad = (
                isinstance(idx, ast.Call)
                and isinstance(idx.func, ast.Name)
                and idx.func.id == "str"
            )
            assert not bad, (
                f"{label}: reintrodukowano surowy klucz rostera str(cid) "
                f"(defekt K4 K-powrót)"
            )


def test_shared_owner_uses_canonical_validator():
    """Owner prawdy MUSI walidować CID kanonicznym walidatorem (nie str())."""
    src = inspect.getsource(ROSTER.load_courier_names)
    assert "canonical_numeric_cid" in src, (
        "identity.roster.load_courier_names nie używa canonical_numeric_cid "
        "(jeden owner walidacji CID rozspójniony)"
    )
    # klucz rostera nie może być surowym str(cid)
    fn = ast.parse(textwrap.dedent(src)).body[0]
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Store):
            idx = sub.slice
            assert not (
                isinstance(idx, ast.Call)
                and isinstance(idx.func, ast.Name)
                and idx.func.id == "str"
            ), "owner używa surowego str(cid) jako klucza rostera"
