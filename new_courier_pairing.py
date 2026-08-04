"""New-courier auto-pairing (2026-06-06).

Detect couriers SCHEDULED in the grafik that are not yet wired into the dispatch
roster, resolve their REAL gastro ``id_kurier`` (cid), then either auto-wire them
or DM the ziomek group to confirm — so a new courier becomes visible everywhere
(dispatch proposals, scoring, COD/daily-accounting, courier-app PIN) without
manual file edits, and Adrian gets the PIN on Telegram.

Pipeline:
  trigger  = grafik (schedule_utils.load_schedule — source of truth "who works today")
  cid      = panel_roster (gastro /admin2017/list-users, ACTIVE couriers only)
  wiring   = courier_admin.add_new_courier (atomic 4-file write:
             kurier_ids + kurier_piny + courier_tiers + daily_accounting/kurier_full_names)

Triple safety gate before an AUTO write ("żeby nie było bugów"):
  1. The name has a REAL shift entry today (dict with 'start') — NOT a ``None``
     placeholder row in the sheet. (Avoids resurrecting removed couriers such as
     "Albert Dec", who sits in the grafik as ``None``.)
  2. Exactly one CONFIDENT match in the ACTIVE gastro roster (cid is a real
     id_kurier, never a phantom; ambiguous/none -> ask, never guess).
  3. Post-write self-verification confirms visibility in all 4 subsystems.

Anything uncertain -> Telegram message (once/day per name) asking Adrian to run
``/nowy <cid> <Imię Nazwisko>``.

Flags (flags.json, hot-reload):
  NEW_COURIER_AUTOPAIR_ENABLED   master on/off (default False until ACK'd live)
  NEW_COURIER_AUTOPAIR_AUTOWRITE True = auto-add confident matches; False =
                                 ask-only (detect + DM, never writes). Default True.
  NEW_COURIER_AUTOPAIR_BARE_KEY_STRICT
                                 True (default) = bramka "już zmapowany" NIE ufa
                                 trafieniu przez goły klucz-imię w kurier_ids
                                 ('Gabriel'->179): taki kandydat idzie normalną
                                 ścieżką match-w-rosterze. False = stare zachowanie
                                 (rollback hot przez flags.json).

Run: ``python -m dispatch_v2.new_courier_pairing [--dry-run] [--once]``
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import namedtuple
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

_SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from dispatch_v2.common import flag, setup_logger
from dispatch_v2 import panel_roster
from dispatch_v2.courier_admin import (
    COURIER_NAMES, add_new_courier, derive_alias,
)
from dispatch_v2.identity import schema as identity_schema
from dispatch_v2.identity.normalize import norm
from dispatch_v2.identity.schema import canonical_courier_id

WARSAW = ZoneInfo("Europe/Warsaw")
LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"
_log = setup_logger("new_courier_pairing", LOG_DIR + "new_courier_pairing.log")

STATE_PATH = "/root/.openclaw/workspace/dispatch_state/new_courier_pairing_state.json"
KURIER_IDS = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
KURIER_PINY = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
COURIER_TIERS = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"
KURIER_FULL_NAMES = "/root/.openclaw/workspace/scripts/dispatch_v2/daily_accounting/kurier_full_names.json"
# PANEL-CANON (#1, 2026-06-10): autorytatywne {pełne imię: cid} czytane przez
# courier_resolver._load_courier_names (NAJWYŻSZY priorytet) + panel admin. #2
# (self-heal): auto-parowanie utrzymuje ten plik, by nowy/zmapowany kurier trafiał
# do źródła prawdy dispatchu → kolizja skrótów (Rafał Ja → AMBIGUOUS) nie wraca.
GRAFIK_FULL_NAMES = "/root/.openclaw/workspace/dispatch_state/grafik_full_names.json"

_STATE_RETENTION_DAYS = 7

# --- Module-level shims (tests monkey-patch these) ------------------------- #
load_schedule: Callable[[], Dict[str, Any]]
try:
    from schedule_utils import load_schedule as _load_schedule_real  # type: ignore
    load_schedule = _load_schedule_real
except Exception:  # pragma: no cover
    def load_schedule() -> Dict[str, Any]:  # type: ignore[no-redef]
        return {}

# resolve_cid + garbage filter live in the (otherwise dormant) shift_notifications
# worker — reuse them so name->cid logic stays identical across the codebase.
from dispatch_v2.shift_notifications.worker import (
    resolve_cid, _is_garbage_name, _load_ignored_names, _load_kurier_ids,
)
from dispatch_v2.shift_notifications.telegram_send import tg_send_text_with_keyboard


def _tg(text: str, *, silent: bool = False) -> None:
    """Send a plain message to the ziomek group (SHIFT_NOTIFY_TARGET_CHAT_ID).

    silent=True → cicha dostawa (bez pinga) dla rutynowych „LOW" zdarzeń
    (nowy kurier wpięty automatycznie). Konflikty/akcje zostają głośne.
    Best-effort: never raises.
    """
    try:
        tg_send_text_with_keyboard(text, [], disable_notification=silent)
    except Exception as e:  # noqa: BLE001
        _log.warning(f"_tg send fail: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# Idempotency state
# --------------------------------------------------------------------------- #


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        _log.warning(f"_load_state fail: {type(e).__name__}: {e}")
        return {}


def _save_state(state: dict) -> None:
    # Prune old days (keep last N).
    try:
        days = sorted(d for d in state.keys() if len(d) == 10 and d[4] == "-")
        for d in days[:-_STATE_RETENTION_DAYS]:
            state.pop(d, None)
    except Exception:
        pass
    d = os.path.dirname(STATE_PATH)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-ncp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_PATH)
    except Exception as e:  # noqa: BLE001
        if os.path.exists(tmp):
            os.unlink(tmp)
        _log.warning(f"_save_state fail: {type(e).__name__}: {e}")


def _day_bucket(state: dict, today: str) -> dict:
    b = state.setdefault(today, {})
    b.setdefault("paired", [])
    b.setdefault("alerted", [])
    return b


# --------------------------------------------------------------------------- #
# Post-write verification
# --------------------------------------------------------------------------- #


def _read_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


GRAFIK_OK = "ok"            # wpis już poprawny — nic nie zapisano
GRAFIK_HEALED = "healed"    # dopisano/poprawiono (self-heal)
GRAFIK_REFUSED = "refused"  # ODMOWA: CID nie należy do tej tożsamości


def _read_root_strict(path: str) -> dict:
    """Treść roota tożsamości albo WYJĄTEK — nigdy ciche `{}` (A-6/K7 iter2, F-1).

    BRAK pliku to TREŚĆ (root pusty) i jest bezpieczny. Każda inna awaria —
    EIO, uprawnienia, niepoprawny JSON, obiekt innego typu — MUSI podnieść,
    bo „nie umiem odczytać rootu" nie może być nieodróżnialne od „w roocie nie
    ma konfliktu". Ten drugi przypadek jest zgodą na zapis, pierwszy nie jest.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root tożsamości nie jest obiektem JSON")
    return data


