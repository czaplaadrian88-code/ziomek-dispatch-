#!/usr/bin/env python3
"""OD-7 (owner ACK 2026-08-03): AUTOMAT ARCHIWIZACJI retencyjnej — shadow-first.

PO CO TO JEST
    Dzisiejsza retencja (logrotate `rotate N`, `world_record.py:_gc`, `log_rotation.py
    --retention-days 14`, `event_bus_cleanup`) KASUJE dane bezpowrotnie i nie ma pod sobą
    żadnego archiwum. Ten automat wsuwa brakujący krok: **kopiuje strumień do archiwum
    ZANIM kasownik do niego dojdzie**, weryfikowalnie (sha256 przed/po) i odwracalnie
    (gunzip) aż do terminu kasacji archiwum z polityki OD-7.

JEDEN OWNER KONTRAKTU (CLAUDE.md „NAPRAWA U ŹRÓDŁA")
    * Owner polityki ARCHIWUM = `tools/retention_od7_policy.json` (jedyne źródło liczb).
    * Owner kasowania ŻYWYCH plików = mechanizm zadeklarowany w regule (`live_delete_owner`).
      Archiwizator NIGDY nie kasuje żywego pliku należącego do innego mechanizmu — nie
      powstaje drugi writer tej samej prawdy. `SOURCE_DELETE` istnieje wyłącznie dla reguł
      z `live_delete_owner == "archiver"` (dziś: ZERO takich reguł).
    * Owner PRUNE'u artefaktów bez GC = `tools/state_retention_prune.py` (Z0-4, gałąź
      `z04-retention`). Zakresy rozłączne: tamto kasuje, to kopiuje przed kasacją.

BEZPIECZEŃSTWO (fail-closed, shadow-first)
    * Tryb domyślny = REPORT. W REPORCIE JEDYNĄ ścieżką zapisu jest `--out` (+ `--text-out`);
      pilnuje tego mechaniczna bramka `WriteGate` — każdy zapis poza whitelistą to wyjątek.
    * REPORT nie tworzy `--archive-root` (dysk 91 % — katalog wskazuje owner świadomie).
    * APPLY wymaga JEDNOCZEŚNIE `--apply` ORAZ `--ack-token <token>` (token = dzień UTC
      + hash polityki, więc ACK wygasa po dobie i po każdej zmianie polityki). Bez tokenu =
      twardy exit 2, BEZ degradacji do dry-runu.
    * Zapisy atomowe: temp w katalogu docelowym → fsync → rename. Nigdy edycja w miejscu.
    * Pliki otwarte do ZAPISU przez proces (skan /proc/*/fd) = SKIP z raportem.
    * Każdy błąd w APPLY przerywa bieg (exit 1). W REPORCIE błędy lądują w `errors[]`
      i dają exit 3 (raport i tak powstaje — jest dowodem dla ownera).
    * Klasy `protected` / `unknown` / `live_state` nie są ruszane w ŻADNYM trybie.

UŻYCIE
    retention_archiver.py [--policy P] [--out raport.json] [--text-out raport.md]
                          [--archive-root DIR] [--include-sqlite] [--limit-actions N]
    retention_archiver.py --print-ack-token
    retention_archiver.py --apply --ack-token OD7-YYYYMMDD-xxxxxxxxxxxx --archive-root DIR
"""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POLICY = os.path.join(HERE, "retention_od7_policy.json")
SCHEMA_VERSION = 1

# Korzenie, do których ten skrypt NIE MA PRAWA pisać w żadnym trybie (żywy stan silnika).
FORBIDDEN_WRITE_ROOTS = (
    "/root/.openclaw/workspace/dispatch_state",
    "/root/.openclaw/workspace/scripts/logs",
)
FORBIDDEN_WRITE_FILES = ("/root/.openclaw/workspace/scripts/flags.json",)

_DATE_IN_NAME = re.compile(r"(?<!\d)(20\d{2})-?(\d{2})-?(\d{2})(?!\d)")
_ROTATED_SUFFIX = re.compile(r"\.(\d+)(\.gz)?$|\.gz$")

MASK_SAMPLE_BYTES = 4 * 1024 * 1024
COMPRESS_SAMPLE_BYTES = 2 * 1024 * 1024


# --------------------------------------------------------------------------- #
# 0. Bramka zapisu — mechaniczny dowód „REPORT pisze wyłącznie do --out"       #
# --------------------------------------------------------------------------- #
class WriteGate:
    """Whitelist ścieżek zapisu. W trybie REPORT whitelist = tylko pliki raportu."""

    def __init__(self, mode: str):
        self.mode = mode                 # "report" | "apply"
        self._allowed_files: set[str] = set()
        self._allowed_roots: list[str] = []

    def allow_file(self, path: str) -> None:
        self._allowed_files.add(os.path.abspath(path))

    def allow_root(self, path: str) -> None:
        self._allowed_roots.append(os.path.abspath(path).rstrip("/"))

    def check(self, path: str) -> None:
        ap = os.path.abspath(path)
        real_parent = os.path.realpath(os.path.dirname(ap) or "/")
        for bad in FORBIDDEN_WRITE_FILES:
            if os.path.realpath(ap) == os.path.realpath(bad):
                raise RuntimeError(f"OD7-WRITE-GATE: zapis do żywego pliku zabroniony: {ap}")
        for bad in FORBIDDEN_WRITE_ROOTS:
            bad_real = os.path.realpath(bad)
            if real_parent == bad_real or real_parent.startswith(bad_real + "/"):
                raise RuntimeError(f"OD7-WRITE-GATE: zapis do żywego korzenia zabroniony: {ap}")
        if ap in self._allowed_files:
            return
        # temp-pliki obok pliku docelowego (atomic write) też przechodzą
        for f in self._allowed_files:
            if os.path.dirname(ap) == os.path.dirname(f) and os.path.basename(ap).startswith(".od7_"):
                return
        for root in self._allowed_roots:
            if ap == root or ap.startswith(root + "/"):
                return
        raise RuntimeError(
            f"OD7-WRITE-GATE: zapis poza whitelistą (tryb={self.mode}): {ap}"
        )


GATE = WriteGate("report")


def _under_forbidden(path: str) -> bool:
    """Czy ścieżka leży w ŻYWYM korzeniu silnika (dispatch_state / scripts/logs / flags.json)."""
    real = os.path.realpath(path)
    if any(real == os.path.realpath(f) for f in FORBIDDEN_WRITE_FILES):
        return True
    for root in FORBIDDEN_WRITE_ROOTS:
        rr = os.path.realpath(root)
        if real == rr or real.startswith(rr + "/"):
            return True
    return False


# --------------------------------------------------------------------------- #
# 1. Prymitywy IO (atomic, fail-closed)                                        #
# --------------------------------------------------------------------------- #
def atomic_write_bytes(path: str, data: bytes) -> None:
    GATE.check(path)
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".od7_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(d)
    except BaseException:
        _rm_quiet(tmp)
        raise


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def _fsync_dir(d: str) -> None:
    try:
        dfd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _rm_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def sha256_file(path: str, *, limit: int | None = None) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            if limit is not None and n + len(chunk) > limit:
                chunk = chunk[: limit - n]
            h.update(chunk)
            n += len(chunk)
            if limit is not None and n >= limit:
                break
    return h.hexdigest(), n


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# 2. Polityka                                                                  #
# --------------------------------------------------------------------------- #
class PolicyError(RuntimeError):
    pass


