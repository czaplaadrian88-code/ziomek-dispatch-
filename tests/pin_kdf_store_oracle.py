"""SECURITY ORACLE magazynu KDF PIN-ów (`kurier_piny_kdf.json`) — JEDNO ŹRÓDŁO.

Kanoniczny właściciel asercji „z magazynu KDF nie da się odczytać PIN-u".
Importują go: bramka `tests/test_a6_security_pin_kdf.py` ORAZ dowód anty-flake
`eod_drafts/2026-08-05/pin_oracle_flake_297/sweep_oracle_flake.py` — żadnych kopii.

────────────────────────────────────────────────────────────────────────────────
HISTORIA (sesja 297, bramka `tests.pin-kdf-oracle-substring-flake`)
Oracle mastera robił `assert pin not in json.dumps(store)`. Przy PRODUKCYJNYM
4-cyfrowym PIN-ie (`identity/schema.PIN_LENGTH`) i 96 znakach losowego hexa na
rekord skan trafiał PIN przypadkiem w ~0,6% biegów → fałszywa czerwień nocnego
strażnika. Iteracja 1 (d09a783eb) usunęła flake, ale WYCIĘŁA skan dosłowny i
zastąpiła go samym KSZTAŁTEM pól — przez co sól stała się polem SWOBODNYM:
mutant produkcji wyprowadzający sól z PIN-u zapisywał PIN CZYTELNIE do pliku i
przechodził na zielono (blind review: F1 CRITICAL / F2 HIGH / F3 MEDIUM).

ROZWIĄZANIE (iteracja 2): źródłem flake'a była DŁUGOŚĆ PIN-u, nie idea skanu.
Bramka używa SYNTETYCZNYCH PIN-ów o długości >= `MIN_ORACLE_PIN_LEN`, a oracle
EGZEKWUJE ten kontrakt fail-closed — więc skan dosłowny wraca BEZ flake'a:
prawdopodobieństwo przypadkowego trafienia 12 cyfr w hexie magazynu to
~190 pozycji × 16⁻¹² ≈ 7×10⁻¹³ na bieg (dla fragmentu 10-znakowego ≈ 5×10⁻¹⁰).

CO ORACLE EGZEKWUJE (każda asercja deterministyczna):
  (A) klucz magazynu (tożsamość kuriera) nie zawiera PIN-u, jego powierzchniowej
      formy ani FRAGMENTU >= `KEY_FRAGMENT_MIN_LEN` znaków; klucze są nazwami
      (dane deterministyczne, nie losowy hex), więc skan fragmentów tu nie flakuje,
  (B) rekord ma DOKŁADNIE kontraktowe pola {kdf,salt,iter,hash} — brak pola-śmietnika,
      w którym mógłby wylądować PIN (również jako NAZWA pola),
  (C) `kdf` ma dokładną wartość kontraktową, a `iter` jest RÓWNY produkcyjnemu
      `pin_auth.PBKDF2_ITERATIONS` (nie „gdzieś powyżej floora") — dzięki temu pole
      liczbowe przestaje być kanałem na cyfry PIN-u (np. 200000+int(pin)); osobno
      sprawdzamy, że sama stała produkcji nie zeszła poniżej `_MIN_ITER_FLOOR`,
  (D) `salt`/`hash` mają DOKŁADNY kształt wyjścia KDF (czysty lowercase-hex o
      dokładnej długości) → doklejenie/podmiana wartości = RED,
  (E) żadna WARTOŚĆ pola nie jest równa PIN-owi,
  (F) sole są unikatowe per-user ORAZ każdy rekord jest PRAWDZIWYM PBKDF2 dokładnie
      jednego PIN-u z listy (bijekcja PIN↔rekord przez `verify_record`) — to zamyka
      drogę „hash to jakieś odwracalne zakodowanie PIN-u",
  (G) SKAN DOSŁOWNY CAŁEGO BLOBU (przywrócony oracle mastera, wzmocniony): ani PIN,
      ani jego forma odwrócona, ani hex-ASCII, ani żaden jego fragment >=
      `BLOB_FRAGMENT_MIN_LEN` znaków nie może występować w pliku magazynu —
      niezależnie od tego, w którym polu. To domyka sól i `iter` jako pola swobodne.

CZEGO ORACLE NIE ZAMYKA (jawnie, żeby dokumentacja nie obiecywała więcej niż
egzekwuje — F3 z blind review):
  * PIN pocięty na fragmenty KRÓTSZE niż `BLOB_FRAGMENT_MIN_LEN` i rozrzucony po
    losowym hexie soli/hasha — takie fragmenty są statystycznie nieodróżnialne od
    hexa i skan na nie oznaczałby powrót flake'a,
  * NIEWYMIENIONE odwracalne kodowanie soli (base64, permutacja, szyfr): sól jest
    parametrem PUBLICZNYM i WOLNYM, więc z samego artefaktu nie da się wykluczyć
    każdego kodowania. Zamyka to sonda PRODUKCYJNA `assert_salt_not_derived_from_secret`
    (ta sama entropia + różne PIN-y = ta sama sól), która patrzy na KOD, nie na plik.

DLACZEGO SYNTETYCZNY 12-CYFROWY PIN NIE OSŁABIA BRAMKI: `identity/pin_auth` nie ma
ŻADNEJ gałęzi zależnej od długości PIN-u (`_hash_pin` → `str(pin).encode()`,
`make_record`/`verify_record`/`resolve_pin` traktują PIN jako nieprzezroczysty
tekst), a bramka woła PRODUKCYJNE funkcje. Forma wycieku (sól z sekretu, PIN w
`iter`, PIN w kluczu, PIN plaintext w polu) manifestuje się więc identycznie dla
12 i dla 4 cyfr — różni się tylko szansa PRZYPADKOWEJ kolizji, która jest tu
źródłem flake'a. Trzyma to asercją test `test_record_shape_is_pin_length_independent`.

BEZPIECZEŃSTWO: moduł nigdy nie drukuje ani nie umieszcza PIN-u (ani jego formy /
fragmentu) w treści asercji — patrz `_redact`.
"""
import hashlib
import json
import re