def _grafik_cid_value(cid_s: str):
    """Wartość zapisywana w grafiku dla kanonicznego `cid_s`, albo None = ODMOWA.

    NIEZMIENNIK (A-6/K7 iter3, G-1): reprezentacja SPRAWDZANA jest tożsama z
    ZAPISYWANĄ — `canonical_courier_id(zapis) == cid_s`. `canonical_courier_id`
    jest bramką TYPU, nie kanonizatorem WARTOŚCI: `'017'`, `'１７'`, `'١٧'`
    przechodzą ją bez zmiany, więc wszystkie checki własności (klucze
    `courier_names`/`courier_tiers`, guard duplikatu w grafiku) liczą się na tym
    stringu, a `int()` zwija go do INNEJ tożsamości. To jest literalny rdzeń
    klasy K7 — jedna literówka omijała wszystkie trzy checki cross-root.
    Dlatego wartość do zapisu wyprowadzamy w JEDNYM miejscu i odrzucamy każdą,
    która nie odczytuje się z powrotem jako dokładnie ten sprawdzony CID.

    `str.isdigit()` jest True także dla znaków kategorii No (`'²'`), których
    `int()` nie przyjmuje — ValueError łapiemy TU (G-2), bo poza `try` leciał
    przez `scan_once` do `main()` (żaden nie ma `except`) i wywalał CAŁY skan,
    podczas gdy baza degradowała łagodnie.

    Warunek jest JEDEN i obejmuje CAŁĄ przestrzeń wejść — grafik przyjmuje
    wyłącznie kanoniczną dziesiętną formę liczby całkowitej (R-1). Wcześniej
    wartości nieliczbowe szły osobną gałęzią, w której round-trip był
    TRYWIALNIE prawdziwy (zapisywano samo `cid_s`), więc niezmiennik był tam
    martwy: `'+17'` i `'1_7'` nie są `isdigit`, ale `int()` czytelnika je
    PRZYJMUJE (`manual_overrides._all_name_to_cid` robi `int(cid)`), więc
    writer utrwalał string, a czytelnik widział CID ofiary. Ten sam wymóg ma
    już najsurowszy konsument grafiku: `courier_availability._canon_cid` żąda
    `isdigit`, a na pierwszym wpisie bez tego `_schedule_names` zwraca
    `{}, 'grafik_identity_invalid_cid'` — czyli kasuje tożsamość grafiku dla
    WSZYSTKICH kurierów, nie tylko dla zatrutego wpisu.
    """
    if not cid_s.isdigit():
        return None
    try:
        value = int(cid_s)
    except (ValueError, TypeError):
        return None
    return value if canonical_courier_id(value) == cid_s else None


