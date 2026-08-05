"""SECURITY ORACLE magazynu KDF PIN-ów (`kurier_piny_kdf.json`) — JEDNO ŹRÓDŁO.

Kanoniczny właściciel asercji „żaden plaintext PIN nie ląduje w magazynie KDF".
Importują go: bramka `tests/test_a6_security_pin_kdf.py` ORAZ dowód anty-flake
`eod_drafts/2026-08-05/pin_oracle_flake_297/sweep_oracle_flake.py` — żadnych kopii.

────────────────────────────────────────────────────────────────────────────────
DLACZEGO NIE SKAN SUBSTRINGIEM (sesja 297, bramka `tests.pin-kdf-oracle-substring-flake`)
Poprzednia wersja oracle robiła `assert pin not in json.dumps(store)`. Sól i hash to
LOSOWY hex (16 B + 32 B → 96 znaków `0-9a-f`), więc 4-cyfrowy PIN trafiał się w nim
przypadkowo z prawdopodobieństwem ~0,56% na bieg (≈ 2 PIN-y × ~190 pozycji × 16⁻⁴)
→ fałszywa czerwień nocnego strażnika co noc. Cyfry PIN-u w hexie wyjścia KDF NIE SĄ
wyciekiem: PBKDF2 jest jednokierunkowy, a sól jest publiczna z definicji.

CO ORACLE GWARANTUJE ZAMIAST TEGO (asercje strukturalne, deterministyczne):
  (A) klucz magazynu (tożsamość kuriera) nie zawiera PIN-u,
  (B) rekord ma DOKŁADNIE kontraktowe pola {kdf,salt,iter,hash} — brak pola-śmietnika,
      w którym mógłby wylądować PIN (również jako NAZWA pola),
  (C) pola tekstowo-metadanowe (`kdf`) mają dokładną wartość kontraktową; `iter` jest
      liczbą ≥ floor (koszt KDF nieosłabiony),
  (D) `salt`/`hash` mają DOKŁADNY kształt wyjścia KDF (czysty lowercase-hex o
      dokładnej długości) → PIN doklejony do wartości albo PIN wpisany zamiast
      hasha zmienia długość/alfabet → RED,
  (E) żadna WARTOŚĆ pola nie jest równa PIN-owi,
  (F) sole są unikatowe per-user ORAZ każdy rekord jest PRAWDZIWYM PBKDF2 dokładnie
      jednego PIN-u z listy (bijekcja PIN↔rekord przez `verify_record`) — to zamyka
      drogę „hash to jakieś odwracalne zakodowanie PIN-u", bo hash musi się zgadzać
      z niezależnie policzonym PBKDF2(pin, salt, iter).

Dowód, że oracle NIE został osłabiony: `test_mutation_leak_forms_fail_oracle`
(każda realna forma wycieku → RED) + zachowany `test_mutation_plaintext_store_fails_oracle`.

BEZPIECZEŃSTWO: moduł nigdy nie drukuje ani nie umieszcza PIN-u w treści asercji.
"""
import hashlib
import re

from dispatch_v2.identity import pin_auth

# Kontrakt rekordu KDF (`pin_auth.make_record`).
KDF_REC_FIELDS = frozenset({"kdf", "salt", "iter", "hash"})
# Pola będące WYJŚCIEM KDF — losowy hex, sprawdzane KSZTAŁTEM, nie substringiem.
KDF_HEX_FIELDS = ("salt", "hash")
SALT_HEX_LEN = pin_auth.SALT_BYTES * 2
HASH_HEX_LEN = hashlib.sha256().digest_size * 2  # dklen PBKDF2-HMAC-SHA256 = 32 B
_HEX_RE = re.compile(r"[0-9a-f]+\Z")


def _redact(text, pins):
    """Komunikat asercji NIGDY nie może wynieść PIN-u do logów strażnika — nawet
    gdy oracle czerwieni WŁAŚNIE dlatego, że PIN wyciekł do klucza/nazwy pola."""
    out = str(text)
    for pin in sorted(pins, key=len, reverse=True):
        out = out.replace(pin, "***")
    return out


def _is_kdf_hex(value, exact_len):
    """True tylko dla dokładnego kształtu `bytes.hex()` o zadanej długości.
    Dokładna długość + czysty alfabet hex = doklejenie PIN-u do soli/hasha lub
    wpisanie PIN-u zamiast hasha ZAWSZE łamie warunek."""
    return (isinstance(value, str) and len(value) == exact_len
            and _HEX_RE.match(value) is not None)


