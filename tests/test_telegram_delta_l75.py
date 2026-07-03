"""L7.5 delta-fix (audyt 2.0 O1, pas telegram-delta) — dowód, że telegram pisze DELTAMI
(`locked_set`/`locked_pop`), a nie blind-overwrite całego dicta z nieświeżej pamięci.

Problem: telegram trzymał pełny `state["pending"]` w pamięci (load raz na starcie) i przy
KAŻDEJ operacji nadpisywał CAŁY plik (`save_pending`). Nawet pod LOCK_EX to gubiło wpisy
dołożone współbieżnie przez shadow (`upsert_proposals`) PO ostatnim load telegramu —
lock serializuje zapis, ale blind-overwrite i tak zdejmuje cudzy klucz.

Dowody (nie deklaracje):
  A. DELTA `locked_pop` na świeżym stanie: wpis shadow dołożony po snapshot telegramu
     PRZEŻYWA pop telegramu. (kontrast do blind-save)
  B. MUTACJA (wróć do blind-save) = ten sam scenariusz reprodukuje UTRATĘ wpisu shadow
     → gdyby ktoś cofnął fix, ten test PADA (asercja `lost` przy blind, `survived` przy delta).
  C. WSPÓŁBIEŻNOŚĆ wieloprocesowa (realny fcntl): telegram-pop || shadow-upsert naraz →
     delta: wszystkie wpisy shadow obecne + popnięte telegramu usunięte; blind: część
     shadow zgubiona (dowód, że tylko delta jest race-free dla telegramu).
  D. Wrappery telegramu (`set_pending`/`pop_pending`) i drain (`locked_merge_missing`)
     zachowują format pliku (indent=2, unicode) i czytelników wstecznych 1:1;
     drain jest ADDITIVE (nie wskrzesza popów, nie kasuje cudzych).
"""
import json
import time
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor

from dispatch_v2 import pending_proposals_store as store


def _tg_entry(oid, mid):
    return {
        "order_id": str(oid),
        "message_id": mid,
        "sent_at": "2026-07-02T18:00:00+00:00",
        "expires_at": "2026-07-02T18:30:00+00:00",
        "decision_record": {"order_id": str(oid), "verdict": "PROPOSE"},
    }


# ---------- A. DELTA locked_pop zachowuje wpis shadow dołożony po snapshot ----------

def test_delta_pop_preserves_concurrent_shadow_entry(tmp_path):
    path = str(tmp_path / "pending_proposals.json")
    # telegram wysłał T1 (jest na dysku i w jego pamięci-snapshot)
    store.save({"T1": _tg_entry("T1", 111)}, path)
    tg_snapshot = store.load(path)  # nieświeża pamięć telegramu (bez przyszłego S1)

    # shadow dokłada S1 PO snapshot telegramu (współbieżnie), własnym kanałem
    store.locked_set("S1", _tg_entry("S1", None), path)

    # telegram finalizuje T1 → DELTA pop (nie blind-save snapshotu)
    store.locked_pop("T1", path)

    final = store.load(path)
    assert "T1" not in final, "pop telegramu nie usunął własnego wpisu"
    assert "S1" in final, "DELTA zgubiła wpis shadow dołożony po snapshot (regres lost-update)"

    # kontrola negatywna: gdyby telegram zrobił blind-save swojego snapshotu (po pop T1),
    # wpis shadow S1 zostałby SKASOWANY — dowód konieczności delty.
    tg_snapshot.pop("T1", None)
    store.locked_save(tg_snapshot, path)  # <- stary blind-overwrite
    after_blind = store.load(path)
    assert "S1" not in after_blind, (
        "blind-save NIE skasował S1 — scenariusz nie dowodzi różnicy delta vs blind"
    )


# ---------- B/C. WSPÓŁBIEŻNOŚĆ wieloprocesowa (realny fcntl cross-process) ----------

def _shadow_upsert_worker(path, m, window_s):
    """Shadow dokłada m wpisów realnym kanałem `upsert_proposals` (RMW pod LOCK_EX).
    UWAGA: `path=` OBOWIĄZKOWO — bez niego `upsert_proposals` defaultuje do
    PRODUKCYJNEGO PENDING_PATH (nie do tmp_path)."""
    from dispatch_v2 import pending_proposals_store as s
    assert path != s.PENDING_PATH, "test nie może pisać do produkcyjnego PENDING_PATH"
    now = datetime.now(timezone.utc)
    for i in range(m):
        if window_s:
            time.sleep(window_s)
        s.upsert_proposals([(f"S_{i}", {"order_id": f"S_{i}", "verdict": "PROPOSE"})], now, path=path)
    return m


def _tg_delta_worker(path, keys, window_s):
    """Telegram usuwa własne klucze DELTĄ (`locked_pop`) — świeży stan spod locka."""
    from dispatch_v2 import pending_proposals_store as s
    for k in keys:
        if window_s:
            time.sleep(window_s)
        s.locked_pop(k, path)
    return len(keys)


