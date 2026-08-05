"""A-6/G2 — RATCHET wrażliwych operacji na POŚWIADCZENIACH kuriera (test-only).

Bramka: ``engine.a6-ratchet-dict-get-alias`` (reskope na masterze ``c91b7ede8``).
ITER2: domknięcie 6 findingów blind-review (CONFIRMED_DEFECT, 2026-08-04) —
patrz sekcja „CO ZMIENIŁA ITER2" na końcu docstringa.

CO CHRONIMY I DLACZEGO
----------------------
Master ma DOKŁADNIE JEDNEGO właściciela kryptografii poświadczeń:
``identity/pin_auth.py`` (A-6 SECURITY P0, 2026-08-02) — PBKDF2-HMAC-SHA256 +
sól per-user nad magazynem PIN-ów kurierów. Ten sam moduł jest jedynym, który
bierze ``fcntl.flock`` na PLIKU POŚWIADCZEŃ (``kurier_piny_kdf.json`` +
lockfile obok) — serializacja cross-proces leniwego re-hashu i onboardingu.

Bez strażnika KAŻDY przyszły merge może dopisać DRUGIEGO konsumenta krypto
poświadczeń albo drugiego lockera magazynu PIN-ów i nikt tego nie zauważy:
  * drugi KDF = druga polityka kosztu/soli → ciche osłabienie magazynu
    (rekord z ``iter=1000`` wygląda jak legalny),
  * drugi locker = własny protokół blokady → lost-update na magazynie
    poświadczeń (dokładnie klasa defektu K03/A-1, tam już zmierzona),
  * jedno i drugie omija ``verify_record``/``_MIN_ITER_FLOOR`` i oracle
    z ``tests/test_a6_security_pin_kdf.py``, bo tamten testuje OWNERA,
    a nie CAŁE repo.

ZAMROŻONE LISTY POLITYKI (stan mastera ``c91b7ede8``): ``FROZEN_CRYPTO_OWNERS``,
``FROZEN_CREDENTIAL_LOCK_OWNERS``, ``FROZEN_MODULE_SCOPE_LOCK_EXEMPT`` — każdy
wpis ma uzasadnienie przy stałej. Dopisanie wpisu = ŚWIADOMA zmiana polityki
bezpieczeństwa (owner ACK), nie „poprawka czerwonego testu".

ZAKRES SKANU (discovery, nie enumeracja — lekcja O1 z G3)
---------------------------------------------------------
Skanujemy CAŁE drzewo repo ``*.py`` znalezione przejściem katalogów, z prune
wyłącznie katalogów NIE-ŹRÓDŁOWYCH (``PRUNED_DIR_NAMES`` — po NAZWIE, na
dowolnej głębokości) oraz KORZENIOWEGO ``tests/`` (``PRUNED_ROOT_RELATIVE_DIRS``
— po ŚCIEŻCE WZGLĘDNEJ). Nie ma listy modułów do sprawdzenia — nowy plik
w nowym podkatalogu jest objęty od razu (dowód:
``test_discovery_picks_up_brand_new_nested_module``).

KORZENIOWY ``tests/`` jest poza skanem ŚWIADOMIE: zagrożeniem jest DRUGI
KONSUMENT PRODUKCYJNY (kod, który dotyka żywego magazynu PIN-ów), a korzeniowa
suita ani nie działa na produkcji, ani nie ma dostępu do żywego
``dispatch_state`` (HERMETIC-GUARD). Testy bezpieczeństwa MUSZĄ móc wołać
prymitywy KDF i kompilować syntetyczne obejścia (robi to ten plik), więc
objęcie ich polityką ownera dałoby stały fałszywy alarm i presję na
rozluźnianie ratchetu.

ZAGNIEŻDŻONE katalogi ``*/tests/`` (dziś ``daily_accounting/tests``,
``tools/eta_calibration/tests``) SĄ w zakresie: leżą WEWNĄTRZ pakietów
produkcyjnych, są normalnie importowalne przez kod produkcyjny i nie mają nic
wspólnego z HERMETIC-GUARD korzeniowej suity (finding F6 blind-review — iter1
zostawiało tam gotowe miejsce na drugiego konsumenta KDF).

ODPORNOŚĆ NA OBEJŚCIA (lekcja K9 + blind-review iter1)
------------------------------------------------------
Wykrywane formy dostępu do chronionego prymitywu (każda ma test mutacyjny):
bezpośredni atrybut, ``import ... as``, ``from ... import ... as``, alias przez
zmienną, ``getattr`` z literałem, LITERALNY ``dict.get`` / subskrypcja słownika
(defekt K9), string sklejany (``"pbkdf2" + "_hmac"``), f-string, ``crypt.crypt``,
importy KDF z bibliotek trzecich (``passlib``/``argon2``/``cryptography…kdf``),
ręcznie zbudowany KDF (iterowany hash), NIEROZWIĄZYWALNY dostęp dynamiczny
(``getattr(hashlib, os.environ[...])``, ``importlib.import_module``) oraz — dla
locków — dowolne sięgnięcie po moduł ``fcntl`` w zakresie dotykającym
poświadczeń, także wtedy, gdy lock siedzi w helperze albo context-managerze.

GRANICE HEURYSTYK (jawnie, żeby nikt ich nie odkrywał metodą prób)
------------------------------------------------------------------
1. ``credential-lock`` (noga precyzyjna) — lock jest przypisywany do zakresu,
   który dotyka poświadczeń BEZPOŚREDNIO **albo** został tak „zabrudzony"
   przez PRZEPŁYW WARTOŚCI: wołający przekazuje do helpera wartość wywodzącą
   się ze ścieżki/uchwytu magazynu poświadczeń (``_take_lock(lk)``, gdzie
   ``lk = open(kdf_path + '.lock')``), albo helper jest wołany z funkcji,
   której WŁASNA NAZWA zdradza magazyn (``rewrite_kurier_piny_kdf(p, ...)``
   → jej parametry są traktowane jako poświadczeniowe). Propagacja jest
   intra-modułowa (po nazwach funkcji zdefiniowanych w tym samym pliku),
   liczona do punktu stałego, więc łańcuch ``save → mid → _lock`` też jest
   objęty. NIE śledzimy: atrybutów obiektów (``self.path``), globalnych
   mutowalnych rejestrów, przepływu cross-moduł — od tego jest noga 2.
2. ``credential-lock-module`` (noga fail-closed) — domyka resztę: moduł, który
   GDZIEKOLWIEK dotyka ścieżek poświadczeń i JEDNOCZEŚNIE sięga po ``fcntl``
   (import albo użycie), jest findingiem, chyba że leży na liście
   ``FROZEN_CREDENTIAL_LOCK_OWNERS`` albo ``FROZEN_MODULE_SCOPE_LOCK_EXEMPT``
   (wyjątki Z UZASADNIENIEM, zmierzone na masterze). Wyjątek dotyczy WYŁĄCZNIE
   nogi modułowej — noga precyzyjna działa w tych modułach dalej, więc wpis
   nie jest licencją na lock magazynu PIN-ów.
2b. ZMIERZONE granice nogi lock (obie nogi patrzą WYŁĄCZNIE na ``fcntl``):
   NIE wykrywamy locka zbudowanego na ``os.open(..., O_EXCL)``, bibliotece
   ``filelock``/``portalocker``, ``multiprocessing.Lock``/``threading.Lock``
   ani ścieżki magazynu wyprodukowanej dopiero w runtime (``os.environ[...]``).
   Powód jest POMIAROWY, nie „nie chciało się": objęcie prymitywów
   wątkowo/procesowych zapaliłoby nogę modułową na ``common.py``
   i ``gps_server.py`` (oba dotykają nazw PIN-owych i używają ``threading``),
   czyli kupilibyśmy dwa nowe wyjątki-ślepe-plamy za jedną hipotetyczną formę.
   Do rozstrzygnięcia osobną bramką, gdy pojawi się realny konsument.
3. ``crypto`` / ręczny KDF (F2) — minimalna tractable reguła: wywołanie
   prymitywu haszującego (``hmac.new``, ``hashlib.new``, ``hashlib.sha*``…)
   WEWNĄTRZ pętli ``for``/``while``, w pliku dotykającym ścieżek poświadczeń
   ALBO w zakresie, który jednocześnie nazywa materiał sekretny
   (``pin``/``password``/``secret``…) i parametr kosztu/soli
   (``salt``/``rounds``/``kdf``/``derive``…; dopasowanie CZŁONEM identyfikatora,
   bo substring „pin" siedzi w „shipping"). NIE wykrywamy: KDF rozwiniętego bez
   pętli (``functools.reduce``, rekurencja), „pętli" zapisanej jako
   comprehension, ani KDF w innym repo. To ŚWIADOMY kompromis: szersza reguła
   (każdy hash w pętli) dałaby fałszywe alarmy na legalnym haszowaniu kolekcji
   i rozbroiłaby bramkę.
4. Dostęp dynamiczny (F5) — NIE próbujemy rozwiązywać ``os.environ``, ``bytes``
   ani ``chr()``. Fail-closed: NIEROZWIĄZYWALNA nazwa w ``getattr(<chroniony
   moduł>, …)`` = finding; ``importlib``/``__import__`` chronionego modułu =
   finding; dynamiczny import o nierozwiązywalnej nazwie = finding TYLKO
   w pliku dotykającym poświadczeń (inaczej fałszywy alarm na legalnym
   ładowaniu modułów po nazwie, np. ``daily_accounting/tests/run_all.py``).

Ratchet jest KONSERWATYWNY: przy niejednoznaczności woli fałszywy alarm niż
cichego writera (ta sama zasada co ``test_committed_pickup_authority_ratchet``).
Fałszywy alarm kosztuje jedno wyjaśnienie w review; przeoczony drugi konsument
krypto poświadczeń kosztuje wyciek PIN-ów całej floty.

CO ZMIENIŁA ITER2 (6 findingów blind-review, każdy z testem regresyjnym)
------------------------------------------------------------------------
* **F1** — noga lock była ślepa na dekompozycję helperem (``_take_lock(fh)``,
  ``@contextmanager exclusive(path)``): propagacja „taint" po grafie wywołań
  w module + fail-closed noga modułowa (``credential-lock-module``).
* **F6** — prune zjadał KAŻDY katalog ``tests`` na dowolnej głębokości (656
  z 1332 plików poza skanem): teraz prune tylko korzeniowego ``tests/``;
  próg anty-pustki podniesiony z 400 do stanu faktycznego (690 → próg 650).
* **F3** — ``crypt``/``crypt.crypt`` dopisane do chronionych nazw (l. 353 iter1
  deklarowała moduł ``crypt`` w intencji, implementacja nazwy nie miała).
* **F4** — wykrywanie KDF z bibliotek trzecich po ŚCIEŻCE MODUŁU IMPORTU
  (``passlib.*``, ``cryptography…kdf.*``, ``argon2``, ``nacl.pwhash``).
* **F2** — ręczny KDF (iterowany ``hmac.new``/``hashlib.sha256`` w pętli).
* **F5** — fail-closed na nierozwiązywalny dostęp dynamiczny do chronionych
  modułów (``getattr`` z ``os.environ``/``bytes``/``chr``, ``importlib``).

HERMETYCZNY: wyłącznie ODCZYT plików repo + skan syntetycznych drzew w
``tmp_path``. Zero I/O do ``dispatch_state``/``logs``/``flags.json``, zero
uruchamiania wstrzykniętego kodu (mutacje są PARSOWANE, nigdy importowane).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# Korzeń repo. ``resolve()`` jest konieczny: pod pkgroot testy widzą repo przez
# symlink (``pkgroot/dispatch_v2 -> worktree``) i bez rozwinięcia skanowalibyśmy
# ścieżkę symlinkową zamiast realnego drzewa kandydata.
ROOT = Path(__file__).resolve().parents[1]

# ── polityka: co jest wrażliwe ───────────────────────────────────────────────

# Prymitywy hashowania POŚWIADCZEŃ. ``pbkdf2_hmac`` to dzisiejszy KDF ownera;
# pozostałe są dopisane celowo, żeby „drugi konsument" nie wszedł tylnymi
# drzwiami przez INNY prymityw (scrypt/bcrypt/argon2/crypt dają dokładnie tę
# samą zdolność: zamienić PIN w rekord uwierzytelniający poza kontrolą ownera).
# ``crypt`` — finding F3 blind-review: iter1 obejmowała moduł ``crypt``
# w imporcie, ale nazwy nie miała, więc ``crypt.crypt(pin, mksalt(METHOD_SHA512))``
# przechodziło zielone.
# ``hmac.new`` NIE jest tu celowo — na masterze ma legalnych, niepowiązanych
# z poświadczeniami konsumentów (``tools/process_debt_gate``,
# ``uwagi_bridge_envelope``); ręczny KDF z ``hmac.new`` łapie reguła F2 niżej.
PROTECTED_CRYPTO_NAMES = frozenset({
    "pbkdf2_hmac", "scrypt", "bcrypt", "argon2", "argon2id", "hash_password",
    "crypt",
})
# Fragmenty stringów zdradzające dynamiczny dostęp do powyższych
# (``getattr(hashlib, "pbkdf2" + "_hmac")``, ``FUNCS.get("pbkdf2_hmac")``).
# UWAGA: „crypt" ŚWIADOMIE nie jest tokenem STRINGOWYM — złapałby każde
# „encrypted"/„decrypt" w repo (a fałszywy alarm rozbraja bramkę); dostęp do
# modułu ``crypt`` jest łapany po NAZWIE i po IMPORCIE.
PROTECTED_CRYPTO_STR_TOKENS = frozenset({
    "pbkdf2", "scrypt", "bcrypt", "argon2",
})

# F4: moduły KDF z bibliotek trzecich. Sam IMPORT jest findingiem — te pakiety
# istnieją po to, żeby liczyć hash poświadczeń, więc ich pojawienie się poza
# ownerem jest z definicji drugą polityką kosztu/soli. Dopasowanie po ŚCIEŻCE
# MODUŁU (prefiks), nie po identyfikatorze: ``from passlib.hash import
# pbkdf2_sha256`` nie wnosi do pliku ŻADNEJ chronionej nazwy.
PROTECTED_KDF_IMPORT_MODULES = frozenset({
    "passlib", "argon2", "argon2_cffi", "bcrypt", "scrypt", "crypt",
    "nacl.pwhash", "cryptography.hazmat.primitives.kdf", "Crypto.Protocol.KDF",
})

# F5: moduły, których DYNAMICZNY dostęp (nierozwiązywalny ``getattr``,
# ``importlib.import_module``, ``__import__``) jest fail-closed findingiem.
DYNAMIC_SENSITIVE_MODULES = frozenset({
    "hashlib", "hmac", "crypt", "fcntl", "passlib", "argon2", "bcrypt",
    "cryptography", "nacl",
})

# F2: prymitywy haszujące, z których buduje się ręczny KDF (iterowany hash).
HASH_PRIMITIVE_NAMES = frozenset({
    "new", "md5", "sha1", "sha224", "sha256", "sha384", "sha512",
    "sha3_224", "sha3_256", "sha3_384", "sha3_512", "blake2b", "blake2s",
    "shake_128", "shake_256",
})
HASH_PRIMITIVE_MODULES = frozenset({"hashlib", "hmac"})
# Podpowiedzi „to jest KDF poświadczeń", dopasowywane do CZŁONÓW identyfikatorów
# (nie substringiem — „shipping" zawiera „pin", „rounded" zawiera „round").
KDF_SECRET_HINTS = frozenset({
    "pin", "pins", "password", "passwd", "secret", "passphrase",
    "credential", "credentials",
})
KDF_COST_HINTS = frozenset({
    "salt", "round", "rounds", "iter", "iters", "iteration", "iterations",
    "stretch", "kdf", "derive", "derivation",
})

# Prymitywy blokady plikowej.
LOCK_NAMES = frozenset({"flock", "lockf"})
LOCK_CONST_NAMES = frozenset({"LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"})
LOCK_MODULE_NAMES = frozenset({"fcntl"})
LOCK_STR_TOKENS = frozenset({"flock", "lockf", "fcntl", "lock_ex", "lock_sh"})

# Ślady PLIKU POŚWIADCZEŃ (magazyn PIN-ów + jego lockfile + parametry ścieżek).
# Dopasowanie substringiem, case-insensitive, ZARÓWNO w stringach jak i w
# identyfikatorach — stała ``KURIER_PINY_PATH`` zdradza cel tak samo dobrze
# jak literał ``".../kurier_piny.json"``.
CREDENTIAL_TOKENS = frozenset({
    "kurier_piny", "kurier_pin", "piny_path", "piny_json",
    "kdf_path", "pin_kdf", "kdf_store", "pin_auth",
})

# ── ZAMROŻONE listy polityki (master c91b7ede8) ──────────────────────────────

# UZASADNIENIE: ``identity/pin_auth`` jest kanonicznym (jedynym) modułem A-6,
# który zamienia PIN w rekord KDF — ma sól per-user, próg kosztu
# (``_MIN_ITER_FLOOR``), stałoczasowe porównanie i własny oracle bezpieczeństwa
# w ``tests/test_a6_security_pin_kdf.py``. Każdy inny moduł czyta PIN-y wyłącznie
# jako legacy mapę ``{pin: name}`` i NIE MA prawa liczyć własnego hasha.
FROZEN_CRYPTO_OWNERS = {
    "identity/pin_auth.py": (
        "A-6 P0: jedyny właściciel KDF poświadczeń (PBKDF2 + sól per-user + "
        "_MIN_ITER_FLOOR + verify_record w stałym czasie)."
    ),
}

# UZASADNIENIE: ``register_pin``/``_atomic_write_json`` trzymają CAŁY cykl
# load→set→write magazynu KDF pod ``fcntl.LOCK_EX`` na DEDYKOWANYM lockfile
# obok pliku (wzorzec P0.5b z ``gps_pwa_store``: lockfile przeżywa ``os.replace``).
# Drugi locker = drugi protokół = lost-update na poświadczeniach.
FROZEN_CREDENTIAL_LOCK_OWNERS = {
    "identity/pin_auth.py": (
        "A-6 P0: jedyny writer magazynu KDF; cykl load→set→write pod "
        "dedykowanym lockfile (serializacja cross-proces lazy re-hash/onboarding)."
    ),
    # ZMIERZONE w iter2 (noga precyzyjna po naprawie F1 — na iter1 niewidoczne):
    # ``_atomic_write(path, …)`` bierze własny ``fcntl.LOCK_EX`` na companion
    # ``.lock`` i JEST wołane z ``KURIER_PINY_PATH`` (l. 392/572) — czyli to
    # realny, drugi writer LEGACY magazynu ``kurier_piny.json``, dokładnie
    # w formie „helper bierze lock", którą opisał recenzent. Zamrażamy JAWNIE,
    # zamiast zwężać regułę: to jednorazowa migracja rosteru z 2026-05-05,
    # już wykonana, poza ścieżką runtime dispatchu (żaden serwis jej nie
    # importuje). Usunięcie/archiwizacja pliku ⇒ usuń też ten wpis.
    "migrations/migrate_couriers_2026-05-05.py": (
        "Jednorazowa migracja rosteru 2026-05-05 (wykonana, poza runtime): "
        "_atomic_write pod fcntl.LOCK_EX na kurier_piny.json — historyczny "
        "writer magazynu LEGACY PIN-ów, zamrożony świadomie."
    ),
}

# Wyjątki WYŁĄCZNIE od nogi MODUŁOWEJ (fail-closed). Moduł ma ``fcntl`` i gdzieś
# wspomina poświadczenia, ale lock dotyczy UDOWODNIONO innego pliku. Noga
# precyzyjna (``credential-lock``) działa w tych modułach DALEJ — wpis tutaj NIE
# jest licencją na lock magazynu PIN-ów, tylko przyzwoleniem na współistnienie
# niepowiązanego locka z odczytem legacy mapy PIN-ów w jednym pliku.
FROZEN_MODULE_SCOPE_LOCK_EXEMPT = {
    "courier_resolver.py": (
        "flock wyłącznie w _save_last_known_pos (store pozycji GPS); "
        "kurier_piny.json czytany read-only w innej funkcji "
        "(_load_kurier_piny), bez locka i bez zapisu."
    ),
    "courier_admin.py": (
        "kanoniczny onboarding rosteru (CLAUDE.md: add_new_courier = jedyne "
        "wejście); pisze kurier_piny.json przez _atomic_write_json "
        "(temp+fsync+rename), a ``fcntl`` jest tam zaimportowany, ale NIE "
        "UŻYWANY (docstring l.13 deklaruje LOCK_EX, którego w kodzie nie ma "
        "— osobny dług, nie ta bramka)."
    ),
}

# Katalogi poza skanem — po NAZWIE, na dowolnej głębokości. Wyłącznie
# NIE-ŹRÓDŁOWE (artefakty narzędzi). Lista jest KRÓTKA i jawna, żeby każde
# poszerzenie zakresu ślepoty było widoczne w diffie.
PRUNED_DIR_NAMES = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "htmlcov",
})
# Katalogi poza skanem — po ŚCIEŻCE WZGLĘDNEJ od korzenia repo (F6): prune
# dotyczy WYŁĄCZNIE korzeniowej suity testowej, nie każdego katalogu o nazwie
# „tests" wewnątrz pakietów produkcyjnych.
PRUNED_ROOT_RELATIVE_DIRS = frozenset({"tests"})


# ── discovery ────────────────────────────────────────────────────────────────

def iter_python_files(root: Path) -> list[Path]:
    """Wszystkie ``*.py`` pod ``root`` (discovery, nie enumeracja modułów).

    Prune: nazwy z ``PRUNED_DIR_NAMES`` na dowolnej głębokości + ścieżki
    względne z ``PRUNED_ROOT_RELATIVE_DIRS`` (tylko korzeń repo).

    Katalogi-symlinki są POMIJANE świadomie: pkgroot wystawia
    ``pkgroot/dispatch_v2 -> <worktree>``, więc podążanie za symlinkiem
    skanowałoby to samo drzewo dwa razy (albo w kółko).
    """
    out: list[Path] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = sorted(cur.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in PRUNED_DIR_NAMES:
                    continue
                if entry.relative_to(root).as_posix() in PRUNED_ROOT_RELATIVE_DIRS:
                    continue
                stack.append(entry)
            elif entry.suffix == ".py":
                out.append(entry)
    return out


# ── prymitywy AST ────────────────────────────────────────────────────────────

def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _docstring_nodes(tree: ast.AST) -> set[ast.AST]:
    """Węzły będące docstringami — string „flock" w docstringu opisuje kod,
    nie wykonuje go (inaczej każdy moduł z opisem wzorca blokady byłby czerwony)."""
    out: set[ast.AST] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(body[0].value)
    return out


def _string_aliases(tree: ast.AST) -> dict[str, set[str]]:
    """Nazwa → możliwe wartości stringowe (punkt stały, konserwatywnie).

    Domyka ``A = "kurier_"; B = A + "piny.json"`` niezależnie od kolejności
    obchodzenia drzewa. Rebinding to UNIA (nie ostatnia wartość) — ratchet ma
    nie przegapić żadnego wiązania.
    """
    assignments: list[tuple[list[ast.AST], ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        assignments.append((targets, value))

    aliases: dict[str, set[str]] = {}
    for _ in range(len(assignments) + 1):
        changed = False
        for targets, value in assignments:
            resolved = _resolved_strings(value, aliases)
            if not resolved:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                known = aliases.setdefault(target.id, set())
                before = len(known)
                known.update(resolved)
                changed = changed or len(known) != before
        if not changed:
            break
    return aliases


def _resolved_strings(node: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
    """Statycznie policzalne wartości stringowe wyrażenia (konserwatywnie)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return aliases.get(node.id, set())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolved_strings(node.left, aliases)
        right = _resolved_strings(node.right, aliases)
        return {a + b for a in left for b in right}
    if isinstance(node, ast.JoinedStr):
        combined = {""}
        for part in node.values:
            values = _resolved_strings(part, aliases)
            if not values:
                return set()
            combined = {a + b for a in combined for b in values}
        return combined
    if isinstance(node, ast.FormattedValue):
        if node.format_spec is not None:
            return set()
        return _resolved_strings(node.value, aliases)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"join", "format"}):
        # os.path.join("...","kurier_piny.json") / "%s".format(...) — interesują
        # nas TYLKO literalne człony; wystarczy do dopasowania substringiem.
        parts: set[str] = set()
        for arg in list(node.args) + [node.func.value]:
            parts |= _resolved_strings(arg, aliases)
        return parts
    return set()