def _cid_identity_set(cid_s: str, kids: dict) -> set:
    """Zbiór tożsamości danego CID wg kanonicznego rejestru kurier_ids.

    kurier_ids trzyma też aliasy wtórne (Lekcja #78), więc jeden CID ma zwykle
    kilka kluczy ("Darek os" i "Darek Osmólski"). Porównujemy przez `norm`, bo
    rejestr bywa zapisany inną wielkością liter niż grafik, a wartości przez
    ten sam `canonical_courier_id`, którym bramkujemy wejście — inaczej można
    dopasować się jedną reprezentacją, a zapisać inną.
    """
    return {norm(k) for k, v in kids.items() if canonical_courier_id(v) == cid_s}


def _grafik_ownership_refusal(full_name: str, cid_s: str, grafik: dict) -> Optional[str]:
    """None = wolno zapisać; tekst = powód odmowy.

    Grafik jest JEDNOZNACZNYM wiązaniem cid↔pełne imię, więc wolno w nim utrwalić
    wyłącznie wiązanie już potwierdzone w kanonicznym rejestrze tożsamości.

    ŚWIADOMIE NIE sprawdzamy `kurier_full_names` — jest kluczowany SKRÓTEM, który
    legalnie koliduje ("Rafał Ja" → Jankowski|Jabłoński). Rozstrzyganie tej
    kolizji to powód istnienia grafiku; traktowanie jej jako konfliktu odmawiałoby
    zapisu realnym kurierom (pomiar 04.08 na żywych danych: 2 z 58).

    Rooty konfliktu czytamy STRICT: nieczytelny root podnosi wyjątek, który
    wołający zamienia na odmowę (F-1). Dowód własności też — nieczytelny
    `kurier_ids` nie może uchodzić za pusty rejestr.
    """
    identity = _cid_identity_set(cid_s, _load_kurier_ids())
    if not identity & {norm(full_name), norm(derive_alias(full_name))}:
        return (f"CID {cid_s} nie należy do {full_name!r} wg kurier_ids "
                f"(właściciele: {sorted(identity) or 'brak'})")
    owner = _read_root_strict(COURIER_NAMES).get(cid_s)
    if owner is not None and norm(owner) not in identity:
        return f"courier_names[{cid_s}]={owner!r} spoza tożsamości CID"
    entry = _read_root_strict(COURIER_TIERS).get(cid_s)
    if (isinstance(entry, dict) and entry.get("name") is not None
            and norm(entry["name"]) not in identity):
        return f"courier_tiers[{cid_s}].name={entry['name']!r} spoza tożsamości CID"
    taken = [g for g, c in grafik.items()
             if canonical_courier_id(c) == cid_s and norm(g) != norm(full_name)]
    if taken:
        return f"CID {cid_s} jest już w grafiku związany z {taken!r}"
    return None


