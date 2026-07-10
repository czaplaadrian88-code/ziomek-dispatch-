"""hermetic_support — Z-P2-07 (Sprint 4): implementacja hermetyzacji suity.

Additywne. Aktywowane WYLACZNIE przez root `dispatch_v2/conftest.py` (rollback =
usun tamten plik). NIE modyfikuje zadnego istniejacego modulu ani testu.

Udostepnia:
  * `classify()`            — CZYSTY klasyfikator celu FS: allow / block_write / block_read,
  * `resolve_target()`      — rozwiazanie sciezki celu (utwardzenie realpath(parent)),
  * `install_guard()`       — instalacja guarda na PRYMITYWACH FS (open/os.open/replace/rename),
  * `make_sandbox_state_dir()` — fabryka izolowanego DISPATCH_STATE_DIR (+seed anonim. fixture),
  * `load_quarantine()`     — loader jawnej listy testow live/nonhermetycznych,
  * `strict_enabled()`      — detekcja trybu STRICT (HERMETIC_STRICT=1).

FILOZOFIA = DENYLIST. Blokujemy tylko cele pod ZYWYMI korzeniami produkcji;
wszystko inne (tmp, worktree, __pycache__, /dev/null, /proc) przechodzi bez zmian.
Dzieki temu tryb DEFAULT = zero zmiany zachowania zielonych testow (wg mapy A4 zaden
test nie pisze do produkcji), a guard jest BACKSTOPEM dla 631 hardcode + 30 zamrozonych
default-arg sciezek, ktorych env/monkeypatch NIE pokryja jednolicie.

Warstwa prymitywow (nie stale modulowe) bo:
  - `builtins.open` (tryb w/a/x/+)         — wiekszosc zapisow + json.dump(open(...)),
  - `os.open` (O_WRONLY/RDWR/CREAT/TRUNC/APPEND) — tempfile.mkstemp + Path.touch (low-level),
  - `os.replace` + `os.rename` (cel=dst)   — dominujacy idiom atomic mkstemp->replace silnika,
  - `os.unlink` + `os.remove` (klasa DELETE) — KASOWANIE zywego stanu = mutacja produkcji
    (Path.unlink idzie przez os.unlink); blokowane jak zapis pod zywymi korzeniami.
`shutil.move/copyfile` swiadomie NIE patchowane: dekomponuja sie do powyzszych prymitywow.
`os.rmdir` NIE patchowany (kasowanie katalogu = rzadkie; poza spec).
"""
from __future__ import annotations

import builtins
import errno
import json
import os
import shutil
import tempfile
from pathlib import Path

# ── Zywe korzenie produkcji (kanon) ─────────────────────────────────────────
LIVE_STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
LIVE_LOGS_DIR = "/root/.openclaw/workspace/scripts/logs"
LIVE_FLAGS_FILE = "/root/.openclaw/workspace/scripts/flags.json"

# ZAPIS blokowany pod tymi korzeniami we WSZYSTKICH trybach:
_WRITE_DIR_ROOTS = (LIVE_STATE_DIR, LIVE_LOGS_DIR)
# ODCZYT blokowany TYLKO w STRICT i TYLKO tu (DoD "suita bez dispatch_state"):
_READ_DIR_ROOTS_STRICT = (LIVE_STATE_DIR,)

ALLOW = "allow"
BLOCK_WRITE = "block_write"
BLOCK_READ = "block_read"

_TMP_REAL = os.path.realpath(tempfile.gettempdir())


def _under(path: str, root: str) -> bool:
    """True gdy `path` == root albo lezy pod katalogiem `root`."""
    root = root.rstrip(os.sep)
    return path == root or path.startswith(root + os.sep)


def resolve_target(path) -> "str | None":
    """Absolutna, znormalizowana sciezka celu do dopasowania denylisty.

    Utwardzenie (sugestia lidera): jesli katalog-RODZIC istnieje, rozwiaz go
    przez `os.path.realpath` — lapie dowiazania symboliczne wskazujace w zywy
    stan. Sam plik-lisc NIE jest realpathowany (tanio; dowiazanie liscia w zywy
    stan = ZNANY LIMIT, udokumentowany). `int` (fd) / nie-sciezka → None
    (wywolujacy przepuszcza)."""
    try:
        p = os.fspath(path)
    except TypeError:
        return None
    if isinstance(p, bytes):
        try:
            p = p.decode("utf-8", "surrogateescape")
        except Exception:
            return None
    if not p:
        return None
    ap = os.path.abspath(p)
    parent, base = os.path.split(ap)
    try:
        if parent and os.path.isdir(parent):
            parent = os.path.realpath(parent)
    except OSError:
        pass
    return os.path.normpath(os.path.join(parent, base) if base else parent)