def assert_no_plaintext_pin_in_store(kdf_path, pins):
    """Raisuje AssertionError, gdy magazyn KDF pod `kdf_path` narusza którykolwiek
    warunek (A)-(F) opisany w docstringu modułu.

    KONTRAKT WOŁAJĄCEGO: `pins` to PEŁNY zbiór PIN-ów odpowiadających rekordom w
    magazynie (1:1) — dzięki temu (F) może sprawdzić bijekcję PIN↔rekord.
    """
    store = pin_auth._load_json(kdf_path)
    pins = [str(p) for p in pins]
    assert store, "magazyn KDF pusty — oracle nie ma czego weryfikować"
    assert len(store) == len(set(pins)), (
        f"kontrakt oracle: {len(set(pins))} PIN-ów vs {len(store)} rekordów "
        "(przekaż PEŁNY zbiór PIN-ów magazynu)")

    salts = []
    for name, rec in store.items():
        # (A) klucz magazynu = tożsamość (metadana tekstowa) — PIN NIGDY tu nie może być.
        assert isinstance(name, str), "klucz magazynu nie jest tekstem"
        safe_name = _redact(name, pins)
        for pin in pins:
            assert pin not in name, f"{safe_name!r}: PIN w KLUCZU magazynu (wyciek)"
        # (B) dokładny zestaw pól — obce pole (także o NAZWIE równej PIN-owi) = RED.
        assert isinstance(rec, dict), f"{safe_name}: rekord nie jest obiektem"
        assert set(rec) == KDF_REC_FIELDS, \
            f"{safe_name}: obcy/niepełny zestaw pól rekordu: {_redact(sorted(rec), pins)}"
        # (C) metadane tekstowe/liczbowe — wartości kontraktowe, koszt KDF nieosłabiony.
        assert rec["kdf"] == pin_auth.KDF_NAME, f"{safe_name}: obcy/zmodyfikowany znacznik kdf"
        assert isinstance(rec["iter"], int) and not isinstance(rec["iter"], bool), \
            f"{safe_name}: iter nie jest liczbą całkowitą"
        assert rec["iter"] >= pin_auth._MIN_ITER_FLOOR, \
            f"{safe_name}: koszt KDF poniżej floor (osłabienie)"
        # (D) sól i hash: DOKŁADNY kształt wyjścia KDF.
        assert _is_kdf_hex(rec["salt"], SALT_HEX_LEN), \
            f"{safe_name}: sól nie ma kształtu wyjścia KDF ({SALT_HEX_LEN} zn. hex)"
        assert _is_kdf_hex(rec["hash"], HASH_HEX_LEN), \
            f"{safe_name}: hash nie ma kształtu wyjścia KDF ({HASH_HEX_LEN} zn. hex)"
        # (E) żadna wartość pola nie JEST PIN-em.
        for field, value in rec.items():
            for pin in pins:
                assert str(value) != pin, f"{safe_name}: PLAINTEXT PIN w polu {_redact(field, pins)!r}"
        salts.append(rec["salt"])

    # (F) sole unikatowe + hash to PRAWDZIWY PBKDF2 dokładnie jednego PIN-u (bijekcja).
    assert len(salts) == len(set(salts)), "sole NIE są per-user unikatowe"
    matched = {}
    for pin in pins:
        hits = [n for n, rec in store.items() if pin_auth.verify_record(pin, rec)]
        assert len(hits) == 1, (
            "rekord KDF nie jest PBKDF2 dokładnie jednego PIN-u "
            f"(trafień: {len(hits)}) — hash/salt nie pochodzą z KDF")
        matched[pin] = hits[0]
    assert len(set(matched.values())) == len(store), \
        "dwa PIN-y wskazują ten sam rekord (kolizja/zduplikowany rekord)"


# ── KATALOG REALNYCH FORM WYCIEKU (bramka mutacyjna oracle) ──────────────────
# Każda funkcja mutuje POPRAWNY magazyn tak, by PIN dało się z niego odzyskać.
# Kontrakt: po KAŻDEJ z nich `assert_no_plaintext_pin_in_store` MUSI być RED.
# Egzekwują to: `tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle`
# oraz sekcja `--mutations` sweepa anty-flake (jedno źródło, zero kopii).

def _leak_hash_is_pin(store, name, pin):
    store[name]["hash"] = pin  # PLAINTEXT PIN jako wartość pola


def _leak_salt_is_pin(store, name, pin):
    store[name]["salt"] = pin


def _leak_extra_field(store, name, pin):
    store[name]["pin_plain"] = pin  # pole-śmietnik z PIN-em


def _leak_pin_as_field_name(store, name, pin):
    store[name][pin] = "x"  # PIN jako NAZWA pola


def _leak_pin_in_store_key(store, name, pin):
    store[f"{name} ({pin})"] = store.pop(name)  # PIN w kluczu tożsamości


def _leak_pin_appended_to_hash(store, name, pin):
    store[name]["hash"] = store[name]["hash"] + pin


def _leak_pin_appended_to_salt(store, name, pin):
    store[name]["salt"] = store[name]["salt"] + pin


def _leak_pin_in_kdf_label(store, name, pin):
    store[name]["kdf"] = pin_auth.KDF_NAME + ":" + pin


def _leak_pin_hex_encoded_as_hash(store, name, pin):
    # Wyciek NIEWIDOCZNY dla starego skanu substringiem (PIN zakodowany hexem,
    # kształt 64 zn. hex zachowany) — łapie go warunek (F) bijekcji KDF.
    store[name]["hash"] = pin.encode("utf-8").hex().ljust(HASH_HEX_LEN, "0")


def _leak_iter_below_floor(store, name, pin):
    # Osłabienie kosztu KDF = PIN 4-cyfrowy do złamania offline w sekundy.
    store[name]["iter"] = 1


LEAK_FORMS = {
    "hash_is_pin": _leak_hash_is_pin,
    "salt_is_pin": _leak_salt_is_pin,
    "extra_field": _leak_extra_field,
    "pin_as_field_name": _leak_pin_as_field_name,
    "pin_in_store_key": _leak_pin_in_store_key,
    "pin_appended_to_hash": _leak_pin_appended_to_hash,
    "pin_appended_to_salt": _leak_pin_appended_to_salt,
    "pin_in_kdf_label": _leak_pin_in_kdf_label,
    "pin_hex_encoded_as_hash": _leak_pin_hex_encoded_as_hash,
    "iter_below_floor": _leak_iter_below_floor,
}