def _ensure_grafik_full_name(full_name: str, cid) -> str:
    """Utrzymuj grafik_full_names.json = {pełne imię: cid} — źródło prawdy cid↔imię
    dla dispatchu (courier_resolver._load_courier_names, PANEL-CANON #1) i panelu.
    Zwraca GRAFIK_OK / GRAFIK_HEALED / GRAFIK_REFUSED. Atomic (temp+fsync+rename).

    Fail-soft zostaje WYŁĄCZNIE na samym zapisie grafiku (intencja pętli self-heal:
    błąd dysku nie blokuje skanu). Odmowa z powodu konfliktu własności ORAZ awaria
    odczytu rootów są twarde i widoczne: log ERROR + status REFUSED, który wołający
    raportuje operatorowi. Bez tego self-heal mógł po cichu związać CID należący do
    innej tożsamości (A-6/K7)."""
    # Bramka typu PRZED int(cid) (F-2): sprawdzana i zapisywana reprezentacja musi
    # być ta sama. canonical_courier_id odrzuca bool jawnie — inaczej check liczyłby
    # się na "True", a zapis utrwalał int(True)==1, przejmując cudzy CID.
    cid_s = canonical_courier_id(cid)
    if cid_s is None:
        _log.error(
            f"grafik_full_names ODMOWA zapisu {full_name!r} -> {cid!r}: "
            f"niekanoniczny CID (typ {type(cid).__name__})"
        )
        return GRAFIK_REFUSED
    # Bramka WARTOŚCI (G-1/G-2): dopiero ona domyka niezmiennik dla całej klasy,
    # a nie dla pojedynczego typu. Odmowa PRZED checkami własności, żeby forma
    # niekanoniczna nigdy nie trafiła do lookupów kluczowanych po cid_s.
    cid_val = _grafik_cid_value(cid_s)
    if cid_val is None:
        _log.error(
            f"grafik_full_names ODMOWA zapisu {full_name!r} -> {cid!r}: "
            f"CID {cid_s!r} nie jest w kanonicznej formie liczbowej — zapis "
            f"utrwaliłby inną tożsamość niż ta, którą sprawdzamy"
        )
        return GRAFIK_REFUSED
    try:
        grafik = _read_root_strict(GRAFIK_FULL_NAMES)
        refusal = _grafik_ownership_refusal(full_name, cid_s, grafik)
    except Exception as e:  # noqa: BLE001
        # Nieczytelny root != brak konfliktu. Nie wolno zgodzić się na zapis.
        refusal = f"nie można zweryfikować własności CID: {type(e).__name__}: {e}"
    if refusal is not None:
        _log.error(
            f"grafik_full_names ODMOWA zapisu {full_name!r} -> {cid!r}: {refusal}"
        )
        return GRAFIK_REFUSED
    try:
        data = grafik
        if canonical_courier_id(data.get(full_name)) == cid_s:
            return GRAFIK_OK  # już poprawne — no-op
        data[full_name] = cid_val
        _d = os.path.dirname(GRAFIK_FULL_NAMES)
        fd, tmp = tempfile.mkstemp(dir=_d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, GRAFIK_FULL_NAMES)
        _log.info(f"grafik_full_names self-heal: {full_name!r} -> {cid_val}")
        return GRAFIK_HEALED
    except Exception as e:  # noqa: BLE001
        _log.warning(f"_ensure_grafik_full_name fail {full_name!r}: {e}")
        return GRAFIK_OK  # fail-soft na I/O: nie blokuj scanu


def _refers_to_audited_cid(value, cid_s: str) -> bool:
    """Czy rekord mówi o audytowanej tożsamości — także w formie NIEKANONICZNEJ.

    Formę kanoniczną CID rozstrzyga WYŁĄCZNIE ``identity.schema``; tu pada inne
    pytanie: „czyj to rekord". Czytelnicy oparci o ``int()``
    (``manual_overrides._all_name_to_cid``, ``courier_availability._canon_cid``)
    zwijają ``'017'``, ``' 17 '``, ``'+17'`` i ``17.0`` do TEGO SAMEGO kuriera —
    więc taki wpis DOTYCZY audytowanej tożsamości i właśnie dlatego jest groźny:
    writer utrwalił jedną reprezentację, a kanoniczny czytelnik widzi inną albo
    nie widzi żadnej (klasa K7/G3). Rozstrzygnięcie jest świadomie SZERSZE niż
    kanon — lepiej zameldować ✗ nad rekordem, który tylko wygląda na nasz, niż
    przemilczeć rozdarcie własnej tożsamości.
    """
    if isinstance(value, bool):  # bool jest podklasą int — nigdy nie jest CID-em
        return False
    if canonical_courier_id(value) == cid_s:
        return True
    try:
        cid_num = int(cid_s)
    except (TypeError, ValueError):
        return False  # audytowany CID nieliczbowy — tylko dokładne dopasowanie
    text = str(value).strip()
    try:
        return int(text, 10) == cid_num
    except (TypeError, ValueError):
        pass
    try:
        as_float = float(text)
    except (TypeError, ValueError):
        return False
    return as_float.is_integer() and int(as_float) == cid_num


def _refers_to_audited_name(value, names: frozenset) -> bool:
    """Czy `value` to nazwa audytowanej tożsamości (pełne imię albo alias)."""
    return isinstance(value, str) and norm(value) in names


#: Wynik audytu jednego roota. ``healthy`` = rekordy, które kanoniczny czytelnik
#: naprawdę zobaczy (na nich liczone są checki), ``identity_broken`` = zepsuty
#: jest rekord audytowanej tożsamości, ``unrelated`` = licznik zepsutych rekordów
#: CUDZYCH tożsamości (raport, bez wpływu na werdykt).
_RootScan = namedtuple("_RootScan", "root healthy identity_broken unrelated")