def load_policy(path: str) -> tuple[dict, str]:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        pol = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise PolicyError(f"polityka nieparsowalna: {path}: {exc}") from exc
    for key in ("policy_version", "roots", "classes", "rules", "pii"):
        if key not in pol:
            raise PolicyError(f"polityka bez wymaganego klucza '{key}': {path}")
    for cname, cfg in pol["classes"].items():
        for key in ("live_days", "archive_days", "pii_mask_days", "archivable"):
            if key not in cfg:
                raise PolicyError(f"klasa '{cname}' bez klucza '{key}'")
    for rule in pol["rules"]:
        for key in ("id", "root", "globs", "class", "granularity"):
            if key not in rule:
                raise PolicyError(f"reguła bez klucza '{key}': {rule.get('id', rule)}")
        if rule["class"] not in pol["classes"]:
            raise PolicyError(f"reguła {rule['id']} wskazuje nieznaną klasę {rule['class']}")
        if rule["root"] not in pol["roots"]:
            raise PolicyError(f"reguła {rule['id']} wskazuje nieznany root {rule['root']}")
    return pol, sha256_bytes(raw)


def ack_token(policy_sha: str, day: str | None = None) -> str:
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"OD7-{day}-{policy_sha[:12]}"


# --------------------------------------------------------------------------- #
# 3. Klasyfikacja plików                                                       #
# --------------------------------------------------------------------------- #
class FileFact(dict):
    """Fakt o pliku (dict, żeby wprost lądował w raporcie JSON)."""


def _glob_match(rel: str, pattern: str) -> bool:
    if pattern.startswith("**/"):
        tail = pattern[3:]
        return fnmatch.fnmatch(rel, tail) or fnmatch.fnmatch(os.path.basename(rel), tail) \
            or fnmatch.fnmatch(rel, pattern)
    if "/" in pattern:
        return fnmatch.fnmatch(rel, pattern)
    return fnmatch.fnmatch(os.path.basename(rel), pattern) and "/" not in rel


def _date_from_name(name: str) -> datetime | None:
    m = _DATE_IN_NAME.search(name)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def resolve_granularity(declared: str, rel: str) -> str:
    """auto → sealed_dated / sealed_rotated / live_append."""
    if declared != "auto":
        return declared
    base = os.path.basename(rel)
    if _date_from_name(base) is not None:
        return "sealed_dated"
    if _ROTATED_SUFFIX.search(base):
        return "sealed_rotated"
    return "live_append"


def match_rule(pol: dict, root_key: str, rel: str) -> dict | None:
    for rule in pol["rules"]:
        if rule["root"] != root_key:
            continue
        for g in rule["globs"]:
            if _glob_match(rel, g):
                return rule
    return None


def is_excluded(pol: dict, rel: str) -> bool:
    return any(_glob_match(rel, g) for g in pol.get("exclude_globs", []))


def scan_roots(pol: dict, now: datetime, errors: list) -> list[FileFact]:
    facts: list[FileFact] = []
    for root_key, root_path in pol["roots"].items():
        if not os.path.isdir(root_path):
            errors.append({"where": f"root:{root_key}", "error": f"brak katalogu {root_path}"})
            continue
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                ap = os.path.join(dirpath, fn)
                rel = os.path.relpath(ap, root_path)
                try:
                    st = os.lstat(ap)
                except OSError as exc:
                    errors.append({"where": rel, "error": f"lstat: {exc}"})
                    continue
                if not os.path.isfile(ap) or os.path.islink(ap):
                    continue
                if is_excluded(pol, rel):
                    continue
                rule = match_rule(pol, root_key, rel)
                cls = rule["class"] if rule else "unknown"
                gran = resolve_granularity(rule["granularity"], rel) if rule else "any"
                data_dt = _date_from_name(os.path.basename(rel))
                if gran == "sealed_dated" and data_dt is not None:
                    # plik dobowy jest domknięty dopiero po końcu swojej doby
                    sealed_at = data_dt + timedelta(days=1)
                else:
                    sealed_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                    data_dt = data_dt or sealed_at
                facts.append(FileFact(
                    root=root_key,
                    rel_path=rel,
                    abs_path=ap,
                    size=st.st_size,
                    mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    data_month=data_dt.strftime("%Y-%m"),
                    age_days=round((now - sealed_at).total_seconds() / 86400.0, 2),
                    rule_id=rule["id"] if rule else None,
                    cls=cls,
                    granularity=gran,
                ))
    facts.sort(key=lambda f: (-f["size"], f["rel_path"]))
    return facts


# --------------------------------------------------------------------------- #
# 4. Progi: kiedy kopiować do archiwum                                         #
# --------------------------------------------------------------------------- #
def effective_archive_at_days(pol: dict, rule: dict | None, cls_cfg: dict) -> tuple[float | None, dict | None]:
    """Kopiujemy do archiwum wcześniej, niż OD-7 pozwala kasować, jeśli inny mechanizm
    kasuje szybciej. Zwraca (próg_dni, konflikt|None)."""
    live = cls_cfg.get("live_days")
    if live is None:
        return None, None
    margin = pol.get("default_margin_days", 3)
    gc = (rule or {}).get("competing_gc") or None
    if not gc or gc.get("deletes_after_days") is None:
        return float(live), None
    gc_days = float(gc["deletes_after_days"])
    conflict = None
    if gc_days < live:
        conflict = {
            "rule_id": (rule or {}).get("id"),
            "od7_live_days": live,
            "existing_gc_deletes_after_days": gc_days,
            "existing_gc_owner": (rule or {}).get("live_delete_owner"),
            "rule_note": (rule or {}).get("_note"),
            "skutek": ("istniejący mechanizm kasuje dane ZANIM polityka OD-7 pozwala je zdjąć "
                       "z żywego dysku — archiwum musi wyprzedzić kasownik"),
        }
    return max(0.0, min(float(live), gc_days - margin)), conflict


# --------------------------------------------------------------------------- #
# 5. Otwarte pliki (skan /proc — bez zależności od lsof)                        #
# --------------------------------------------------------------------------- #
def open_for_write_paths() -> set[str]:
    out: set[str] = set()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        # ŚWIADOMIE bez wykluczania własnego PID-u: to narzędzie nigdy nie otwiera pliku
        # ŹRÓDŁOWEGO do zapisu, więc self-hit oznaczałby realny konflikt i ma zablokować akcję.
        fddir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fddir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fddir, fd))
            except OSError:
                continue
            if not target.startswith("/"):
                continue
            try:
                with open(f"/proc/{pid}/fdinfo/{fd}", "r", encoding="ascii", errors="replace") as fh:
                    info = fh.read()
            except OSError:
                continue
            m = re.search(r"^flags:\s*(\d+)", info, re.M)
            if not m:
                continue
            flags = int(m.group(1), 8)
            if flags & (os.O_WRONLY | os.O_RDWR):
                out.add(os.path.realpath(target))
    return out


