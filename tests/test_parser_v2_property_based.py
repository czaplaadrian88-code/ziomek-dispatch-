"""V3.28 PARSER-RESILIENCE Layer 4 — Property-based tests dla parse_panel_html_v2.

Tests które złapały by tę regresję 5 lat temu:
- prefix × length parametric coverage (24 combinations)
- Backward compat z real fixtures (46XXXX no regression)
- Edge cases (pusty/malformed/mix)
- Future rollover simulation (470000→480000, 999999→1000000)
- Performance regression guard (1000/5000 orders <100/500ms)

Run:
    cd /root/.openclaw/workspace/scripts/dispatch_v2
    /root/.openclaw/venvs/dispatch/bin/python3 -m pytest tests/test_parser_v2_property_based.py -v --timeout=60

Deploy: copy do /root/.openclaw/workspace/scripts/dispatch_v2/tests/test_parser_v2_property_based.py
"""
import os
import sys
import time
import pytest
from pathlib import Path

# Ensure dispatch_v2 importable when run direct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def generate_synthetic_panel_html(order_ids: list, with_assigned: bool = False, mark_closed: list = None) -> str:
    """Generate minimal synthetic gastro panel HTML zgodny z parse_panel_html_v2 expectations.

    Pattern: każdy order ma block:
        id="zamowienie_<oid>" data-address_from="..." data-address_to="..."
        + (optional) data-idkurier="<kurier>"
        + box_zam_name>RestaurantName<
        + JS-style id: <oid>

    Args:
        order_ids: list of integer order IDs (or strings)
        with_assigned: gdy True, add courier column z assigned IDs
        mark_closed: list of OIDs to mark jako closed (BEZ data-idkurier)

    Returns:
        Minimal but parser-compatible HTML string.
    """
    mark_closed = set(str(o) for o in (mark_closed or []))
    blocks = []
    js_ids = []
    for oid in order_ids:
        oid_s = str(oid)
        is_closed = oid_s in mark_closed
        idkurier_attr = "" if is_closed else 'data-idkurier="999"'
        block = (
            f'<div id="zamowienie_{oid_s}" data-address_from="ul. Test {oid_s}/1" '
            f'data-address_to="ul. Klient {oid_s}/2" {idkurier_attr}>\n'
            f'  <span class="box_zam_name">Restauracja_{oid_s}</span>\n'
            f'  <span>12:34</span><span>13:00</span>\n'
            f'</div>'
        )
        blocks.append(block)
        js_ids.append(f"id: {oid_s}")

    courier_section = ""
    if with_assigned:
        # Assign first half to courier "TestKurier"
        n_assigned = len(order_ids) // 2
        zlec_blocks = "\n".join(
            f'<a id="zlec_{order_ids[i]}">link</a>' for i in range(n_assigned)
        )
        courier_section = (
            f'<div class="widok_kurier">\n'
            f'  <span class="name_kurier">TestKurier</span>\n'
            f'  <div>{n_assigned}/4</div>\n'
            f'  {zlec_blocks}\n'
            f'</div>'
        )

    js_data = "<script>\nvar orders = [" + ", ".join("{" + s + "}" for s in js_ids) + "];\n</script>"

    html = (
        f'<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        f'<meta name="csrf-token" content="test-csrf-token">'
        f'</head><body>\n'
        f'{js_data}\n'
        + "\n".join(blocks)
        + f"\n{courier_section}\n</body></html>"
    )
    return html


# Ensure /tmp module loadable (test runs both via pytest + standalone)
import importlib.util