def _scan_root(root: str, store, issue, relates) -> _RootScan:
    """Rozdziel rekordy roota na zdrowe / zepsute-nasze / zepsute-obce.

    ``issue`` to walidator rekordu z ``identity.schema`` (jedyny owner kształtu
    rootów), ``relates(key, value)`` odpowiada, czy zepsuty rekord dotyczy
    audytowanej tożsamości. Root, który nie jest mapą, to „nie umiem tego
    odczytać", a nie „nie ma tam problemu" — fail-closed, zero rekordów zdrowych.
    """
    if not isinstance(store, dict):
        return _RootScan(root, {}, True, 0)
    healthy, unrelated, identity_broken = {}, 0, False
    for key, value in store.items():
        if issue(key, value) is None:
            healthy[key] = value
        elif relates(key, value):
            identity_broken = True
        else:
            unrelated += 1
    return _RootScan(root, healthy, identity_broken, unrelated)


def verify_courier_wired(cid: int, full_name: str) -> tuple[bool, List[str]]:
    """Re-read the roster files and confirm the courier is visible everywhere.

    Returns (all_ok, checklist_lines). Pure read — safe to call anytime.

    A-6/G6 (K1): każdy czytany root przechodzi walidację SCHEMATU delegowaną do
    ``identity.schema`` — sama OBECNOŚĆ wpisu (``str(cid) in tiers``,
    ``alias in full``) nie jest dowodem wpięcia, bo zepsuty rekord (``True``,
    lista, ``"017"``, wartość złego typu) jest dla kanonicznego czytelnika
    niewidzialny, a werdykt tego audytu jest ostatnią bramką ``_auto_wire``.
    Zepsuty rekord DOTYCZĄCY audytowanej tożsamości = check ✗ (fail-closed);
    zepsuty rekord CUDZY = osobna linia ``⚠ malformed w <root>: N`` bez zmiany
    ``all_ok`` (to narzędzie diagnostyczne operatora — nie blokuje wpięcia
    kuriera A z powodu rekordu kuriera B).
    """
    from dispatch_v2.daily_accounting.config import EXCLUDED_CIDS

    cid_s = str(cid)
    alias = derive_alias(full_name)
    names = frozenset({norm(full_name), norm(alias)})

    kids = _scan_root(
        "kurier_ids", _read_json(KURIER_IDS), identity_schema.ids_record_issue,
        lambda name, cid_v: (_refers_to_audited_name(name, names)
                             or _refers_to_audited_cid(cid_v, cid_s)))
    piny = _scan_root(
        "kurier_piny", _read_json(KURIER_PINY), identity_schema.piny_record_issue,
        lambda pin, alias_v: _refers_to_audited_name(alias_v, names))
    tiers = _scan_root(
        "courier_tiers", _read_json(COURIER_TIERS),
        identity_schema.tiers_record_issue,
        lambda cid_k, row: _refers_to_audited_cid(cid_k, cid_s))
    full = _scan_root(
        "kurier_full_names", _read_json(KURIER_FULL_NAMES),
        identity_schema.full_names_record_issue,
        lambda alias_k, name_v: (_refers_to_audited_name(alias_k, names)
                                 or _refers_to_audited_name(name_v, names)))
    # Reverse-check (niżej) czyta cid->imię przez roster + grafik. Roster pomija
    # niekanoniczny rekord POJEDYNCZO (G3, fail-soft), więc bez tego audytu
    # zatruta tożsamość znika z rostera i reverse melduje „nie potwierdzam,
    # nie blokuję" — czyli ✓. Oba roota trzeba więc sprawdzić WPROST.
    names_root = _scan_root(
        "courier_names", _read_json(COURIER_NAMES),
        identity_schema.courier_names_record_issue,
        lambda cid_k, name_v: (_refers_to_audited_cid(cid_k, cid_s)
                               or _refers_to_audited_name(name_v, names)))
    grafik = _scan_root(
        "grafik_full_names", _read_json(GRAFIK_FULL_NAMES),
        identity_schema.grafik_record_issue,
        lambda name_k, cid_v: (_refers_to_audited_name(name_k, names)
                               or _refers_to_audited_cid(cid_v, cid_s)))
    scans = [kids, piny, tiers, full, names_root, grafik]

    checks = []
    dispatch_ok = (resolve_cid(full_name, kids.healthy) == cid_s
                   or kids.healthy.get(full_name) == cid)
    checks.append(("dyspozytornia (resolve_cid)",
                   dispatch_ok and not kids.identity_broken))
    tiers_ok = cid_s in tiers.healthy
    checks.append(("scoring (courier_tiers, tier=new)",
                   tiers_ok and not tiers.identity_broken))
    pin_ok = alias in set(piny.healthy.values())
    checks.append(("apka kuriera (PIN login)",
                   pin_ok and not piny.identity_broken))
    cod_ok = alias in full.healthy and cid not in EXCLUDED_CIDS
    checks.append(("liczenie COD (kurier_full_names)",
                   cod_ok and not full.identity_broken))

    # #2 (2026-06-10): REVERSE. Dispatch używa cid->imię->match_courier(grafik).
    # Forward resolve_cid(imię)->cid przechodzi nawet gdy reverse zatruty skrótem
    # ("Rafał Ja" wygrywa w _load_courier_names -> AMBIGUOUS -> kurier niewidzialny),
    # dając false "✓ dyspozytornia". Sprawdź, że cid->imię->grafik resolwuje
    # JEDNOZNACZNIE do full_name. Fail-soft (błąd introspekcji nie blokuje).
    # Egzekwuj TYLKO gdy kurier realnie na grafiku (full_name in schedule) — wtedy
    # dispatch MUSI resolwować cid->imię->ten full_name. Gdy brak disp_name / nie na
    # grafiku → nie potwierdzamy, nie blokujemy (reverse_ok=True). Łapie POZYTYWNĄ
    # niezgodność (ambiguous/zły), nie brak danych (test-isolation: mock roster bez
    # realnego grafiku → reverse_ok=True).
    reverse_ok = True
    try:
        from dispatch_v2 import courier_resolver as _cr
        from schedule_utils import load_schedule as _ls, match_courier as _mc
        _sched = _ls() or {}
        _disp_name = _cr._load_courier_names().get(str(cid))
        if _disp_name and full_name in _sched:
            reverse_ok = (_mc(_disp_name, _sched) == full_name)
    except Exception:  # noqa: BLE001
        reverse_ok = True  # nie blokuj na błędzie introspekcji
    checks.append(("dispatch reverse (cid->imię->grafik)",
                   reverse_ok and not names_root.identity_broken
                   and not grafik.identity_broken))

    lines = [f"{'✓' if ok else '✗'} {label}" for label, ok in checks]
    # Zepsuty rekord CUDZEJ tożsamości nie zmienia werdyktu, ale NIE MOŻE zniknąć
    # w ciszy: operator dostaje licznik per root (nigdy nazwisk ani PIN-ów).
    lines += [f"⚠ malformed w {s.root}: {s.unrelated}" for s in scans if s.unrelated]
    return all(ok for _, ok in checks), lines