def _is_whitelisted(resolved: str) -> bool:
    """Belt-and-suspenders: nigdy nie blokuj (nawet gdyby sandbox wyladowal pod
    zywym korzeniem). Denylist i tak przepuszcza te sciezki — to zabezpieczenie."""
    if resolved == os.devnull or resolved.startswith("/dev/") or resolved.startswith("/proc/"):
        return True
    if _under(resolved, _TMP_REAL):
        return True
    sb = os.environ.get("DISPATCH_STATE_DIR")
    if sb:
        try:
            if _under(resolved, os.path.normpath(sb)):
                return True
        except Exception:
            pass
    return False


def classify(resolved: "str | None", is_write: bool, strict: bool) -> str:
    """CZYSTY klasyfikator (bez env poza whitelista sandboxa). resolved=None → allow."""
    if resolved is None:
        return ALLOW
    if _is_whitelisted(resolved):
        return ALLOW
    if is_write:
        if resolved == LIVE_FLAGS_FILE:
            return BLOCK_WRITE
        for root in _WRITE_DIR_ROOTS:
            if _under(resolved, root):
                return BLOCK_WRITE
        return ALLOW
    # odczyt
    if strict:
        for root in _READ_DIR_ROOTS_STRICT:
            if _under(resolved, root):
                return BLOCK_READ
    return ALLOW


# ── Guard: instalacja na prymitywach FS ─────────────────────────────────────
_ORIG: dict = {}
_STRICT_MODE = False


def strict_enabled() -> bool:
    return os.environ.get("HERMETIC_STRICT") == "1"


def _opt_out() -> bool:
    """Swiadomy wyjatek (parytet z state_machine / setup_logger)."""
    return os.environ.get("ALLOW_PROD_STATE_IN_TEST") == "1"


def _check(resolved, is_write) -> str:
    if _opt_out():
        return ALLOW
    return classify(resolved, is_write, _STRICT_MODE)


def _write_block_message(resolved: str) -> str:
    return (
        f"HERMETIC-GUARD: zablokowano ZAPIS/KASOWANIE zywego stanu produkcyjnego: {resolved}. "
        f"Test nieizolowany — ryzyko nadpisania/skasowania/zatrucia stanu floty/logow/flag. "
        f"Napraw U ZRODLA: DISPATCH_STATE_DIR=<tmp> albo monkeypatch stalej sciezki "
        f"modulu na tmp_path. Swiadomy wyjatek (read-only smoke): ALLOW_PROD_STATE_IN_TEST=1."
    )


def _read_block_error(target) -> FileNotFoundError:
    return FileNotFoundError(
        errno.ENOENT,
        "HERMETIC-STRICT: odczyt zywego dispatch_state zablokowany "
        "(symulacja braku katalogu — suita ma przechodzic bez produkcji)",
        str(target),
    )


def _guarded_open(file, mode="r", *args, **kwargs):
    m = str(mode)
    is_write = any(c in m for c in ("w", "a", "x", "+"))
    resolved = resolve_target(file)
    action = _check(resolved, is_write)
    if action == BLOCK_WRITE:
        raise RuntimeError(_write_block_message(resolved))
    if action == BLOCK_READ:
        raise _read_block_error(file)
    return _ORIG["open"](file, mode, *args, **kwargs)


def _guarded_os_open(path, flags, mode=0o777, *, dir_fd=None):
    is_write = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    resolved = resolve_target(path) if dir_fd is None else None
    action = _check(resolved, is_write) if resolved is not None else ALLOW
    if action == BLOCK_WRITE:
        raise RuntimeError(_write_block_message(resolved))
    if action == BLOCK_READ:
        raise _read_block_error(path)
    return _ORIG["os_open"](path, flags, mode, dir_fd=dir_fd)


def _guarded_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    resolved = resolve_target(dst) if dst_dir_fd is None else None
    if resolved is not None and _check(resolved, True) == BLOCK_WRITE:
        raise RuntimeError(_write_block_message(resolved))
    return _ORIG["replace"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)


