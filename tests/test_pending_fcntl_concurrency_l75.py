"""L7.5 (audyt 2.0 finding O1) — dowód, że kanon fcntl na `pending_proposals.json`
serializuje pisarzy i eliminuje lost-update.

Trzy dowody:
  1. KANON (`locked_mutate`) pod współbieżnością wieloprocesową → ZERO zgubionych wpisów.
  2. MUTACJA #1 (zdejmij lock) = surowy load→save bez LOCK_EX → lost-update REPRODUKOWALNY
     (część wpisów nadpisana) — dowód, że lock realnie coś robi.
  3. MUTACJA #2 (zdejmij atomic) = zapis in-place (truncate+write) → czytelnik łapie
     torn-read (JSONDecodeError); atomowy `save` (os.replace) → zero torn-read.

Plus test round-trip: format pliku niezmieniony (indent=2), wszyscy czytelnicy wsteczni
(store.load / telegram_approver.load_pending / postpone_sweeper._load_json_safe) czytają
zapis kanonu 1:1.
"""
import json
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from dispatch_v2 import pending_proposals_store as store


# ---- workery uruchamiane w OSOBNYCH PROCESACH (realny fcntl cross-process) ----

def _canon_worker(path: str, tag: str, n: int) -> int:
    """n razy dokłada unikalny klucz przez KANON (RMW pod LOCK_EX)."""
    from dispatch_v2 import pending_proposals_store as s
    for i in range(n):
        key = f"{tag}_{i}"
        s.locked_mutate(lambda p, _k=key: p.__setitem__(_k, {"tag": tag, "i": i}), path)
    return n


def _nolock_worker(path: str, tag: str, n: int, window_s: float) -> int:
    """n razy dokłada klucz surowym load→(okno)→save BEZ locka — replika stanu sprzed L7.5.
    `save` jest atomowy (os.replace), więc utrata bierze się WYŁĄCZNIE z braku LOCK_EX
    obejmującego cały cykl RMW (klasyczny lost-update)."""
    from dispatch_v2 import pending_proposals_store as s
    for i in range(n):
        d = s.load(path)
        if window_s:
            time.sleep(window_s)  # poszerza okno wyścigu (symuluje scheduling delay)
        d[f"{tag}_{i}"] = {"tag": tag, "i": i}
        s.save(d, path)
    return n


def _run_procs(fn, path, args_per_worker):
    with ProcessPoolExecutor(max_workers=len(args_per_worker)) as ex:
        futs = [ex.submit(fn, path, *a) for a in args_per_worker]
        for f in futs:
            f.result()


def test_canon_no_lost_update(tmp_path):
    """3 procesy × 120 wpisów przez KANON → wszystkie 360 kluczy obecne (0 lost)."""
    path = str(tmp_path / "pending_proposals.json")
    store.save({}, path)
    n = 120
    tags = ["W1", "W2", "W3"]
    _run_procs(_canon_worker, path, [(t, n) for t in tags])

    final = store.load(path)
    expected = {f"{t}_{i}" for t in tags for i in range(n)}
    missing = expected - set(final.keys())
    assert not missing, f"KANON zgubił {len(missing)} wpisów (lock nie serializuje): {list(missing)[:5]}"
    assert len(final) == len(expected) == 3 * n


def test_nolock_reproduces_race(tmp_path):
    """MUTACJA #1 (bez locka): ten sam scenariusz surowym RMW → część wpisów zgubiona.
    Dowód, że LOCK_EX kanonu jest konieczny (usunięcie go = regres)."""
    path = str(tmp_path / "pending_proposals.json")
    store.save({}, path)
    n = 120
    tags = ["W1", "W2", "W3"]
    # okno 2ms poszerza wyścig → utrata praktycznie pewna na współdzielonym pliku
    _run_procs(_nolock_worker, path, [(t, n, 0.002) for t in tags])

    final = store.load(path)
    expected = {f"{t}_{i}" for t in tags for i in range(n)}
    missing = expected - set(final.keys())
    assert missing, (
        "Bez locka NIE odtworzono lost-update — okno za wąskie / środowisko zbyt "
        "sekwencyjne; zwiększ window_s/n. (Ten test MUSI wykazać stratę, inaczej "
        "dowód konieczności locka jest pusty.)"
    )
    assert len(final) < len(expected)