def _identifiers(node: ast.AST) -> set[str]:
    """Wszystkie identyfikatory w poddrzewie (nazwy, atrybuty, argumenty,
    aliasy importów) — nazwa stałej zdradza cel tak samo jak literał."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
        elif isinstance(sub, ast.arg):
            out.add(sub.arg)
        elif isinstance(sub, ast.keyword) and sub.arg:
            out.add(sub.arg)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(sub.name)
        elif isinstance(sub, ast.alias):
            out.add(sub.asname or sub.name)
            out.add(sub.name)
        elif isinstance(sub, ast.ImportFrom) and sub.module:
            out.add(sub.module)
    return out


def _strings_in(node: ast.AST, aliases: dict[str, set[str]],
                docstrings: set[ast.AST]) -> set[str]:
    """Statycznie policzalne stringi w poddrzewie, BEZ docstringów."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if sub in docstrings:
            continue
        if isinstance(sub, (ast.Constant, ast.BinOp, ast.JoinedStr, ast.Name)):
            out |= _resolved_strings(sub, aliases)
    return out


def _touches(tokens: frozenset[str], identifiers: set[str],
             strings: set[str]) -> bool:
    lowered = [i.lower() for i in identifiers] + [s.lower() for s in strings]
    return any(tok in value for tok in tokens for value in lowered)


