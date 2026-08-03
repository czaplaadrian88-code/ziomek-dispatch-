"""NEW-1: fizyczny klucz pickup nie może zjadać nazw ulic na literę M.

Przypadki pochodzą z niezależnego blind review 491870 iter2.  Własności są
ważniejsze od konkretnej pisowni: numer domu i tożsamość ulicy muszą pozostać
w kluczu, natomiast lokal/piętro nie tworzą nowego fizycznego stopu.
"""
from __future__ import annotations

import ast
import itertools
import json
import re
from pathlib import Path

import pytest

from dispatch_v2 import address_canon
from dispatch_v2 import common
from dispatch_v2 import route_order


CITY = "Białystok"
ROOT = Path(__file__).resolve().parents[1]
LIVE_REPLAY = json.loads(
    (Path(__file__).parent / "golden" / "pickup_addresses_live_20260803.json").read_text(
        encoding="utf-8"
    )
)


def _address_key(address: str):
    return route_order.pickup_physical_key(
        {"pickup_address": address, "pickup_city": CITY}
    )


@pytest.mark.parametrize(
    ("address", "expected_address_part"),
    [
        ("Czesława Miłosza 2", "czesława miłosza 2|białystok"),
        ("Czesława Miłosza 40", "czesława miłosza 40|białystok"),
        ("Czesława Miłosza 137", "czesława miłosza 137|białystok"),
        ("Czesława MICKIEWICZA 7", "czesława mickiewicza 7|białystok"),
        ("Jana Matejki 5", "jana matejki 5|białystok"),
        ("Jana Matejki 99", "jana matejki 99|białystok"),
        ("Stefana Meissnera 3", "stefana meissnera 3|białystok"),
        ("Ludwika Mierosławskiego 8", "ludwika mierosławskiego 8|białystok"),
    ],
)
def test_blind_review_two_component_street_keeps_name_and_house_number(
    address,
    expected_address_part,
):
    assert _address_key(address) == (
        route_order.PHYSICAL_PICKUP_CONTRACT_VERSION,
        "address",
        expected_address_part,
    )


@pytest.mark.parametrize(
    "street",
    [
        "Czesława Miłosza",
        "Jana Matejki",
        "Stanisława Moniuszki",
        "Icchoka Małmeda",
    ],
)
def test_property_different_house_number_means_different_physical_key(street):
    assert _address_key(f"{street} 2") != _address_key(f"{street} 40")


@pytest.mark.parametrize(
    "streets",
    [
        ("Czesława Miłosza", "Czesława Mickiewicza"),
        ("Jana Matejki", "Jana Moniuszki"),
        ("Stanisława Moniuszki", "Stanisława Małmeda"),
    ],
)
def test_property_different_street_means_different_physical_key(streets):
    keys = {_address_key(f"{street} 7") for street in streets}
    assert len(keys) == len(streets)


def test_blind_review_collision_set_is_injective():
    addresses = [
        "Czesława Miłosza 2",
        "Czesława Miłosza 40",
        "Czesława MICKIEWICZA 7",
    ]
    keys = [_address_key(address) for address in addresses]
    assert all(left != right for left, right in itertools.combinations(keys, 2))


@pytest.mark.parametrize(
    "address",
    [
        "Czesława Miłosza 2 m 4",
        "Czesława Miłosza 2 m4",
        "Czesława Miłosza 2 m. 4",
        "Czesława Miłosza 2 lokal 4",
        "Czesława Miłosza 2 lok. 4U",
        "Czesława Miłosza 2 mieszkanie 4",
        "Czesława Miłosza 2 piętro 4",
        "Czesława Miłosza 2/4",
    ],
)
def test_numeric_unit_suffix_is_still_removed(address):
    assert _address_key(address) == _address_key("Czesława Miłosza 2")


def test_m_dot_in_registered_street_name_is_not_an_apartment_marker():
    assert address_canon.normalize_physical_building(
        "M. Curie-Skłodowskiej 5",
        CITY,
    ) == "skłodowskiej-curie marii 5|białystok"