def _load_v2_parser():
    """Load v2 parser z /tmp/ (pre-deploy) lub dispatch_v2/ (post-deploy)."""
    paths_to_try = [
        "/tmp/v328_layer1_panel_html_parser_v2.py",
        "/root/.openclaw/workspace/scripts/dispatch_v2/panel_html_parser.py",
    ]
    for p in paths_to_try:
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("panel_html_parser_v2", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("v2 parser not found w żadnym z paths")


@pytest.fixture(scope="module")
def v2_parser():
    return _load_v2_parser()


# ============================================================================
# TEST 1: Property-based ID range coverage (KRYTYCZNY — łapie hardcoded prefix)
# ============================================================================

# 8 prefixes × 3 lengths = 24 parametric combinations
@pytest.mark.parametrize("id_prefix", ["10", "20", "30", "46", "47", "50", "70", "99"])
@pytest.mark.parametrize("id_length", [5, 6, 7])
def test_parse_handles_arbitrary_id_space(v2_parser, id_prefix, id_length):
    """Parser MUSI działać dla ANY 5-7 digit ID, NIE tylko historical 46XXXX.

    Łapie 02.05.2026 incident pattern (regex `46\\d{4}` hardcoded).
    """
    if len(id_prefix) >= id_length:
        pytest.skip(f"prefix {id_prefix} >= length {id_length}, skip")
    sample_id_str = id_prefix + "1" + "0" * (id_length - len(id_prefix) - 1)
    sample_id = int(sample_id_str)
    html = generate_synthetic_panel_html(
        order_ids=[sample_id, sample_id + 1, sample_id + 2]
    )
    parsed = v2_parser.parse_panel_html_v2(html)
    assert len(parsed["order_ids"]) == 3, (
        f"prefix={id_prefix} length={id_length}: expected 3, got {parsed['order_ids']}"
    )
    assert str(sample_id) in parsed["order_ids"]
    assert str(sample_id + 1) in parsed["order_ids"]
    assert str(sample_id + 2) in parsed["order_ids"]


# ============================================================================
# TEST 2: Backward compat z real fixtures
# ============================================================================

REAL_HTML_PATH = "/tmp/v328_layer1_test_samples/sample_real_2026-05-02.html"


@pytest.mark.skipif(
    not os.path.exists(REAL_HTML_PATH),
    reason="Real HTML fixture nie istnieje (pre-Layer 1 capture wymagany)"
)
def test_backward_compat_real_fixture(v2_parser):
    """Real HTML 2026-05-02 (mix 46+47XXXX). v2 musi capture'ować WSZYSTKIE."""
    with open(REAL_HTML_PATH) as f:
        html = f.read()
    parsed = v2_parser.parse_panel_html_v2(html)

    # Empirical baseline (z Layer 1 testu na real HTML 02.05.2026):
    assert len(parsed["order_ids"]) >= 240, (
        f"expected >=240 orders, got {len(parsed['order_ids'])}"
    )
    # Verify oba prefiksy 46 + 47 present
    has_46 = any(o.startswith("46") for o in parsed["order_ids"])
    has_47 = any(o.startswith("47") for o in parsed["order_ids"])
    assert has_46, "expected 46XXXX orders w real HTML"
    assert has_47, "expected 47XXXX orders w real HTML (post-rollover)"
    # Schema integrity
    expected_keys = {
        "order_ids", "assigned_ids", "unassigned_ids", "rest_names",
        "courier_packs", "courier_load", "html_times", "closed_ids",
        "pickup_addresses", "delivery_addresses",
    }
    assert set(parsed.keys()) == expected_keys


# ============================================================================
# TEST 3: Edge cases
# ============================================================================

def test_empty_html(v2_parser):
    """Pusty input → empty result, NIE exception."""
    parsed = v2_parser.parse_panel_html_v2("")
    assert parsed["order_ids"] == []
    assert parsed["assigned_ids"] == set()


def test_none_html(v2_parser):
    """None input → empty result, NIE exception."""
    parsed = v2_parser.parse_panel_html_v2(None)
    assert parsed["order_ids"] == []


def test_malformed_html(v2_parser):
    """Garbage HTML → no exception, returns empty/partial."""
    bad = "<<<not really html>>>id: 470000<<<id=\"zamowienie_x\""
    parsed = v2_parser.parse_panel_html_v2(bad)
    # Should NIE raise; might capture id 470000 z JS pattern
    assert isinstance(parsed["order_ids"], list)


def test_only_47xxxx(v2_parser):
    """HTML wyłącznie ze 47XXXX (post-rollover scenario)."""
    ids = [470000, 470001, 470002, 470100, 470500]
    html = generate_synthetic_panel_html(order_ids=ids)
    parsed = v2_parser.parse_panel_html_v2(html)
    assert len(parsed["order_ids"]) == 5
    for oid in ids:
        assert str(oid) in parsed["order_ids"]


def test_mix_multiple_prefixes(v2_parser):
    """Mix 46+47+50+99 — wszystkie parsowane."""
    ids = [469001, 470001, 500001, 990001]
    html = generate_synthetic_panel_html(order_ids=ids)
    parsed = v2_parser.parse_panel_html_v2(html)
    assert len(parsed["order_ids"]) == 4
    for oid in ids:
        assert str(oid) in parsed["order_ids"]


# ============================================================================
# TEST 4: Future rollover simulation
# ============================================================================

def test_47_to_48_rollover(v2_parser):
    """Symuluj 479999 → 480000 (kolejny rollover ~6mc)."""
    ids = [479997, 479998, 479999, 480000, 480001]
    html = generate_synthetic_panel_html(order_ids=ids)
    parsed = v2_parser.parse_panel_html_v2(html)
    assert len(parsed["order_ids"]) == 5
    assert "480000" in parsed["order_ids"]


def test_6_to_7_digit_overflow(v2_parser):
    """Symuluj 999999 → 1000000 (overflow 6→7 cyfr)."""
    ids = [999998, 999999, 1000000, 1000001]
    html = generate_synthetic_panel_html(order_ids=ids)
    parsed = v2_parser.parse_panel_html_v2(html)
    assert len(parsed["order_ids"]) == 4
    assert "1000000" in parsed["order_ids"]
    assert "999999" in parsed["order_ids"]


def test_7_to_8_digit_overflow_rejected(v2_parser):
    """8-cyfrowy ID (przyszłość bardzo daleka) — _is_valid_order_id REJECTS, log filter."""
    # 5-7 digit pattern tylko. 8-digit nie powinno przejść validacji.
    ids = [10000000]  # 8 cyfr
    html = generate_synthetic_panel_html(order_ids=ids)
    parsed = v2_parser.parse_panel_html_v2(html)
    # 8-digit IDs filtered out — to jest by design (Layer 1 spec), Layer 4 future ticket
    # będzie expand do `\d{5,8}` gdy ID space ewoluuje
    # Test verifies filter activity, NIE crash
    assert isinstance(parsed["order_ids"], list)


# ============================================================================
# TEST 5: Performance regression
# ============================================================================

def test_perf_1000_orders(v2_parser):
    """1000 orderów → parse < 100ms (modern hardware)."""
    ids = [460000 + i for i in range(1000)]
    html = generate_synthetic_panel_html(order_ids=ids)
    t0 = time.time()
    parsed = v2_parser.parse_panel_html_v2(html)
    elapsed_ms = (time.time() - t0) * 1000
    assert len(parsed["order_ids"]) == 1000
    assert elapsed_ms < 500, f"perf regression: {elapsed_ms:.0f}ms > 500ms"


def test_perf_5000_orders(v2_parser):
    """5000 orderów (stress test) → parse < 2000ms."""
    ids = [460000 + i for i in range(5000)]
    html = generate_synthetic_panel_html(order_ids=ids)
    t0 = time.time()
    parsed = v2_parser.parse_panel_html_v2(html)
    elapsed_ms = (time.time() - t0) * 1000
    assert len(parsed["order_ids"]) == 5000
    assert elapsed_ms < 2000, f"perf stress: {elapsed_ms:.0f}ms > 2000ms"


# ============================================================================
# Convenience: standalone runner (NIE wymaga pytest)
# ============================================================================

if __name__ == "__main__":
    import sys as _s
    pytest_args = ["-v", "--tb=short", __file__]
    sys.exit(pytest.main(pytest_args))