from dispatch_v2.identity import pin_auth

# Kontrakt rekordu KDF (`pin_auth.make_record`).
KDF_REC_FIELDS = frozenset({"kdf", "salt", "iter", "hash"})
# Pola będące WYJŚCIEM KDF — losowy hex o dokładnym kształcie.
KDF_HEX_FIELDS = ("salt", "hash")
SALT_HEX_LEN = pin_auth.SALT_BYTES * 2
HASH_HEX_LEN = hashlib.sha256().digest_size * 2  # dklen PBKDF2-HMAC-SHA256 = 32 B
_HEX_RE = re.compile(r"[0-9a-f]+\Z")

# ── kontrakt anty-flake (sesja 297, iter2) ───────────────────────────────────
# Minimalna długość SYNTETYCZNEGO PIN-u bramki. 12 cyfr → p(kolizja z hexem
# magazynu) ≈ 190 × 16⁻¹² ≈ 7×10⁻¹³ na bieg; produkcyjne 4 cyfry dawały ~0,6%.
MIN_ORACLE_PIN_LEN = 12
# Fragment PIN-u szukany w KLUCZACH magazynu (dane deterministyczne — nazwy).
KEY_FRAGMENT_MIN_LEN = 4
# Fragment PIN-u szukany w CAŁYM blobie (zawiera losowy hex) — 10 znaków trzyma
# p(fałszywej czerwieni) ≈ 5×10⁻¹⁰ na bieg.
BLOB_FRAGMENT_MIN_LEN = 10


def _surface_forms(pin):
    """Powierzchniowe, TRYWIALNIE ODWRACALNE zapisy PIN-u — każdy z nich w pliku
    magazynu oznacza, że czytelnik pliku odzyskuje PIN bez brute-force."""
    return {
        "dosłowna": pin,
        "odwrócona": pin[::-1],
        "hex-ASCII": pin.encode("utf-8").hex(),
    }


def _fragments(pin, min_len):
    """Wszystkie spójne fragmenty PIN-u o długości >= min_len."""
    return {pin[i:i + n]
            for n in range(min_len, len(pin) + 1)
            for i in range(len(pin) - n + 1)}