# --------------------------------------------------------------------------- #
# Core scan
# --------------------------------------------------------------------------- #


def _has_real_shift(entry: Any) -> bool:
    """True only for an actual shift today (dict with a truthy 'start')."""
    return isinstance(entry, dict) and bool(entry.get("start"))


def _resolve_cid_trusted(full_name: str) -> Optional[str]:
    """resolve_cid dla bramki "już zmapowany" — bez zaufania do gołych kluczy-imion.

    Goły klucz ('Gabriel' -> 179) wygrywa score-fallback (score=1) dla KAŻDEGO
    nowego kuriera o tym imieniu, przez co scan uznawał go za już zmapowanego,
    cicho pomijał parowanie, a self-heal utrwalał zatrucie w grafik_full_names
    (case 2026-07-06: Gabriel Przyborowski 541 zduszony przez 'Gabriel'->179
    Ostapczuk). Tu resolve dostaje kurier_ids BEZ kluczy jednowyrazowych —
    exact match pełnego imienia i dopasowania po nazwisku ('Adrian Cit')
    przechodzą bez zmian; pomiar 06.07 na żywym grafiku (41 nazwisk): zero
    kurierów zależnych wyłącznie od gołego klucza.

    Fail-open: flaga OFF / imię jednowyrazowe / pusty odczyt kurier_ids ->
    zachowanie sprzed fixu (zwykłe resolve_cid). NIE zmienia semantyki
    resolve_cid dla innych konsumentów (shift-worker, verify)."""
    if not flag("NEW_COURIER_AUTOPAIR_BARE_KEY_STRICT", True):
        return resolve_cid(full_name)
    if " " not in (full_name or "").strip():
        # Jednowyrazowe imię z grafiku — goły klucz to jedyne sensowne mapowanie.
        return resolve_cid(full_name)
    try:
        kids = _load_kurier_ids()
    except Exception as e:  # noqa: BLE001 — fail-open jak reszta scanu
        _log.warning(f"_resolve_cid_trusted load fail {full_name!r}: {e}")
        return resolve_cid(full_name)
    if not kids:
        return resolve_cid(full_name)
    filtered = {k: v for k, v in kids.items() if " " in k.strip()}
    return resolve_cid(full_name, filtered)