# ---- MUTACJA #2: atomiczność (torn-read) — wątki w jednym procesie ----

def _nonatomic_save(pending, path):
    """Zapis in-place (truncate+write) BEZ os.replace — zostawia okno częściowego pliku."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=2, ensure_ascii=False)


def _hammer_reads(path, stop_evt, torn_counter):
    while not stop_evt.is_set():
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError:
            torn_counter.append(1)
        except (FileNotFoundError, ValueError):
            pass


def _torn_read_count(path, save_fn, big):
    stop = threading.Event()
    torn: list = []
    reader = threading.Thread(target=_hammer_reads, args=(path, stop, torn), daemon=True)
    reader.start()
    try:
        for _ in range(60):
            save_fn(big, path)
    finally:
        stop.set()
        reader.join(timeout=2)
    return len(torn)


def test_nonatomic_torn_read_vs_atomic(tmp_path):
    """MUTACJA #2: zapis in-place daje torn-read u czytelnika; atomowy `save` — zero."""
    path = str(tmp_path / "pending_proposals.json")
    big = {f"k{i}": {"decision_record": {"x": "y" * 200}, "sent_at": "2026-07-02T00:00:00+00:00"}
           for i in range(400)}
    store.save(big, path)  # zainicjuj poprawnym plikiem

    torn_nonatomic = _torn_read_count(path, _nonatomic_save, big)
    torn_atomic = _torn_read_count(path, store.save, big)

    assert torn_nonatomic > 0, (
        "Nie zaobserwowano torn-read przy zapisie in-place — zwiększ rozmiar `big`/liczbę "
        "iteracji (dowód atomiczności pusty)."
    )
    assert torn_atomic == 0, f"Atomowy save NIE powinien dawać torn-read, a dał {torn_atomic}"


# ---- round-trip: format niezmieniony, czytelnicy wsteczni czytają 1:1 ----

def test_roundtrip_format_and_readers(tmp_path):
    path = str(tmp_path / "pending_proposals.json")
    payload = {
        "485071": {
            "message_id": None,
            "sent_at": "2026-07-02T18:00:00+00:00",
            "expires_at": "2026-07-02T18:30:00+00:00",
            "decision_record": {"order_id": "485071", "verdict": "PROPOSE", "ą": "ł"},
        }
    }
    store.locked_save(payload, path)

    raw = open(path, encoding="utf-8").read()
    # format: 2-spacjowy indent + ensure_ascii=False (polskie znaki dosłownie)
    assert '\n  "485071"' in raw, "indent=2 utracony (format pliku zmieniony!)"
    assert '"ą": "ł"' in raw, "ensure_ascii=False utracone (unicode zescapowany)"

    # wszyscy czytelnicy wsteczni czytają identycznie
    from dispatch_v2 import telegram_approver as tg
    from dispatch_v2 import postpone_sweeper as ps
    assert store.load(path) == payload
    assert tg.load_pending(path) == payload
    assert ps._load_json_safe(path, {}) == payload


def test_locked_mutate_preserves_concurrent_entry(tmp_path):
    """RMW kanonu widzi świeży dysk: wpis dołożony między load a save NIE ginie
    (kontrast do blind-overwrite). Symulacja: mutate_fn dokłada B, ale plik na dysku
    już ma A (zapisane wcześniej) — po locked_mutate obecne OBA."""
    path = str(tmp_path / "pending_proposals.json")
    store.save({"A": {"v": 1}}, path)
    store.locked_mutate(lambda p: p.__setitem__("B", {"v": 2}), path)
    final = store.load(path)
    assert final == {"A": {"v": 1}, "B": {"v": 2}}