def _secret_strings(pins):
    """Wszystko, co w komunikacie asercji mogłoby wynieść PIN (lub jego czytelny
    fragment) do logów nocnego strażnika."""
    out = set()
    for pin in pins:
        out |= set(_surface_forms(pin).values())
        out |= _fragments(pin, KEY_FRAGMENT_MIN_LEN)
    return out


def _redact(text, pins):
    """Komunikat asercji NIGDY nie może wynieść PIN-u — nawet gdy oracle czerwieni
    WŁAŚNIE dlatego, że PIN (albo jego fragment) wyciekł do klucza/nazwy pola."""
    out = str(text)
    for secret in sorted(_secret_strings(pins), key=len, reverse=True):
        out = out.replace(secret, "***")
    return out


def _is_kdf_hex(value, exact_len):
    """True tylko dla dokładnego kształtu `bytes.hex()` o zadanej długości.
    Dokładna długość + czysty alfabet hex = doklejenie PIN-u do soli/hasha lub
    wpisanie PIN-u zamiast hasha ZAWSZE łamie warunek."""
    return (isinstance(value, str) and len(value) == exact_len
            and _HEX_RE.match(value) is not None)


def assert_no_plaintext_pin_in_store(kdf_path, pins):
    """Raisuje AssertionError, gdy magazyn KDF pod `kdf_path` narusza którykolwiek
    warunek (A)-(G) opisany w docstringu modułu.

    KONTRAKT WOŁAJĄCEGO:
      * `pins` to PEŁNY zbiór PIN-ów odpowiadających rekordom magazynu (1:1) —
        dzięki temu (F) może sprawdzić bijekcję PIN↔rekord,
      * każdy PIN ma >= `MIN_ORACLE_PIN_LEN` znaków (SYNTETYCZNY PIN bramki).
        Krótszy PIN jest odrzucany FAIL-CLOSED, bo skan dosłowny na 4 cyfrach
        czerwieni losowo (~0,6% biegów) — to był flake, przez który oracle
        wcześniej osłabiono.
    """
    store = pin_auth._load_json(kdf_path)
    pins = [str(p) for p in pins]
    assert store, "magazyn KDF pusty — oracle nie ma czego weryfikować"
    for pin in pins:
        assert len(pin) >= MIN_ORACLE_PIN_LEN, (
            "kontrakt anty-flake oracle: bramka MUSI używać SYNTETYCZNYCH PIN-ów o "
            f"długości >= MIN_ORACLE_PIN_LEN ({MIN_ORACLE_PIN_LEN}); dostano długość "
            f"{len(pin)} — krótszy PIN koliduje losowo z hexem soli/hasha")
    assert len(store) == len(set(pins)), (
        f"kontrakt oracle: {len(set(pins))} PIN-ów vs {len(store)} rekordów "
        "(przekaż PEŁNY zbiór PIN-ów magazynu)")

    salts = []
    for name, rec in store.items():
        # (A) klucz magazynu = tożsamość (metadana tekstowa) — ani PIN, ani jego
        #     forma/fragment nie mogą tu wystąpić.
        assert isinstance(name, str), "klucz magazynu nie jest tekstem"
        safe_name = _redact(name, pins)
        for pin in pins:
            for label, form in _surface_forms(pin).items():
                assert form not in name, \
                    f"{safe_name!r}: PIN w KLUCZU magazynu w postaci {label} (wyciek)"
            for frag in _fragments(pin, KEY_FRAGMENT_MIN_LEN):
                assert frag not in name, (
                    f"{safe_name!r}: fragment PIN-u (>= {KEY_FRAGMENT_MIN_LEN} zn.) "
                    "w KLUCZU magazynu (wyciek częściowy)")
        # (B) dokładny zestaw pól — obce pole (także o NAZWIE równej PIN-owi) = RED.
        assert isinstance(rec, dict), f"{safe_name}: rekord nie jest obiektem"
        assert set(rec) == KDF_REC_FIELDS, \
            f"{safe_name}: obcy/niepełny zestaw pól rekordu: {_redact(sorted(rec), pins)}"
        # (C) metadane: wartości KONTRAKTOWE (nie „w widełkach") — pole liczbowe
        #     przestaje być kanałem na cyfry sekretu.
        assert rec["kdf"] == pin_auth.KDF_NAME, f"{safe_name}: obcy/zmodyfikowany znacznik kdf"
        assert isinstance(rec["iter"], int) and not isinstance(rec["iter"], bool), \
            f"{safe_name}: iter nie jest liczbą całkowitą"
        assert pin_auth.PBKDF2_ITERATIONS >= pin_auth._MIN_ITER_FLOOR, (
            "KONTRAKT PRODUKCJI: PBKDF2_ITERATIONS zeszło poniżej floora "
            "(osłabienie kosztu KDF)")
        assert rec["iter"] == pin_auth.PBKDF2_ITERATIONS, (
            f"{safe_name}: iter != kontraktowy PBKDF2_ITERATIONS "
            "(osłabienie kosztu ALBO cyfry sekretu w parametrze KDF)")
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

    # (G) SKAN DOSŁOWNY CAŁEGO BLOBU — oracle mastera przywrócony (bez flake'a
    #     dzięki kontraktowi długości PIN-u). Domyka pola SWOBODNE: sól i iter.
    blob = json.dumps(store, ensure_ascii=False)
    for pin in pins:
        for label, form in _surface_forms(pin).items():
            assert form not in blob, (
                f"PIN w magazynie KDF w postaci {label} (wyciek) — pole swobodne "
                "(sól/iter/klucz) niesie sekret")
        for frag in _fragments(pin, BLOB_FRAGMENT_MIN_LEN):
            assert frag not in blob, (
                f"fragment PIN-u (>= {BLOB_FRAGMENT_MIN_LEN} zn.) w magazynie KDF "
                "(wyciek częściowy)")