def _auto_wire(full_name: str, cid: int) -> dict:
    """Wire one courier; return {'ok':bool, 'pin':?, 'lines':[...], 'note':?}."""
    try:
        result = add_new_courier(cid, full_name)
    except ValueError as e:
        # Conflict (alias/cid already present) — surface to Adrian, never silent.
        return {"ok": False, "conflict": True, "note": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "note": f"{type(e).__name__}: {e}"}
    # #2: wpisz pełne imię do źródła prawdy dispatchu PRZED weryfikacją, by reverse
    # (cid->imię->grafik) resolwował (PANEL-CANON #1). Self-heal nowego kuriera.
    grafik_status = _ensure_grafik_full_name(full_name, cid)
    ok, lines = verify_courier_wired(cid, full_name)
    wired = {"ok": ok, "pin": result.get("pin"), "alias": result.get("alias"),
             "lines": lines}
    if grafik_status == GRAFIK_REFUSED:
        # Bez wpisu w źródle prawdy cid↔imię kurier NIE jest podpięty — nie wolno
        # zameldować sukcesu tylko dlatego, że pozostałe pliki się zapisały.
        wired["ok"] = False
        wired["note"] = ("grafik_full_names: ODMOWA zapisu (konflikt własności "
                         "CID) — sprawdź logi i rejestr tożsamości")
    return wired


