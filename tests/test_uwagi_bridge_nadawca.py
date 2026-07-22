# -*- coding: utf-8 -*-
"""P0 bridge-NADAWCA: adres ODBIORU z verbose_uwagi mostu epaki (2026-07-21).

Fixture = REALNE uwagi z produkcji (watcher.log P3-edge rejects, 6 firm catch-all
rid=161). Kontrakt: NADAWCA = punkt odbioru; `oryg. adres` = doręczenie (NIE odbiór);
pickup_rules mostu ustawiają tylko CZAS. P0 wymaga HMAC v2; tekstowy marker v1
jest fixturem negatywnym. OFF = bajt-parytet z legacy.
"""
import logging
import json
import os
from types import SimpleNamespace

import pytest

from dispatch_v2.uwagi_address_parser import (
    inspect_bridge_nadawca as _inspect_bridge_nadawca,
    parse_pickup_from_uwagi as _parse_pickup_from_uwagi,
)
from dispatch_v2.uwagi_bridge_envelope import (
    BridgeCredentialError,
    build_verbose_uwagi_envelope,
    load_bridge_hmac,
    sign_bridge_envelope,
)

TEST_HMAC_MATERIAL = b"dispatch-v2-test-only-hmac-material-32-bytes-minimum"


def inspect_bridge_nadawca(text):
    return _inspect_bridge_nadawca(text, hmac_material=TEST_HMAC_MATERIAL)


def parse_pickup_from_uwagi(text, bridge_format=False):
    return _parse_pickup_from_uwagi(
        text,
        bridge_format=bridge_format,
        bridge_hmac_material=(TEST_HMAC_MATERIAL if bridge_format else None),
    )

# --- realne fixture z produkcji (skrócone pola nieistotne dla parsera) ---------
STREET_SPORT = (
    "Street-Sport #45520 | NADAWCA: FLM SP.K tel 604 593 684 | "
    "FLM PAWEŁ POTOCZEK TOMASZ POTOCZEK SP.K., NIP 5423039093, Boruty 17, "
    "15-157 Białystok, sklep@street-sport.pl | Odbiorca: FLM SP.K. | 7Kicks | "
    "oryg. adres: Kijowska 12 lok. U2 | Okno odbioru: 11:00 - 14:00 | "
    "Okno doreczenia: 12:00 - 15:00 | Paczek: 1 | SRC:EPAKA_BRIDGE:v1"
)
CHWIESKO = (
    "Adam Chwiesko #45508 | NADAWCA: Adam Chwieśko tel 607169514 | "
    "Endogastrodent sp. z o.o., NIP 9662201067, Gajowa 29, 15-794 Białystok, "
    "patolog@adamchwiesko.com | Odbiorca: Pani w Punkcie przyjęć | "
    "Akademicki Ośrodek Diagnostyki Patomorfologicznej | oryg. adres: Waszyngtona 13 | "
    "Okno odbioru: 008:00 - 11:00 (następny dzień roboczy)) | Paczek: 1 | "
    "SRC:EPAKA_BRIDGE:v1"
)
BRAVILOR = (
    "Bravilor Bonamat #45430 | NADAWCA: Krzysztof Jakoniuk tel 507602506 | "
    "Bravilor Bonamat Sp. z o.o., NIP 5342506604, Jacka Kuronia 2, 15-569 Białystok, "
    "accountspayable-pl@bravilor.com | Odbiorca: Jowita Kruk | FBR Mazur i Partnerzy | "
    "oryg. adres: Bagienna 1 | Okno odbioru: 10:00 - 13:00 | Paczek: 1 | "
    "SRC:EPAKA_BRIDGE:v1"
)
WOJOWNICY_50_TO_43 = (
    "Mali Wojownicy #45272 | NADAWCA: KUCHNIA KUCHNIA tel 000000000 | "
    "Przedszkole Niepubliczne Dzielne Zuchy, Mickiewicza 50, 15-000 Białystok, "
    "mali.wojownicy@nadajesz.pl | Odbiorca: MALI WOJOWNICY MALI WOJOWNICY | "
    "MALI WOJOWNICY | oryg. adres: ul. Mickiewicza 43 budynek C | "
    "Okno odbioru: 10:00 - 11:00 | Okno doreczenia: Natychmiast - | Paczek: 1 | "
    "SRC:EPAKA_BRIDGE:v1"
)
WOJOWNICY_43_TO_50 = (
    "Mali Wojownicy #45273 | NADAWCA: MALI WOJOWNICY MALI WOJOWNICY tel 000000000 | "
    "MALI WOJOWNICY, ul. Mickiewicza 43 budynek C, 15-000 Białystok, "
    "mali.wojownicy@nadajesz.pl | Odbiorca: KUCHNIA KUCHNIA | "
    "Przedszkole Niepubliczne Dzielne Zuchy | oryg. adres: Mickiewicza 50 | "
    "Okno odbioru: 10:00 - 11:00 | Paczek: 1 | SRC:EPAKA_BRIDGE:v1"
)
MATKA_POLKA = (
    "Matka Polka Hybrydowa #45313 | NADAWCA: Paweł Paśnikowski tel +48504153737 | "
    "UPMAKE Paweł Paśnikowski, NIP 5422934638, Ul. Gen. Gustawa Orlicz-Dreszera 3 "
    "Lokal 1, 15-979 Białystok, biuro@matkapolkahybrydowa.com | Odbiorca: SYLWIA GIBA | "
    "LABORATORIUM URODY SYLWIA GIBA | oryg. adres: Kardynała Stefana Wyszyńskiego 2/50 | "
    "Okno odbioru: 11:00 - 14:00 | Paczek: 1 | SRC:EPAKA_BRIDGE:v1"
)
NADZWYCZAJNIE = (
    "Nadzwyczajnie #45285 | NADAWCA: Nadzwyczajnie .pl tel 609345380 | "
    "Nadzwyczajnie, NIP 5423474966, Komunalna 5, 15-197 Białystok, "
    "biuro@nadzwyczajnie.pl | Odbiorca: jolanta jankowska | oryg. adres: "
    "Jarzębinowa 12/45 | Okno odbioru: 11:00 - 14:00 | Paczek: 1 | "
    "SRC:EPAKA_BRIDGE:v1"
)