_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def _word_parts(values: set[str]) -> set[str]:
    """Człony identyfikatorów/stringów (snake + camel) — do dopasowania SŁOWEM.

    Substring dałby tu fałszywe alarmy: „shipping" zawiera „pin",
    „rounded" zawiera „round"."""
    out: set[str] = set()
    for value in values:
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value).lower()
        for chunk in _WORD_SPLIT.split(spaced):
            if chunk:
                out.add(chunk)
    return out


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    """Najbliższy obejmujący ``def``/``async def`` (albo moduł)."""
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return cur
    return cur


def _scope_label(scope: ast.AST) -> str:
    return getattr(scope, "name", "<module>")


def _in_loop(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Czy węzeł leży w ciele pętli ``for``/``while`` swojej funkcji."""
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.For, ast.AsyncFor, ast.While)):
            return True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
    return False


def _module_matches(module: str, prefixes: frozenset[str]) -> bool:
    """``passlib.hash`` pasuje do prefiksu ``passlib``; ``cryptography.hazmat``
    NIE pasuje do ``cryptography.hazmat.primitives.kdf``."""
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _is_dynamic_import_call(call: ast.Call) -> bool:
    func = call.func
    return bool(
        (isinstance(func, ast.Attribute) and func.attr == "import_module")
        or (isinstance(func, ast.Name) and func.id == "__import__")
    )


def _dynamic_import_targets(call: ast.Call,
                            aliases: dict[str, set[str]]) -> set[str]:
    """Statycznie policzalne nazwy modułów ładowanych ``import_module``/``__import__``."""
    if not _is_dynamic_import_call(call) or not call.args:
        return set()
    return _resolved_strings(call.args[0], aliases)


def _module_refs(tree: ast.AST, aliases: dict[str, set[str]]) -> dict[str, str]:
    """Lokalna nazwa → ścieżka modułu, którą reprezentuje.

    Obejmuje ``import x as y``, ``from x import y``, a także wiązania
    ``m = importlib.import_module("hashlib")`` / ``__import__("hashlib")``
    (bez tego F5 kończyłby się na pierwszym poziomie pośrednictwa)."""
    refs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                if al.asname:
                    refs[al.asname] = al.name
                else:
                    head = al.name.split(".")[0]
                    refs[head] = head
        elif isinstance(node, ast.ImportFrom) and node.module:
            for al in node.names:
                refs[al.asname or al.name] = f"{node.module}.{al.name}"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        for module in _dynamic_import_targets(node.value, aliases):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    refs[target.id] = module
    return refs


def _sensitive_module_of(expr: ast.AST, refs: dict[str, str]) -> str | None:
    """Jeśli wyrażenie odnosi się do chronionego modułu — zwróć jego korzeń."""
    if isinstance(expr, ast.Name):
        root = refs.get(expr.id, expr.id).split(".")[0]
        if root in DYNAMIC_SENSITIVE_MODULES:
            return root
    elif isinstance(expr, ast.Attribute):
        cur: ast.AST = expr
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            root = refs.get(cur.id, cur.id).split(".")[0]
            if root in DYNAMIC_SENSITIVE_MODULES:
                return root
    return None


def _is_hash_primitive_call(call: ast.Call, refs: dict[str, str]) -> bool:
    """``hashlib.sha256(...)`` / ``hmac.new(...)`` / ``sha512(...)`` po
    ``from hashlib import sha512`` (F2)."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in HASH_PRIMITIVE_NAMES:
        base = func.value
        if isinstance(base, ast.Name):
            root = refs.get(base.id, base.id).split(".")[0]
            if root in HASH_PRIMITIVE_MODULES:
                return True
    elif isinstance(func, ast.Name):
        target = refs.get(func.id)
        if target:
            parts = target.split(".")
            if parts[0] in HASH_PRIMITIVE_MODULES and parts[-1] in HASH_PRIMITIVE_NAMES:
                return True
    return False


# ── skaner ───────────────────────────────────────────────────────────────────

class Finding:
    __slots__ = ("kind", "relpath", "scope", "lineno", "detail")

    def __init__(self, kind: str, relpath: str, scope: str, lineno: int,
                 detail: str):
        self.kind = kind
        self.relpath = relpath
        self.scope = scope
        self.lineno = lineno
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - tylko komunikat błędu
        return (f"{self.kind} {self.relpath}:{self.lineno} "
                f"({self.scope}) {self.detail}")


def _credential_tainted_functions(tree: ast.AST, parents: dict[ast.AST, ast.AST],
                                  aliases: dict[str, set[str]],
                                  docstrings: set[ast.AST]) -> set[str]:
    """Nazwy funkcji, do których PRZEPŁYWA wartość magazynu poświadczeń (F1).

    Model: helper jest „w służbie poświadczeń", jeżeli jakiekolwiek wywołanie
    w module przekazuje mu wartość dotykającą ścieżek poświadczeń — wprost
    (``_atomic_write(KURIER_PINY_PATH, …)``), przez lokalne wiązanie
    (``lk = open(kdf_path + '.lock'); _take_lock(lk)``) albo przez parametr
    funkcji, której NAZWA zdradza magazyn (``save_kurier_piny(path)``).
    Zabrudzony helper brudzi własne parametry, więc łańcuchy ``save → mid →
    _lock`` też są objęte (punkt stały). Zasięg: JEDEN moduł — dopasowanie po
    nazwie funkcji zdefiniowanej w tym pliku; przepływ cross-moduł i przez
    atrybuty obiektów domyka noga ``credential-lock-module``.
    """
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    local_names = {fn.name for fn in functions}
    if not local_names:
        return set()

    scopes: list[ast.AST] = list(functions) + [tree]

    def _static_touch(expr: ast.AST) -> bool:
        return _touches(CREDENTIAL_TOKENS, _identifiers(expr),
                        _strings_in(expr, aliases, docstrings))

    def _names_used(expr: ast.AST) -> set[str]:
        return {sub.id for sub in ast.walk(expr) if isinstance(sub, ast.Name)}

    # Prekalkulacja: punkt stały niżej propaguje już tylko bity, nie chodzi po AST.
    binds: dict[ast.AST, list[tuple[set[str], bool, set[str]]]] = {}
    calls: dict[ast.AST, list[tuple[str, bool, set[str]]]] = {}
    params: dict[ast.AST, set[str]] = {}

    for fn in functions:
        args = fn.args
        collected = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        if args.vararg:
            collected.append(args.vararg)
        if args.kwarg:
            collected.append(args.kwarg)
        params[fn] = {a.arg for a in collected}

    def _record_bind(node: ast.AST, targets: list[ast.AST], value: ast.AST) -> None:
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if not names:
            return
        scope = _enclosing_scope(node, parents)
        binds.setdefault(scope, []).append(
            (names, _static_touch(value), _names_used(value)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            _record_bind(node, list(node.targets), node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            _record_bind(node, [node.target], node.value)
        elif isinstance(node, ast.NamedExpr):
            _record_bind(node, [node.target], node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _record_bind(node, [node.target], node.iter)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                _record_bind(node.context_expr, [node.optional_vars],
                             node.context_expr)
        elif isinstance(node, ast.Call):
            func = node.func
            callee = (func.id if isinstance(func, ast.Name)
                      else func.attr if isinstance(func, ast.Attribute) else None)
            if callee not in local_names:
                continue
            payload = list(node.args) + [kw.value for kw in node.keywords]
            touch = any(_static_touch(arg) for arg in payload)
            used: set[str] = set()
            for arg in payload:
                used |= _names_used(arg)
            calls.setdefault(_enclosing_scope(node, parents), []).append(
                (callee, touch, used))

    name_touch = {fn: _touches(CREDENTIAL_TOKENS, {fn.name}, set())
                  for fn in functions}

    tainted: set[str] = set()
    for _ in range(len(local_names) + 2):
        changed = False
        for scope in scopes:
            tainted_names: set[str] = set()
            if scope in params and (name_touch[scope] or scope.name in tainted):
                tainted_names |= params[scope]
            scope_binds = binds.get(scope, [])
            for _pass in range(len(scope_binds) + 1):
                grew = False
                for names, touch, used in scope_binds:
                    if (touch or (used & tainted_names)) and not names <= tainted_names:
                        tainted_names |= names
                        grew = True
                if not grew:
                    break
            for callee, touch, used in calls.get(scope, []):
                if (touch or (used & tainted_names)) and callee not in tainted:
                    tainted.add(callee)
                    changed = True
        if not changed:
            break
    return tainted


def _crypto_findings(tree: ast.AST, relpath: str, parents: dict[ast.AST, ast.AST],
                     aliases: dict[str, set[str]], docstrings: set[ast.AST],
                     refs: dict[str, str], file_touches: bool) -> list[Finding]:
    findings: list[Finding] = []

    def add(node: ast.AST, detail: str, kind: str = "crypto") -> None:
        findings.append(Finding(
            kind, relpath, _scope_label(_enclosing_scope(node, parents)),
            getattr(node, "lineno", 0), detail))

    # (a1) IMPORTY: chroniona nazwa z hashlib/crypt + moduły KDF trzecich (F4).
    crypto_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in {"hashlib", "crypt"}:
                for al in node.names:
                    if al.name in PROTECTED_CRYPTO_NAMES:
                        crypto_aliases.add(al.asname or al.name)
            if _module_matches(node.module, PROTECTED_KDF_IMPORT_MODULES):
                add(node, f"import KDF z modułu {node.module!r} (biblioteka trzecia)")
                for al in node.names:
                    crypto_aliases.add(al.asname or al.name)
        elif isinstance(node, ast.Import):
            for al in node.names:
                head = al.name.split(".")[0]
                if head in PROTECTED_CRYPTO_NAMES:
                    crypto_aliases.add(al.asname or head)
                if _module_matches(al.name, PROTECTED_KDF_IMPORT_MODULES):
                    add(node, f"import KDF z modułu {al.name!r} (biblioteka trzecia)")
                    crypto_aliases.add(al.asname or head)

    # (a2) KRYPTO POŚWIADCZEŃ — każde SIĘGNIĘCIE, nie tylko wywołanie: alias
    #      ``h = hashlib.pbkdf2_hmac`` jest wykrywany w miejscu wiązania.
    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.Attribute) and node.attr in PROTECTED_CRYPTO_NAMES:
            hit = f"atrybut .{node.attr}"
        elif isinstance(node, ast.Name) and (
                node.id in PROTECTED_CRYPTO_NAMES or node.id in crypto_aliases):
            hit = f"nazwa {node.id}"
        elif node in docstrings:
            continue
        elif isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr)):
            for value in _resolved_strings(node, aliases):
                low = value.lower()
                if any(tok in low for tok in PROTECTED_CRYPTO_STR_TOKENS):
                    hit = f"string {value!r} (dostęp dynamiczny)"
                    break
        if hit:
            add(node, hit)

    # (a3) F5 — DOSTĘP DYNAMICZNY do chronionego modułu (fail-closed).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            module = _sensitive_module_of(node.args[0], refs)
            if module and not _resolved_strings(node.args[1], aliases):
                kind = "credential-lock" if module in LOCK_MODULE_NAMES else "crypto"
                add(node, f"getattr({module}, <nazwa nierozwiązywalna statycznie>) "
                          "— fail-closed", kind)
        if _is_dynamic_import_call(node):
            targets = _dynamic_import_targets(node, aliases)
            if targets:
                for module in sorted(targets):
                    root = module.split(".")[0]
                    if (root in DYNAMIC_SENSITIVE_MODULES
                            or _module_matches(module, PROTECTED_KDF_IMPORT_MODULES)):
                        kind = ("credential-lock" if root in LOCK_MODULE_NAMES
                                else "crypto")
                        add(node, f"dynamiczny import chronionego modułu {module!r}",
                            kind)
            elif file_touches and node.args:
                add(node, "dynamiczny import o nazwie nierozwiązywalnej statycznie, "
                          "w pliku dotykającym magazynu poświadczeń — fail-closed")

    # (a4) F2 — RĘCZNY KDF: prymityw haszujący w pętli (granice w docstringu).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_hash_primitive_call(node, refs):
            continue
        if not _in_loop(node, parents):
            continue
        if not file_touches:
            scope = _enclosing_scope(node, parents)
            words = _word_parts(_identifiers(scope)
                                | _strings_in(scope, aliases, docstrings))
            if not (words & KDF_SECRET_HINTS and words & KDF_COST_HINTS):
                continue
        add(node, "iterowany prymityw haszujący w zakresie poświadczeń "
                  "(ręcznie zbudowany KDF)")

    return findings


def _lock_findings(tree: ast.AST, relpath: str, parents: dict[ast.AST, ast.AST],
                   aliases: dict[str, set[str]], docstrings: set[ast.AST],
                   file_touches: bool) -> list[Finding]:
    findings: list[Finding] = []

    lock_aliases: set[str] = set()
    module_uses_fcntl = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in LOCK_MODULE_NAMES:
            module_uses_fcntl = True
            for al in node.names:
                if al.name in LOCK_NAMES or al.name in LOCK_CONST_NAMES:
                    lock_aliases.add(al.asname or al.name)
        elif isinstance(node, ast.Import):
            for al in node.names:
                if al.name.split(".")[0] in LOCK_MODULE_NAMES:
                    module_uses_fcntl = True
                    lock_aliases.add(al.asname or al.name.split(".")[0])

    lock_sites: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (
                node.attr in LOCK_NAMES or node.attr in LOCK_CONST_NAMES):
            lock_sites.append(node)
        elif isinstance(node, ast.Name) and (
                node.id in LOCK_NAMES or node.id in LOCK_CONST_NAMES
                or node.id in LOCK_MODULE_NAMES or node.id in lock_aliases):
            lock_sites.append(node)
        elif node in docstrings:
            continue
        elif isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr)):
            for value in _resolved_strings(node, aliases):
                low = value.lower()
                if any(tok in low for tok in LOCK_STR_TOKENS):
                    lock_sites.append(node)
                    break
    if lock_sites:
        module_uses_fcntl = True

    # Propagacja taintu jest droga (punkt stały po całym module), a ma sens
    # wyłącznie gdy w pliku W OGÓLE jest jakiś lock — 90 % repo nie ma.
    tainted_functions = (
        _credential_tainted_functions(tree, parents, aliases, docstrings)
        if lock_sites else set())

    for node in lock_sites:
        scope = _enclosing_scope(node, parents)
        direct = _touches(CREDENTIAL_TOKENS, _identifiers(scope),
                          _strings_in(scope, aliases, docstrings))
        inherited = (isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and scope.name in tainted_functions)
        if direct or inherited:
            detail = ("blokada plikowa w zakresie dotykającym magazynu poświadczeń"
                      if direct else
                      "blokada plikowa w helperze wołanym ze ścieżki poświadczeń "
                      "(propagacja po grafie wywołań)")
            findings.append(Finding(
                "credential-lock", relpath, _scope_label(scope),
                getattr(node, "lineno", 0), detail))

    # Noga FAIL-CLOSED (F1): moduł dotyka poświadczeń i sięga po fcntl —
    # niezależnie od tego, czy statyczna analiza zakresu połączyła kropki.
    if module_uses_fcntl and file_touches:
        first = min((getattr(n, "lineno", 0) for n in lock_sites), default=0)
        findings.append(Finding(
            "credential-lock-module", relpath, "<module>", first,
            "moduł sięga po fcntl i dotyka ścieżek magazynu poświadczeń "
            "(reguła fail-closed — dekompozycja helperem nie ukrywa locka)"))

    return findings


