"""Fix recordera (2026-07-18, GO Adriana; finding fali #7 klasy #15/C10):
`world_record` snapshotuje `courier_last_pos.json` (filtr do floty, wzór `plans`),
`world_replay._serve_live_inputs` przekierowuje loader na nagrany snapshot —
koniec dziennego dryfu replayu na kandydatach no_gps (store TTL 25 min; nocny
„PARITY" bywał parity-bo-noc).

Pinuje: (1) capture zapisuje zawartość store'a przefiltrowaną do floty;
(2) fail-soft przy braku store'a (pole nieobecne = rekord legacy-zgodny);
(3) redirect w replayu → loader czyta NAGRANE, nie żywe; (4) rekord legacy
(brak pola) → ścieżka nietknięta (passthrough do żywego, jak przed fixem).
"""
import json
import types

from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import world_record as WR
from dispatch_v2.tools import world_replay as WRP

_SAMPLE = {"457": {"lat": 53.12, "lon": 23.15, "ts": "2026-07-18T10:00:00+00:00",
                   "source": "last_delivered"},
           "999": {"lat": 1.0, "lon": 1.0, "ts": "2026-07-18T10:01:00+00:00",
                   "source": "last_delivered"}}


def test_capture_includes_fleet_filtered_store(tmp_path, monkeypatch):
    p = tmp_path / "last_pos.json"
    p.write_text(json.dumps(_SAMPLE), encoding="utf-8")
    monkeypatch.setattr(CR, "COURIER_LAST_POS_PATH", str(p))
    out = {}
    WR._capture_courier_last_pos(out, fleet_cids={"457"})
    assert out["courier_last_pos"] == {"457": _SAMPLE["457"]}  # 999 poza flotą


def test_capture_fail_soft_missing_store(tmp_path, monkeypatch):
    monkeypatch.setattr(CR, "COURIER_LAST_POS_PATH", str(tmp_path / "nie_ma.json"))
    out = {}
    WR._capture_courier_last_pos(out, fleet_cids={"457"})
    assert "courier_last_pos" not in out  # legacy-zgodny rekord


def _serve(li, tmp_path):
    rec = {"live_inputs": li}
    patches = []

    def _patch(mod, attr, val):
        patches.append((mod, attr, getattr(mod, attr)))
        setattr(mod, attr, val)
    WRP._serve_live_inputs(rec, dp=None, C=types.SimpleNamespace(), tmpdir=str(tmp_path),
                           _patch=_patch)
    return patches


def test_replay_redirect_reads_recorded_not_live(tmp_path, monkeypatch):
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"999": {"lat": 1.0, "lon": 1.0}}), encoding="utf-8")
    monkeypatch.setattr(CR, "COURIER_LAST_POS_PATH", str(live))
    recorded = {"457": _SAMPLE["457"]}
    _serve({"courier_last_pos": recorded}, tmp_path)
    assert CR.COURIER_LAST_POS_PATH != str(live)  # przekierowane na tmp-snapshot
    assert CR._load_last_known_pos() == recorded  # nagrane, nie „żywe"


def test_replay_legacy_record_passthrough(tmp_path, monkeypatch):
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"457": _SAMPLE["457"]}), encoding="utf-8")
    monkeypatch.setattr(CR, "COURIER_LAST_POS_PATH", str(live))
    _serve({}, tmp_path)  # legacy: brak pola
    assert CR.COURIER_LAST_POS_PATH == str(live)  # nietknięte
    assert CR._load_last_known_pos() == {"457": _SAMPLE["457"]}
