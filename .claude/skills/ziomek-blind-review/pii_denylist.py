#!/usr/bin/env python3
"""Kanoniczna polityka PII/sekretów dla bundlera blind-review — FAIL-CLOSED.

JEDYNY właściciel decyzji „czy ten plik wolno w ogóle wpuścić w zakres ślepego
bundla". `driver.py blind` ma dokładnie JEDNO wywołanie `screen_tree()` i NIE ma
własnych reguł wrażliwości — nie dokładaj drugiej warstwy filtrów w driverze,
w promptcie recenzenta ani w manifeście. Zmiana polityki = zmiana TEGO pliku.

Kontrakt (powód istnienia: near-miss 2026-08-01, `blind .` objął chroniony plik
klasy PII, bo denylista bundlera znała tylko nazwy niosące werdykt):

  * dopasowanie = ODMOWA budowy całego bundla, nie cichy skip pojedynczego pliku
    (cichy skip nie odróżnia „odfiltrowałem" od „nigdy nie było w zakresie",
    a to właśnie ta różnica przepuściła near-miss);
  * skanowanie jest DWUFAZOWE: najpierw cała klasyfikacja drzewa, dopiero potem
    kopiowanie — przy odmowie do bundla nie trafia ANI JEDEN bajt;
  * zdjęcie dopasowania wyłącznie jawnym `--allow-sensitive <ścieżka_wzgl>`
    (per plik, nigdy per katalog, nigdy per wzorzec);
  * komunikat odmowy NIGDY nie cytuje dopasowanej treści — tylko klasa, id reguły
    i licznik trafień. Raport z odmowy ma być bezpieczny do wklejenia.

GRANICE POKRYCIA (świadome; nie udajemy kompletności):
  * `path` łapie znane KLASY po ścieżce/nazwie — nie wykryje pliku nazwanego
    neutralnie (`dane.json`), jeśli treść nie odpali heurystyki;
  * `content` skanuje wyłącznie pliki tekstowe ≤ CONTENT_MAX_BYTES o rozszerzeniu
    ze SCANNABLE_SUFFIXES; binaria, archiwa i pliki większe NIE są skanowane
    treściowo (do bundla i tak nie wchodzą — patrz ALLOW_SUFFIXES w driverze —
    ale ich zawartości nie potwierdzamy);
  * heurystyka „imię+nazwisko" działa TYLKO na wartościach plików strukturalnych
    (json/jsonl/csv/tsv/yaml) i wymaga NAME_VALUE_MIN_HITS różnych trafień —
    w prozie (md/txt) nazwisk NIE wykrywamy, bo próg fałszywych alarmów byłby
    nie do utrzymania. Prozę pokrywa `path` + dyscyplina zakresu, nie ten skaner;
  * gołe `address`/`adres` celowo NIE jest regułą — w tym repo adresy punktów
    biznesowych są wszędzie (geokoder), więc reguła dawałaby szum, który zmusza
    do hurtowego allowlistowania i psuje bramkę;
  * katalogi `.git`/cache są POMIJANE w skanie (nigdy nie trafiają do bundla);
  * dowiązanie wyprowadzające poza katalog kandydata = odmowa klasy `scope_escape`,
    NIEallowlistowalna — treści spoza kandydata nie da się przeskanować jako jego
    części, więc jedyne uczciwe wyjście to zmaterializować plik albo zawęzić zakres.

Zero sieci, zero zapisu. Moduł tylko czyta i klasyfikuje.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

# ── klasy ────────────────────────────────────────────────────────────────────
CLASS_SECRET = "secret"
CLASS_IDENTITY_PII = "identity_pii"
CLASS_CLIENT_DATA = "client_data"
CLASS_SCOPE = "scope_escape"  # dowiązanie wyprowadzające bundle poza kandydata

# ── parametry skanu ──────────────────────────────────────────────────────────
CONTENT_MAX_BYTES = 2 * 1024 * 1024
NAME_VALUE_MIN_HITS = 3
PHONE_MIN_HITS = 3
MAX_VALUES_PER_FILE = 200_000
MAX_JSONL_LINES = 20_000

PRUNED_DIRS = (".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")

DATA_SUFFIXES = (
    ".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".yaml", ".yml", ".txt",
    ".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet",
)
STRUCTURED_SUFFIXES = (".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".yaml", ".yml")
SCANNABLE_SUFFIXES = DATA_SUFFIXES + (
    ".md", ".py", ".sh", ".bash", ".ini", ".cfg", ".conf", ".toml", ".env",
    ".sql", ".js", ".ts", ".html", ".log", ".service", ".timer", ".patch",
    ".diff", ".properties", "",
)

# ── reguły po ŚCIEŻCE ────────────────────────────────────────────────────────
# glob dopasowywany do nazwy pliku ORAZ do ścieżki względnej (posix, lowercase).
SECRET_GLOBS = (
    "*.env", ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks",
    "*.keystore", "*.kdbx", "*.secret", "*.secrets", "*.ppk", "*.htpasswd",
    "id_rsa*", "id_ecdsa*", "id_ed25519*", ".netrc", ".pgpass", "*.asc",
    "service-account*.json", "*serviceaccount*.json",
)
# segment ścieżki równy nazwie katalogu → cała zawartość jest sekretem
SECRET_DIRS = (".secrets", "secrets", ".ssh", ".gnupg", "private_keys", "keystore")
# token w nazwie pliku (dowolne rozszerzenie — sekret w .py jest gorszy niż w .env)
SECRET_NAME_TOKENS = (
    "credential", "password", "passwd", "apikey", "api_key", "privkey",
    "private_key", "secret", "bot_token", "auth_token", "access_token",
)

# katalogi z danymi osobowymi — reguła dotyczy WYŁĄCZNIE plików danych
# (`identity/` to także kod: normalize.py/registry.py — kod ma być recenzowalny).
PII_DATA_DIRS = ("identity", "daily_accounting", "grafik", "grafiki", "pii", "osobowe")
# tokeny mocne — dowolny typ pliku
PII_NAME_TOKENS_ANY = (
    "full_names", "fullnames", "pelne_nazwiska", "nazwisk", "imiona",
    "imie_nazwisko", "dane_osobowe", "personal_data", "pesel", "msisdn",
    "telefon", "phone_numbers", "kurier_piny", "courier_names", "kurier_names",
)
# tokeny słabsze — tylko pliki danych, żeby kod o PII (`pii_denylist.py`,
# `api_client.py`) był recenzowalny, a dane o tej samej nazwie już nie.
PII_NAME_TOKENS_DATA = (
    "grafik", "roster", "employee", "pracownic", "kontakty", "contacts",
    "piny", "adresy_klientow", "personal", "pii",
)
CLIENT_NAME_TOKENS_DATA = (
    "client", "klient", "customer", "odbiorc", "nadawc", "recipient",
    "zamawiajac", "subscriber",
)

# ── reguły po TREŚCI ─────────────────────────────────────────────────────────
SECRET_CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem-private-key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-like-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("http-basic-auth-url", re.compile(r"://[A-Za-z0-9._%-]{2,}:[^\s/@\"']{6,}@")),
    ("assigned-credential-literal", re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|auth[_-]?token|bot[_-]?token)\b"
        r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']")),
)
# Wartości-atrapy przy `klucz = "wartość"`. Bez tego filtra bramka zapalałaby się
# na dokumentacji i testach (`secret = "sensitive-order-and-courier-data"`,
# `BOT_TOKEN=<token>`), a operator nauczyłby się hurtowo allowlistować.
CREDENTIAL_PLACEHOLDER_SUBSTRINGS = (
    "example", "fake", "dummy", "placeholder", "redacted", "changeme", "sample",
    "test", "xxxx", "your-", "your_", "secret", "todo", "unset", "none",
    "sensitive-",
)
# otwarcie wrappera nigdy nie zaczyna prawdziwego sekretu (także gdy przykład jest
# ucięty: `BOT_TOKEN="<12345:AAA…"`), a znaki maskujące oznaczają zredagowany przykład
CREDENTIAL_WRAPPER_PREFIXES = ("<", "${", "{{", "{", "%", "«")
CREDENTIAL_MASK_MARKERS = ("…", "...", "***", "xxx", "???")


def _looks_like_credential(val: str) -> bool:
    """Czy wartość przypisana do `password/token/...` wygląda na PRAWDZIWY materiał.

    Świadomy kompromis: żądamy kształtu losowego sekretu (mieszanka cyfr/wielkości
    liter). Hasło slownikowe typu `correcthorsebattery` przejdzie ten filtr jako
    atrapa — łapie je warstwa `path` (`*.env`, `credentials*`), nie ta heurystyka.
    """
    v = val.strip()
    if len(v) < 8:
        return False
    low = v.lower()
    if v.startswith(CREDENTIAL_WRAPPER_PREFIXES):
        return False
    if any(s in low for s in CREDENTIAL_MASK_MARKERS):
        return False
    if any(s in low for s in CREDENTIAL_PLACEHOLDER_SUBSTRINGS):
        return False
    has_digit = any(c.isdigit() for c in v)
    has_alpha = any(c.isalpha() for c in v)
    mixed_case = v != low and v != v.upper()
    return has_alpha and (has_digit or mixed_case)

_UP = "A-ZĄĆĘŁŃÓŚŹŻ"
_LO = "a-ząćęłńóśźż"
NAME_VALUE_RE = re.compile(rf"^[{_UP}][{_LO}]{{2,}}(?:[- ][{_UP}][{_LO}]{{2,}}){{1,2}}$")
PHONE_VALUE_RE = re.compile(r"^(?:\+?48[ -]?)?\d{3}[ -]?\d{3}[ -]?\d{3}$")
PII_KEY_RE = re.compile(
    r"(?i)^(?:pesel|nazwisko|surname|last_?name|imie|imię|first_?name|full_?name|"
    r"imie_nazwisko|telefon|phone|phone_number|msisdn|e-?mail|iban|"
    r"account_number|numer_konta|dowod|id_card|birth_?date|data_urodzenia|pin)$")
CLIENT_KEY_RE = re.compile(
    r"(?i)^(?:client_name|customer_name|nazwa_klienta|odbiorca|nadawca|"
    r"recipient_name|recipient_phone|customer_phone)$")


@dataclass(frozen=True)
class Hit:
    """Jedno trafienie polityki. `detail` NIGDY nie zawiera dopasowanej treści."""

    rel: str
    klass: str
    layer: str  # "path" | "content"
    rule: str
    detail: str = ""

    def line(self) -> str:
        d = f"  ({self.detail})" if self.detail else ""
        return f"{self.rel}  [{self.klass}/{self.layer}:{self.rule}]{d}"


@dataclass
class Screening:
    hits: list[Hit] = field(default_factory=list)
    allowlisted: list[str] = field(default_factory=list)
    scanned_files: int = 0
    content_scanned: int = 0
    content_skipped: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.hits)

    def summary(self) -> dict:
        return {
            "blocked": self.blocked,
            "hits": [
                {"path": h.rel, "class": h.klass, "layer": h.layer,
                 "rule": h.rule, "detail": h.detail}
                for h in self.hits
            ],
            "allowlisted": sorted(self.allowlisted),
            "scanned_files": self.scanned_files,
            "content_scanned": self.content_scanned,
            "content_not_scanned": len(self.content_skipped),
        }


def _segments(rel_low: str) -> tuple[str, ...]:
    return tuple(rel_low.split("/")[:-1])


def _glob_hit(rel_low: str, base_low: str, pattern: str) -> bool:
    return fnmatch(base_low, pattern) or fnmatch(rel_low, pattern)


def classify_path(rel: str) -> list[Hit]:
    """Reguły po ścieżce/nazwie. Bez I/O — czysta funkcja, łatwa do zratchetowania."""
    rel_low = rel.lower()
    base_low = rel_low.rsplit("/", 1)[-1]
    suffix = os.path.splitext(base_low)[1]
    is_data = suffix in DATA_SUFFIXES
    hits: list[Hit] = []

    for pat in SECRET_GLOBS:
        if _glob_hit(rel_low, base_low, pat):
            hits.append(Hit(rel, CLASS_SECRET, "path", f"glob:{pat}"))
            break
    segs = _segments(rel_low)
    for d in SECRET_DIRS:
        if d in segs:
            hits.append(Hit(rel, CLASS_SECRET, "path", f"dir:{d}"))
            break
    for tok in SECRET_NAME_TOKENS:
        if tok in base_low:
            hits.append(Hit(rel, CLASS_SECRET, "path", f"name:{tok}"))
            break

    for tok in PII_NAME_TOKENS_ANY:
        if tok in base_low:
            hits.append(Hit(rel, CLASS_IDENTITY_PII, "path", f"name:{tok}"))
            break
    if is_data:
        for d in PII_DATA_DIRS:
            if d in segs:
                hits.append(Hit(rel, CLASS_IDENTITY_PII, "path", f"dir+data:{d}"))
                break
        for tok in PII_NAME_TOKENS_DATA:
            if tok in base_low:
                hits.append(Hit(rel, CLASS_IDENTITY_PII, "path", f"name+data:{tok}"))
                break
        for tok in CLIENT_NAME_TOKENS_DATA:
            if tok in base_low:
                hits.append(Hit(rel, CLASS_CLIENT_DATA, "path", f"name+data:{tok}"))
                break
    return hits


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > CONTENT_MAX_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _walk_json(node, keys: list[str], values: list[str]) -> None:
    stack = [node]
    while stack and len(values) < MAX_VALUES_PER_FILE:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(k, str):
                    keys.append(k)
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str):
            values.append(cur)


def _extract_keys_values(rel_low: str, text: str) -> tuple[list[str], list[str]]:
    """Klucze i wartości-stringi z pliku strukturalnego. Best-effort, nigdy nie rzuca."""
    keys: list[str] = []
    values: list[str] = []
    suffix = os.path.splitext(rel_low)[1]
    if suffix == ".json":
        try:
            _walk_json(json.loads(text), keys, values)
            return keys, values
        except (ValueError, RecursionError):
            pass
    elif suffix in (".jsonl", ".ndjson"):
        parsed_any = False
        for i, line in enumerate(text.splitlines()):
            if i >= MAX_JSONL_LINES:
                break
            line = line.strip()
            if not line:
                continue
            try:
                _walk_json(json.loads(line), keys, values)
                parsed_any = True
            except ValueError:
                continue
        if parsed_any:
            return keys, values
    elif suffix in (".csv", ".tsv"):
        sep = "\t" if suffix == ".tsv" else ","
        lines = text.splitlines()
        if lines:
            keys.extend(c.strip().strip('"') for c in lines[0].split(sep))
            for line in lines[1:]:
                values.extend(c.strip().strip('"') for c in line.split(sep))
        return keys, values
    elif suffix in (".yaml", ".yml"):
        for line in text.splitlines():
            m = re.match(r"^\s*-?\s*([A-Za-z_][\w.-]*)\s*:\s*(.*)$", line)
            if m:
                keys.append(m.group(1))
                val = m.group(2).strip().strip("\"'")
                if val:
                    values.append(val)
        return keys, values

    # fallback: surowe pary "klucz": "wartość" (uszkodzony/nietypowy plik)
    keys.extend(re.findall(r'"([^"\n]{1,60})"\s*:', text)[:MAX_VALUES_PER_FILE])
    values.extend(re.findall(r':\s*"([^"\n]{1,80})"', text)[:MAX_VALUES_PER_FILE])
    return keys, values


def classify_content(rel: str, text: str) -> list[Hit]:
    """Heurystyki treści. Zwraca trafienia BEZ cytowania dopasowanej treści."""
    hits: list[Hit] = []
    for rule, rx in SECRET_CONTENT_RULES:
        matched = False
        for m in rx.finditer(text):
            # przy `klucz = "wartość"` odsiewamy atrapy z docs/testów, ale sprawdzamy
            # KAŻDE wystąpienie — jedna atrapa nie ma unieważniać reguły dla pliku
            if rule == "assigned-credential-literal" and not _looks_like_credential(m.group(1) or ""):
                continue
            matched = True
            break
        if matched:
            hits.append(Hit(rel, CLASS_SECRET, "content", rule))
            break

    rel_low = rel.lower()
    if os.path.splitext(rel_low)[1] not in STRUCTURED_SUFFIXES:
        return hits

    keys, values = _extract_keys_values(rel_low, text)
    pii_keys = sorted({k for k in keys if PII_KEY_RE.match(k.strip())})
    if pii_keys:
        hits.append(Hit(rel, CLASS_IDENTITY_PII, "content", "pii-key-name",
                        f"{len(pii_keys)} pól klasy PII"))
    client_keys = sorted({k for k in keys if CLIENT_KEY_RE.match(k.strip())})
    if client_keys:
        hits.append(Hit(rel, CLASS_CLIENT_DATA, "content", "client-key-name",
                        f"{len(client_keys)} pól klasy klienta"))

    names, phones = set(), set()
    for v in values:
        v = v.strip()
        if not v or len(v) > 60:
            continue
        if NAME_VALUE_RE.match(v):
            names.add(v)
        elif PHONE_VALUE_RE.match(v):
            phones.add(v)
    if len(names) >= NAME_VALUE_MIN_HITS:
        hits.append(Hit(rel, CLASS_IDENTITY_PII, "content", "name-shaped-values",
                        f"{len(names)} różnych wartości w kształcie imię+nazwisko"))
    if len(phones) >= PHONE_MIN_HITS:
        hits.append(Hit(rel, CLASS_IDENTITY_PII, "content", "phone-shaped-values",
                        f"{len(phones)} różnych wartości w kształcie numeru telefonu"))
    return hits


def screen_file(path: Path, rel: str) -> tuple[list[Hit], bool]:
    """(trafienia, czy_zeskanowano_treść) dla jednego pliku."""
    hits = list(classify_path(rel))
    suffix = os.path.splitext(rel.lower())[1]
    if suffix not in SCANNABLE_SUFFIXES:
        return hits, False
    text = _read_text(path)
    if text is None:
        return hits, False
    hits.extend(classify_content(rel, text))
    return hits, True


def _resolves_inside(p: Path, root_resolved: Path) -> bool:
    try:
        target = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return False
    return True


def iter_candidate_files(root: Path):
    """Pliki w zakresie skanu = dokładnie te, które bundler może skopiować.

    Ten sam iterator karmi skan i kopiowanie — inaczej powstałaby luka „coś, czego
    nie przeskanowano, ale skopiowano". Dowiązania wskazujące POZA katalog kandydata
    nie są tu wpuszczane; zgłasza je `iter_scope_escapes()` jako odmowę, nie cichy skip.
    """
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNED_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink() and not _resolves_inside(p, root_resolved):
                continue
            if not p.is_file():
                continue
            yield p, p.relative_to(root).as_posix()


def iter_scope_escapes(root: Path):
    """Dowiązania (plików i katalogów) wyprowadzające poza katalog kandydata."""
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNED_DIRS)
        for name in sorted(dirnames) + sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink() and not _resolves_inside(p, root_resolved):
                yield p.relative_to(root).as_posix()


def screen_tree(root: Path, allow: tuple[str, ...] = ()) -> Screening:
    """FAZA 1: klasyfikuje CAŁE drzewo kandydata. Nie kopiuje i nie zapisuje nic."""
    allow_set = {a.strip().lstrip("./") for a in allow if a.strip()}
    res = Screening()
    seen: set[str] = set()
    for path, rel in iter_candidate_files(root):
        res.scanned_files += 1
        seen.add(rel)
        if rel in allow_set:
            res.allowlisted.append(rel)
            continue
        hits, content_done = screen_file(path, rel)
        if content_done:
            res.content_scanned += 1
        else:
            res.content_skipped.append(rel)
        res.hits.extend(hits)
    # Ucieczka zakresu jest CELOWO nieallowlistowalna: treści spoza katalogu kandydata
    # nie da się przeskanować jako jego części, a bundler jej nie skopiuje — jedyne
    # uczciwe wyjście to zmaterializować plik w katalogu kandydata albo zawęzić zakres.
    for rel in iter_scope_escapes(root):
        seen.add(rel)
        res.hits.append(Hit(rel, CLASS_SCOPE, "path", "symlink-outside-candidate",
                            "dowiązanie wyprowadza zakres poza katalog kandydata"))
    unknown = sorted(allow_set - seen)
    if unknown:
        # allowlista z literówką dawałaby fałszywe poczucie przejrzanego pliku
        res.hits.append(Hit(", ".join(unknown), CLASS_SECRET, "path",
                            "allowlist-path-not-found",
                            "wpis --allow-sensitive nie wskazuje istniejącego pliku"))
    return res


def refusal_text(res: Screening, root: Path) -> str:
    """Komunikat odmowy — bezpieczny do wklejenia (zero dopasowanej treści)."""
    lines = [
        "ODMOWA: zakres kandydata zawiera pliki klasy PII/sekret — bundle NIE powstał.",
        f"  kandydat: {root}",
        f"  trafienia: {len(res.hits)} (przeskanowano plików: {res.scanned_files}, "
        f"treściowo: {res.content_scanned})",
        "",
    ]
    lines.extend("  " + h.line() for h in res.hits)
    lines += [
        "",
        "Co zrobić (w tej kolejności):",
        "  1. ZAWĘŹ zakres — `blind .` na korzeniu repo to prawie zawsze błąd;",
        "     wskaż katalog samego kandydata.",
        "  2. Jeśli plik jest syntetyczny/atrapa i MA trafić do recenzenta:",
        "     `--allow-sensitive <ścieżka_względna>` (osobno dla KAŻDEGO pliku).",
        "  3. Nigdy nie obchodź bramki kopiując pliki poza driverem.",
    ]
    return "\n".join(lines)