def scan_source(source: str, relpath: str) -> list[Finding]:
    """Znajdź w JEDNYM module: (a) użycia krypto poświadczeń, (b) blokady
    plikowe w zakresie/module dotykającym poświadczeń. Zwraca listę ``Finding``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Plik niekompilowalny nie jest konsumentem; kompilowalność pilnują
        # inne bramki (py_compile w workflow deployu).
        return []

    parents = _parents(tree)
    docstrings = _docstring_nodes(tree)
    aliases = _string_aliases(tree)
    refs = _module_refs(tree, aliases)
    file_touches = _touches(CREDENTIAL_TOKENS, _identifiers(tree),
                            _strings_in(tree, aliases, docstrings))

    findings = _crypto_findings(tree, relpath, parents, aliases, docstrings,
                                refs, file_touches)
    findings += _lock_findings(tree, relpath, parents, aliases, docstrings,
                               file_touches)
    return findings


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_source(source, rel))
    return findings


def _by_kind(findings: list[Finding], kind: str) -> dict[str, list[Finding]]:
    out: dict[str, list[Finding]] = {}
    for f in findings:
        if f.kind == kind:
            out.setdefault(f.relpath, []).append(f)
    return out


@pytest.fixture(scope="module")
def repo_findings() -> list[Finding]:
    return scan_tree(ROOT)


# ── RATCHET: repo trzyma zamrożoną listę właścicieli ─────────────────────────

def test_crypto_owner_is_frozen(repo_findings):
    """Krypto poświadczeń (PBKDF2/scrypt/bcrypt/argon2/crypt/passlib/ręczny KDF)
    — WYŁĄCZNIE u ownera.

    RED = ktoś dopisał drugiego konsumenta KDF. Nie „napraw testu": albo
    deleguj do ``identity.pin_auth``, albo (świadomie, z owner ACK) rozszerz
    ``FROZEN_CRYPTO_OWNERS`` wraz z uzasadnieniem.
    """
    hits = _by_kind(repo_findings, "crypto")
    intruders = {p: v for p, v in hits.items() if p not in FROZEN_CRYPTO_OWNERS}
    assert not intruders, (
        "DRUGI konsument krypto poświadczeń poza właścicielem "
        f"{sorted(FROZEN_CRYPTO_OWNERS)}: "
        + "; ".join(f"{p} -> {[repr(f) for f in v]}" for p, v in sorted(intruders.items()))
    )


def test_credential_lock_owner_is_frozen(repo_findings):
    """Blokada plikowa na magazynie poświadczeń — WYŁĄCZNIE u ownera.
    Obejmuje lock wzięty w helperze/context-managerze (F1)."""
    hits = _by_kind(repo_findings, "credential-lock")
    intruders = {p: v for p, v in hits.items()
                 if p not in FROZEN_CREDENTIAL_LOCK_OWNERS}
    assert not intruders, (
        "DRUGI locker magazynu poświadczeń poza właścicielem "
        f"{sorted(FROZEN_CREDENTIAL_LOCK_OWNERS)}: "
        + "; ".join(f"{p} -> {[repr(f) for f in v]}" for p, v in sorted(intruders.items()))
    )


def test_module_scope_lock_is_frozen(repo_findings):
    """Fail-closed (F1): moduł z ``fcntl`` + ścieżkami poświadczeń musi być
    JAWNIE rozstrzygnięty — albo jest właścicielem, albo ma wpis wyjątku
    z uzasadnieniem. Nowy moduł łączący te dwie rzeczy = RED, także wtedy, gdy
    lock siedzi w helperze, którego analiza zakresu nie połączyła z magazynem."""
    allowed = set(FROZEN_CREDENTIAL_LOCK_OWNERS) | set(FROZEN_MODULE_SCOPE_LOCK_EXEMPT)
    hits = _by_kind(repo_findings, "credential-lock-module")
    intruders = {p: v for p, v in hits.items() if p not in allowed}
    assert not intruders, (
        "moduł sięga po fcntl i dotyka magazynu poświadczeń, a nie jest "
        f"rozstrzygnięty w polityce {sorted(allowed)}: "
        + "; ".join(f"{p} -> {[repr(f) for f in v]}" for p, v in sorted(intruders.items()))
    )


# ── ANTY-PUSTKA: ratchet MUSI nadal coś widzieć ──────────────────────────────

def test_owner_files_exist():
    """Zamrożony właściciel/wyjątek musi istnieć — przeniesienie/rename
    ``pin_auth`` bez aktualizacji polityki = RED (fail-closed), nie cichy GREEN."""
    for rel in (set(FROZEN_CRYPTO_OWNERS) | set(FROZEN_CREDENTIAL_LOCK_OWNERS)
                | set(FROZEN_MODULE_SCOPE_LOCK_EXEMPT)):
        assert (ROOT / rel).is_file(), f"zamrożony wpis polityki zniknął: {rel}"


def test_owner_still_detected_as_crypto_consumer(repo_findings):
    """Anty-pustka: skaner NADAL wykrywa PBKDF2 u ownera. Gdyby ktoś zepsuł
    detektor (np. wyczyścił ``PROTECTED_CRYPTO_NAMES``), oba ratchety wyszłyby
    zielone bez żadnej ochrony — ten test to blokuje."""
    hits = _by_kind(repo_findings, "crypto")
    assert "identity/pin_auth.py" in hits, (
        "skaner nie widzi PBKDF2 w identity/pin_auth.py — detektor krypto "
        "jest ślepy (ratchet stałby się dekoracją)")


def test_owner_still_detected_as_credential_locker(repo_findings):
    """Anty-pustka dla drugiej nogi: lock na magazynie KDF u ownera JEST widziany."""
    hits = _by_kind(repo_findings, "credential-lock")
    assert "identity/pin_auth.py" in hits, (
        "skaner nie widzi blokady magazynu poświadczeń w identity/pin_auth.py "
        "— detektor locków jest ślepy")
    scopes = {f.scope for f in hits["identity/pin_auth.py"]}
    assert "register_pin" in scopes, (
        f"oczekiwany zakres register_pin nie wykryty; wykryte: {sorted(scopes)}")


def test_module_scope_leg_still_sees_owner(repo_findings):
    """Anty-pustka dla nogi fail-closed: gdyby ktoś ją wyłączył, lista wyjątków
    stałaby się martwą literą, a defekt F1 wróciłby bez czerwieni."""
    hits = _by_kind(repo_findings, "credential-lock-module")
    assert "identity/pin_auth.py" in hits, (
        "noga modułowa nie widzi nawet ownera — jest wyłączona")


def test_module_scope_exemptions_are_still_needed(repo_findings):
    """Wyjątki mają być ŻYWE, nie kolekcjonerskie: każdy wpis w
    ``FROZEN_MODULE_SCOPE_LOCK_EXEMPT`` musi dziś realnie generować finding
    nogi modułowej. Zniknął powód (np. moduł przestał brać fcntl) ⇒ RED,
    czyli wpis do usunięcia, a nie cicha licencja na przyszłość."""
    hits = _by_kind(repo_findings, "credential-lock-module")
    stale = sorted(set(FROZEN_MODULE_SCOPE_LOCK_EXEMPT) - set(hits))
    assert not stale, (
        f"wyjątki bez powodu (usuń je z polityki): {stale}")


# ── DISCOVERY (lekcja O1): skan obejmuje drzewo, nie listę modułów ───────────

def test_discovery_covers_repo_tree():
    files = {p.relative_to(ROOT).as_posix() for p in iter_python_files(ROOT)}
    # kotwice z RÓŻNYCH poziomów drzewa — brak którejkolwiek = dziura w skanie
    for anchor in ("common.py", "identity/pin_auth.py", "core/decide.py",
                   "courier_resolver.py", "gps_server.py",
                   # F6: ZAGNIEŻDŻONE tests/ wewnątrz pakietów produkcyjnych
                   # SĄ w zakresie (iter1 prunowało je razem z korzeniem)
                   "daily_accounting/tests/test_bucket_logic.py",
                   "tools/eta_calibration/tests/test_models.py"):
        assert anchor in files, f"discovery nie objęło {anchor}"
    assert not any(p.startswith("tests/") for p in files), \
        "korzeniowe tests/ miało być poza zakresem (patrz docstring modułu)"
    assert not any("__pycache__" in p for p in files)
    # Skala: master c91b7ede8 ma 690 plików w zakresie (676 z iter1 + 14
    # z zagnieżdżonych tests/). Próg chroni przed cichym zwinięciem skanu;
    # margines to 40 plików na normalny obrót repo, a nie 41 % jak przed iter2.
    assert len(files) >= 650, f"discovery znalazło tylko {len(files)} plików"


def test_discovery_picks_up_brand_new_nested_module(tmp_path):
    """Dowód, że to DISCOVERY, nie enumeracja: plik w nowo utworzonym,
    nieznanym podkatalogu jest skanowany bez żadnej rejestracji."""
    nested = tmp_path / "brand" / "new" / "deep"
    nested.mkdir(parents=True)
    (nested / "sneaky.py").write_text("import hashlib\n", encoding="utf-8")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_python_files(tmp_path)}
    assert "brand/new/deep/sneaky.py" in found


def test_discovery_prunes_only_declared_dirs(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    for pruned in ("tests", "__pycache__", ".git"):
        d = tmp_path / pruned
        d.mkdir()
        (d / "mod.py").write_text("x = 1\n", encoding="utf-8")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_python_files(tmp_path)}
    assert found == {"pkg/mod.py"}


def test_discovery_prunes_root_tests_only(tmp_path):
    """F6: ``tests`` prunujemy WYŁĄCZNIE w korzeniu repo. Zagnieżdżony
    ``<pakiet>/tests/`` jest normalnie importowalny z produkcji, więc jest
    w zakresie — inaczej wystarczy położyć drugiego konsumenta KDF
    w ``tools/tests/``, żeby bramka go nie widziała (dowód recenzenta)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "root_suite.py").write_text("x = 1\n", encoding="utf-8")
    nested = tmp_path / "tools" / "tests"
    nested.mkdir(parents=True)
    (nested / "helper.py").write_text("x = 1\n", encoding="utf-8")
    deep = tmp_path / "pkg" / "sub" / "tests"
    deep.mkdir(parents=True)
    (deep / "deep_helper.py").write_text("x = 1\n", encoding="utf-8")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_python_files(tmp_path)}
    assert found == {"tools/tests/helper.py", "pkg/sub/tests/deep_helper.py"}


