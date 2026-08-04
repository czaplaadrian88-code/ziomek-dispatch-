"""A-6/G2 — RATCHET wrażliwych operacji na POŚWIADCZENIACH kuriera (test-only).

Bramka: ``engine.a6-ratchet-dict-get-alias`` (reskope na masterze ``c91b7ede8``).

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

ZAMROŻONA LISTA WŁAŚCICIELI (stan mastera ``c91b7ede8``) — patrz
``FROZEN_CRYPTO_OWNERS`` / ``FROZEN_CREDENTIAL_LOCK_OWNERS`` niżej; każdy wpis
ma uzasadnienie przy stałej. Dopisanie właściciela = ŚWIADOMA zmiana polityki
bezpieczeństwa (owner ACK), nie „poprawka czerwonego testu".

ZAKRES SKANU (discovery, nie enumeracja — lekcja O1 z G3)
---------------------------------------------------------
Skanujemy CAŁE drzewo repo ``*.py`` znalezione przejściem katalogów, z prune
wyłącznie katalogów NIE-ŹRÓDŁOWYCH (``PRUNED_DIRS``). Nie ma listy modułów do
sprawdzenia — nowy plik w nowym podkatalogu jest objęty od razu (dowód:
``test_discovery_picks_up_brand_new_nested_module``).

``tests/`` jest poza skanem ŚWIADOMIE: zagrożeniem jest DRUGI KONSUMENT
PRODUKCYJNY (kod, który dotyka żywego magazynu PIN-ów), a drzewo testowe ani
nie działa na produkcji, ani nie ma dostępu do żywego ``dispatch_state``
(HERMETIC-GUARD). Testy bezpieczeństwa MUSZĄ móc wołać prymitywy KDF i
kompilować syntetyczne obejścia (robi to ten plik), więc objęcie ``tests/``
polityką ownera dałoby stały fałszywy alarm i presję na rozluźnianie ratchetu.

ODPORNOŚĆ NA OBEJŚCIA OD PIERWSZEGO DNIA (lekcja K9)
----------------------------------------------------
Wykrywane formy dostępu do chronionego prymitywu (każda ma test mutacyjny
w ``EVASIONS``): bezpośredni atrybut, ``import ... as``, ``from ... import
... as``, alias przez zmienną, ``getattr`` z literałem, LITERALNY ``dict.get``
/ subskrypcja słownika (defekt K9), string sklejany (``"pbkdf2" + "_hmac"``),
f-string, oraz — dla locków — dowolne sięgnięcie po moduł ``fcntl`` wewnątrz
zakresu dotykającego poświadczeń (zamyka ``getattr(fcntl, zmienna)``).

Ratchet jest KONSERWATYWNY: przy niejednoznaczności woli fałszywy alarm niż
cichego writera (ta sama zasada co ``test_committed_pickup_authority_ratchet``).
Fałszywy alarm kosztuje jedno wyjaśnienie w review; przeoczony drugi konsument
krypto poświadczeń kosztuje wyciek PIN-ów całej floty.

HERMETYCZNY: wyłącznie ODCZYT plików repo + skan syntetycznych drzew w
``tmp_path``. Zero I/O do ``dispatch_state``/``logs``/``flags.json``, zero
uruchamiania wstrzykniętego kodu (mutacje są PARSOWANE, nigdy importowane).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Korzeń repo. ``resolve()`` jest konieczny: pod pkgroot testy widzą repo przez
# symlink (``pkgroot/dispatch_v2 -> worktree``) i bez rozwinięcia skanowalibyśmy
# ścieżkę symlinkową zamiast realnego drzewa kandydata.
ROOT = Path(__file__).resolve().parents[1]

# ── polityka: co jest wrażliwe ───────────────────────────────────────────────

# Prymitywy hashowania POŚWIADCZEŃ. ``pbkdf2_hmac`` to dzisiejszy KDF ownera;
# pozostałe są dopisane celowo, żeby „drugi konsument" nie wszedł tylnymi
# drzwiami przez INNY prymityw (scrypt/bcrypt/argon2 dają dokładnie tę samą
# zdolność: zamienić PIN w rekord uwierzytelniający poza kontrolą ownera).
# ``hmac.new`` NIE jest tu celowo — na masterze ma legalnych, niepowiązanych z
# poświadczeniami konsumentów (``tools/process_debt_gate``, ``uwagi_bridge_envelope``).
PROTECTED_CRYPTO_NAMES = frozenset({
    "pbkdf2_hmac", "scrypt", "bcrypt", "argon2", "argon2id", "hash_password",
})
# Fragmenty stringów zdradzające dynamiczny dostęp do powyższych
# (``getattr(hashlib, "pbkdf2" + "_hmac")``, ``FUNCS.get("pbkdf2_hmac")``).
PROTECTED_CRYPTO_STR_TOKENS = frozenset({
    "pbkdf2", "scrypt", "bcrypt", "argon2",
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

# ── ZAMROŻONA lista właścicieli (master c91b7ede8) ───────────────────────────

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
}

# Katalogi poza skanem. Wyłącznie NIE-ŹRÓDŁOWE albo świadomie wyłączone
# (``tests`` — uzasadnienie w docstringu modułu). Lista jest KRÓTKA i jawna,
# żeby każde poszerzenie zakresu ślepoty było widoczne w diffie.
PRUNED_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "htmlcov", "tests",
})


# ── discovery ────────────────────────────────────────────────────────────────

def iter_python_files(root: Path) -> list[Path]:
    """Wszystkie ``*.py`` pod ``root`` (discovery, nie enumeracja modułów).

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
                if entry.name not in PRUNED_DIRS:
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