def _upgrade_fixture(unsigned_v1):
    payload, marker = unsigned_v1.rsplit(" | ", 1)
    assert marker == "SRC:EPAKA_BRIDGE:v1"
    return sign_bridge_envelope(payload, TEST_HMAC_MATERIAL)


def _resign_payload(envelope, transform):
    payload = envelope.rsplit(" | SRC:", 1)[0]
    return sign_bridge_envelope(transform(payload), TEST_HMAC_MATERIAL)


UNSIGNED_V1_STREET_SPORT = STREET_SPORT
STREET_SPORT = _upgrade_fixture(STREET_SPORT)
CHWIESKO = _upgrade_fixture(CHWIESKO)
BRAVILOR = _upgrade_fixture(BRAVILOR)
WOJOWNICY_50_TO_43 = _upgrade_fixture(WOJOWNICY_50_TO_43)
WOJOWNICY_43_TO_50 = _upgrade_fixture(WOJOWNICY_43_TO_50)
MATKA_POLKA = _upgrade_fixture(MATKA_POLKA)
NADZWYCZAJNIE = _upgrade_fixture(NADZWYCZAJNIE)

BRIDGE_CASES = [
    (STREET_SPORT, "Boruty", "17"),
    (CHWIESKO, "Gajowa", "29"),
    (BRAVILOR, "Jacka Kuronia", "2"),
    (WOJOWNICY_50_TO_43, "Mickiewicza", "50"),
    (WOJOWNICY_43_TO_50, "Mickiewicza", "43 budynek C"),
    (MATKA_POLKA, "Gen. Gustawa Orlicz-Dreszera", "3 Lokal 1"),
    (NADZWYCZAJNIE, "Komunalna", "5"),
]

