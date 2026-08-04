"""Bramka na incydent rotacji 2026-08-04 05:22Z — cicha utrata surowej rotacji.

CO SIĘ STAŁO: `decision_eta_log.jsonl.1` (407,3 MB, NIESKOMPRESOWANE) zniknęło
w jednym biegu logrotate. W logu biegu NIE MA ani jednej linii `removing` — bo
plik nie został skasowany, tylko NADPISANY przez `rename(live, <log>.1)`.

MECHANIZM (zreprodukowany w 3 wariantach, `/root/artifacts/zniwa-od7-20260803/incydent-rotacja/`):
przy `compress` BEZ `delaycompress` logrotate zakłada, że każda już zrotowana
generacja jest skompresowana, więc w pętli przesuwającej szuka wyłącznie
`<log>.<N>.gz`. Surowe `<log>.<N>` jest dla niej niewidoczne — nie zostaje
przesunięte do `.<N+1>`, a slot `.1` jest zaraz potem nadpisany.

Surowe `<log>.1` to NORMALNY stan pod `delaycompress`. Dlatego samo zdjęcie
`delaycompress` z configu, przy zaległym surowym `.1`, zamienia następny bieg
w cichą utratę danych — i dokładnie to zaszło.

Testy są hermetyczne: własny tmp_path, zero żywych ścieżek, zero uruchamiania
prawdziwego logrotate.
"""
import pytest

from dispatch_v2.core import jsonl_rotation as jr


def _conf(tmp_path, logfile, *, delaycompress: bool) -> str:
    linie = [str(logfile), "{", "    daily", "    rotate 30", "    maxage 30",
             "    maxsize 100M", "    compress"]
    if delaycompress:
        linie.append("    delaycompress")
    linie += ["    missingok", "    notifempty", "    su root root",
              "    create 0644 root root", "    sharedscripts", "}"]
    conf = tmp_path / "logrotate.conf"
    conf.write_text("\n".join(linie) + "\n", encoding="utf-8")
    return str(conf)


# ---------- negatywny oracle: reprodukcja incydentu ----------

def test_surowa_rotacja_pod_compress_blokuje_bieg(tmp_path):
    """NEGATYWNY ORACLE: dokładny układ z 04.08 — bieg MUSI zostać odmówiony."""
    log = tmp_path / "decision_eta_log.jsonl"
    log.write_text('{"live":1}\n', encoding="utf-8")
    stara = tmp_path / "decision_eta_log.jsonl.1"
    stara.write_text('{"stare":1}\n' * 500, encoding="utf-8")

    conf = _conf(tmp_path, log, delaycompress=False)

    with pytest.raises(jr.RawRotationWouldBeClobberedError) as exc:
        jr.run_logrotate(conf, paths=(log,))

    komunikat = str(exc.value)
    assert "decision_eta_log.jsonl.1" in komunikat
    assert "gzip" in komunikat, "komunikat musi podawać komendę naprawczą"
    assert stara.exists(), "guard nie może niczego ruszać"


def test_guard_nie_wola_logrotate_gdy_odmawia(tmp_path, monkeypatch):
    """Odmowa musi nastąpić PRZED uruchomieniem logrotate."""
    log = tmp_path / "a.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    (tmp_path / "a.jsonl.1").write_text("{}\n", encoding="utf-8")
    wywolania = []
    monkeypatch.setattr(jr.subprocess, "run",
                        lambda *a, **k: wywolania.append(a))

    with pytest.raises(jr.RawRotationWouldBeClobberedError):
        jr.run_logrotate(_conf(tmp_path, log, delaycompress=False), paths=(log,))

    assert wywolania == []


# ---------- warianty kontrolne z reprodukcji ----------

def test_delaycompress_nie_jest_blokowany(tmp_path, monkeypatch):
    """WARIANT B repro: pod delaycompress surowe .1 jest normalne i bezpieczne.

    Gdyby guard blokował także ten układ, zatrzymałby rotację 10 plikom
    (~755 MB), ktore dzis legalnie leza surowe pod GRUPA A/B/B-2.
    """
    log = tmp_path / "b.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    (tmp_path / "b.jsonl.1").write_text("{}\n", encoding="utf-8")

    class Completed:
        returncode = 0

    monkeypatch.setattr(jr.subprocess, "run", lambda *a, **k: Completed())
    assert jr.run_logrotate(_conf(tmp_path, log, delaycompress=True), paths=(log,)) == 0


def test_juz_skompresowana_rotacja_przechodzi(tmp_path, monkeypatch):
    """WARIANT C repro: po `gzip` przesunięcie .1.gz -> .2.gz działa poprawnie."""
    log = tmp_path / "c.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    (tmp_path / "c.jsonl.1.gz").write_bytes(b"\x1f\x8b\x08\x00")

    class Completed:
        returncode = 0

    monkeypatch.setattr(jr.subprocess, "run", lambda *a, **k: Completed())
    assert jr.run_logrotate(_conf(tmp_path, log, delaycompress=False), paths=(log,)) == 0


def test_brak_zaleglych_rotacji_przechodzi(tmp_path, monkeypatch):
    log = tmp_path / "d.jsonl"
    log.write_text("{}\n", encoding="utf-8")

    class Completed:
        returncode = 0

    monkeypatch.setattr(jr.subprocess, "run", lambda *a, **k: Completed())
    assert jr.run_logrotate(_conf(tmp_path, log, delaycompress=False), paths=(log,)) == 0


# ---------- parser configu ----------

def test_parser_widzi_wiele_sciezek_w_bloku(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    for p in (a, b):
        p.write_text("{}\n", encoding="utf-8")
    (tmp_path / "b.jsonl.1").write_text("{}\n", encoding="utf-8")
    conf = tmp_path / "wiele.conf"
    conf.write_text(f"{a}\n{b}\n{{\n    compress\n    rotate 30\n}}\n", encoding="utf-8")

    zagrozone = [p.name for p in jr.raw_rotations_at_risk(str(conf))]
    assert zagrozone == ["b.jsonl.1"]


def test_komentarze_nie_wlaczaja_dyrektyw(tmp_path):
    """`# delaycompress` w komentarzu NIE może wyłączyć ochrony."""
    log = tmp_path / "e.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    (tmp_path / "e.jsonl.1").write_text("{}\n", encoding="utf-8")
    conf = tmp_path / "komentarz.conf"
    conf.write_text(
        f"# delaycompress zdjety w OD-7 krok 1\n{log}\n{{\n    compress\n}}\n",
        encoding="utf-8",
    )
    assert [p.name for p in jr.raw_rotations_at_risk(str(conf))] == ["e.jsonl.1"]


def test_skompresowane_rozszerzenia_nie_sa_zagrozeniem(tmp_path):
    log = tmp_path / "f.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    for ext in (".gz", ".bz2", ".xz", ".zst"):
        (tmp_path / f"f.jsonl.1{ext}").write_bytes(b"x")
    assert jr.raw_rotations_at_risk(_conf(tmp_path, log, delaycompress=False)) == []


def test_nieczytelny_config_nie_wysypuje_wrappera(tmp_path):
    """Brakującym configiem zajmuje się logrotate; guard nie dubluje walidacji."""
    assert jr.raw_rotations_at_risk(str(tmp_path / "nie-ma.conf")) == []