def test_new2_alias_registry_has_one_owner_and_route_order_delegates():
    assert common.V327_STREET_ALIASES is address_canon.STREET_ALIASES
    assert "_PHYSICAL_STREET_ALIASES" not in (ROOT / "route_order.py").read_text(
        encoding="utf-8"
    )
    assert common.canonicalize_geocode_address(
        "Kilińskiego 12"
    ) == "jana kilińskiego 12"
    assert _address_key("Kilińskiego 12") == _address_key(
        "ul. Jana Kilińskiego 12/lok 1U"
    )


def test_address_canon_is_pure_and_unit_digit_anchor_is_structural():
    tree = ast.parse((ROOT / "address_canon.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.name != "annotations"
    }
    assert imported == {"re"}
    assert "unit_number" in address_canon.UNIT_SUFFIX_RE.groupindex
    assert r"\d+" in address_canon.UNIT_SUFFIX_RE.pattern
    assert "normalize_physical_building" in (
        route_order._normalized_pickup_address.__code__.co_names
    )


def _oracle_street(address: str) -> str | None:
    """Niezależny, testowy ekstraktor części ulicznej z realnego korpusu."""
    text = " ".join(address.strip().casefold().split())
    text = re.sub(r"\b\d{2}-\d{3}\b", "", text).strip()
    text = re.sub(r"^(?:ul(?:ica)?\.?\s+)", "", text)
    text = re.sub(
        r"(?:[/,]\s*|\s+)(?:lok(?:al)?|mieszkanie|pi[eę]tro|m)"
        r"\.?\s*\d+[a-z-]*\b.*$",
        "",
        text,
    )
    text = re.sub(r"/[^\s]+$", "", text).strip(" ,./")
    match = re.fullmatch(r"(.+?\D)\s*(\d+[a-z]?)", text)
    return " ".join(match.group(1).strip(" ,.").split()) if match else None


def _registered_street(street: str) -> str:
    return address_canon.STREET_ALIASES.get(street, street)


def test_new3_live_replay_is_sanitized_and_source_bound():
    assert LIVE_REPLAY["schema"] == "pickup-address-live-replay.v1"
    source = LIVE_REPLAY["source"]
    assert source["kind"] == "read-only-orders-state"
    assert source["basename"] == "orders_state.json"
    assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
    cases = LIVE_REPLAY["cases"]
    assert len(cases) == source["unique_pickup_pairs"] >= 30
    assert all(set(case) == {"pickup_address", "pickup_city"} for case in cases)
    assert all(case["pickup_address"].strip() for case in cases)
    numbered = [
        (index, case)
        for index, case in enumerate(cases)
        if any(char.isdigit() for char in case["pickup_address"])
    ]
    assert len(numbered) >= 30
    lost_house_number = [
        index
        for index, case in numbered
        if not any(
            char.isdigit()
            for char in route_order.pickup_physical_key(case)[2]
        )
    ]
    assert lost_house_number == []


def test_new3_real_streets_property_different_number_means_different_key():
    parsed = [
        (_oracle_street(case["pickup_address"]), case["pickup_city"])
        for case in LIVE_REPLAY["cases"]
    ]
    usable = {(street, city) for street, city in parsed if street}
    assert len(usable) >= 3 * len(LIVE_REPLAY["cases"]) // 4
    for street, city in usable:
        first = route_order.pickup_physical_key(
            {"pickup_address": f"{street} 2", "pickup_city": city}
        )
        second = route_order.pickup_physical_key(
            {"pickup_address": f"{street} 40", "pickup_city": city}
        )
        assert first != second, street


def test_new3_real_streets_property_different_street_means_different_key():
    streets = {
        _registered_street(street)
        for case in LIVE_REPLAY["cases"]
        if (street := _oracle_street(case["pickup_address"]))
    }
    assert len(streets) >= 25
    keys = {
        route_order.pickup_physical_key(
            {"pickup_address": f"{street} 7", "pickup_city": CITY}
        )
        for street in streets
    }
    assert len(keys) == len(streets)