# nie-mostowe P3-edge z produkcji: instrukcje doręczenia, NIE adres odbioru —
# muszą zostać None także z bridge_format=True (żadnych fałszywych parse'ów)
NON_BRIDGE_P3 = [
    "Dostawa 12-14, Uniwersytecki Dziecięcy Szpital Kliniczny, 6 piętro, sekretariat",
    "DOSTAWA DO 13: BIO ZDROWIE SP.Z.O.O.",
    "DOSTAWA: USK, Blok E, 1 piętro",
    "Jedziesz do Galerii Jurowieckiej, pobierasz platnosc kartą od klienta z rossmana.",
]


@pytest.mark.parametrize("text,street,number", BRIDGE_CASES)
def test_bridge_on_parses_sender_address(text, street, number):
    r = parse_pickup_from_uwagi(text, bridge_format=True)
    assert r is not None, "bridge_format=True MUSI parsować format mostu"
    assert r.street == street
    assert r.number == number
    assert r.city == "Białystok"
    assert r.confidence == 0.95


@pytest.mark.parametrize("text,street,number", BRIDGE_CASES)
def test_bridge_off_byte_parity_with_legacy(text, street, number):
    """OFF = dzisiejsze zachowanie: format mostu NIE jest parsowany (P3 → KOORD)."""
    assert parse_pickup_from_uwagi(text, bridge_format=False) is None
    assert parse_pickup_from_uwagi(text) is None  # default = OFF


def test_oryg_adres_is_delivery_not_pickup():
    """oryg. adres (Kijowska 12) = DORĘCZENIE — nigdy nie może wyjść jako pickup."""
    r = parse_pickup_from_uwagi(STREET_SPORT, bridge_format=True)
    assert "Kijowska" not in r.street
    r2 = parse_pickup_from_uwagi(WOJOWNICY_50_TO_43, bridge_format=True)
    assert r2.number == "50"  # nadawca Mickiewicza 50, NIE odbiorca 43


@pytest.mark.parametrize("text", NON_BRIDGE_P3)
def test_non_bridge_p3_stays_none_with_flag_on(text):
    assert parse_pickup_from_uwagi(text, bridge_format=True) is None


def test_legacy_format_unaffected_by_flag_on():
    """Legacy uwagi (bez markera NADAWCA) parsują się identycznie ON vs OFF."""
    legacy = "Odbiór: Wyszyńskiego 2/75, Drtusz"
    on = parse_pickup_from_uwagi(legacy, bridge_format=True)
    off = parse_pickup_from_uwagi(legacy, bridge_format=False)
    assert on == off


def test_company_extracted_for_display():
    r = parse_pickup_from_uwagi(CHWIESKO, bridge_format=True)
    assert r.company == "Endogastrodent sp. z o.o."


def test_stoplist_blocks_street_named_like_company():
    """Mutation-probe kierunku: podstawiony 'adres' będący nazwą firmy ze stoplisty
    NIE przechodzi (plauzybilność ulicy dalej obowiązuje w gałęzi bridge)."""
    poisoned = sign_bridge_envelope((
        "X #1 | NADAWCA: A tel 1 | Firma, NIP 123, Mali Wojownicy 7, "
        "15-000 Białystok, a@b.c | Odbiorca: B | oryg. adres: C 1 | Paczek: 1 | "
    ).removesuffix(" | "), TEST_HMAC_MATERIAL)
    assert parse_pickup_from_uwagi(poisoned, bridge_format=True) is None


def _bridge_text(address, *, city="Białystok"):
    payload = (
        "Test #1 | NADAWCA: Jan Kowalski tel 500500500 | "
        f"Firma Testowa, {address}, 15-001 {city}, test@example.invalid | "
        "Odbiorca: Anna Nowak | oryg. adres: Inna 99 | Paczek: 1"
    )
    return sign_bridge_envelope(payload, TEST_HMAC_MATERIAL)


def test_manual_fake_marker_without_complete_envelope_stays_none():
    text = "Ręczna uwaga 161: Boruty 17 | SRC:EPAKA_BRIDGE:v1"
    assert parse_pickup_from_uwagi(text, bridge_format=True) is None