def assert_salt_not_derived_from_secret(records):
    """SONDA PRODUKCYJNA uzupełniająca oracle plikowy (blind review F1).

    `records`: rekordy zbudowane PRODUKCYJNYM `make_record` dla RÓŻNYCH PIN-ów
    przy IDENTYCZNEJ entropii (wołający przypina `secrets.token_bytes`/`os.urandom`).
    Sól i `iter` są parametrami PUBLICZNYMI i WOLNYMI — z samego pliku nie da się
    wykluczyć każdego odwracalnego zakodowania PIN-u w soli, więc niezależność od
    sekretu sprawdzamy na KODZIE: ta sama entropia + różne PIN-y = ta sama sól.
    """
    assert len(records) >= 2, "sonda wymaga rekordów dla >= 2 RÓŻNYCH PIN-ów"
    salts = {rec["salt"] for rec in records}
    assert len(salts) == 1, (
        "SÓL ZALEŻY OD PIN-u (salt derived from secret): przy TEJ SAMEJ entropii "
        f"różne PIN-y dały {len(salts)} różnych soli — sól niesie sekret")
    iters = {rec["iter"] for rec in records}
    assert len(iters) == 1, (
        f"iter ZALEŻY OD PIN-u ({len(iters)} różnych wartości przy tej samej "
        "konfiguracji) — parametr KDF niesie sekret")


# ── KATALOG REALNYCH FORM WYCIEKU (bramka mutacyjna oracle) ──────────────────
# Każda funkcja mutuje POPRAWNY magazyn tak, by PIN dało się z niego odzyskać.
# Kontrakt: po KAŻDEJ z nich `assert_no_plaintext_pin_in_store` MUSI być RED.
# Egzekwują to: `tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle`
# oraz sekcja `--mutations` sweepa anty-flake (jedno źródło, zero kopii).
# Formy zachowujące kształt pól przeliczają hash (`_rehash`), żeby rekord POZOSTAŁ
# prawdziwym PBKDF2 — mutacja ma być łapana przez SWOJĄ asercję, a nie przypadkiem
# przez bijekcję (F). To jest dokładnie luka, którą wykazał blind review.

