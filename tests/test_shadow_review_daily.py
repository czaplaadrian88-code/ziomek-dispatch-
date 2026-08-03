"""Bramka testowa dobowego czytelnika cienia (tools/shadow_review_daily.py).

Kontekst (ŻNIWA 2026-08-03): siedem timerów-czytelników było JEDNORAZOWYCH i wygasło
26.06–06.07, a pisarze chodzą co 1-5 min. Zastępuje je jeden job dobowy. Dwa defekty,
które ten job MUSI wykluczać strukturalnie, bo oba już raz wyprodukowały fałszywy werdykt:

  * werdykt z MARTWEGO korpusu — `bundle_calib_review` ma default na korpus λ=1.5
    zamrożony 02.07, podczas gdy drop-in z 03.07 przepiął PISARZA na korpus λ=0 (L0);
  * odczyt cienia wołający Telegram bez ACK ownera.

Testy są hermetyczne: zero systemd, zero żywego stanu, zero sieci.
"""
import importlib.util
import os
import time

import pytest

TOOL = os.path.join(os.path.dirname(__file__), "..", "tools", "shadow_review_daily.py")


def _load():
    spec = importlib.util.spec_from_file_location("shadow_review_daily", os.path.abspath(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


# ---------- korpus: fail-closed ----------

def test_korpus_swiezy_przechodzi(tmp_path):
    p = tmp_path / "shadow.jsonl"
    p.write_text('{"a":1}\n')
    ok, why, meta = M.corpus_health(str(p), 48.0)
    assert ok is True and why == ""
    assert meta["age_hours"] < 1


def test_korpus_martwy_blokuje_werdykt(tmp_path):
    """NEGATYWNY ORACLE: korpus sprzed 40 dni NIE może dać werdyktu.

    To reprodukcja defektu z żniw — czytelnik b_route wydał `MIXED` na danych,
    których realny join zwrócił zero wierszy, a nikt nie zauważył.
    """
    p = tmp_path / "shadow.jsonl"
    p.write_text('{"a":1}\n')
    old = time.time() - 40 * 86400
    os.utime(p, (old, old))
    ok, why, meta = M.corpus_health(str(p), 48.0)
    assert ok is False
    assert "MARTWY" in why
    assert meta["age_hours"] > 900


def test_korpus_pusty_blokuje_werdykt(tmp_path):
    p = tmp_path / "shadow.jsonl"
    p.write_text("")
    ok, why, _ = M.corpus_health(str(p), 48.0)
    assert ok is False and "PUSTY" in why


def test_korpus_nieistniejacy_blokuje_werdykt(tmp_path):
    ok, why, _ = M.corpus_health(str(tmp_path / "nie-ma.jsonl"), 48.0)
    assert ok is False and "nie istnieje" in why


# ---------- ścieżka korpusu: jeden właściciel = konfiguracja pisarza ----------

UNIT_Z_DROPINEM = """# /etc/systemd/system/dispatch-bundle-calib-shadow.service
[Service]
Environment=BUNDLE_CALIB_LAMBDA_CZAS=1.5
Environment=BUNDLE_CALIB_OUT_JSONL=/var/state/bundle_calib_shadow.jsonl

# /etc/systemd/system/dispatch-bundle-calib-shadow.service.d/lambda0.conf
[Service]
Environment=BUNDLE_CALIB_LAMBDA_CZAS=0
Environment=BUNDLE_CALIB_OUT_JSONL=/var/state/bundle_calib_shadow_l0.jsonl
"""


def test_dropin_wygrywa_nad_baza_unitu():
    """Drop-in przepina pisarza na korpus L0 — czytelnik MUSI iść za nim, nie za bazą."""
    got = M.parse_env_from_unit_text(UNIT_Z_DROPINEM, "BUNDLE_CALIB_OUT_JSONL")
    assert got == "/var/state/bundle_calib_shadow_l0.jsonl"


def test_brak_klucza_to_brak_sciezki_a_nie_default():
    assert M.parse_env_from_unit_text("[Service]\nExecStart=/bin/true\n",
                                      "BUNDLE_CALIB_OUT_JSONL") is None


def test_cudzyslowy_i_spacje_nie_lamia_parsera():
    txt = '[Service]\nEnvironment="BUNDLE_CALIB_OUT_JSONL=/var/state/x.jsonl"\n'
    assert M.parse_env_from_unit_text(txt, "BUNDLE_CALIB_OUT_JSONL") == "/var/state/x.jsonl"


def test_czytelnik_bez_ustalonego_korpusu_jest_BLOCKED(tmp_path, monkeypatch):
    """Nie da się ustalić korpusu → status BLOCKED i ZERO uruchomienia czytelnika."""
    monkeypatch.setattr(M, "resolve_corpus_from_unit", lambda u, k: (None, "brak klucza"))
    r = M.Reader(name="x", argv=["-c", "raise SystemExit(0)"], corpus=None,
                 corpus_from_unit=("jakis.service", "KLUCZ"))
    res = M.run_reader(r, str(tmp_path), 48.0, dry=False)
    assert res["status"] == "BLOCKED"
    assert res["exit_code"] is None  # czytelnik nie został w ogóle wywołany


# ---------- ratchet: żaden odczyt cienia nie wysyła Telegrama ----------

def test_ratchet_kazdy_czytelnik_ma_no_telegram():
    """Każdy wołany czytelnik albo ma --no-telegram, albo nie zna Telegrama w ogóle.

    Nowy czytelnik dopisany bez tego przełącznika czerwieni ten test — to jedyna
    bramka chroniąca przed dobowym spamem na Telegram bez ACK ownera.
    """
    tools_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
    for reader in M.READERS:
        if "--no-telegram" in reader.argv:
            continue
        mod = [a for a in reader.argv if a.startswith("dispatch_v2.tools.")]
        assert mod, f"{reader.name}: brak --no-telegram i nieznana ścieżka modułu {reader.argv}"
        path = os.path.join(tools_dir, mod[0].rsplit(".", 1)[-1] + ".py")
        src = open(path, encoding="utf-8").read()
        assert "send_admin_alert" not in src and "sendMessage" not in src, (
            f"{reader.name} umie wysłać Telegram, a jest wołany bez --no-telegram")


def test_ratchet_zaden_czytelnik_nie_pisze_do_zywego_stanu():
    """Każdy wołany czytelnik musi być bezpisemny wobec dispatch_state/ i scripts/logs/.

    Ten ratchet powstał, bo złapał realny defekt: `pickup_slip_monitor` był początkowo na
    liście czytelników, a dopisuje raport do dispatch_state/pickup_slip_monitor.jsonl
    (pickup_slip_monitor.py:175-181). Pod sandboxem systemd (ReadWritePaths tylko katalog
    raportów) job by się wywalił — ale dopiero PO instalacji, na produkcji.
    """
    tools_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
    otwarcie_do_zapisu = ('"w"', "'w'", '"a"', "'a'", '"wb"', '"ab"')
    for reader in M.READERS:
        mod = [a for a in reader.argv if a.startswith("dispatch_v2.tools.")]
        if not mod:
            continue
        path = os.path.join(tools_dir, mod[0].rsplit(".", 1)[-1] + ".py")
        for nr, line in enumerate(open(path, encoding="utf-8"), 1):
            if "open(" not in line or not any(m in line for m in otwarcie_do_zapisu):
                continue
            assert not any(d in line for d in M.LIVE_STATE_DIRS), (
                f"{reader.name} pisze do żywego stanu: {path}:{nr}: {line.strip()}")
        src = open(path, encoding="utf-8").read()
        # zapis przez stałą modułową (OUT = ".../dispatch_state/...") też się liczy
        for nr, line in enumerate(src.splitlines(), 1):
            if any(d in line for d in M.LIVE_STATE_DIRS) and "OUT" in line and "=" in line:
                nazwa = line.split("=", 1)[0].strip()
                assert f'open({nazwa}, "a")' not in src and f'open({nazwa}, "w")' not in src, (
                    f"{reader.name} pisze do żywego stanu przez {nazwa} ({path}:{nr})")


def test_wykluczeni_maja_powod():
    assert M.EXCLUDED, "lista świadomie nieuruchamianych nie może być pusta bez wyjaśnienia"
    for e in M.EXCLUDED:
        assert e.get("reason") and len(e["reason"]) > 30
        assert e.get("unit", "").endswith(".service")


# ---------- read-only ----------

def test_dry_run_nie_uruchamia_czytelnika(tmp_path):
    r = M.Reader(name="x", argv=["-c", "open('/tmp/NIE-WOLNO','w')"], corpus=None)
    res = M.run_reader(r, str(tmp_path), 48.0, dry=True)
    assert res["status"] == "DRY_RUN"
    assert res["exit_code"] is None
    assert not os.path.exists("/tmp/NIE-WOLNO")


def test_zrodlo_nie_zawiera_operacji_pisemnych_na_stanie():
    """Ratchet: narzędzie nie może zyskać ścieżki zapisu do żywego stanu."""
    src = open(os.path.abspath(TOOL), encoding="utf-8").read()
    for zakazane in ("os.remove", "os.unlink", "shutil.rmtree", "truncate("):
        assert zakazane not in src, f"czytelnik ma operację kasującą: {zakazane}"
    # jedyne otwarcie do zapisu to raporty w out_dir
    for line in src.splitlines():
        if 'open(' in line and ('"w"' in line or "'w'" in line or '"a"' in line):
            assert "log_path" in line or "out_dir" in line or "os.path.join(out_dir" in line, (
                f"zapis poza katalogiem raportów: {line.strip()}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