def test_poc_complete_manual_envelope_with_pasted_marker_is_rejected():
    """PoC #1: pełna ręczna koperta nie zna HMAC i nie może wejść w P0."""
    forged = UNSIGNED_V1_STREET_SPORT.replace(
        "SRC:EPAKA_BRIDGE:v1",
        "SRC:EPAKA_BRIDGE:v2;hmac-sha256=" + ("0" * 64),
    )
    attempt = inspect_bridge_nadawca(forged)
    assert attempt.pickup is None
    assert attempt.reason == "hmac_mismatch"


def test_missing_odbiorca_boundary_fails_closed():
    text = _resign_payload(
        STREET_SPORT,
        lambda payload: payload.replace("| Odbiorca:", "| Klient:", 1),
    )
    assert parse_pickup_from_uwagi(text, bridge_format=True) is None


def test_duplicate_nadawca_segment_fails_closed():
    text = _resign_payload(
        STREET_SPORT,
        lambda payload: payload.replace(
            "| Odbiorca:", "| NADAWCA: duplikat | Odbiorca:", 1
        ),
    )
    assert parse_pickup_from_uwagi(text, bridge_format=True) is None


def test_poc_two_raw_nadawca_prefixes_in_one_segment_are_rejected():
    """PoC #2: surowe prefiksy liczymy przed split/normalizacją."""
    payload = STREET_SPORT.rsplit(" | SRC:", 1)[0].replace(
        "NADAWCA: FLM", "NADAWCA: NADAWCA: FLM", 1
    )
    signed = sign_bridge_envelope(payload, TEST_HMAC_MATERIAL)
    attempt = inspect_bridge_nadawca(signed)
    assert attempt.pickup is None
    assert attempt.reason == "raw_nadawca_prefix_count:2"


def test_nadawca_label_without_leading_pipe_is_not_an_envelope_segment():
    text = _bridge_text("Boruty 17").removeprefix("Test #1 | ")
    assert text.startswith("NADAWCA:")
    assert parse_pickup_from_uwagi(text, bridge_format=True) is None


def test_duplicate_odbiorca_boundary_fails_closed():
    text = _resign_payload(
        STREET_SPORT,
        lambda payload: payload.replace(
            "| Odbiorca:", "| Odbiorca: pierwszy | Odbiorca:", 1
        ),
    )
    assert parse_pickup_from_uwagi(text, bridge_format=True) is None


def test_two_postal_anchors_fail_closed_instead_of_first_wins():
    text = _resign_payload(
        STREET_SPORT,
        lambda payload: payload.replace(
            "| Odbiorca:", ", Druga 2, 16-001 Kleosin | Odbiorca:", 1
        ),
    )
    assert parse_pickup_from_uwagi(text, bridge_format=True) is None


@pytest.mark.parametrize(
    "address, expected_number",
    [
        ("Boruty 17 budynek C", "17 budynek C"),
        ("Boruty 17 Lokal 1", "17 Lokal 1"),
        ("Boruty 17 lok. U2", "17 lok. U2"),
        ("Boruty 17 m. 12", "17 m. 12"),
    ],
)
def test_recognized_local_qualifier_is_preserved_in_number(address, expected_number):
    parsed = parse_pickup_from_uwagi(_bridge_text(address), bridge_format=True)
    assert parsed is not None
    assert parsed.number == expected_number


def test_unknown_address_extra_with_second_number_fails_closed():
    assert (
        parse_pickup_from_uwagi(
            _bridge_text("Pierwsza 1 Druga 2"), bridge_format=True
        )
        is None
    )


def test_two_word_city_is_preserved():
    parsed = parse_pickup_from_uwagi(
        _bridge_text("Brzeska 7", city="Biała Podlaska"), bridge_format=True
    )
    assert parsed is not None
    assert parsed.city == "Biała Podlaska"


def test_exact_epaka_marker_spelling_is_required():
    typo = STREET_SPORT.replace("SRC:EPAKA_BRIDGE:v2", "SRC:EPAKI_BRIDGE:v2")
    assert parse_pickup_from_uwagi(typo, bridge_format=True) is None


