"""Tests for new-courier auto-pairing (2026-06-06).

Covers panel_roster parsing + matching, new_courier_pairing.scan_once gating
(incl. the "Albert Dec None-entry" trap and the "Bartosz Ch." period-abbrev
match), verify_courier_wired, and the /nowy Telegram handler.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import panel_roster as pr
from dispatch_v2 import new_courier_pairing as ncp


# --------------------------------------------------------------------------- #
# Fixture HTML mirroring real /admin2017/list-users structure
# --------------------------------------------------------------------------- #

def _li(name, cid, role="kurier", active=True):
    btn = "btn-success" if active else "btn-danger"
    return (
        "<li class='list-group-item li_hover_user_list'>\n"
        f"    {name}\n"
        "    <span class='btn-group pull-right'>\n"
        f'        <a onclick="activeKurier({cid}, this)" class="btn btn-xs  {btn} ">active</a>\n'
        f"        <a href='https://www.gastro.nadajesz.pl/admin2017/edit-user/{cid}' class=\"btn btn-primary btn-xs\">Edit</a>\n"
        f'        <a onclick="removeUser({cid}, this)" class="delete-user-link btn btn-danger btn-xs">Delete</a>\n'
        f'        <span class="typ_user">{role}</span>\n'
        "    </span>\n"
        "</li>\n"
    )


LIST_USERS_FIXTURE = (
    "<ul>\n"
    + _li("Bartek O,", 123, "kurier", active=True)
    + _li("Bartosz Ch.", 530, "kurier", active=True)
    + _li("Bartosz K", 526, "kurier", active=True)
    + _li("Piotr Wr", 527, "kurier", active=True)
    + _li("Piotr Kul", 531, "kurier", active=True)
    + _li("Michał Tok", 163, "kurier", active=False)     # inactive
    + _li("Bar Eljot", 27, "restauracja", active=True)   # not a courier
    + "</ul>\n"
)


# --------------------------------------------------------------------------- #
# parse_list_users
# --------------------------------------------------------------------------- #

def test_parse_list_users_filters_role_and_active():
    rows = pr.parse_list_users(LIST_USERS_FIXTURE)
    by_cid = {cid: (name, act) for cid, name, act in rows}
    assert 27 not in by_cid                  # restauracja excluded
    assert by_cid[123] == ("Bartek O", True)
    assert by_cid[530] == ("Bartosz Ch.", True)
    assert by_cid[163][1] is False           # inactive flag detected


def test_parse_list_users_active_subset():
    rows = pr.parse_list_users(LIST_USERS_FIXTURE)
    active = {cid: name for cid, name, act in rows if act}
    assert set(active) == {123, 530, 526, 527, 531}
    assert 163 not in active


# --------------------------------------------------------------------------- #
# match_name_to_cid
# --------------------------------------------------------------------------- #

@pytest.fixture
def roster():
    rows = pr.parse_list_users(LIST_USERS_FIXTURE)
    return {cid: name for cid, name, act in rows if act}


def test_match_period_abbrev(roster):
    # "Bartosz Ch." in roster must match full "Bartosz Choiński" (period stripped)
    m = pr.match_name_to_cid("Bartosz Choiński", roster)
    assert m.status == "matched" and m.cid == 530


def test_match_disambiguates_two_piotr(roster):
    assert pr.match_name_to_cid("Piotr Wrona", roster).cid == 527
    assert pr.match_name_to_cid("Piotr Kulaszewski", roster).cid == 531


def test_match_disambiguates_two_bartosz(roster):
    assert pr.match_name_to_cid("Bartosz Klejna", roster).cid == 526
    assert pr.match_name_to_cid("Bartosz Choiński", roster).cid == 530


def test_match_disambiguates_abbrev_collision():
    # Rafał Jankowski (gastro "Rafał Jan", 529) vs Rafał Jabłoński (gastro "Rafał J", 101):
    # longer matched abbrev wins -> no false tie.
    r = {101: "Rafał J", 529: "Rafał Jan"}
    assert pr.match_name_to_cid("Rafał Jankowski", r).cid == 529
    assert pr.match_name_to_cid("Rafał Jabłoński", r).cid == 101


def test_match_exact_known(roster):
    m = pr.match_name_to_cid("Bartek Ołdziej", roster)
    assert m.status == "matched" and m.cid == 123


def test_match_none_when_absent(roster):
    assert pr.match_name_to_cid("Jan Kowalski", roster).status == "none"


def test_match_ambiguous_tie():
    # Two couriers, same first name, both bare-first OR equal-prefix -> tie
    r = {800: "Marek", 801: "Marek"}
    m = pr.match_name_to_cid("Marek Nowak", r)
    assert m.status == "ambiguous"


def test_match_empty_roster():
    assert pr.match_name_to_cid("Jan Kowalski", {}).status == "none"


# --------------------------------------------------------------------------- #
# scan_once gating
# --------------------------------------------------------------------------- #

@pytest.fixture
def patched_scan(monkeypatch, tmp_path):
    """Isolate scan_once: temp state, fake schedule/roster, capture writes+sends."""
    state_file = tmp_path / "ncp_state.json"
    monkeypatch.setattr(ncp, "STATE_PATH", str(state_file))
    # Anty-prod (C17): self-heal + trusted-resolve nie mogą dotykać żywych plików.
    monkeypatch.setattr(ncp, "GRAFIK_FULL_NAMES", str(tmp_path / "grafik_full_names.json"))
    monkeypatch.setattr(ncp, "_load_kurier_ids",
                        lambda: {"Bartek O": "123", "Bartek Ołdziej": "123"})
    assert "/dispatch_state/" not in ncp.STATE_PATH
    assert "/dispatch_state/" not in ncp.GRAFIK_FULL_NAMES

    sent = []
    monkeypatch.setattr(ncp, "_tg", lambda text, *, silent=False: sent.append(text))

    added = []

    def fake_add(cid, full_name):
        added.append((cid, full_name))
        return {"cid": cid, "full_name": full_name,
                "alias": full_name.split()[0] + " " + full_name.split()[1][:2],
                "pin": "4242"}

    monkeypatch.setattr(ncp, "add_new_courier", fake_add)
    monkeypatch.setattr(ncp, "verify_courier_wired",
                        lambda cid, name: (True, ["✓ ok"]))

    # roster from fixture (active only)
    rows = pr.parse_list_users(LIST_USERS_FIXTURE)
    active = {cid: name for cid, name, act in rows if act}
    monkeypatch.setattr(ncp.panel_roster, "fetch_active_roster", lambda force=False: active)

    flags = {"NEW_COURIER_AUTOPAIR_AUTOWRITE": True}
    monkeypatch.setattr(ncp, "flag", lambda name, default=False: flags.get(name, default))

    # Isolate from the real shift_ignored_names.json (tests opt-in per case)
    monkeypatch.setattr(ncp, "_load_ignored_names", lambda: set())

    # resolve_cid: only "Bartek Ołdziej" already mapped
    monkeypatch.setattr(ncp, "resolve_cid",
                        lambda name, kids=None: "123" if name == "Bartek Ołdziej" else None)

    return {"sent": sent, "added": added, "flags": flags, "state_file": state_file}


def _sched(monkeypatch, mapping):
    monkeypatch.setattr(ncp, "load_schedule", lambda: mapping)


def test_scan_autowire_confident(patched_scan, monkeypatch):
    _sched(monkeypatch, {"Bartosz Choiński": {"start": "09:00", "end": "19:00"}})
    s = ncp.scan_once(dry_run=False)
    assert patched_scan["added"] == [(530, "Bartosz Choiński")]
    assert len(s["paired"]) == 1 and s["paired"][0]["cid"] == 530
    assert any("PIN" in t and "4242" in t for t in patched_scan["sent"])


def test_scan_skips_none_entry_albert_dec(patched_scan, monkeypatch):
    """The None-entry trap: 'Albert Dec' sits in the sheet as None -> NEVER wired."""
    _sched(monkeypatch, {
        "Albert Dec": None,                                   # placeholder, not working
        "Bartosz Choiński": {"start": "09:00", "end": "19:00"},
    })
    ncp.scan_once(dry_run=False)
    wired = [n for _, n in patched_scan["added"]]
    assert "Albert Dec" not in wired
    assert "Bartosz Choiński" in wired


def test_scan_skips_already_mapped(patched_scan, monkeypatch):
    _sched(monkeypatch, {"Bartek Ołdziej": {"start": "09:00", "end": "24:00"}})
    s = ncp.scan_once(dry_run=False)
    assert patched_scan["added"] == []
    assert s["paired"] == [] and s["asked"] == []


def test_scan_idempotent(patched_scan, monkeypatch):
    _sched(monkeypatch, {"Bartosz Choiński": {"start": "09:00", "end": "19:00"}})
    ncp.scan_once(dry_run=False)
    ncp.scan_once(dry_run=False)   # second pass: already paired today
    assert patched_scan["added"] == [(530, "Bartosz Choiński")]  # only once


def test_scan_no_match_asks(patched_scan, monkeypatch):
    _sched(monkeypatch, {"Daniel Malicki": {"start": "10:00", "end": "18:00"}})
    s = ncp.scan_once(dry_run=False)
    assert patched_scan["added"] == []
    assert len(s["asked"]) == 1 and s["asked"][0]["status"] == "none"
    assert any("Nowy w grafiku" in t for t in patched_scan["sent"])


def test_scan_autowrite_off_asks_even_when_matched(patched_scan, monkeypatch):
    patched_scan["flags"]["NEW_COURIER_AUTOPAIR_AUTOWRITE"] = False
    _sched(monkeypatch, {"Bartosz Choiński": {"start": "09:00", "end": "19:00"}})
    s = ncp.scan_once(dry_run=False)
    assert patched_scan["added"] == []
    assert len(s["asked"]) == 1
    assert any("530" in t for t in patched_scan["sent"])  # tells Adrian the cid


def test_scan_ignored_name_skipped(patched_scan, monkeypatch):
    """Retired/duplicate accounts on the skiplist are never paired even with a shift."""
    monkeypatch.setattr(ncp, "_load_ignored_names", lambda: {"Albert Dec"})
    _sched(monkeypatch, {"Albert Dec": {"start": "09:00", "end": "17:00"}})
    s = ncp.scan_once(dry_run=False)
    assert patched_scan["added"] == []
    assert s["asked"] == [] and s["paired"] == []


def test_scan_garbage_name_skipped(patched_scan, monkeypatch):
    _sched(monkeypatch, {"Opony, odpisac na maila": {"start": "09:00", "end": "12:00"}})
    s = ncp.scan_once(dry_run=False)
    assert patched_scan["added"] == [] and s["asked"] == []


def test_scan_dry_run_no_side_effects(patched_scan, monkeypatch):
    _sched(monkeypatch, {"Bartosz Choiński": {"start": "09:00", "end": "19:00"}})
    ncp.scan_once(dry_run=True)
    assert patched_scan["added"] == []
    assert patched_scan["sent"] == []
    assert not patched_scan["state_file"].exists()


# --------------------------------------------------------------------------- #
# Bare-key strict gate (2026-07-06 — oracle: realny case Gabriel Przyborowski 541
# cicho zduszony + zatruty przez goły klucz 'Gabriel'->179 w kurier_ids)
# --------------------------------------------------------------------------- #

GABRIEL_KIDS = {
    "Gabriel": "179",             # goły klucz-imię (Ostapczuk, label planszy gastro)
    "Gabriel Ostapczuk": "179",
    "Gabriel J": "503",
    "Adrian Cit": "457",
}


@pytest.fixture
def bare_key_scan(patched_scan, monkeypatch):
    """patched_scan z REALNYM resolve_cid + kontrolowanym kurier_ids (case 541)."""
    from dispatch_v2.shift_notifications import worker as snw
    monkeypatch.setattr(ncp, "resolve_cid", snw.resolve_cid)
    monkeypatch.setattr(ncp, "_load_kurier_ids", lambda: dict(GABRIEL_KIDS))
    # Ścieżka plain-resolve (resolve_cid bez kids) ładuje przez loader WORKERA —
    # patch też tam, żeby test nie czytał żywego kurier_ids.json (C17 anty-prod).
    monkeypatch.setattr(snw, "_load_kurier_ids", lambda: dict(GABRIEL_KIDS))
    # Aktywny roster gastro jak 06.07: nowy 541 'Gabriel P' + stary 179 'Gabriel'.
    monkeypatch.setattr(ncp.panel_roster, "fetch_active_roster",
                        lambda force=False: {541: "Gabriel P", 179: "Gabriel"})
    return patched_scan


def test_bare_key_strict_pairs_new_courier(bare_key_scan, monkeypatch):
    """ORACLE 06.07: strict ON (default) — Przyborowski NIE jest 'już zmapowany'
    przez 'Gabriel'->179, idzie do rosteru i zostaje wpięty jako 541."""
    _sched(monkeypatch, {"Gabriel Przyborowski": {"start": "11:00", "end": "21:00"}})
    s = ncp.scan_once(dry_run=False)
    assert bare_key_scan["added"] == [(541, "Gabriel Przyborowski")]
    assert len(s["paired"]) == 1 and s["paired"][0]["cid"] == 541


def test_bare_key_strict_off_reproduces_silent_skip(bare_key_scan, monkeypatch):
    """Flaga OFF = stare zachowanie (ON≠OFF): cichy skip (0 wpięć, 0 pytań)
    + self-heal utrwala ZATRUCIE Przyborowski->179 w grafik_full_names."""
    bare_key_scan["flags"]["NEW_COURIER_AUTOPAIR_BARE_KEY_STRICT"] = False
    _sched(monkeypatch, {"Gabriel Przyborowski": {"start": "11:00", "end": "21:00"}})
    s = ncp.scan_once(dry_run=False)
    assert bare_key_scan["added"] == []
    assert s["paired"] == [] and s["asked"] == []
    poisoned = json.load(open(ncp.GRAFIK_FULL_NAMES))
    assert str(poisoned["Gabriel Przyborowski"]) == "179"   # udokumentowany bug


def test_bare_key_strict_no_poison_selfheal(bare_key_scan, monkeypatch):
    """Strict ON: po wpięciu 541 self-heal pisze PRAWDZIWE mapowanie, nie 179."""
    _sched(monkeypatch, {"Gabriel Przyborowski": {"start": "11:00", "end": "21:00"}})
    ncp.scan_once(dry_run=False)
    healed = json.load(open(ncp.GRAFIK_FULL_NAMES))
    assert str(healed["Gabriel Przyborowski"]) == "541"


def test_bare_key_strict_exact_fullname_still_mapped(bare_key_scan, monkeypatch):
    """Exact-klucz pełnego imienia przechodzi bez zmian (Ostapczuk nietknięty)."""
    _sched(monkeypatch, {"Gabriel Ostapczuk": {"start": "09:00", "end": "17:00"}})
    s = ncp.scan_once(dry_run=False)
    assert bare_key_scan["added"] == []
    assert s["paired"] == [] and s["asked"] == []


def test_bare_key_strict_surname_alias_still_mapped(bare_key_scan, monkeypatch):
    """Alias nazwiskowy ('Adrian Cit'->'Adrian Citko') dalej liczy się jako zmapowany."""
    _sched(monkeypatch, {"Adrian Citko": {"start": "09:00", "end": "17:00"}})
    s = ncp.scan_once(dry_run=False)
    assert bare_key_scan["added"] == []
    assert s["paired"] == [] and s["asked"] == []


def test_bare_key_single_word_name_uses_plain_resolve(bare_key_scan, monkeypatch):
    """Jednowyrazowe imię w grafiku — goły klucz to jedyne sensowne mapowanie,
    strict go NIE odcina (fail-open do zwykłego resolve)."""
    _sched(monkeypatch, {"Gabriel": {"start": "09:00", "end": "17:00"}})
    s = ncp.scan_once(dry_run=False)
    assert bare_key_scan["added"] == []
    assert s["paired"] == [] and s["asked"] == []


# --------------------------------------------------------------------------- #
# verify_courier_wired
# --------------------------------------------------------------------------- #

def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def test_verify_courier_wired_ok(monkeypatch, tmp_path):
    cid, name, alias = 9001, "Jan Kowalski", "Jan Ko"
    kids = tmp_path / "ids.json"; piny = tmp_path / "piny.json"
    tiers = tmp_path / "tiers.json"; full = tmp_path / "full.json"
    _write(kids, {alias: cid, name: cid})
    _write(piny, {"1234": alias})
    _write(tiers, {str(cid): {"name": alias}})
    _write(full, {alias: name})
    monkeypatch.setattr(ncp, "KURIER_IDS", str(kids))
    monkeypatch.setattr(ncp, "KURIER_PINY", str(piny))
    monkeypatch.setattr(ncp, "COURIER_TIERS", str(tiers))
    monkeypatch.setattr(ncp, "KURIER_FULL_NAMES", str(full))
    monkeypatch.setattr(ncp, "resolve_cid",
                        lambda n, kids=None: str(cid) if n == name else None)
    ok, lines = ncp.verify_courier_wired(cid, name)
    assert ok is True
    assert all(line.startswith("✓") for line in lines)


def test_verify_courier_wired_detects_missing_cod(monkeypatch, tmp_path):
    cid, name, alias = 9002, "Anna Nowak", "Anna No"
    kids = tmp_path / "ids.json"; piny = tmp_path / "piny.json"
    tiers = tmp_path / "tiers.json"; full = tmp_path / "full.json"
    _write(kids, {alias: cid, name: cid})
    _write(piny, {"1234": alias})
    _write(tiers, {str(cid): {"name": alias}})
    _write(full, {})  # MISSING from COD full-names map
    monkeypatch.setattr(ncp, "KURIER_IDS", str(kids))
    monkeypatch.setattr(ncp, "KURIER_PINY", str(piny))
    monkeypatch.setattr(ncp, "COURIER_TIERS", str(tiers))
    monkeypatch.setattr(ncp, "KURIER_FULL_NAMES", str(full))
    monkeypatch.setattr(ncp, "resolve_cid",
                        lambda n, kids=None: str(cid) if n == name else None)
    ok, lines = ncp.verify_courier_wired(cid, name)
    assert ok is False
    assert any(line.startswith("✗") and "COD" in line for line in lines)


# --------------------------------------------------------------------------- #
# /nowy Telegram handler
# --------------------------------------------------------------------------- #

@pytest.fixture
def patched_nowy(monkeypatch):
    import dispatch_v2.telegram_approver as ta
    monkeypatch.setattr(ta, "_authorized_user_ids", lambda: {111})
    added = []

    def fake_add(cid, full_name):
        added.append((cid, full_name))
        return {"cid": cid, "full_name": full_name, "alias": "X Yz", "pin": "7777"}

    monkeypatch.setattr("dispatch_v2.courier_admin.add_new_courier", fake_add)
    monkeypatch.setattr("dispatch_v2.new_courier_pairing.verify_courier_wired",
                        lambda cid, name: (True, ["✓ ok"]))
    rows = pr.parse_list_users(LIST_USERS_FIXTURE)
    active = {cid: name for cid, name, act in rows if act}
    monkeypatch.setattr("dispatch_v2.panel_roster.fetch_active_roster",
                        lambda force=False: active)
    return {"ta": ta, "added": added}


def _msg(text, uid=111):
    return {"from": {"id": uid}, "chat": {"id": -1}, "message_id": 1, "text": text}


def test_nowy_unauthorized(patched_nowy):
    ta = patched_nowy["ta"]
    r = ta._handle_nowy_command({}, _msg("/nowy 530 Bartosz Choiński", uid=999),
                                "/nowy 530 Bartosz Choiński")
    assert "unauthorized" in r


def test_nowy_with_cid_ok(patched_nowy):
    ta = patched_nowy["ta"]
    r = ta._handle_nowy_command({}, _msg("/nowy 530 Bartosz Choiński"),
                                "/nowy 530 Bartosz Choiński")
    assert patched_nowy["added"] == [(530, "Bartosz Choiński")]
    assert "7777" in r and "Wpięty" in r


def test_nowy_cid_mismatch_refused(patched_nowy):
    ta = patched_nowy["ta"]
    # cid 530 is "Bartosz Ch." but we claim "Janusz Tracz" -> different first name
    r = ta._handle_nowy_command({}, _msg("/nowy 530 Janusz Tracz"),
                                "/nowy 530 Janusz Tracz")
    assert patched_nowy["added"] == []
    assert "literówk" in r.lower() or "gastro to" in r


def test_nowy_without_cid_autoresolve(patched_nowy):
    ta = patched_nowy["ta"]
    r = ta._handle_nowy_command({}, _msg("/nowy Piotr Kulaszewski"),
                                "/nowy Piotr Kulaszewski")
    assert patched_nowy["added"] == [(531, "Piotr Kulaszewski")]
    assert "7777" in r


def test_nowy_without_cid_none(patched_nowy):
    ta = patched_nowy["ta"]
    r = ta._handle_nowy_command({}, _msg("/nowy Jan Kowalski"), "/nowy Jan Kowalski")
    assert patched_nowy["added"] == []
    assert "Nie znajduję" in r


def test_nowy_without_cid_ambiguous(patched_nowy, monkeypatch):
    ta = patched_nowy["ta"]
    monkeypatch.setattr("dispatch_v2.panel_roster.fetch_active_roster",
                        lambda force=False: {800: "Marek", 801: "Marek"})
    r = ta._handle_nowy_command({}, _msg("/nowy Marek Nowak"), "/nowy Marek Nowak")
    assert patched_nowy["added"] == []
    assert "Kilku pasuje" in r