def _guarded_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    resolved = resolve_target(dst) if dst_dir_fd is None else None
    if resolved is not None and _check(resolved, True) == BLOCK_WRITE:
        raise RuntimeError(_write_block_message(resolved))
    return _ORIG["rename"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)


def _guarded_unlink(path, *, dir_fd=None):
    """Klasa DELETE (os.unlink/os.remove, w tym Path.unlink). KASOWANIE zywego stanu
    = mutacja produkcji → traktowane jak zapis (BLOCK_WRITE pod zywymi korzeniami)."""
    resolved = resolve_target(path) if dir_fd is None else None
    if resolved is not None and _check(resolved, True) == BLOCK_WRITE:
        raise RuntimeError(_write_block_message(resolved))
    return _ORIG["unlink"](path, dir_fd=dir_fd)


def install_guard(monkeypatch) -> bool:
    """Zainstaluj write/read/delete-guard na prymitywach FS przez przekazany MonkeyPatch.
    Kolejnosc: kopiujemy oryginaly RAZ (idempotencja) → podmieniamy. Zwraca tryb STRICT."""
    global _STRICT_MODE
    _STRICT_MODE = strict_enabled()
    if not _ORIG:
        _ORIG["open"] = builtins.open
        _ORIG["os_open"] = os.open
        _ORIG["replace"] = os.replace
        _ORIG["rename"] = os.rename
        _ORIG["unlink"] = os.unlink
    monkeypatch.setattr(builtins, "open", _guarded_open)
    monkeypatch.setattr(os, "open", _guarded_os_open)
    monkeypatch.setattr(os, "replace", _guarded_replace)
    monkeypatch.setattr(os, "rename", _guarded_rename)
    # os.remove == os.unlink semantycznie (ten sam syscall); jeden wrapper dla obu.
    monkeypatch.setattr(os, "unlink", _guarded_unlink)
    monkeypatch.setattr(os, "remove", _guarded_unlink)
    return _STRICT_MODE


def install_guard_subprocess() -> bool:
    """Instalator guarda dla SUBPROCESOW pytest (wolany z sitecustomize.py
    generowanego przez root-conftest; katalog na poczatku PYTHONPATH sesji).

    Bez MonkeyPatch i bez undo — patch na CZAS ZYCIA procesu-dziecka (umiera z nim).
    Idempotentny. Domyka luke 'script-runnery subprocess poza in-process guardem'
    (raport ZP207 §znane-luki #1). Aktywacja wylacznie pod DISPATCH_UNDER_PYTEST=1
    (pilnuje sitecustomize); opt-out: HERMETIC_SUBPROCESS_GUARD=0."""
    global _STRICT_MODE
    _STRICT_MODE = strict_enabled()
    if not _ORIG:
        _ORIG["open"] = builtins.open
        _ORIG["os_open"] = os.open
        _ORIG["replace"] = os.replace
        _ORIG["rename"] = os.rename
        _ORIG["unlink"] = os.unlink
    if builtins.open is _guarded_open:
        return _STRICT_MODE  # juz zainstalowany (idempotencja)
    builtins.open = _guarded_open
    os.open = _guarded_os_open
    os.replace = _guarded_replace
    os.rename = _guarded_rename
    os.unlink = _guarded_unlink
    os.remove = _guarded_unlink
    return _STRICT_MODE


# ── Sandbox state-dir + kwarantanna ─────────────────────────────────────────
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "hermetic"
_QUARANTINE_PATH = Path(__file__).resolve().parent / "hermetic_quarantine.json"


def make_sandbox_state_dir() -> str:
    """Utworz izolowany katalog stanu (tmp) i zasiej minimalnymi ANONIMOWYMI
    fixture'ami. Zwraca sciezke. Wolane przy imporcie root-conftest (guard jeszcze
    NIE zainstalowany; cel = tmp, poza zywym korzeniem = bezpieczne)."""
    d = tempfile.mkdtemp(prefix="hermetic_state_")
    try:
        for f in sorted(_FIXTURES_DIR.glob("*.json")):
            try:
                shutil.copyfile(str(f), os.path.join(d, f.name))
            except OSError:
                pass
    except OSError:
        pass
    return d


def load_quarantine() -> list:
    """Wczytaj jawna liste testow live/nonhermetycznych. Fail-soft → []."""
    try:
        data = json.loads(_QUARANTINE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries", []) if isinstance(data, dict) else []
    return [e for e in entries if isinstance(e, dict) and e.get("match")]