def scan_once(now: Optional[datetime] = None, *, dry_run: bool = False) -> dict:
    """One pass: detect new scheduled couriers, auto-wire or ask. Returns summary."""
    now = now or datetime.now(WARSAW)
    today = now.date().isoformat()
    autowrite = flag("NEW_COURIER_AUTOPAIR_AUTOWRITE", True)

    summary = {"today": today, "scanned": 0, "paired": [], "asked": [],
               "conflict": [], "skipped_already": 0, "dry_run": dry_run,
               "autowrite": autowrite}

    try:
        schedule = load_schedule() or {}
    except Exception as e:  # noqa: BLE001 — fail-open (dispatch unaffected)
        _log.warning(f"load_schedule fail: {type(e).__name__}: {e}")
        return summary

    state = _load_state()
    bucket = _day_bucket(state, today)
    roster = panel_roster.fetch_active_roster()
    ignored = _load_ignored_names()  # shared skiplist: retired / duplicate accounts
    dirty = False

    for full_name, entry in schedule.items():
        if not _has_real_shift(entry):
            continue
        if _is_garbage_name(full_name):
            continue
        if full_name in ignored:
            # Permanent-inactive / duplicate (e.g. Albert Dec retired) — never pair.
            continue
        summary["scanned"] += 1
        _cid_existing = _resolve_cid_trusted(full_name)
        if _cid_existing is not None:
            # #2 self-heal: already-mapped (forward resolve_cid) NIE gwarantuje
            # reverse (cid->imię w dispatchu). Upewnij się, że pełne imię grafiku
            # jest w grafik_full_names -> cid (źródło prawdy #1). Bez tego kolizja
            # skrótów wraca dla istniejących kurierów spoza grafik_full_names.
            if not dry_run:
                _grafik_status = _ensure_grafik_full_name(full_name, _cid_existing)
                if _grafik_status == GRAFIK_HEALED:
                    summary.setdefault("healed", []).append(
                        {"name": full_name, "cid": _cid_existing})
                elif _grafik_status == GRAFIK_REFUSED:
                    # Odmowa MUSI dojść do operatora, inaczej cicha korupcja
                    # źródła prawdy cid↔imię wraca tylnymi drzwiami.
                    summary.setdefault("grafik_refused", []).append(
                        {"name": full_name, "cid": _cid_existing})
            continue  # zmapowany + zapewniony w źródle prawdy
        if full_name in bucket["paired"]:
            summary["skipped_already"] += 1
            continue

        m = panel_roster.match_name_to_cid(full_name, roster)

        if m.status == "matched" and autowrite:
            if dry_run:
                summary["paired"].append({"name": full_name, "cid": m.cid, "pin": "DRY"})
                _log.info(f"[dry] would auto-wire {full_name!r} -> cid={m.cid} ({m.name})")
                continue
            res = _auto_wire(full_name, m.cid)
            if res.get("conflict"):
                # cid/alias already exists with different mapping -> ask Adrian
                if full_name not in bucket["alerted"]:
                    _tg(
                        f"⚠️ Nowy kurier '<b>{full_name}</b>' (grafik) — chciałem wpiąć "
                        f"cid {m.cid} z gastro, ale: {res['note']}\n"
                        f"Sprawdź ręcznie / popraw nazwę. Komenda: "
                        f"<code>/nowy &lt;cid&gt; {full_name}</code>"
                    )
                    bucket["alerted"].append(full_name)
                    dirty = True
                summary["conflict"].append({"name": full_name, "cid": m.cid, "note": res["note"]})
                _log.warning(f"auto-wire conflict {full_name!r} cid={m.cid}: {res['note']}")
                continue
            if res.get("ok"):
                bucket["paired"].append(full_name)
                dirty = True
                pin = res.get("pin")
                lines = "\n".join(res.get("lines", []))
                _tg(
                    f"✅ <b>Nowy kurier wpięty automatycznie</b>\n"
                    f"{full_name} (cid {m.cid}, gastro: {m.name})\n"
                    f"PIN: <code>{pin}</code> — prześlij kurierowi.\n"
                    f"Widoczny w:\n{lines}\n"
                    f"Grafik dopasuje się sam (alias '{res.get('alias')}').",
                    silent=True,  # rutynowy sukces → cicha dostawa (LOW); konflikt niżej zostaje głośny
                )
                summary["paired"].append({"name": full_name, "cid": m.cid, "pin": pin})
                _log.info(f"AUTO-WIRED {full_name!r} -> cid={m.cid} pin={pin}")
            else:
                # write happened but verification failed — loud alert (Z2)
                _note = f"\n{res['note']}" if res.get("note") else ""
                _tg(
                    f"❗ Wpiąłem '<b>{full_name}</b>' (cid {m.cid}) ale "
                    f"WERYFIKACJA NIE PRZESZŁA:\n" + "\n".join(res.get("lines", []))
                    + _note + f"\nSprawdź roster ręcznie."
                )
                summary["conflict"].append({"name": full_name, "cid": m.cid,
                                            "note": "verify_failed"})
                _log.error(f"auto-wire verify FAILED {full_name!r} cid={m.cid}: {res}")
        else:
            # no match / ambiguous / autowrite disabled -> ask once per day
            if full_name in bucket["alerted"]:
                summary["skipped_already"] += 1
                continue
            if not dry_run:
                if m.status == "ambiguous":
                    cands = ", ".join(f"{c} {n}" for c, n, _ in m.candidates[:3])
                    body = (f"kilku pasuje w gastro: {cands}. Wybierz cid i wpisz "
                            f"<code>/nowy &lt;cid&gt; {full_name}</code>.")
                elif not autowrite:
                    if m.status == "matched":
                        body = (f"pasuje cid {m.cid} ({m.name}) — auto-zapis WYŁĄCZONY. "
                                f"Wpisz <code>/nowy {m.cid} {full_name}</code>.")
                    else:
                        body = (f"nie znajduję cid w aktywnych gastro. Wpisz "
                                f"<code>/nowy &lt;cid&gt; {full_name}</code>.")
                else:
                    body = (f"nie widzę go wśród aktywnych kurierów gastro "
                            f"(jeszcze nie założony / inna pisownia?). Załóż konto w gastro "
                            f"lub wpisz <code>/nowy &lt;cid&gt; {full_name}</code>.")
                _tg(f"🆕 <b>Nowy w grafiku</b>: {full_name} (zmiana "
                    f"{entry.get('start')}–{entry.get('end')}) — {body}")
                bucket["alerted"].append(full_name)
                dirty = True
            summary["asked"].append({"name": full_name, "status": m.status,
                                     "cid": m.cid})
            _log.info(f"ASK {full_name!r} status={m.status} cid={m.cid}")

    if dirty and not dry_run:
        _save_state(state)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    dry = "--dry-run" in argv
    if not flag("NEW_COURIER_AUTOPAIR_ENABLED", False):
        _log.info("NEW_COURIER_AUTOPAIR_ENABLED=False — skip")
        return 0
    summary = scan_once(dry_run=dry)
    _log.info(
        f"scan done: scanned={summary['scanned']} paired={len(summary['paired'])} "
        f"asked={len(summary['asked'])} conflict={len(summary['conflict'])} "
        f"grafik_refused={len(summary.get('grafik_refused', []))} dry={dry}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