def scan_source(source: str, relpath: str) -> list[Finding]:
    """Znajdź w JEDNYM module: (a) użycia krypto poświadczeń, (b) blokady
    plikowe w zakresie dotykającym poświadczeń. Zwraca listę ``Finding``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Plik niekompilowalny nie jest konsumentem; kompilowalność pilnują
        # inne bramki (py_compile w workflow deployu).
        return []

    parents = _parents(tree)
    docstrings = _docstring_nodes(tree)
    aliases = _string_aliases(tree)
    findings: list[Finding] = []

    # (a) KRYPTO POŚWIADCZEŃ — każde SIĘGNIĘCIE, nie tylko wywołanie: alias
    #     ``h = hashlib.pbkdf2_hmac`` jest wykrywany w miejscu wiązania.
    crypto_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"hashlib", "crypt"}:
            for al in node.names:
                if al.name in PROTECTED_CRYPTO_NAMES:
                    crypto_aliases.add(al.asname or al.name)
        elif isinstance(node, ast.Import):
            for al in node.names:
                if al.name.split(".")[0] in PROTECTED_CRYPTO_NAMES:
                    crypto_aliases.add(al.asname or al.name.split(".")[0])

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
            findings.append(Finding(
                "crypto", relpath, _scope_label(_enclosing_scope(node, parents)),
                getattr(node, "lineno", 0), hit))

    # (b) BLOKADA PLIKU POŚWIADCZEŃ — lock w zakresie, który dotyka poświadczeń.
    lock_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in LOCK_MODULE_NAMES:
            for al in node.names:
                if al.name in LOCK_NAMES or al.name in LOCK_CONST_NAMES:
                    lock_aliases.add(al.asname or al.name)
        elif isinstance(node, ast.Import):
            for al in node.names:
                if al.name.split(".")[0] in LOCK_MODULE_NAMES:
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

    for node in lock_sites:
        scope = _enclosing_scope(node, parents)
        idents = _identifiers(scope)
        strings = _strings_in(scope, aliases, docstrings)
        if _touches(CREDENTIAL_TOKENS, idents, strings):
            findings.append(Finding(
                "credential-lock", relpath, _scope_label(scope),
                getattr(node, "lineno", 0),
                "blokada plikowa w zakresie dotykającym magazynu poświadczeń"))

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
    """Krypto poświadczeń (PBKDF2/scrypt/bcrypt/argon2) — WYŁĄCZNIE u ownera.

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
    """Blokada plikowa na magazynie poświadczeń — WYŁĄCZNIE u ownera."""
    hits = _by_kind(repo_findings, "credential-lock")
    intruders = {p: v for p, v in hits.items()
                 if p not in FROZEN_CREDENTIAL_LOCK_OWNERS}
    assert not intruders, (
        "DRUGI locker magazynu poświadczeń poza właścicielem "
        f"{sorted(FROZEN_CREDENTIAL_LOCK_OWNERS)}: "
        + "; ".join(f"{p} -> {[repr(f) for f in v]}" for p, v in sorted(intruders.items()))
    )


# ── ANTY-PUSTKA: ratchet MUSI nadal coś widzieć ──────────────────────────────

def test_owner_files_exist():
    """Zamrożony właściciel musi istnieć — przeniesienie/rename ``pin_auth``
    bez aktualizacji polityki = RED (fail-closed), nie cichy GREEN."""
    for rel in set(FROZEN_CRYPTO_OWNERS) | set(FROZEN_CREDENTIAL_LOCK_OWNERS):
        assert (ROOT / rel).is_file(), f"zamrożony właściciel zniknął: {rel}"


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


# ── DISCOVERY (lekcja O1): skan obejmuje drzewo, nie listę modułów ───────────

def test_discovery_covers_repo_tree():
    files = {p.relative_to(ROOT).as_posix() for p in iter_python_files(ROOT)}
    # kotwice z RÓŻNYCH poziomów drzewa — brak którejkolwiek = dziura w skanie
    for anchor in ("common.py", "identity/pin_auth.py", "core/decide.py",
                   "courier_resolver.py", "gps_server.py"):
        assert anchor in files, f"discovery nie objęło {anchor}"
    assert not any(p.startswith("tests/") for p in files), \
        "tests/ miało być poza zakresem (patrz docstring modułu)"
    assert not any("__pycache__" in p for p in files)
    # Skala: master ma ~640 modułów produkcyjnych. Próg chroni przed cichym
    # zwinięciem skanu do garstki plików (np. przez poszerzenie PRUNED_DIRS).
    assert len(files) >= 400, f"discovery znalazło tylko {len(files)} plików"


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
]


@pytest.mark.parametrize("source", NON_VIOLATIONS)
def test_legit_code_is_not_flagged(source, tmp_path):
    """Ratchet ma być ostry, ale nie histeryczny: sha256 do cache'a, hmac do
    podpisu, flock na pliku spoza poświadczeń i sam OPIS wzorca w docstringu
    NIE są naruszeniem (inaczej pierwszy fałszywy alarm rozbroiłby bramkę)."""
    (tmp_path / "legit.py").write_text(source, encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert findings == [], f"fałszywy alarm na legalnym kodzie: {findings}"