def test_unknown_bridge_version_is_none_with_distinguishable_reason():
    future = STREET_SPORT.replace("SRC:EPAKA_BRIDGE:v2", "SRC:EPAKA_BRIDGE:v3")
    attempt = inspect_bridge_nadawca(future)
    assert attempt.pickup is None
    assert attempt.reason == "unsupported_source_version:v3"
    assert parse_pickup_from_uwagi(future, bridge_format=True) is None
    legacy_collision = (
        "Odbiór: Lepsza 9 | NADAWCA: A | Firma, Boruty 17, "
        "15-157 Białystok | Odbiorca: B | SRC:EPAKA_BRIDGE:v3"
    )
    assert parse_pickup_from_uwagi(legacy_collision, bridge_format=True) is None


def test_duplicate_source_marker_fails_closed():
    duplicate = STREET_SPORT + " | " + STREET_SPORT.rsplit(" | ", 1)[1]
    assert parse_pickup_from_uwagi(duplicate, bridge_format=True) is None


def test_marker_must_be_the_terminal_nonempty_segment():
    assert (
        parse_pickup_from_uwagi(
            STREET_SPORT + " | nieznany segment", bridge_format=True
        )
        is None
    )


def test_loose_nadawca_without_marker_uses_unchanged_legacy_path():
    text = (
        "Odbiór: Lepsza 9 | NADAWCA: A | Firma, Boruty 17, "
        "15-157 Białystok | Odbiorca: B"
    )
    assert parse_pickup_from_uwagi(text, bridge_format=True) == (
        parse_pickup_from_uwagi(text, bridge_format=False)
    )


def test_mutation_probe_swapped_nadawca_and_odbiorca_is_rejected():
    swapped = _resign_payload(
        STREET_SPORT,
        lambda payload: (
            payload.replace("NADAWCA:", "__SENDER__:", 1)
            .replace("Odbiorca:", "NADAWCA:", 1)
            .replace("__SENDER__:", "Odbiorca:", 1)
        ),
    )
    assert parse_pickup_from_uwagi(swapped, bridge_format=True) is None


def _producer_detail(**sender_overrides):
    sender = {
        "name": "Jan",
        "lastname": "Kowalski",
        "phone": "500500500",
        "company": "Firma Nadawcy",
        "invoice_nip": "1234567890",
        "street": "Boruty 17",
        "post_code": "15-157",
        "city": "Białystok",
        "email": "sender@example.invalid",
    }
    sender.update(sender_overrides)
    return {
        "sender": sender,
        "name": "Anna",
        "lastname": "Nowak",
        "company": "Odbiorca Sp. z o.o.",
        "address": "Kijowska 12",
        "czas_odbioru_okno": "11:00 - 14:00",
        "czas_doreczenia_okno": "12:00 - 15:00",
        "ilosc_paczek": "1",
    }


def test_poc_producer_sender_name_cannot_inject_address_segment():
    """PoC #3: `|` z pola producenta jest danymi, nie separatorem koperty."""
    envelope = build_verbose_uwagi_envelope(
        {"name": "Street-Sport", "verbose_uwagi": True},
        45520,
        _producer_detail(name="Jan | Firma Atak, Zła 99"),
        TEST_HMAC_MATERIAL,
    )
    assert "%7C" in envelope
    assert envelope.count("NADAWCA:") == 1
    parsed = parse_pickup_from_uwagi(envelope, bridge_format=True)
    assert parsed is not None
    assert (parsed.street, parsed.number) == ("Boruty", "17")


def test_postal_code_after_odbiorca_boundary_does_not_collide():
    detail = _producer_detail(address="Kijowska 12, 00-001 Warszawa")
    envelope = build_verbose_uwagi_envelope(
        {"name": "Street-Sport", "verbose_uwagi": True},
        45520,
        detail,
        TEST_HMAC_MATERIAL,
    )
    parsed = parse_pickup_from_uwagi(envelope, bridge_format=True)
    assert parsed is not None
    assert (parsed.street, parsed.number, parsed.city) == (
        "Boruty", "17", "Białystok"
    )