def _tg_blind_worker(path, keys, window_s):
    """STARE zachowanie: load raz (snapshot), potem pop-ze-snapshotu + blind-save CAŁOŚCI.
    Gubi wpisy shadow dołożone po snapshot (dowód reprodukcji buga sprzed fixa)."""
    from dispatch_v2 import pending_proposals_store as s
    snap = s.load(path)  # snapshot RAZ, jak telegram na starcie
    for k in keys:
        if window_s:
            time.sleep(window_s)
        snap.pop(k, None)
        s.locked_save(snap, path)  # blind-overwrite pod lockiem
    return len(keys)


def _seed_tg_keys(path, n):
    d = {f"T_{i}": _tg_entry(f"T_{i}", 1000 + i) for i in range(n)}
    store.save(d, path)
    return [f"T_{i}" for i in range(n)]


def test_delta_concurrent_shadow_survives(tmp_path):
    """DELTA: telegram-pop || shadow-upsert naraz → OBA efekty przeżywają."""
    path = str(tmp_path / "pending_proposals.json")
    tg_keys = _seed_tg_keys(path, 60)
    m = 60
    with ProcessPoolExecutor(max_workers=2) as ex:
        f_tg = ex.submit(_tg_delta_worker, path, tg_keys, 0.001)
        f_sh = ex.submit(_shadow_upsert_worker, path, m, 0.001)
        f_tg.result()
        f_sh.result()

    final = store.load(path)
    missing_shadow = {f"S_{i}" for i in range(m)} - set(final.keys())
    leftover_tg = {k for k in tg_keys if k in final}
    assert not missing_shadow, f"DELTA zgubiła {len(missing_shadow)} wpisów shadow: {list(missing_shadow)[:5]}"
    assert not leftover_tg, f"telegram nie usunął własnych: {list(leftover_tg)[:5]}"


def test_blind_concurrent_reproduces_shadow_loss(tmp_path):
    """MUTACJA (blind-save): ten sam scenariusz → część wpisów shadow ZGUBIONA.
    Dowód, że tylko delta jest race-free dla telegramu (cofnięcie fixa = regres)."""
    path = str(tmp_path / "pending_proposals.json")
    tg_keys = _seed_tg_keys(path, 60)
    m = 60
    with ProcessPoolExecutor(max_workers=2) as ex:
        f_tg = ex.submit(_tg_blind_worker, path, tg_keys, 0.001)
        f_sh = ex.submit(_shadow_upsert_worker, path, m, 0.001)
        f_tg.result()
        f_sh.result()

    final = store.load(path)
    missing_shadow = {f"S_{i}" for i in range(m)} - set(final.keys())
    assert missing_shadow, (
        "blind-save NIE odtworzył utraty wpisów shadow — okno za wąskie / środowisko "
        "zbyt sekwencyjne; zwiększ m/window. (Bez straty dowód konieczności delty pusty.)"
    )


# ---------- D. wrappery telegramu + drain: format i additive-reconcile ----------

def test_telegram_wrappers_roundtrip_and_readers(tmp_path):
    """`set_pending`/`pop_pending` telegramu → format niezmieniony, czytelnicy wsteczni 1:1."""
    from dispatch_v2 import telegram_approver as tg
    path = str(tmp_path / "pending_proposals.json")
    store.save({}, path)

    tg.set_pending(path, "485071", {
        "order_id": "485071", "message_id": 7, "sent_at": "2026-07-02T18:00:00+00:00",
        "expires_at": "2026-07-02T18:30:00+00:00",
        "decision_record": {"order_id": "485071", "ą": "ł"},
    })
    raw = open(path, encoding="utf-8").read()
    assert '\n  "485071"' in raw, "indent=2 utracony przez set_pending"
    assert '"ą": "ł"' in raw, "ensure_ascii=False utracone przez set_pending"

    from dispatch_v2 import postpone_sweeper as ps
    loaded = store.load(path)
    assert tg.load_pending(path) == loaded
    assert ps._load_json_safe(path, {}) == loaded

    tg.pop_pending(path, "485071")
    assert store.load(path) == {}
    # idempotencja: drugi pop nieistniejącego klucza = no-op (nie rzuca)
    tg.pop_pending(path, "485071")
    assert store.load(path) == {}


def test_drain_merge_missing_is_additive(tmp_path):
    """Drain (`locked_merge_missing`): dołóż brakujące własne, NIGDY nie kasuj cudzych
    ani nie wskrzeszaj popów. `entries` = pamięć telegramu (już bez popniętych)."""
    path = str(tmp_path / "pending_proposals.json")
    # na dysku: wpis shadow (S1) + wpis telegramu (T2) już zapisany deltą
    store.save({"S1": _tg_entry("S1", None), "T2": _tg_entry("T2", 22)}, path)
    # pamięć telegramu: T2 (żywy) + T3 (dołożony, jeszcze nie na dysku); T1 popnięty → NIE ma go
    tg_mem = {"T2": _tg_entry("T2", 22), "T3": _tg_entry("T3", 33)}

    store.locked_merge_missing(tg_mem, path)

    final = store.load(path)
    assert "S1" in final, "drain skasował cudzy wpis shadow (blind zamiast additive)"
    assert "T3" in final, "drain nie dołożył brakującego wpisu telegramu"
    assert "T2" in final
    assert "T1" not in final, "drain WSKRZESIŁ popnięty wpis (additive nie może dodać spoza entries)"
