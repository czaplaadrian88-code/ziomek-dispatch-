# -*- coding: utf-8 -*-
"""P0 bridge-NADAWCA: adres ODBIORU z verbose_uwagi mostu epaki (2026-07-21).

Fixture = REALNE uwagi z produkcji (watcher.log P3-edge rejects, 6 firm catch-all
rid=161). Kontrakt: NADAWCA = punkt odbioru; `oryg. adres` = doręczenie (NIE odbiór);
pickup_rules mostu ustawiają tylko CZAS. Flaga ENABLE_UWAGI_BRIDGE_NADAWCA podawana
parametrem `bridge_format` (parser pure) — OFF = bajt-parytet z legacy.
"""
import pytest

from dispatch_v2.uwagi_address_parser import parse_pickup_from_uwagi

# --- realne fixture z produkcji (skrócone pola nieistotne dla parsera) ---------
STREET_SPORT = (
    "Street-Sport #45520 | NADAWCA: FLM SP.K tel 604 593 684 | "
    "FLM PAWEŁ POTOCZEK TOMASZ POTOCZEK SP.K., NIP 5423039093, Boruty 17, "
    "15-157 Białystok, sklep@street-sport.pl | Odbiorca: FLM SP.K. | 7Kicks | "
    "oryg. adres: Kijowska 12 lok. U2 | Okno odbioru: 11:00 - 14:00 | "
    "Okno doreczenia: 12:00 - 15:00 | Paczek: 1"
)
CHWIESKO = (
    "Adam Chwiesko #45508 | NADAWCA: Adam Chwieśko tel 607169514 | "
    "Endogastrodent sp. z o.o., NIP 9662201067, Gajowa 29, 15-794 Białystok, "
    "patolog@adamchwiesko.com | Odbiorca: Pani w Punkcie przyjęć | "
    "Akademicki Ośrodek Diagnostyki Patomorfologicznej | oryg. adres: Waszyngtona 13 | "
    "Okno odbioru: 008:00 - 11:00 (następny dzień roboczy)) | Paczek: 1"
)
BRAVILOR = (
    "Bravilor Bonamat #45430 | NADAWCA: Krzysztof Jakoniuk tel 507602506 | "
    "Bravilor Bonamat Sp. z o.o., NIP 5342506604, Jacka Kuronia 2, 15-569 Białystok, "
    "accountspayable-pl@bravilor.com | Odbiorca: Jowita Kruk | FBR Mazur i Partnerzy | "
    "oryg. adres: Bagienna 1 | Okno odbioru: 10:00 - 13:00 | Paczek: 1"
)
WOJOWNICY_50_TO_43 = (
    "Mali Wojownicy #45272 | NADAWCA: KUCHNIA KUCHNIA tel 000000000 | "
    "Przedszkole Niepubliczne Dzielne Zuchy, Mickiewicza 50, 15-000 Białystok, "
    "mali.wojownicy@nadajesz.pl | Odbiorca: MALI WOJOWNICY MALI WOJOWNICY | "
    "MALI WOJOWNICY | oryg. adres: ul. Mickiewicza 43 budynek C | "
    "Okno odbioru: 10:00 - 11:00 | Okno doreczenia: Natychmiast - | Paczek: 1"
)
WOJOWNICY_43_TO_50 = (
    "Mali Wojownicy #45273 | NADAWCA: MALI WOJOWNICY MALI WOJOWNICY tel 000000000 | "
    "MALI WOJOWNICY, ul. Mickiewicza 43 budynek C, 15-000 Białystok, "
    "mali.wojownicy@nadajesz.pl | Odbiorca: KUCHNIA KUCHNIA | "
    "Przedszkole Niepubliczne Dzielne Zuchy | oryg. adres: Mickiewicza 50 | "
    "Okno odbioru: 10:00 - 11:00 | Paczek: 1"
)
MATKA_POLKA = (
    "Matka Polka Hybrydowa #45313 | NADAWCA: Paweł Paśnikowski tel +48504153737 | "
    "UPMAKE Paweł Paśnikowski, NIP 5422934638, Ul. Gen. Gustawa Orlicz-Dreszera 3 "
    "Lokal 1, 15-979 Białystok, biuro@matkapolkahybrydowa.com | Odbiorca: SYLWIA GIBA | "
    "LABORATORIUM URODY SYLWIA GIBA | oryg. adres: Kardynała Stefana Wyszyńskiego 2/50 | "
    "Okno odbioru: 11:00 - 14:00 | Paczek: 1"
)
NADZWYCZAJNIE = (
    "Nadzwyczajnie #45285 | NADAWCA: Nadzwyczajnie .pl tel 609345380 | "
    "Nadzwyczajnie, NIP 5423474966, Komunalna 5, 15-197 Białystok, "
    "biuro@nadzwyczajnie.pl | Odbiorca: jolanta jankowska | oryg. adres: "
    "Jarzębinowa 12/45 | Okno odbioru: 11:00 - 14:00 | Paczek: 1"
)

BRIDGE_CASES = [
    (STREET_SPORT, "Boruty", "17"),
    (CHWIESKO, "Gajowa", "29"),
    (BRAVILOR, "Jacka Kuronia", "2"),
    (WOJOWNICY_50_TO_43, "Mickiewicza", "50"),
    (WOJOWNICY_43_TO_50, "Mickiewicza", "43"),
    (MATKA_POLKA, "Gen. Gustawa Orlicz-Dreszera", "3"),
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
    poisoned = (
        "X #1 | NADAWCA: A tel 1 | Firma, NIP 123, Mali Wojownicy 7, "
        "15-000 Białystok, a@b.c | Odbiorca: B | oryg. adres: C 1 | Paczek: 1"
    )
    assert parse_pickup_from_uwagi(poisoned, bridge_format=True) is None