def _rehash(rec, pin):
    """Przelicz hash dla aktualnej soli/iter — rekord zostaje prawdziwym PBKDF2."""
    rec["hash"] = pin_auth._hash_pin(pin, bytes.fromhex(rec["salt"]), int(rec["iter"])).hex()


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
    # Wyciek NIEWIDOCZNY dla skanu samą wartością (PIN zakodowany hexem, kształt
    # 64 zn. hex zachowany) — łapie go warunek (F) bijekcji KDF oraz (G).
    store[name]["hash"] = pin.encode("utf-8").hex().ljust(HASH_HEX_LEN, "0")[:HASH_HEX_LEN]


def _leak_iter_below_floor(store, name, pin):
    # Osłabienie kosztu KDF = PIN do złamania offline w sekundy.
    store[name]["iter"] = 1


# ── formy ZACHOWUJĄCE kształt pól (F1/F2 z blind review 2026-08-05) ──────────

def _leak_pin_prefix_of_salt(store, name, pin):
    """Sól = PIN + reszta losowego hexa: cyfry PIN-u SĄ znakami hex, więc długość
    i alfabet pozostają poprawne. Realny wzorzec błędu: „salt derived from secret"."""
    rec = store[name]
    rec["salt"] = pin + rec["salt"][len(pin):]
    _rehash(rec, pin)


def _leak_pin_embedded_in_salt(store, name, pin):
    """PIN w ŚRODKU soli — długość i alfabet zachowane."""
    rec = store[name]
    s = rec["salt"]
    off = (len(s) - len(pin)) // 2
    rec["salt"] = s[:off] + pin + s[off + len(pin):]
    _rehash(rec, pin)


def _leak_pin_suffix_of_salt(store, name, pin):
    rec = store[name]
    rec["salt"] = rec["salt"][:-len(pin)] + pin
    _rehash(rec, pin)


def _leak_pin_reversed_in_salt(store, name, pin):
    """PIN odwrócony w soli — odzysk trywialny, kształt zachowany."""
    rec = store[name]
    rec["salt"] = pin[::-1] + rec["salt"][len(pin):]
    _rehash(rec, pin)


def _leak_pin_hexencoded_in_salt(store, name, pin):
    """Sól = hex(utf-8 PIN) dopełniony zerami — PIN odzyskiwalny `bytes.fromhex`."""
    rec = store[name]
    rec["salt"] = pin.encode("utf-8").hex().ljust(SALT_HEX_LEN, "0")[:SALT_HEX_LEN]
    _rehash(rec, pin)


def _leak_pin_split_salt_and_key(store, name, pin):
    """Połowa PIN-u w kluczu tożsamości, połowa w soli (rekonstrukcja trywialna)."""
    half = len(pin) // 2
    rec = store.pop(name)
    rec["salt"] = pin[half:] + rec["salt"][len(pin) - half:]
    _rehash(rec, pin)
    store[f"{name} #{pin[:half]}"] = rec


def _leak_pin_in_iter_value(store, name, pin):
    """Cyfry PIN-u wpisane w parametr kosztu (`iter`): wartość zostaje powyżej
    floora, a hash jest przeliczany, więc rekord pozostaje prawdziwym PBKDF2 —
    forma czerwieni WYŁĄCZNIE przez kontraktową równość `iter`.
    (Nosimy 4 ostatnie cyfry, bo `PBKDF2_ITERATIONS + int(pin)` dla 12-cyfrowego
    PIN-u oznaczałoby ~10¹² iteracji PBKDF2 w teście.)"""
    rec = store[name]
    rec["iter"] = pin_auth.PBKDF2_ITERATIONS + int(pin[-4:])
    _rehash(rec, pin)


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
    # iter2 (blind review 2026-08-05): formy ZACHOWUJĄCE kształt pól.
    "pin_prefix_of_salt": _leak_pin_prefix_of_salt,
    "pin_embedded_in_salt": _leak_pin_embedded_in_salt,
    "pin_suffix_of_salt": _leak_pin_suffix_of_salt,
    "pin_reversed_in_salt": _leak_pin_reversed_in_salt,
    "pin_hexencoded_in_salt": _leak_pin_hexencoded_in_salt,
    "pin_split_salt_and_key": _leak_pin_split_salt_and_key,
    "pin_in_iter_value": _leak_pin_in_iter_value,
}