def test_discovery_does_not_follow_symlinked_dirs(tmp_path):
    """Pkgroot wystawia symlink na to samo drzewo — podążanie za nim dublowałoby
    skan (i w skrajnym przypadku zapętliło go)."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    found = {p.relative_to(tmp_path).as_posix() for p in iter_python_files(tmp_path)}
    assert found == {"real/mod.py"}


# ── MUTACJE: wstrzyknięty DRUGI konsument = RED (każda forma obejścia) ───────

# Każdy wpis: (id, źródło modułu-intruza). Kod jest WYŁĄCZNIE parsowany —
# nigdy importowany ani wykonywany.
CRYPTO_EVASIONS = [
    pytest.param(
        "import hashlib\n"
        "def h(p, s):\n"
        "    return hashlib.pbkdf2_hmac('sha256', p, s, 1000)\n",
        id="bezposredni-atrybut"),
    pytest.param(
        "import hashlib as _hl\n"
        "def h(p, s):\n"
        "    return _hl.pbkdf2_hmac('sha256', p, s, 1000)\n",
        id="import-modulu-as"),
    pytest.param(
        "from hashlib import pbkdf2_hmac as _k\n"
        "def h(p, s):\n"
        "    return _k('sha256', p, s, 1000)\n",
        id="from-import-as"),
    pytest.param(
        "import hashlib\n"
        "_K = hashlib.pbkdf2_hmac\n"
        "def h(p, s):\n"
        "    return _K('sha256', p, s, 1000)\n",
        id="alias-przez-zmienna"),
    pytest.param(
        "import hashlib\n"
        "def h(p, s):\n"
        "    fn = getattr(hashlib, 'pbkdf2_hmac')\n"
        "    return fn('sha256', p, s, 1000)\n",
        id="getattr-literal"),
    pytest.param(
        "import hashlib\n"
        "_FUNCS = {'kdf': hashlib.pbkdf2_hmac}\n"
        "def h(p, s):\n"
        "    return _FUNCS.get('kdf')('sha256', p, s, 1000)\n",
        id="dict-literal-wartosc"),
    pytest.param(
        "import hashlib\n"
        "_NAMES = {'kdf': 'pbkdf2_hmac'}\n"
        "def h(p, s):\n"
        "    return getattr(hashlib, _NAMES.get('kdf'))('sha256', p, s, 1000)\n",
        id="K9-literalny-dict-get-nazwy"),
    pytest.param(
        "import hashlib\n"
        "def h(p, s):\n"
        "    return getattr(hashlib, {'k': 'pbkdf2_hmac'}['k'])('sha256', p, s, 1000)\n",
        id="K9-subskrypcja-slownika"),
    pytest.param(
        "import hashlib\n"
        "_A = 'pbkdf2'\n"
        "_B = _A + '_hmac'\n"
        "def h(p, s):\n"
        "    return getattr(hashlib, _B)('sha256', p, s, 1000)\n",
        id="string-sklejany"),
    pytest.param(
        "import hashlib\n"
        "_A = 'pbkdf2'\n"
        "def h(p, s):\n"
        "    return getattr(hashlib, f'{_A}_hmac')('sha256', p, s, 1000)\n",
        id="f-string"),
    pytest.param(
        "import hashlib\n"
        "def h(p, s):\n"
        "    return hashlib.scrypt(p, salt=s, n=2, r=8, p=1)\n",
        id="inny-prymityw-scrypt"),
]


@pytest.mark.parametrize("source", CRYPTO_EVASIONS)
def test_injected_second_crypto_consumer_is_red(source, tmp_path):
    """MUTATION PROOF: drugi konsument KDF, w KAŻDEJ formie obejścia, jest
    wykrywany jako naruszenie polityki właściciela."""
    intruder = tmp_path / "sneaky_consumer.py"
    intruder.write_text(source, encoding="utf-8")
    findings = scan_tree(tmp_path)
    crypto = _by_kind(findings, "crypto")
    assert "sneaky_consumer.py" in crypto, (
        "obejście NIE wykryte — ratchet przepuściłby drugiego konsumenta KDF:\n"
        + source)
    # I ta sama treść w repo wywróciłaby ratchet właścicielski:
    intruders = {p for p in crypto if p not in FROZEN_CRYPTO_OWNERS}
    assert intruders == {"sneaky_consumer.py"}


_CRED_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_piny_kdf.json"

CREDENTIAL_LOCK_EVASIONS = [
    pytest.param(
        "import fcntl\n"
        f"KDF_PATH = '{_CRED_PATH}'\n"
        "def w(data):\n"
        "    with open(KDF_PATH + '.lock', 'w') as lk:\n"
        "        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)\n",
        id="bezposredni-fcntl-flock"),
    pytest.param(
        "from fcntl import flock as _fl, LOCK_EX as _EX\n"
        f"def w(kdf_path='{_CRED_PATH}'):\n"
        "    with open(kdf_path + '.lock', 'w') as lk:\n"
        "        _fl(lk.fileno(), _EX)\n",
        id="from-import-as"),
    pytest.param(
        "import fcntl as _f\n"
        "def w(piny_path):\n"
        "    with open(piny_path + '.lock', 'w') as lk:\n"
        "        _f.flock(lk.fileno(), 2)\n",
        id="import-modulu-as"),
    pytest.param(
        "import fcntl\n"
        "_LOCKER = fcntl.flock\n"
        "def w(kdf_path):\n"
        "    with open(kdf_path + '.lock', 'w') as lk:\n"
        "        _LOCKER(lk.fileno(), 2)\n",
        id="alias-przez-zmienna"),
    pytest.param(
        "import fcntl\n"
        "def w(kdf_store_path):\n"
        "    with open(kdf_store_path + '.lock', 'w') as lk:\n"
        "        getattr(fcntl, 'flock')(lk.fileno(), 2)\n",
        id="getattr-literal"),
    pytest.param(
        "import fcntl\n"
        "_OPS = {'lock': fcntl.flock}\n"
        "def w(kdf_path):\n"
        "    with open(kdf_path + '.lock', 'w') as lk:\n"
        "        _OPS.get('lock')(lk.fileno(), 2)\n",
        id="K9-literalny-dict-get"),
    pytest.param(
        "import fcntl\n"
        "_NAMES = {'op': 'flock'}\n"
        "def w(kdf_path):\n"
        "    with open(kdf_path + '.lock', 'w') as lk:\n"
        "        getattr(fcntl, _NAMES.get('op'))(lk.fileno(), 2)\n",
        id="K9-dict-get-nazwy"),
    pytest.param(
        "import fcntl\n"
        "_PREFIX = '/root/.openclaw/workspace/dispatch_state/kurier_'\n"
        "def w():\n"
        "    with open(_PREFIX + 'piny.json.lock', 'w') as lk:\n"
        "        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)\n",
        id="sciezka-sklejana"),
    pytest.param(
        "import fcntl\n"
        "_STATE = '/root/.openclaw/workspace/dispatch_state'\n"
        "def w():\n"
        "    with open(f'{_STATE}/kurier_piny.json.lock', 'w') as lk:\n"
        "        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)\n",
        id="sciezka-f-string"),
    pytest.param(
        "import fcntl, os\n"
        "def w(state_dir):\n"
        "    p = os.path.join(state_dir, 'kurier_piny.json')\n"
        "    with open(p + '.lock', 'w') as lk:\n"
        "        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)\n",
        id="os-path-join"),
    pytest.param(
        "import fcntl\n"
        "def w(kdf_path):\n"
        "    with open(kdf_path + '.lock', 'w') as lk:\n"
        "        op = getattr(fcntl, _pick())\n"
        "        op(lk.fileno(), 2)\n"
        "def _pick():\n"
        "    return 'fl' + 'ock'\n",
        id="dynamiczny-getattr-przez-modul"),
]


@pytest.mark.parametrize("source", CREDENTIAL_LOCK_EVASIONS)
def test_injected_second_credential_locker_is_red(source, tmp_path):
    """MUTATION PROOF: drugi locker magazynu poświadczeń, w KAŻDEJ formie
    obejścia, jest wykrywany."""
    intruder = tmp_path / "sneaky_locker.py"
    intruder.write_text(source, encoding="utf-8")
    findings = scan_tree(tmp_path)
    locks = _by_kind(findings, "credential-lock")
    assert "sneaky_locker.py" in locks, (
        "obejście NIE wykryte — ratchet przepuściłby drugiego lockera "
        "magazynu poświadczeń:\n" + source)


def test_owner_source_itself_would_be_flagged_elsewhere(tmp_path):
    """Najostrzejsza mutacja: BAJTOWA KOPIA modułu ownera pod inną ścieżką
    (klasyczny „drugi writer przez copy-paste") = RED na obu nogach."""
    clone = tmp_path / "identity_copy" / "pin_auth_clone.py"
    clone.parent.mkdir()
    clone.write_text((ROOT / "identity/pin_auth.py").read_text(encoding="utf-8"),
                     encoding="utf-8")
    findings = scan_tree(tmp_path)
    rel = "identity_copy/pin_auth_clone.py"
    assert rel in _by_kind(findings, "crypto")
    assert rel in _by_kind(findings, "credential-lock")


# ── REGRESJA BLIND-REVIEW: 6 findingów recenzenta (na iter1 = ZIELONE) ───────
#
# Poniższe źródła to sondy recenzenta z
# ``/root/artifacts/a6-descope-g2-20260804/blind/reviewer-scratch/``
# (``probe_evasions.py`` rundy 1 i 3, ``probe_round2.py``,
# ``intruders/pin_store_v2.py``). Na kandydacie iter1 KAŻDA przechodziła
# ZIELONO — to jest negatywny oracle tej iteracji. Odwrócenie/usunięcie fixu
# czerwieni je z powrotem.

F1_LOCK_HELPER_PROBES = [
    pytest.param(
        "import fcntl\n"
        "def _take_lock(fh):\n"
        "    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
        "def write_kdf_store(kdf_path, data):\n"
        "    with open(kdf_path + '.lock', 'w') as lk:\n"
        "        _take_lock(lk)\n"
        "        open(kdf_path, 'w').write(data)\n",
        id="L1-helper-lock-poza-zakresem"),
    pytest.param(
        "import fcntl\n"
        f"KDF_PATH = '{_CRED_PATH}'\n"
        "class Store:\n"
        "    def _guard(self, fh):\n"
        "        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
        "    def save(self, data):\n"
        "        with open(KDF_PATH + '.lock', 'w') as lk:\n"
        "            self._guard(lk)\n",
        id="L2-metoda-klasy-bez-tokenu"),
    pytest.param(
        "import fcntl, os\n"
        "def _lk(path):\n"
        "    fd = os.open(path, os.O_CREAT | os.O_RDWR)\n"
        "    fcntl.flock(fd, 2)\n"
        "    return fd\n"
        "def rewrite_kurier_piny_kdf(p, data):\n"
        "    fd = _lk(p + '.lock')\n"
        "    open(p, 'w').write(data)\n",
        id="L3-os-open-fcntl-w-helperze"),
    pytest.param(
        "import fcntl as _f\n"
        "def _serialize(fh):\n"
        "    _f.lockf(fh.fileno(), _f.LOCK_EX)\n"
        "def save_pin_kdf_store(pin_kdf_path, data):\n"
        "    with open(pin_kdf_path + '.lock', 'w') as lk:\n"
        "        _serialize(lk)\n",
        id="L8-fcntl-alias-modulu-w-helperze"),
    pytest.param(
        "import fcntl\n"
        "def save_kurier_piny(path, data):\n"
        "    def _inner(fh):\n"
        "        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
        "    with open(path + '.lock', 'w') as lk:\n"
        "        _inner(lk)\n",
        id="L9-zagniezdzona-funkcja-wewnetrzna"),
    pytest.param(
        "import fcntl\n"
        "def _lock(fh):\n"
        "    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
        "def write_kurier_piny_kdf(kdf_path):\n"
        "    with open(kdf_path + '.lock', 'w') as lk:\n"
        "        _lock(lk)\n",
        id="R5-helper-token-tylko-u-wolajacego"),
    pytest.param(
        "import contextlib, fcntl\n"
        "@contextlib.contextmanager\n"
        "def exclusive(path):\n"
        "    with open(path + '.lock', 'w') as lk:\n"
        "        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)\n"
        "        yield\n"
        "def save_pin_kdf(kdf_path, data):\n"
        "    with exclusive(kdf_path):\n"
        "        open(kdf_path, 'w').write(data)\n",
        id="R6-contextmanager-helper"),
    pytest.param(
        "import fcntl\n"
        "def _take(fh):\n"
        "    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
        "def _mid(fh):\n"
        "    _take(fh)\n"
        "def save_pin_kdf_store(pin_kdf_path):\n"
        "    with open(pin_kdf_path + '.lock', 'w') as lk:\n"
        "        _mid(lk)\n",
        id="lancuch-dwoch-helperow"),
]


@pytest.mark.parametrize("source", F1_LOCK_HELPER_PROBES)
def test_f1_lock_taken_in_helper_is_red(source, tmp_path):
    """F1: dekompozycja helperem/context-managerem NIE ukrywa locka magazynu
    poświadczeń — taint płynie po grafie wywołań w module (noga precyzyjna)."""
    (tmp_path / "sneaky_helper_locker.py").write_text(source, encoding="utf-8")
    locks = _by_kind(scan_tree(tmp_path), "credential-lock")
    assert "sneaky_helper_locker.py" in locks, (
        "F1 wrócił: lock w helperze niewidoczny dla nogi precyzyjnej:\n" + source)


@pytest.mark.parametrize("source", F1_LOCK_HELPER_PROBES)
def test_f1_lock_taken_in_helper_is_red_on_module_leg(source, tmp_path):
    """Ta sama bateria na nodze FAIL-CLOSED: nawet gdyby ktoś rozmontował
    propagację taintu, moduł łączący ``fcntl`` ze ścieżką poświadczeń zostaje
    czerwony (dwie niezależne nogi = brak jednego punktu awarii)."""
    (tmp_path / "sneaky_helper_locker.py").write_text(source, encoding="utf-8")
    module_hits = _by_kind(scan_tree(tmp_path), "credential-lock-module")
    assert "sneaky_helper_locker.py" in module_hits, (
        "F1 wrócił: noga fail-closed nie widzi modułu:\n" + source)


# Dosłowny intruz recenzenta (dowód end-to-end: na iter1 „37 passed" z tym
# plikiem w ``identity/``). Własny KDF (iterowany HMAC, rounds=1000) + własny
# protokół flock na ``kurier_piny_kdf.json``, blokadę bierze helper ``_take_lock``.
REVIEWER_INTRUDER_PIN_STORE_V2 = (
    '"""DRUGI WRITER magazynu poświadczeń (repro recenzenta, NIE do mergu)."""\n'
    "import fcntl\n"
    "import hashlib\n"
    "import hmac\n"
    "import json\n"
    "import os\n"
    "\n"
    f'STORE = "{_CRED_PATH}"\n'
    "\n"
    "def _take_lock(fh):\n"
    "    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
    "\n"
    "def _release(fh):\n"
    "    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)\n"
    "\n"
    "def derive(secret, salt, rounds=1000):\n"
    "    out = hmac.new(salt.encode(), secret.encode(), hashlib.sha256).digest()\n"
    "    for _ in range(rounds - 1):\n"
    "        out = hmac.new(salt.encode(), out, hashlib.sha256).digest()\n"
    "    return out.hex()\n"
    "\n"
    "def upsert(name, secret):\n"
    '    with open(STORE + ".lock", "w") as lk:\n'
    "        _take_lock(lk)\n"
    "        try:\n"
    "            data = json.loads(open(STORE).read()) if os.path.exists(STORE) else {}\n"
    '            data[name] = {"kdf": "hmac-sha256-loop", "salt": "static",\n'
    '                          "iter": 1000, "hash": derive(secret, "static")}\n'
    '            tmp = STORE + ".tmp"\n'
    '            with open(tmp, "w") as f:\n'
    "                f.write(json.dumps(data))\n"
    "                f.flush()\n"
    "                os.fsync(f.fileno())\n"
    "            os.replace(tmp, STORE)\n"
    "        finally:\n"
    "            _release(lk)\n"
)


def test_reviewer_end_to_end_intruder_is_red_on_all_legs(tmp_path):
    """Dowód end-to-end recenzenta: realistyczny „drugi writer" magazynu
    poświadczeń (helper-lock + ręczny KDF) jest RED na WSZYSTKICH trzech
    nogach, także położony wprost w ``identity/``."""
    target = tmp_path / "identity" / "pin_store_v2.py"
    target.parent.mkdir()
    target.write_text(REVIEWER_INTRUDER_PIN_STORE_V2, encoding="utf-8")
    findings = scan_tree(tmp_path)
    rel = "identity/pin_store_v2.py"
    for kind in ("credential-lock", "credential-lock-module", "crypto"):
        assert rel in _by_kind(findings, kind), (
            f"intruz recenzenta niewidoczny na nodze {kind}: {findings}")
    # …i nie jest żadnym z zamrożonych właścicieli, więc wywraca ratchety
    assert rel not in FROZEN_CRYPTO_OWNERS
    assert rel not in FROZEN_CREDENTIAL_LOCK_OWNERS
    assert rel not in FROZEN_MODULE_SCOPE_LOCK_EXEMPT


F2_MANUAL_KDF_PROBES = [
    pytest.param(
        "import hmac, hashlib\n"
        "def kdf_kurier_pin(pin, salt, rounds=1000):\n"
        "    out = hmac.new(salt, pin, hashlib.sha256).digest()\n"
        "    for _ in range(rounds - 1):\n"
        "        out = hmac.new(salt, out, hashlib.sha256).digest()\n"
        "    return out.hex()\n",
        id="C6-recznie-pbkdf2-przez-hmac"),
    pytest.param(
        "import hashlib\n"
        "def derive_pin_record(pin, salt, rounds=200000):\n"
        "    out = (salt + pin).encode()\n"
        "    for _ in range(rounds):\n"
        "        out = hashlib.sha256(out).digest()\n"
        "    return out.hex()\n",
        id="C8-petla-hashlib-sha256"),
    pytest.param(
        "import hashlib\n"
        f"KDF_PATH = '{_CRED_PATH}'\n"
        "def _stretch(material, n):\n"
        "    while n > 0:\n"
        "        material = hashlib.new('sha512', material).digest()\n"
        "        n -= 1\n"
        "    return material\n",
        id="hashlib-new-w-while-plik-poswiadczen"),
]


@pytest.mark.parametrize("source", F2_MANUAL_KDF_PROBES)
def test_f2_hand_rolled_kdf_is_red(source, tmp_path):
    """F2: własnoręczny KDF (iterowany hash) to „druga polityka kosztu i soli"
    z threat modelu — musi być czerwony, mimo że samo ``hmac.new`` chronione
    nie jest (ma legalnych konsumentów na masterze)."""
    (tmp_path / "sneaky_kdf.py").write_text(source, encoding="utf-8")
    crypto = _by_kind(scan_tree(tmp_path), "crypto")
    assert "sneaky_kdf.py" in crypto, ("F2 wrócił: ręczny KDF niewidoczny:\n"
                                       + source)


F3_CRYPT_PROBES = [
    pytest.param(
        "import crypt\n"
        "def hash_kurier_pin(pin):\n"
        "    return crypt.crypt(pin, crypt.mksalt(crypt.METHOD_SHA512))\n",
        id="C7-import-crypt-atrybut"),
    pytest.param(
        "from crypt import crypt as _c\n"
        "def hash_pin(pin, salt):\n"
        "    return _c(pin, salt)\n",
        id="K1-from-crypt-import-crypt"),
    pytest.param(
        # Forma, której NIE łapie reguła importowa (F4): hasher wstrzyknięty
        # z zewnątrz, w pliku nie ma słowa „import crypt". Trzyma przy życiu
        # ``crypt`` w PROTECTED_CRYPTO_NAMES — bez tego wpisu ta sonda zzielenieje.
        "def hash_pin(pin, salt, hasher):\n"
        "    return hasher.crypt(pin, salt)\n",
        id="crypt-przez-wstrzyknietego-hashera"),
]


@pytest.mark.parametrize("source", F3_CRYPT_PROBES)
def test_f3_crypt_module_is_red(source, tmp_path):
    """F3: ``crypt.crypt(METHOD_SHA512)`` to pełnoprawny hasher poświadczeń
    z własną polityką kosztu — intencja w iter1 była, implementacji nie."""
    (tmp_path / "sneaky_crypt.py").write_text(source, encoding="utf-8")
    crypto = _by_kind(scan_tree(tmp_path), "crypto")
    assert "sneaky_crypt.py" in crypto, ("F3 wrócił: crypt niewidoczny:\n"
                                         + source)


F4_THIRD_PARTY_KDF_PROBES = [
    pytest.param(
        "from passlib.hash import pbkdf2_sha256\n"
        "def hash_pin(pin):\n"
        "    return pbkdf2_sha256.hash(pin)\n",
        id="C9-passlib-pbkdf2"),
    pytest.param(
        "from passlib.hash import argon2 as _A\n"
        "def hash_pin(pin):\n"
        "    return _A.using(rounds=2).hash(pin)\n",
        id="C10-passlib-argon2-as"),
    pytest.param(
        "from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC\n"
        "from cryptography.hazmat.primitives import hashes\n"
        "def hash_pin(pin, salt):\n"
        "    k = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, "
        "iterations=1000)\n"
        "    return k.derive(pin)\n",
        id="C11-cryptography-pbkdf2hmac-klasa"),
    pytest.param(
        "import argon2\n"
        "def hash_pin(pin):\n"
        "    return argon2.PasswordHasher().hash(pin)\n",
        id="argon2-cffi-modul"),
    pytest.param(
        "import nacl.pwhash\n"
        "def hash_pin(pin):\n"
        "    return nacl.pwhash.str(pin)\n",
        id="libsodium-pwhash"),
]


@pytest.mark.parametrize("source", F4_THIRD_PARTY_KDF_PROBES)
def test_f4_third_party_kdf_import_is_red(source, tmp_path):
    """F4: najpopularniejsze w Pythonie sposoby liczenia PBKDF2/argon2 idą
    przez BIBLIOTEKĘ, nie przez ``hashlib`` — wykrywamy po ŚCIEŻCE MODUŁU
    importu, bo do pliku nie trafia żadna chroniona nazwa."""
    (tmp_path / "sneaky_lib_kdf.py").write_text(source, encoding="utf-8")
    crypto = _by_kind(scan_tree(tmp_path), "crypto")
    assert "sneaky_lib_kdf.py" in crypto, (
        "F4 wrócił: KDF z biblioteki trzeciej niewidoczny:\n" + source)


F5_DYNAMIC_NAME_PROBES = [
    pytest.param(
        "import hashlib\n"
        "_N = 'pbkdf' + chr(50) + '_hmac'\n"
        "def h(p, s):\n"
        "    return getattr(hashlib, _N)('sha256', p, s, 1000)\n",
        id="C2-nazwa-nieliczalna-chr"),
    pytest.param(
        "import hashlib, os\n"
        "def h(p, s):\n"
        "    return getattr(hashlib, os.environ['KDF_FUNC'])('sha256', p, s, 1000)\n",
        id="C3-getattr-z-env"),
    pytest.param(
        "import importlib\n"
        "def h(p, s):\n"
        "    m = importlib.import_module('hash' + 'lib')\n"
        "    fn = getattr(m, bytes([112,98,107,100,102,50,95,104,109,97,99]).decode())\n"
        "    return fn('sha256', p, s, 1000)\n",
        id="C5-importlib-bytes-decode"),
    pytest.param(
        "import importlib\n"
        "def h(p, s):\n"
        "    return importlib.import_module('passlib.hash').pbkdf2_sha256.hash(p)\n",
        id="importlib-passlib"),
]


@pytest.mark.parametrize("source", F5_DYNAMIC_NAME_PROBES)
def test_f5_dynamic_protected_module_access_is_red(source, tmp_path):
    """F5: nie rozwiązujemy env/bytes/chr — po prostu czerwień na DYNAMICZNYM
    dostępie do chronionego modułu (fail-closed, granica w docstringu)."""
    (tmp_path / "sneaky_dynamic.py").write_text(source, encoding="utf-8")
    crypto = _by_kind(scan_tree(tmp_path), "crypto")
    assert "sneaky_dynamic.py" in crypto, (
        "F5 wrócił: dynamiczny dostęp do chronionego modułu niewidoczny:\n"
        + source)


def test_f5_dynamic_fcntl_access_is_red(tmp_path):
    """F5 dla nogi lock: ``getattr(fcntl, <env>)`` jest czerwony niezależnie od
    tego, czy analiza zakresu połączyła plik z magazynem poświadczeń."""
    (tmp_path / "sneaky_dyn_lock.py").write_text(
        "import fcntl, os\n"
        "def w(fh):\n"
        "    getattr(fcntl, os.environ['LOCK_OP'])(fh.fileno(), 2)\n",
        encoding="utf-8")
    locks = _by_kind(scan_tree(tmp_path), "credential-lock")
    assert "sneaky_dyn_lock.py" in locks


def test_f6_consumer_in_nested_tests_dir_is_red(tmp_path):
    """F6: dosłowna reprodukcja recenzenta — drugi konsument KDF położony
    w ``tools/tests/`` (katalog wewnątrz pakietu produkcyjnego) MUSI być
    widziany; iter1 prunowało każdy katalog o nazwie ``tests``."""
    nested = tmp_path / "tools" / "tests"
    nested.mkdir(parents=True)
    (nested / "pin_hash_helper.py").write_text(
        "import hashlib\n"
        f"KDF_PATH = '{_CRED_PATH}'\n"
        "def hash_pin(pin, salt):\n"
        "    return hashlib.pbkdf2_hmac('sha256', pin, salt, 1000)\n",
        encoding="utf-8")
    crypto = _by_kind(scan_tree(tmp_path), "crypto")
    assert "tools/tests/pin_hash_helper.py" in crypto


# ── KONTROLE NEGATYWNE: ratchet nie krzyczy na legalny kod ───────────────────

NON_VIOLATIONS = [
    pytest.param(
        "import hashlib\n"
        "def cache_key(s):\n"
        "    return hashlib.sha256(s.encode()).hexdigest()\n",
        id="sha256-cache-key"),
    pytest.param(
        "import hmac\n"
        "def sign(key, msg):\n"
        "    return hmac.new(key, msg, 'sha256').hexdigest()\n",
        id="hmac-new-podpis-nie-poswiadczen"),
    pytest.param(
        "import fcntl\n"
        "def write_positions(path, data):\n"
        "    with open(path + '.lock', 'w') as lk:\n"
        "        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)\n",
        id="flock-na-pliku-niebedacym-poswiadczeniem"),
    pytest.param(
        '"""Moduł opisuje wzorzec: fcntl.flock LOCK_EX na kurier_piny.json.lock."""\n'
        "def noop():\n"
        "    return None\n",
        id="docstring-opisujacy-wzorzec"),
    pytest.param(
        "def load_piny(kurier_piny_path):\n"
        "    with open(kurier_piny_path) as f:\n"
        "        return f.read()\n",
        id="czytanie-piny-bez-locka"),
    # ── kontrole negatywne DODANE w iter2 (do nowych reguł) ──
    pytest.param(
        "import hashlib\n"
        "def fingerprint_all(items):\n"
        "    out = []\n"
        "    for it in items:\n"
        "        out.append(hashlib.sha256(it.encode()).hexdigest())\n"
        "    return out\n",
        id="F2-hash-w-petli-bez-materialu-sekretnego"),
    pytest.param(
        "from cryptography.hazmat.primitives import hashes\n"
        "def alg():\n"
        "    return hashes.SHA256()\n",
        id="F4-cryptography-poza-poddrzewem-kdf"),
    pytest.param(
        "import hashlib\n"
        "def digest(data):\n"
        "    return getattr(hashlib, 'sha256')(data).hexdigest()\n",
        id="F5-getattr-literal-niechronionej-nazwy"),
    pytest.param(
        "import importlib\n"
        "def load():\n"
        "    return importlib.import_module('dispatch_v2.core.decide')\n",
        id="F5-importlib-modulu-wlasnego"),
    pytest.param(
        "import importlib\n"
        "def load_all(names):\n"
        "    return [importlib.import_module(n) for n in names]\n",
        id="F5-importlib-dynamiczny-plik-bez-poswiadczen"),
]


@pytest.mark.parametrize("source", NON_VIOLATIONS)
def test_legit_code_is_not_flagged(source, tmp_path):
    """Ratchet ma być ostry, ale nie histeryczny: sha256 do cache'a, hmac do
    podpisu, flock na pliku spoza poświadczeń, sam OPIS wzorca w docstringu,
    haszowanie kolekcji w pętli, ``cryptography`` spoza poddrzewa ``kdf`` ani
    ładowanie własnych modułów po nazwie NIE są naruszeniem (inaczej pierwszy
    fałszywy alarm rozbroiłby bramkę)."""
    (tmp_path / "legit.py").write_text(source, encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert findings == [], f"fałszywy alarm na legalnym kodzie: {findings}"


def test_unrelated_lock_next_to_credential_read_is_module_scope_only(tmp_path):
    """Precyzja nogi 1 vs fail-closed nogi 2 — kształt ZMIERZONY na
    ``courier_resolver.py``: lock na STORE POZYCJI, a legacy mapa PIN-ów
    czytana w innej funkcji (wołanej z tego samego miejsca). Noga precyzyjna
    MILCZY (to nie jest lock poświadczeń), noga modułowa krzyczy — i dlatego
    moduł ma JAWNY wpis wyjątku, a nie cichą regułę."""
    (tmp_path / "resolverish.py").write_text(
        "import fcntl\n"
        "KURIER_PINY_PATH = '/root/.openclaw/workspace/dispatch_state/kurier_piny.json'\n"
        "def _save_last_known_pos(store):\n"
        "    with open('/x/last_known_pos.json.lock', 'w') as lk:\n"
        "        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)\n"
        "def _load_kurier_piny():\n"
        "    with open(KURIER_PINY_PATH) as f:\n"
        "        return f.read()\n"
        "def build_fleet_snapshot():\n"
        "    piny = _load_kurier_piny()\n"
        "    updates = {}\n"
        "    _save_last_known_pos(updates)\n"
        "    return piny\n",
        encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert "resolverish.py" not in _by_kind(findings, "credential-lock"), (
        f"noga precyzyjna dała fałszywy alarm na locku pliku pozycji: {findings}")
    assert "resolverish.py" in _by_kind(findings, "credential-lock-module")