# --------------------------------------------------------------------------- #
# 6. Maskowanie PII                                                            #
# --------------------------------------------------------------------------- #
class Masker:
    def __init__(self, pii_cfg: dict, salt: bytes | None):
        self.cfg = pii_cfg
        self.salt = salt
        self.redact = pii_cfg.get("redact_token", "[PII-REDACTED]")
        self.prefix = pii_cfg.get("pseudonym_prefix", "pii_")
        # Wzorce kluczy to REGEXY (granice slow), nie gole podciagi — patrz _key_patterns_doc.
        self._kr = [re.compile(k.lower()) for k in pii_cfg.get("key_patterns_redact", [])]
        self._kp = [re.compile(k.lower()) for k in pii_cfg.get("key_patterns_pseudonymize", [])]
        # ŚWIADOMIE case-SENSITIVE: z re.I wzorzec "^[A-Z0-9_]{4,}$" pasowałby do KAŻDEGO
        # klucza i maskowanie byłoby cichym no-opem (najgorszy możliwy tryb awarii RODO).
        self._kx = [re.compile(p) for p in pii_cfg.get("key_regex_exclude", [])]
        self._vp = [(p["name"], re.compile(p["regex"])) for p in pii_cfg.get("value_patterns", [])]
        self.stats: dict[str, int] = {}

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    def key_action(self, key: str) -> str | None:
        """Kolejność jest kontraktem: WYKLUCZENIA → pseudonimizacja (bardziej szczegółowa,
        np. `address_id`) → redakcja. Odwrotna kolejność zamaskowałaby nazwy flag i klucze
        `*_id`, czyli zabiłaby telemetrię silnika w archiwum (znalezione w biegu 05.08)."""
        k = key.lower()
        for rx in self._kx:
            if rx.search(key):
                return None
        for rx in self._kp:
            if rx.search(k):
                return "pseudonymize"
        for rx in self._kr:
            if rx.search(k):
                return "redact"
        return None

    def pseudonym(self, value: str) -> str:
        if self.salt is None:
            raise RuntimeError(
                "OD7-PII: tryb pseudonimizacji wymaga soli w env OD7_PII_SALT (fail-closed)"
            )
        digest = hmac.new(self.salt, value.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{self.prefix}{digest[:12]}"

    def mask_value_text(self, text: str) -> str:
        for name, rx in self._vp:
            text, n = rx.subn(self.redact, text)
            if n:
                self._bump(f"value:{name}")
        return text

    def mask_obj(self, obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                act = self.key_action(str(k))
                if act == "redact" and v is not None:
                    self._bump(f"key:{k}")
                    out[k] = self.redact
                elif act == "pseudonymize" and isinstance(v, (str, int, float)):
                    self._bump(f"key:{k}")
                    out[k] = self.pseudonym(str(v))
                else:
                    out[k] = self.mask_obj(v)
            return out
        if isinstance(obj, list):
            return [self.mask_obj(v) for v in obj]
        if isinstance(obj, str):
            return self.mask_value_text(obj)
        return obj

    def mask_line(self, line: str) -> str:
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                obj = json.loads(stripped)
            except Exception:  # noqa: BLE001
                return self.mask_value_text(line)
            return json.dumps(self.mask_obj(obj), ensure_ascii=False) + "\n"
        return self.mask_value_text(line)

    def mask_stream(self, src: io.BufferedReader, dst) -> None:
        for raw in src:
            line = raw.decode("utf-8", "replace")
            dst.write(self.mask_line(line).encode("utf-8"))


def detect_pii(path: str, masker: Masker, sample_bytes: int = MASK_SAMPLE_BYTES) -> dict:
    """Wykrycie PII BEZ ujawniania wartości — zwraca wyłącznie liczniki trafień."""
    hits: dict[str, int] = {}
    opener = gzip.open if path.endswith(".gz") else open
    read = 0
    try:
        with opener(path, "rb") as fh:  # type: ignore[operator]
            for raw in fh:
                read += len(raw)
                line = raw.decode("utf-8", "replace")
                stripped = line.strip()
                if stripped.startswith("{"):
                    try:
                        obj = json.loads(stripped)
                    except Exception:  # noqa: BLE001
                        obj = None
                    if isinstance(obj, dict):
                        for k in _walk_keys(obj):
                            safe = _safe_key_label(k, masker)
                            act = masker.key_action(k)
                            if act:
                                hits[f"key:{safe}:{act}"] = hits.get(f"key:{safe}:{act}", 0) + 1
                            # Klucz, ktory SAM wyglada na PII (slownik kluczowany adresem):
                            # maskowanie WARTOSCI go nie zakrywa — zgloszamy osobno.
                            for name, rx in masker._vp:  # noqa: SLF001
                                if rx.search(k):
                                    key_hit = f"KEYNAME_IS_PII:{name}"
                                    hits[key_hit] = hits.get(key_hit, 0) + 1
                for name, rx in masker._vp:  # noqa: SLF001 (świadomie: to ten sam kontrakt)
                    n = len(rx.findall(line))
                    if n:
                        hits[f"value:{name}"] = hits.get(f"value:{name}", 0) + n
                if read >= sample_bytes:
                    break
    except OSError as exc:
        return {"error": str(exc)}
    return {"sampled_bytes": read, "hits": hits}


def _safe_key_label(key: str, masker: "Masker") -> str:
    """Etykieta klucza do RAPORTU. Klucz bywa sam nosnikiem PII (slownik kluczowany
    adresem/telefonem) — wtedy do raportu idzie skrot, nigdy wartosc. Raport o PII nie
    moze byc kolejnym miejscem wycieku PII."""
    for _name, rx in masker._vp:  # noqa: SLF001
        if rx.search(key):
            return "sha:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    if len(key) > 40 or " " in key:
        return "sha:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return key


def _walk_keys(obj, depth: int = 0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_keys(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:20]:
            yield from _walk_keys(v, depth + 1)


# --------------------------------------------------------------------------- #
# 7. Archiwum: ścieżki, manifest, kopiowanie                                   #
# --------------------------------------------------------------------------- #
def archive_target(archive_root: str, cls: str, month: str, root_key: str, rel: str) -> str:
    name = rel + (".gz" if not rel.endswith(".gz") else "")
    return os.path.join(archive_root, cls, month, root_key, name)


def manifest_path(archive_root: str) -> str:
    return os.path.join(archive_root, "MANIFEST.jsonl")


def read_manifest(archive_root: str | None) -> list[dict]:
    if not archive_root:
        return []
    mp = manifest_path(archive_root)
    if not os.path.exists(mp):
        return []
    out = []
    with open(mp, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"OD7-MANIFEST: nieparsowalna linia w {mp} (fail-closed)") from exc
    return out


def append_manifest(archive_root: str, record: dict) -> None:
    mp = manifest_path(archive_root)
    GATE.check(mp)
    os.makedirs(os.path.dirname(mp), exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(mp, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def gzip_copy_verified(src: str, dst: str, masker: Masker | None) -> dict:
    """src → dst.gz atomowo; weryfikacja: sha256 zawartości po dekompresji == sha256
    tego, co miało trafić do archiwum. Zwraca rekord manifestu (bez pól kontekstowych)."""
    GATE.check(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    src_sha, src_size = sha256_file(src)
    d = os.path.dirname(dst)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".od7_", suffix=".gz.tmp")
    content_hash = hashlib.sha256()
    mask_stats: dict[str, int] = {}
    try:
        with os.fdopen(fd, "wb") as rawout:
            with gzip.GzipFile(fileobj=rawout, mode="wb", mtime=0) as gz:
                opener = gzip.open if src.endswith(".gz") else open
                with opener(src, "rb") as fin:  # type: ignore[operator]
                    if masker is None:
                        while True:
                            chunk = fin.read(1 << 20)
                            if not chunk:
                                break
                            content_hash.update(chunk)
                            gz.write(chunk)
                    else:
                        for raw in fin:
                            out = masker.mask_line(raw.decode("utf-8", "replace")).encode("utf-8")
                            content_hash.update(out)
                            gz.write(out)
                        mask_stats = dict(masker.stats)
                        masker.stats = {}
            rawout.flush()
            os.fsync(rawout.fileno())
        # weryfikacja przed publikacją (fail-closed)
        verify = hashlib.sha256()
        with gzip.open(tmp, "rb") as gz:
            while True:
                chunk = gz.read(1 << 20)
                if not chunk:
                    break
                verify.update(chunk)
        if verify.hexdigest() != content_hash.hexdigest():
            raise RuntimeError(f"OD7-ARCHIVE-VERIFY: sha po dekompresji != sha zapisanej treści ({src})")
        os.replace(tmp, dst)
        _fsync_dir(d)
    except BaseException:
        _rm_quiet(tmp)
        raise
    arch_sha, arch_size = sha256_file(dst)
    return {
        "source_sha256": src_sha,
        "source_size": src_size,
        "content_sha256": content_hash.hexdigest(),
        "archive_path": dst,
        "archive_sha256": arch_sha,
        "archive_size": arch_size,
        "masked": masker is not None,
        "mask_stats": mask_stats,
    }


def sqlite_snapshot(src_db: str, dst: str) -> dict:
    """Online backup żywej bazy (czyta, NIE modyfikuje) → gzip w archiwum."""
    GATE.check(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    d = os.path.dirname(dst)
    fd, tmp_db = tempfile.mkstemp(dir=d, prefix=".od7_", suffix=".sqlite.tmp")
    os.close(fd)
    try:
        src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        try:
            dstconn = sqlite3.connect(tmp_db)
            try:
                src.backup(dstconn)
            finally:
                dstconn.close()
        finally:
            src.close()
        rec = gzip_copy_verified(tmp_db, dst, None)
        rec["source_size"] = os.path.getsize(src_db)
        rec["snapshot_size"] = os.path.getsize(tmp_db)
        return rec
    finally:
        _rm_quiet(tmp_db)


def compress_ratio_estimate(path: str, size: int) -> float:
    """Szacunek stopnia kompresji na próbce (read-only, bez zapisu na dysk)."""
    if path.endswith(".gz") or size == 0:
        return 1.0
    try:
        with open(path, "rb") as fh:
            sample = fh.read(min(size, COMPRESS_SAMPLE_BYTES))
    except OSError:
        return 1.0
    if not sample:
        return 1.0
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
        gz.write(sample)
    return max(0.01, len(buf.getvalue()) / float(len(sample)))


# --------------------------------------------------------------------------- #
# 8. Plan                                                                      #
# --------------------------------------------------------------------------- #
ACT_ARCHIVE = "ARCHIVE"
ACT_ARCHIVE_MASKED = "ARCHIVE_MASKED"
ACT_SQLITE_SNAPSHOT = "SQLITE_SNAPSHOT"
ACT_MASK_LIVE = "MASK_LIVE"
ACT_ARCHIVE_EXPIRE = "ARCHIVE_EXPIRE"
ACT_SOURCE_DELETE = "SOURCE_DELETE"
SKIP_TOO_YOUNG = "SKIP_TOO_YOUNG"
SKIP_LIVE_APPEND = "SKIP_LIVE_APPEND"
SKIP_OPEN = "SKIP_OPEN_BY_PROCESS"
SKIP_ALREADY = "SKIP_ALREADY_ARCHIVED"
SKIP_NOT_ARCHIVABLE = "SKIP_NOT_ARCHIVABLE"
REPORT_UNKNOWN = "REPORT_UNKNOWN"

MUTATING_ACTIONS = {ACT_ARCHIVE, ACT_ARCHIVE_MASKED, ACT_SQLITE_SNAPSHOT,
                    ACT_MASK_LIVE, ACT_ARCHIVE_EXPIRE, ACT_SOURCE_DELETE}


def build_plan(pol: dict, facts: list[FileFact], now: datetime, archive_root: str | None,
               manifest: list[dict], open_writes: set[str]) -> dict:
    by_rule = {r["id"]: r for r in pol["rules"]}
    archived_rel = {(m.get("root"), m.get("rel_path")) for m in manifest
                    if m.get("action") in (ACT_ARCHIVE, ACT_ARCHIVE_MASKED)}
    last_snapshot: dict[str, str] = {}
    for m in manifest:
        if m.get("action") == ACT_SQLITE_SNAPSHOT:
            key = m.get("rel_path", "")
            if key not in last_snapshot or m.get("ts", "") > last_snapshot[key]:
                last_snapshot[key] = m.get("ts", "")

    actions: list[dict] = []
    conflicts: dict[str, dict] = {}
    rotation_debt: list[dict] = []

    for fact in facts:
        rule = by_rule.get(fact["rule_id"]) if fact["rule_id"] else None
        cls_cfg = pol["classes"][fact["cls"]]
        item = dict(fact)
        at_days, conflict = effective_archive_at_days(pol, rule, cls_cfg)
        if conflict and conflict["rule_id"] not in conflicts:
            conflicts[conflict["rule_id"]] = conflict
        item["archive_at_days"] = at_days
        item["archive_days"] = cls_cfg.get("archive_days")
        item["pii_mask_days"] = cls_cfg.get("pii_mask_days")
        item["live_delete_owner"] = (rule or {}).get("live_delete_owner")

        needs_mask = (cls_cfg.get("pii_mask_days") is not None
                      and fact["age_days"] >= cls_cfg["pii_mask_days"])

        if fact["cls"] == "unknown":
            item["action"] = REPORT_UNKNOWN
            item["reason"] = "poza literalnym zakresem OD-7 — decyzja ownera przed jakąkolwiek akcją"
        elif not cls_cfg.get("archivable"):
            if fact["cls"] == "ops_logs" and needs_mask:
                if fact["granularity"] == "live_append":
                    item["action"] = SKIP_LIVE_APPEND
                    item["reason"] = ("log >3 mies. wymaga maskowania, ale jest ŻYWO dopisywany — "
                                      "maskowanie po rotacji (dług: brak rotacji/za długi cykl)")
                else:
                    item["action"] = ACT_MASK_LIVE
                    item["reason"] = (f"log starszy niż {cls_cfg['pii_mask_days']} dni — "
                                      "maskowanie PII (OD-7)")
                    if _under_forbidden(fact["abs_path"]):
                        item["requires_separate_ack"] = True
                        item["reason"] += ("; leży w ŻYWYM korzeniu → to narzędzie NIE pisze tam "
                                           "w żadnym trybie: maskowanie in-place wymaga osobnej "
                                           "decyzji ownera (kasuje treść bezpowrotnie)")
            else:
                item["action"] = SKIP_NOT_ARCHIVABLE
                item["reason"] = f"klasa '{fact['cls']}' bez klauzuli archiwum w OD-7"
        elif fact["granularity"] == "sqlite_db":
            due = True
            last = last_snapshot.get(fact["rel_path"])
            if last:
                try:
                    due = (now - datetime.fromisoformat(last)).days >= pol.get("sqlite_snapshot_every_days", 30)
                except Exception:  # noqa: BLE001
                    due = True
            item["action"] = ACT_SQLITE_SNAPSHOT if due else SKIP_ALREADY
            item["reason"] = ("snapshot online bazy (Connection.backup, read-only) — archiwum "
                              f"{'bezterminowe' if cls_cfg.get('archive_days') is None else str(cls_cfg['archive_days']) + ' dni'}; "
                              "ŻADNE wiersze nie są kasowane") if due else "snapshot świeży"
        elif fact["granularity"] == "live_state":
            item["action"] = SKIP_NOT_ARCHIVABLE
            item["reason"] = "snapshot stanu (nie historia) — archiwizacja bez wartości"
        elif fact["granularity"] == "live_append":
            item["action"] = SKIP_LIVE_APPEND
            item["reason"] = ("plik ŻYWO dopisywany — archiwizacja całości nie jest atomowa; "
                              "wymaga wpięcia rotacji, wtedy segment .1/.gz trafi do archiwum")
            rotation_debt.append({
                "root": fact["root"], "rel_path": fact["rel_path"], "size": fact["size"],
                "class": fact["cls"], "rule_id": fact["rule_id"],
                "live_delete_owner": (rule or {}).get("live_delete_owner"),
            })
        elif os.path.realpath(fact["abs_path"]) in open_writes:
            item["action"] = SKIP_OPEN
            item["reason"] = "plik otwarty do ZAPISU przez proces (skan /proc/*/fd)"
        elif at_days is not None and fact["age_days"] < at_days:
            item["action"] = SKIP_TOO_YOUNG
            item["reason"] = f"wiek {fact['age_days']}d < próg {at_days}d"
        elif (fact["root"], fact["rel_path"]) in archived_rel:
            item["action"] = SKIP_ALREADY
            item["reason"] = "już w manifeście archiwum"
        else:
            item["action"] = ACT_ARCHIVE_MASKED if needs_mask else ACT_ARCHIVE
            item["reason"] = (f"wiek {fact['age_days']}d >= próg {at_days}d"
                              + (f"; dane >{cls_cfg['pii_mask_days']}d → maskowanie PII" if needs_mask else ""))
            if archive_root:
                item["archive_target"] = archive_target(
                    archive_root, fact["cls"], fact["data_month"], fact["root"], fact["rel_path"])
        actions.append(item)

    # wygasanie archiwum (owner: TEN automat — archive-root jest jego wyłączną własnością)
    expiries: list[dict] = []
    if archive_root:
        for m in manifest:
            if m.get("action") not in (ACT_ARCHIVE, ACT_ARCHIVE_MASKED):
                continue
            cls_cfg = pol["classes"].get(m.get("class", ""), {})
            keep = cls_cfg.get("archive_days")
            if keep is None:
                continue
            try:
                archived_at = datetime.fromisoformat(m["ts"])
            except Exception:  # noqa: BLE001
                continue
            if (now - archived_at).days >= keep and os.path.exists(m.get("archive_path", "")):
                expiries.append({
                    "action": ACT_ARCHIVE_EXPIRE, "class": m.get("class"),
                    "archive_path": m["archive_path"], "archived_at": m["ts"],
                    "archive_size": m.get("archive_size", 0),
                    "reason": f"archiwum starsze niż {keep} dni (OD-7: kasacja po terminie archiwum)",
                })
    return {"actions": actions, "expiries": expiries,
            "policy_conflicts": list(conflicts.values()), "rotation_debt": rotation_debt}


# --------------------------------------------------------------------------- #
# 9. Raport                                                                    #
# --------------------------------------------------------------------------- #
def archive_root_proposal(pol: dict, growth_per_day_est: float, projection_bytes: float) -> dict:
    """Propozycja `--archive-root` — SAM KATALOG NIE JEST TWORZONY (decyzja ownera)."""
    cands = []
    for path in pol.get("archive_root_candidates", []):
        parent = path if os.path.isdir(path) else os.path.dirname(path)
        d = _disk(parent)
        cands.append({
            "path": path,
            "exists": os.path.isdir(path),
            "mount_free_bytes": d.get("free_bytes"),
            "mount_used_pct": d.get("used_pct"),
            "wystarczy_na_stan_ustalony": (d.get("free_bytes") or 0) > projection_bytes * 1.5,
        })
    return {
        "_doc": "Automat NIGDY nie tworzy katalogu archiwum — to świadoma decyzja właściciela.",
        "kandydaci": cands,
        "wzrost_bytes_per_day_est": int(growth_per_day_est),
        "stan_ustalony_bytes_est": int(projection_bytes),
        "dysk_zywy_nie_nadaje_sie": True,
    }


def archive_projection(pol: dict, to_archive: list[dict]) -> dict:
    """Ile archiwum urośnie w stanie ustalonym (per klasa: dzienny wolumen × okno OD-7)."""
    per_class: dict[str, dict] = {}
    for item in to_archive:
        c = per_class.setdefault(item["cls"], {"bytes_est": 0, "days": set()})
        c["bytes_est"] += int(item["size"] * item.get("compress_ratio", 1.0))
        c["days"].add(item["data_month"] + ":" + str(item["age_days"] // 1))
    out: dict[str, dict] = {}
    total_day = 0.0
    total_steady = 0.0
    for cls, v in per_class.items():
        n_days = max(1, len(v["days"]))
        per_day = v["bytes_est"] / n_days
        keep = pol["classes"][cls].get("archive_days")
        steady = per_day * (keep if keep is not None else 365)
        out[cls] = {"bytes_per_day_est": int(per_day), "archive_days": keep,
                    "steady_state_bytes_est": int(steady),
                    "_note": "archiwum bezterminowe — projekcja liczona na 12 mies." if keep is None else None}
        total_day += per_day
        total_steady += steady
    return {"per_class": out, "total_bytes_per_day_est": int(total_day),
            "total_steady_state_bytes_est": int(total_steady)}


def _disk(path: str) -> dict:
    try:
        st = os.statvfs(path)
    except OSError:
        return {}
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return {"path": path, "total_bytes": total, "free_bytes": free,
            "used_pct": round(100.0 * (total - free) / total, 1) if total else None}


def build_report(pol: dict, policy_sha: str, policy_path: str, plan: dict, facts: list[FileFact],
                 now: datetime, archive_root: str | None, mode: str, errors: list,
                 pii_scan: dict, run_id: str) -> dict:
    by_action: dict[str, dict] = {}
    by_class: dict[str, dict] = {}
    for item in plan["actions"]:
        a = by_action.setdefault(item["action"], {"files": 0, "bytes": 0})
        a["files"] += 1
        a["bytes"] += item["size"]
        c = by_class.setdefault(item["cls"], {"files": 0, "bytes": 0})
        c["files"] += 1
        c["bytes"] += item["size"]

    to_archive = [i for i in plan["actions"] if i["action"] in (ACT_ARCHIVE, ACT_ARCHIVE_MASKED)]
    arch_bytes = sum(i["size"] for i in to_archive)
    arch_est = sum(int(i["size"] * i.get("compress_ratio", 1.0)) for i in to_archive)
    snapshots = [i for i in plan["actions"] if i["action"] == ACT_SQLITE_SNAPSHOT]
    snap_est = sum(int(i["size"] * i.get("compress_ratio", 0.35)) for i in snapshots)
    expire_bytes = sum(e.get("archive_size", 0) for e in plan["expiries"])
    projection = archive_projection(pol, to_archive)
    snap_every = pol.get("sqlite_snapshot_every_days", 30) or 30
    growth_day = projection["total_bytes_per_day_est"] + snap_est / snap_every
    steady = projection["total_steady_state_bytes_est"] + snap_est * (365.0 / snap_every)

    return {
        "tool": "retention_archiver",
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": now.isoformat(),
        "mode": mode,
        "policy": {"path": policy_path, "version": pol["policy_version"],
                   "sha256": policy_sha, "source": pol.get("policy_source")},
        "roots": pol["roots"],
        "archive_root": archive_root,
        "archive_root_exists": bool(archive_root and os.path.isdir(archive_root)),
        "disk": {"live": _disk(list(pol["roots"].values())[0]),
                 "archive": _disk(archive_root) if archive_root and os.path.isdir(archive_root) else {}},
        "summary": {
            "files_scanned": len(facts),
            "bytes_scanned": sum(f["size"] for f in facts),
            "by_action": by_action,
            "by_class": by_class,
            "archive_now": {
                "files": len(to_archive), "bytes_live": arch_bytes,
                "bytes_archive_est": arch_est,
                "sqlite_snapshots": len(snapshots), "sqlite_bytes_est": snap_est,
            },
            "space_balance": {
                "_doc": ("Archiwizacja SAMA NIC NIE ZWALNIA na żywym dysku — kasowanie żywych plików "
                         "należy do istniejących GC. Bilans = ile archiwum ZAJMIE."),
                "freed_live_bytes": sum(i["size"] for i in plan["actions"]
                                        if i["action"] == ACT_SOURCE_DELETE),
                "archive_growth_bytes_est": arch_est + snap_est,
                "archive_freed_by_expiry_bytes": expire_bytes,
                "net_bytes_est": arch_est + snap_est - expire_bytes
                - sum(i["size"] for i in plan["actions"] if i["action"] == ACT_SOURCE_DELETE),
            },
            "archive_projection": projection,
        },
        "archive_root_proposal": archive_root_proposal(pol, growth_day, steady),
        "policy_conflicts": plan["policy_conflicts"],
        "rotation_debt": sorted(plan["rotation_debt"], key=lambda r: -r["size"]),
        "pii": pii_scan,
        "actions": plan["actions"],
        "expiries": plan["expiries"],
        "errors": errors,
    }


def _mb(n: float) -> str:
    return f"{n / 1048576.0:,.1f} MB".replace(",", " ")


def render_text(rep: dict) -> str:
    L: list[str] = []
    s = rep["summary"]
    L.append(f"# OD-7 RETENTION ARCHIVER — raport {rep['mode'].upper()}")
    L.append("")
    L.append(f"* run_id: `{rep['run_id']}` · {rep['generated_at']}")
    L.append(f"* polityka: `{rep['policy']['version']}` sha256={rep['policy']['sha256'][:12]} "
             f"(`{rep['policy']['path']}`)")
    L.append(f"* archive-root: {rep['archive_root'] or 'NIE PODANY (raport nie tworzy katalogu)'}"
             f"{' [istnieje]' if rep['archive_root_exists'] else ''}")
    if rep["disk"].get("live"):
        d = rep["disk"]["live"]
        L.append(f"* dysk żywy: {_mb(d['total_bytes'])} total, {_mb(d['free_bytes'])} wolne, {d['used_pct']} % zajęte")
    L.append("")
    L.append(f"## Skan: {s['files_scanned']} plików / {_mb(s['bytes_scanned'])}")
    L.append("")
    L.append("| Akcja | Pliki | Rozmiar |")
    L.append("|---|---:|---:|")
    for act, v in sorted(s["by_action"].items(), key=lambda kv: -kv[1]["bytes"]):
        L.append(f"| {act} | {v['files']} | {_mb(v['bytes'])} |")
    L.append("")
    L.append("| Klasa OD-7 | Pliki | Rozmiar |")
    L.append("|---|---:|---:|")
    for cls, v in sorted(s["by_class"].items(), key=lambda kv: -kv[1]["bytes"]):
        L.append(f"| {cls} | {v['files']} | {_mb(v['bytes'])} |")
    L.append("")
    an = s["archive_now"]
    sb = s["space_balance"]
    L.append("## Bilans miejsca")
    L.append("")
    L.append(f"* do archiwum TERAZ: {an['files']} plików, {_mb(an['bytes_live'])} "
             f"→ po gzip ok. {_mb(an['bytes_archive_est'])}")
    L.append(f"* snapshoty sqlite: {an['sqlite_snapshots']} → ok. {_mb(an['sqlite_bytes_est'])}")
    L.append(f"* zwolnione z żywego dysku przez TEN automat: {_mb(sb['freed_live_bytes'])} "
             f"(kasowanie żywych plików należy do istniejących GC — patrz `live_delete_owner`)")
    L.append(f"* przyrost archiwum netto: ok. {_mb(sb['net_bytes_est'])}")
    L.append("")
    proj = s.get("archive_projection", {})
    if proj.get("per_class"):
        L.append("### Stan ustalony archiwum (przy dzisiejszym tempie)")
        L.append("")
        L.append("| Klasa | Dziennie (po gzip) | Okno archiwum OD-7 | Docelowo |")
        L.append("|---|---:|---:|---:|")
        for cls, v in sorted(proj["per_class"].items(), key=lambda kv: -kv[1]["steady_state_bytes_est"]):
            keep = v["archive_days"] if v["archive_days"] is not None else "bezterminowo"
            L.append(f"| {cls} | {_mb(v['bytes_per_day_est'])} | {keep} | "
                     f"{_mb(v['steady_state_bytes_est'])} |")
        L.append("")
    prop = rep.get("archive_root_proposal", {})
    if prop.get("kandydaci"):
        L.append("### Propozycja `--archive-root` (automat NIE tworzy katalogu)")
        L.append("")
        L.append(f"Wzrost ok. {_mb(prop['wzrost_bytes_per_day_est'])}/dobę, stan ustalony "
                 f"ok. {_mb(prop['stan_ustalony_bytes_est'])}. Żywy dysk (91 %+ zajęty) się NIE nadaje.")
        L.append("")
        L.append("| Kandydat | Istnieje | Wolne na mouncie | Zajęte | Wystarczy |")
        L.append("|---|---|---:|---:|---|")
        for c in prop["kandydaci"]:
            L.append(f"| `{c['path']}` | {'tak' if c['exists'] else 'NIE'} | "
                     f"{_mb(c['mount_free_bytes'] or 0)} | {c['mount_used_pct']} % | "
                     f"{'TAK' if c['wystarczy_na_stan_ustalony'] else 'nie'} |")
        L.append("")
    if rep["policy_conflicts"]:
        L.append("## ⛔ KONFLIKTY POLITYKI (istniejący GC kasuje szybciej niż OD-7 pozwala)")
        L.append("")
        L.append("| Reguła | OD-7 żywe [d] | GC kasuje po [d] | Owner kasowania |")
        L.append("|---|---:|---:|---|")
        for c in rep["policy_conflicts"]:
            L.append(f"| {c['rule_id']} | {c['od7_live_days']} | {c['existing_gc_deletes_after_days']} | "
                     f"{c['existing_gc_owner']} |")
        L.append("")
    if rep["rotation_debt"]:
        L.append("## Dług rotacji (żywe strumienie, których nie da się zarchiwizować w całości)")
        L.append("")
        L.append("| Plik | Klasa | Rozmiar | Kto dziś kasuje |")
        L.append("|---|---|---:|---|")
        for r in rep["rotation_debt"][:25]:
            L.append(f"| `{r['root']}:{r['rel_path']}` | {r['class']} | {_mb(r['size'])} | "
                     f"{r['live_delete_owner'] or 'NIKT'} |")
        L.append("")
    acts = [a for a in rep["actions"] if a["action"] in MUTATING_ACTIONS]
    if acts:
        L.append("## Akcje do wykonania w APPLY")
        L.append("")
        L.append("| Akcja | Plik | Klasa | Rozmiar | Wiek [d] | Powód |")
        L.append("|---|---|---|---:|---:|---|")
        for a in acts[:60]:
            L.append(f"| {a['action']} | `{a['root']}:{a['rel_path']}` | {a['cls']} | "
                     f"{_mb(a['size'])} | {a['age_days']} | {a['reason']} |")
        if len(acts) > 60:
            L.append(f"| … | _(+{len(acts) - 60} kolejnych w JSON)_ | | | | |")
        L.append("")
    unk = [a for a in rep["actions"] if a["action"] == REPORT_UNKNOWN]
    if unk:
        top = sorted(unk, key=lambda a: -a["size"])[:20]
        L.append(f"## UNKNOWN — {len(unk)} plików / "
                 f"{_mb(sum(a['size'] for a in unk))} (NIGDY nie ruszane, decyzja ownera)")
        L.append("")
        L.append("| Plik | Rozmiar | Wiek [d] |")
        L.append("|---|---:|---:|")
        for a in top:
            L.append(f"| `{a['root']}:{a['rel_path']}` | {_mb(a['size'])} | {a['age_days']} |")
        L.append("")
    if rep["pii"].get("files"):
        L.append("## PII — detekcja (liczniki trafień, BEZ wartości)")
        L.append("")
        L.append("| Plik | Klasa | Trafienia |")
        L.append("|---|---|---|")
        for f in rep["pii"]["files"][:25]:
            hits = f.get("hits", {})
            top_hits = ", ".join(f"{k}={v}" for k, v in sorted(hits.items(), key=lambda kv: -kv[1])[:6])
            mark = " ⚠ binarny" if f.get("binary") else ""
            L.append(f"| `{f['rel_path']}`{mark} | {f['cls']} | {top_hits or '—'} |")
        L.append("")
    if rep["errors"]:
        L.append(f"## BŁĘDY ({len(rep['errors'])}) — fail-closed")
        L.append("")
        for e in rep["errors"][:30]:
            L.append(f"* `{e.get('where')}`: {e.get('error')}")
        L.append("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# 10. APPLY                                                                    #
# --------------------------------------------------------------------------- #
def run_apply(pol: dict, plan: dict, archive_root: str, masker: Masker, now: datetime,
              run_id: str, policy_sha: str, limit: int | None) -> dict:
    done = {"archived": 0, "archived_bytes": 0, "snapshots": 0, "masked": 0,
            "masked_deferred": 0, "expired": 0}
    todo = [a for a in plan["actions"] if a["action"] in MUTATING_ACTIONS]
    if limit is not None:
        todo = todo[:limit]
    for item in todo:
        act = item["action"]
        if act in (ACT_ARCHIVE, ACT_ARCHIVE_MASKED):
            dst = archive_target(archive_root, item["cls"], item["data_month"],
                                 item["root"], item["rel_path"])
            rec = gzip_copy_verified(item["abs_path"], dst,
                                     masker if act == ACT_ARCHIVE_MASKED else None)
            rec.update({"ts": now.isoformat(), "run_id": run_id, "action": act,
                        "class": item["cls"], "root": item["root"], "rel_path": item["rel_path"],
                        "source_path": item["abs_path"], "source_mtime": item["mtime"],
                        "data_month": item["data_month"], "policy_version": pol["policy_version"],
                        "policy_sha256": policy_sha})
            append_manifest(archive_root, rec)
            done["archived"] += 1
            done["archived_bytes"] += rec["archive_size"]
        elif act == ACT_SQLITE_SNAPSHOT:
            dst = os.path.join(archive_root, item["cls"], now.strftime("%Y-%m"), item["root"],
                               item["rel_path"] + f".{now.strftime('%Y%m%dT%H%M%SZ')}.sqlite.gz")
            rec = sqlite_snapshot(item["abs_path"], dst)
            rec.update({"ts": now.isoformat(), "run_id": run_id, "action": act,
                        "class": item["cls"], "root": item["root"], "rel_path": item["rel_path"],
                        "source_path": item["abs_path"], "data_month": now.strftime("%Y-%m"),
                        "policy_version": pol["policy_version"], "policy_sha256": policy_sha})
            append_manifest(archive_root, rec)
            done["snapshots"] += 1
        elif act == ACT_MASK_LIVE:
            if _under_forbidden(item["abs_path"]):
                # Świadomie ODROCZONE, nie „po cichu pominięte": maskowanie in-place w żywym
                # korzeniu kasuje treść bezpowrotnie → osobna decyzja ownera. Liczone w raporcie.
                done["masked_deferred"] += 1
                continue
            rec = mask_in_place(item["abs_path"], masker)
            rec.update({"ts": now.isoformat(), "run_id": run_id, "action": act,
                        "class": item["cls"], "root": item["root"], "rel_path": item["rel_path"],
                        "source_path": item["abs_path"], "policy_version": pol["policy_version"],
                        "policy_sha256": policy_sha})
            append_manifest(archive_root, rec)
            done["masked"] += 1
        elif act == ACT_SOURCE_DELETE:
            raise RuntimeError("OD7-APPLY: SOURCE_DELETE nie ma dziś żadnej reguły "
                               "(live_delete_owner=archiver) — fail-closed")
    for exp in plan["expiries"]:
        path = exp["archive_path"]
        GATE.check(path)
        _rm_quiet(path)
        append_manifest(archive_root, {"ts": now.isoformat(), "run_id": run_id,
                                       "action": ACT_ARCHIVE_EXPIRE, "archive_path": path,
                                       "class": exp.get("class"),
                                       "policy_version": pol["policy_version"]})
        done["expired"] += 1
    return done


def mask_in_place(path: str, masker: Masker) -> dict:
    """Maskowanie ZAPIECZĘTOWANEGO pliku: temp obok → fsync → rename (nigdy w miejscu).

    UWAGA: to jedyna operacja, która dotyka pliku w żywym korzeniu, dlatego jest
    zablokowana przez WriteGate — wolno ją włączyć wyłącznie świadomą decyzją ownera
    (allow_root na korzeń logów), po potwierdzeniu, że plik nie ma żywego pisarza.
    """
    GATE.check(path)
    src_sha, src_size = sha256_file(path)
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".od7_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as fin:
            masker.mask_stream(fin, out)
            out.flush()
            os.fsync(out.fileno())
        stats = dict(masker.stats)
        masker.stats = {}
        os.replace(tmp, path)
        _fsync_dir(d)
    except BaseException:
        _rm_quiet(tmp)
        raise
    new_sha, new_size = sha256_file(path)
    return {"source_sha256": src_sha, "source_size": src_size,
            "masked_sha256": new_sha, "masked_size": new_size,
            "masked": True, "mask_stats": stats}


# --------------------------------------------------------------------------- #
# 11. CLI                                                                      #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    global GATE
    ap = argparse.ArgumentParser(description="OD-7 retention archiver (shadow-first)")
    ap.add_argument("--policy", default=DEFAULT_POLICY)
    ap.add_argument("--out", help="ścieżka raportu JSON (JEDYNA ścieżka zapisu w trybie report)")
    ap.add_argument("--text-out", help="ścieżka raportu tekstowego (markdown)")
    ap.add_argument("--archive-root", help="katalog archiwum (NIE jest tworzony w trybie report)")
    ap.add_argument("--apply", action="store_true", help="wykonaj akcje (wymaga --ack-token)")
    ap.add_argument("--ack-token", help="token ACK właściciela na dziś")
    ap.add_argument("--print-ack-token", action="store_true",
                    help="wypisz token ACK na dziś (do świadomego wydania przez ownera)")
    ap.add_argument("--include-sqlite", action="store_true",
                    help="w raporcie policz też wiersze w bazach (otwiera żywe .db read-only)")
    ap.add_argument("--pii-scan-limit", type=int, default=40,
                    help="ile plików-kandydatów przeskanować pod PII (0 = bez skanu)")
    ap.add_argument("--limit-actions", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        pol, policy_sha = load_policy(args.policy)
    except (OSError, PolicyError) as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 1

    if args.print_ack_token:
        print(ack_token(policy_sha))
        print("Token jest ważny do końca doby UTC i unieważnia go KAŻDA zmiana polityki.",
              file=sys.stderr)
        return 0

    now = datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex[:12]
    mode = "report"

    if args.apply:
        expected = ack_token(policy_sha)
        if not args.ack_token:
            print("STOP: tryb APPLY wymaga jawnego ACK właściciela (--ack-token). "
                  "1. bieg archiwizacji jest nieodwracalny w części kasującej archiwum "
                  "i eksportuje dane poza żywy katalog — token wydaje właściciel po "
                  "przeczytaniu raportu (--print-ack-token).", file=sys.stderr)
            return 2
        if not hmac.compare_digest(args.ack_token, expected):
            print("STOP: token ACK nieprawidłowy albo wygasły (ważny 1 dobę UTC, "
                  "unieważniany zmianą polityki).", file=sys.stderr)
            return 2
        if not args.archive_root:
            print("STOP: APPLY wymaga --archive-root.", file=sys.stderr)
            return 2
        if not os.path.isdir(args.archive_root):
            print(f"STOP: --archive-root nie istnieje: {args.archive_root} "
                  "(automat NIGDY nie tworzy katalogu archiwum — robi to właściciel).",
                  file=sys.stderr)
            return 2
        mode = "apply"

    GATE = WriteGate(mode)
    if args.out:
        GATE.allow_file(args.out)
    if args.text_out:
        GATE.allow_file(args.text_out)
    if mode == "apply":
        GATE.allow_root(args.archive_root)

    errors: list = []
    try:
        facts = scan_roots(pol, now, errors)
        manifest = read_manifest(args.archive_root)
        open_writes = open_for_write_paths()
        plan = build_plan(pol, facts, now, args.archive_root, manifest, open_writes)

        salt = os.environ.get("OD7_PII_SALT")
        masker = Masker(pol["pii"], salt.encode("utf-8") if salt else None)

        # oszacowanie kompresji + detekcja PII na kandydatach (read-only)
        cands = [i for i in plan["actions"] if i["action"] in MUTATING_ACTIONS]
        for item in cands:
            try:
                item["compress_ratio"] = round(compress_ratio_estimate(item["abs_path"], item["size"]), 4)
            except OSError as exc:
                errors.append({"where": item["rel_path"], "error": f"compress-est: {exc}"})
        pii_files = []
        if args.pii_scan_limit:
            for item in cands[: args.pii_scan_limit]:
                det = detect_pii(item["abs_path"], masker)
                if det.get("hits"):
                    binary = item["granularity"] == "sqlite_db"
                    pii_files.append({"rel_path": item["rel_path"], "cls": item["cls"],
                                      "root": item["root"], "size": item["size"],
                                      "age_days": item["age_days"],
                                      "sampled_bytes": det.get("sampled_bytes"),
                                      "binary": binary,
                                      "_note": ("plik BINARNY — liczniki wzorców wartościowych są "
                                                "poglądowe (fałszywe trafienia na bajtach), maskowanie "
                                                "wymaga przejścia po schemacie, nie po tekście")
                                      if binary else None,
                                      "hits": det["hits"]})
        pii_scan = {
            "salt_present": bool(salt),
            "recipe": {"redact_keys": pol["pii"]["key_patterns_redact"],
                       "pseudonymize_keys": pol["pii"]["key_patterns_pseudonymize"],
                       "value_patterns": [p["name"] for p in pol["pii"]["value_patterns"]]},
            "scanned_files": min(len(cands), args.pii_scan_limit),
            "files": pii_files,
        }

        if args.include_sqlite:
            for item in plan["actions"]:
                if item["granularity"] != "sqlite_db":
                    continue
                rule: dict = next((r for r in pol["rules"] if r["id"] == item["rule_id"]), {})
                item["sqlite_stats"] = sqlite_age_stats(item["abs_path"], rule, pol, now, errors)

        applied = None
        if mode == "apply":
            applied = run_apply(pol, plan, args.archive_root, masker, now, run_id,
                                policy_sha, args.limit_actions)

        rep = build_report(pol, policy_sha, args.policy, plan, facts, now,
                           args.archive_root, mode, errors, pii_scan, run_id)
        if applied is not None:
            rep["applied"] = applied
        if args.out:
            atomic_write_text(args.out, json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=False))
        if args.text_out:
            atomic_write_text(args.text_out, render_text(rep))
        if not args.quiet:
            s = rep["summary"]
            print(f"[OD-7 {mode}] plików={s['files_scanned']} "
                  f"({_mb(s['bytes_scanned'])}), do archiwum teraz={s['archive_now']['files']} "
                  f"({_mb(s['archive_now']['bytes_live'])} → ~{_mb(s['archive_now']['bytes_archive_est'])} po gzip), "
                  f"konflikty polityki={len(rep['policy_conflicts'])}, "
                  f"dług rotacji={len(rep['rotation_debt'])}, błędy={len(errors)}")
            if args.out:
                print(f"[OD-7] raport JSON: {args.out}")
            if args.text_out:
                print(f"[OD-7] raport tekstowy: {args.text_out}")
    except Exception as exc:  # noqa: BLE001 — fail-closed
        print(f"FAIL-CLOSED ({mode}): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 3 if errors else 0


def sqlite_age_stats(db_path: str, rule: dict, pol: dict, now: datetime, errors: list) -> dict:
    """Statystyka wieku wierszy (READ-ONLY). Świadomie za flagą --include-sqlite:
    otwarcie żywej bazy, choćby read-only, to dotknięcie żywego stanu."""
    spec = (rule or {}).get("sqlite") or {}
    table, ts_col = spec.get("table"), spec.get("ts_column")
    out: dict = {"db": db_path, "table": table, "ts_column": ts_col}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        errors.append({"where": db_path, "error": f"sqlite open: {exc}"})
        return {**out, "error": str(exc)}
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        out["tables"] = [r[0] for r in cur.fetchall()]
        if table and ts_col:
            cls_cfg = pol["classes"][rule["class"]]
            live = cls_cfg.get("live_days")
            cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 (nazwa z polityki, nie z inputu)
            out["rows_total"] = cur.fetchone()[0]
            if live is not None:
                cutoff = now - timedelta(days=live)
                bound = cutoff.timestamp() if spec.get("ts_is_epoch") else cutoff.isoformat()
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {ts_col} < ?", (bound,))  # noqa: S608
                out["rows_older_than_live_window"] = cur.fetchone()[0]
    except sqlite3.Error as exc:
        errors.append({"where": db_path, "error": f"sqlite query: {exc}"})
        out["error"] = str(exc)
    finally:
        conn.close()
    return out


if __name__ == "__main__":
    sys.exit(main())