def test_hmac_file_requires_exact_0600(tmp_path):
    path = tmp_path / "bridge-hmac-fixture"
    path.write_bytes(TEST_HMAC_MATERIAL)
    os.chmod(path, 0o640)
    with pytest.raises(BridgeCredentialError):
        load_bridge_hmac(path)
    os.chmod(path, 0o600)
    assert load_bridge_hmac(path) == TEST_HMAC_MATERIAL


def test_unsigned_v1_is_rejected_even_when_shape_is_complete():
    attempt = inspect_bridge_nadawca(UNSIGNED_V1_STREET_SPORT)
    assert attempt.envelope_seen is True
    assert attempt.version == 1
    assert attempt.reason == "unsigned_source_marker"
    assert attempt.pickup is None


def _run_panel_callsite(
    monkeypatch,
    *,
    reject_on,
    uwagi=STREET_SPORT,
    geocode_result=None,
):
    from dispatch_v2 import panel_detail_prefetch
    from dispatch_v2 import panel_watcher as watcher
    from dispatch_v2 import parse_continuity_guard

    order_id = "900001"
    parsed_panel = {
        "order_ids": [order_id],
        "assigned_ids": set(),
        "unassigned_ids": [order_id],
        "rest_names": {order_id: "Firmowe"},
        "courier_packs": {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }
    normalized = {
        "address_id": 161,
        "uwagi": uwagi,
        "pickup_address": "Adres konta firmowego",
        "pickup_city": "Białystok",
        "delivery_address": None,
        "delivery_city": "Białystok",
        "restaurant": "Firmowe",
        "pickup_at_warsaw": "2026-07-21T12:00:00+02:00",
        "prep_minutes": 30,
        "order_type": "elastic",
        "status_id": 2,
        "id_kurier": None,
        "is_koordynator": True,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
        "decision_deadline": None,
        "zmiana_czasu_odbioru": None,
        "created_at_utc": "2026-07-21T09:45:00Z",
    }
    flag_values = {
        "ENABLE_UWAGI_ADDRESS_PARSER": True,
        "ENABLE_UWAGI_BRIDGE_NADAWCA": True,
        "ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL": reject_on,
        "ENABLE_COORDINATOR_FORCE_TIME_RECHECK": False,
    }
    emitted = []
    geocode_calls = []

    monkeypatch.setattr(watcher, "state_get_all", lambda: {})
    monkeypatch.setattr(watcher, "_ignored_ids", set())
    monkeypatch.setattr(watcher, "_COORDS", {})
    monkeypatch.setattr(watcher, "FIRMOWE_KONTO_ADDRESS_IDS", frozenset({161}))
    monkeypatch.setattr(watcher, "fetch_order_details", lambda *_args: {"id": order_id})
    monkeypatch.setattr(watcher, "normalize_order", lambda *_args: normalized)
    monkeypatch.setattr(watcher, "_build_prefetch_candidates", lambda *_args: [])
    monkeypatch.setattr(
        panel_detail_prefetch,
        "prefetch_details",
        lambda *_args, **_kwargs: ({}, {"prefetch_enabled": False}),
    )
    monkeypatch.setattr(
        parse_continuity_guard,
        "evaluate",
        lambda *_args, **_kwargs: {"freeze_new": False, "suspicious": False},
    )
    def fake_flag(name, default=False):
        return flag_values.get(name, default)

    monkeypatch.setattr(watcher, "flag", fake_flag)
    monkeypatch.setattr(watcher.C, "flag", fake_flag)
    monkeypatch.setattr(watcher, "decision_flag", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(watcher, "load_bridge_hmac", lambda: TEST_HMAC_MATERIAL)
    monkeypatch.setattr(
        watcher,
        "_write_uwagi_bridge_shadow_metric",
        lambda *_args, **_kwargs: None,
    )

    def fake_geocode(address, *, city, timeout):
        geocode_calls.append((address, city, timeout))
        return geocode_result

    def fake_emit(event_type, **kwargs):
        emitted.append((event_type, kwargs))
        return SimpleNamespace(state_ready=True, event_created=False)

    monkeypatch.setattr(watcher, "geocode", fake_geocode)
    monkeypatch.setattr(watcher, "_emit_and_apply_state", fake_emit)

    watcher._diff_and_emit(
        parsed_panel,
        csrf="test",
        _state_outbox_sweeper_on=True,
    )
    new_payload = next(kwargs["payload"] for kind, kwargs in emitted if kind == "NEW_ORDER")
    return new_payload, geocode_calls


def test_callsite_bridge_geocode_fail_never_uses_central_fallback(monkeypatch):
    from dispatch_v2.common import FIRMOWE_KONTO_FALLBACK_COORDS
    from dispatch_v2.dispatch_pipeline import assess_order

    payload, geocode_calls = _run_panel_callsite(monkeypatch, reject_on=True)

    assert geocode_calls == [("Boruty 17", "Białystok", 2.0)]
    assert payload["pickup_coords"] is None
    assert payload["pickup_coords"] != list(FIRMOWE_KONTO_FALLBACK_COORDS)
    assert payload["pickup_address"] == "Boruty 17"
    assert payload["uwagi_pickup_parsed"]["street"] == "Boruty"
    assert "fallback_coords_used" not in payload["uwagi_pickup_parsed"]
    assert payload["uwagi_pickup_parsed"]["bridge_envelope_rejected"] is True
    decision = assess_order(
        {
            **payload,
            "order_id": "900001",
            "delivery_coords": [53.13, 23.16],
        },
        fleet_snapshot={},
    )
    assert decision.verdict == "SKIP"
    assert decision.reason == "no_pickup_geocode"


def test_callsite_poc_manual_complete_envelope_goes_to_koord(monkeypatch):
    forged = UNSIGNED_V1_STREET_SPORT.replace(
        "SRC:EPAKA_BRIDGE:v1",
        "SRC:EPAKA_BRIDGE:v2;hmac-sha256=" + ("0" * 64),
    )
    payload, geocode_calls = _run_panel_callsite(
        monkeypatch,
        reject_on=True,
        uwagi=forged,
    )
    assert geocode_calls == []
    assert payload["pickup_coords"] is None
    assert payload["uwagi_pickup_parsed"]["geocode_rejected"] is True
    assert payload["uwagi_pickup_parsed"]["bridge_envelope_rejected"] is True


def test_callsite_disables_bridge_and_warns_when_reject_flag_is_off(
    monkeypatch, caplog
):
    with caplog.at_level(logging.WARNING, logger="panel_watcher"):
        payload, geocode_calls = _run_panel_callsite(
            monkeypatch,
            reject_on=False,
            uwagi="MALI WOJOWNICY",
        )

    assert geocode_calls == []
    assert payload["uwagi_pickup_parsed"]["fallback_coords_used"] is True
    assert any(
        "ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL=OFF" in record.getMessage()
        for record in caplog.records
    )


def test_incoherent_flags_cannot_revive_signed_envelope_via_legacy(monkeypatch):
    payload, geocode_calls = _run_panel_callsite(monkeypatch, reject_on=False)

    assert geocode_calls == []
    assert payload["pickup_coords"] is None
    assert payload["uwagi_pickup_parsed"]["bridge_envelope_rejected"] is True


def test_downstream_twins_keep_bridge_rejection_fail_closed(monkeypatch):
    from datetime import datetime, timezone

    from dispatch_v2 import czasowka_scheduler, shadow_dispatcher
    from dispatch_v2.uwagi_bridge_envelope import bridge_envelope_was_rejected

    rejected = {
        "address_id": "161",
        "pickup_coords": None,
        "uwagi_pickup_parsed": {"bridge_envelope_rejected": True},
    }
    assert bridge_envelope_was_rejected(rejected) is True
    assert shadow_dispatcher._should_regeocode_pickup(rejected) is False

    monkeypatch.setattr(czasowka_scheduler, "_early_morning_blocked", lambda _now: False)
    monkeypatch.setattr(czasowka_scheduler, "_minutes_to_pickup", lambda *_args: 10.0)
    monkeypatch.setattr(
        czasowka_scheduler.C,
        "FIRMOWE_KONTO_ADDRESS_IDS",
        frozenset({161}),
    )
    monkeypatch.setattr(czasowka_scheduler.C, "flag", lambda *_args: False)
    result = czasowka_scheduler._eval_czasowka_impl(
        "900001",
        rejected,
        datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
    )
    assert result["decision"] == "KOORD"
    assert result["reason"] == "no_pickup_geocode"

    legacy = {"uwagi_pickup_parsed": {"geocode_rejected": True}}
    assert bridge_envelope_was_rejected(legacy) is False
    assert shadow_dispatcher._should_regeocode_pickup(legacy) is True


def test_callsite_logs_distinguishable_unknown_bridge_version(
    monkeypatch, caplog
):
    future = STREET_SPORT.replace("SRC:EPAKA_BRIDGE:v2", "SRC:EPAKA_BRIDGE:v3")
    with caplog.at_level(logging.ERROR, logger="panel_watcher"):
        payload, geocode_calls = _run_panel_callsite(
            monkeypatch,
            reject_on=True,
            uwagi=future,
        )

    assert geocode_calls == []
    assert payload["pickup_coords"] is None
    assert any(
        "bridge_reason='unsupported_source_version:v3'" in record.getMessage()
        for record in caplog.records
    )


def test_callsite_out_of_bbox_geocode_rejects_without_central_fallback(monkeypatch):
    from dispatch_v2.common import FIRMOWE_KONTO_FALLBACK_COORDS

    payload, geocode_calls = _run_panel_callsite(
        monkeypatch,
        reject_on=True,
        geocode_result=(52.2297, 21.0122),  # Warszawa, poza bboxem Białegostoku
    )
    assert geocode_calls == [("Boruty 17", "Białystok", 2.0)]
    assert payload["pickup_coords"] is None
    assert payload["pickup_coords"] != list(FIRMOWE_KONTO_FALLBACK_COORDS)


def test_shadow_metric_is_jsonl_and_contains_no_pii(monkeypatch, tmp_path):
    from dispatch_v2 import panel_watcher as watcher

    path = tmp_path / "uwagi_bridge_envelope.jsonl"
    monkeypatch.setattr(watcher, "_UWAGI_BRIDGE_SHADOW_LOG_PATH", str(path))
    watcher._write_uwagi_bridge_shadow_metric(
        "900001",
        envelope_seen=True,
        version=2,
        reason="parsed_v2",
        parsed=True,
        geocode_ok=True,
        central_fallback=False,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {
        "order_id_hash",
        "envelope_seen",
        "version",
        "reason",
        "parsed",
        "geocode_ok",
        "central_fallback",
    }
    serialized = json.dumps(record, ensure_ascii=False)
    for pii in ("Boruty", "Białystok", "Street-Sport", "900001"):
        assert pii not in serialized


def test_shadow_metric_is_in_canonical_rotation():
    from dispatch_v2.core import jsonl_rotation

    assert (
        "/root/.openclaw/workspace/dispatch_state/uwagi_bridge_envelope.jsonl"
        in jsonl_rotation.JSONL_PATHS
    )


@pytest.mark.parametrize(
    "bridge_enabled,reject_enabled,expected",
    [(False, False, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_fail_closed_flag_binding(bridge_enabled, reject_enabled, expected):
    from dispatch_v2 import common as C

    assert C.uwagi_bridge_flags_coherent(
        bridge_enabled=bridge_enabled,
        reject_enabled=reject_enabled,
    ) is expected


def test_flag_lifecycle_registry_binds_bridge_to_reject():
    registry_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "tools",
        "flag_lifecycle_registry.json",
    )
    flags = json.loads(open(registry_path, encoding="utf-8").read())["flags"]
    bridge = "ENABLE_UWAGI_BRIDGE_NADAWCA"
    reject = "ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL"
    assert flags[bridge]["default"] is False
    assert flags[bridge]["twin_of"] == [reject]
    assert bridge in flags[reject]["twin_of"]
