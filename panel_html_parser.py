"""V3.28 PARSER-RESILIENCE Layer 1 — Universal-ID regex parser dla gastro panel HTML.

Replace fragile regex parser w panel_client.py:213-285. Eliminate hardcoded
ID prefix `46\\d{4}` na linii 223 (root cause incident 02.05.2026).

Architecture (Z3 — proper fix):
- Pure regex (zero external deps, identyczna technika jak v1 dla backward compat)
- Universal ID space [10000-9999999]: `\\d{5,7}` zamiast hardcoded `46\\d{4}`
- ID validation post-extract: weryfikacja każdego ID przez `_is_valid_order_id()`
- Cross-source consistency check: DOM `id="zamowienie_X"` vs JS `id: X` — diff >5% logged
- Defense-in-depth: pusty/malformed → empty dict, NIE exception (Lekcja #32)
- Backward-compat schema: identyczny return dict (10 keys) jak v1

Z3 architectural decisions:
1. NIE bs4/lxml: zero external dep, eliminates supply chain risk, stdlib only
2. NIE HTMLParser stdlib: `self.offset` to col-in-line NIE byte-offset → block boundaries broken
3. Regex z universal pattern + structural validation = same robustness as v1 + bug fix
4. Layer 2 (health monitor) i Layer 4 (property-based tests) zapewnią detection przyszłych
   ID space evolutions (np. 7→8 cyfr za 5+ lat). Layer 1 = focused fix.

Test deliverables:
- /tmp/v328_layer1_test_samples/sample_real_2026-05-02.html (live capture, 1.4 MB, 243 orders)
- /tmp/v328_layer1_test_samples/sample_46xxxx_legacy.html (synthetic regression)
- /tmp/v328_layer1_test_samples/sample_47xxxx_post_rollover.html (synthetic target)
- /tmp/v328_layer1_test_samples/sample_malformed.html (synthetic edge)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)


# Universal ID validation: 5-7 digit numeric.
# Rationale: gastro panel order ID space currently 5-6 digit, future-proofed do 7.
# 5-cyfrowy minimum eliminuje false positives od czasów (HH:MM = 4 chars but \d\d:\d\d).
_VALID_ID_PATTERN = re.compile(r"^\d{5,7}$")


def _is_valid_order_id(s: str) -> bool:
    """Loose order ID validator. 5-7 digits, no prefix assumption."""
    return bool(s) and bool(_VALID_ID_PATTERN.match(s))


def parse_panel_html_v2(html: str) -> dict:
    """V3.28 Layer 1 — Universal-ID regex parser. Replaces v1 panel_client.parse_panel_html.

    Args:
        html: Raw panel HTML string (~1-2 MB typical).

    Returns:
        dict z 10 keys identycznych jak v1 parse_panel_html:
          order_ids: list[str] — wszystkie order IDs (ID space [10000-9999999])
          assigned_ids: set[str] — order IDs widoczne w courier columns
          unassigned_ids: list[str] — order_ids - assigned_ids
          rest_names: dict[zid -> name]
          courier_packs: dict[courier_name -> list[zid]]
          courier_load: dict[courier_name -> "X/Y"]
          html_times: dict[zid -> [created_warsaw, pickup_warsaw]]
          closed_ids: set[str] — order blocks bez data-idkurier (status terminal)
          pickup_addresses: dict[zid -> addr]
          delivery_addresses: dict[zid -> addr]

    Defense-in-depth:
      - Pusty HTML → empty result dict (NIE exception)
      - Malformed HTML → regex tolerant (regex doesn't require well-formed)
      - Cross-source check: DOM order_ids vs JS `id: X` — log WARNING if diff >5%
      - Each ID validated przez `_is_valid_order_id()` post-extract
    """
    if not html or not isinstance(html, str):
        log.warning(f"v2_parser: pusty/non-string html (type={type(html).__name__})")
        return _empty_result()

    # SVG strip — nawigacyjne SVG mogą zawierać noise patterns
    try:
        html_clean = re.sub(r"<svg[^>]*>.*?</svg>", "", html, flags=re.DOTALL)
    except Exception as e:
        log.warning(f"v2_parser: svg strip fail, using raw html: {e}")
        html_clean = html

    # === Primary order_ids extraction — UNIVERSAL ID PATTERN (Layer 1 root fix) ===
    # V1 BUG: r"id:\s*(46\d{4})" — hardcoded prefix `46`, missed wszystkie 47XXXX.
    # V2 FIX: r"id:\s*(\d{5,7})" — universal 5-7 digit, no prefix assumption.
    # Source: JS embedded `id: <num>` w panel HTML (consistent z `id="zamowienie_X"` HTML attr).
    raw_ids = re.findall(r"id:\s*(\d{5,7})", html_clean)
    # Dedupe preserving order, validate each ID
    seen: Set[str] = set()
    order_ids: List[str] = []
    invalid_count = 0
    for rid in raw_ids:
        if rid in seen:
            continue
        if not _is_valid_order_id(rid):
            invalid_count += 1
            continue
        seen.add(rid)
        order_ids.append(rid)
    if invalid_count > 0:
        log.warning(f"v2_parser: {invalid_count} invalid IDs filtered post-extract")

    # === assigned_ids — universal regex (identyczne jak v1, no prefix bug) ===
    assigned_ids: Set[str] = set()
    for zid in re.findall(r'id="zlec_(\d+)"', html_clean):
        if _is_valid_order_id(zid):
            assigned_ids.add(zid)

    unassigned_ids = [z for z in order_ids if z not in assigned_ids]

    # === rest_names: identyczna technika jak v1 ===
    rest_names: Dict[str, str] = {}
    for zid, rname in re.findall(
        r'id="zamowienie_(\d+)".*?box_zam_name[^>]*>\s*([^<]+)',
        html_clean, re.DOTALL,
    ):
        if _is_valid_order_id(zid):
            rest_names[zid] = rname.strip()

    # === html_times: identyczna technika jak v1 ===
    html_times: Dict[str, List[str]] = {}
    blocks = re.findall(
        r'id="zamowienie_(\d+)">(.*?)(?=id="zamowienie_|kurier_col|\Z)',
        html_clean, re.DOTALL,
    )
    for zid, content in blocks:
        if not _is_valid_order_id(zid):
            continue
        times = re.findall(r"\b(\d{1,2}:\d{2})\b", content)
        if times:
            html_times[zid] = times[:2]

    # === courier_packs + courier_load: identyczna technika jak v1 ===
    courier_packs: Dict[str, List[str]] = {}
    courier_load: Dict[str, str] = {}
    kurier_cols = re.findall(
        r'<div[^>]+class="[^"]*widok_kurier[^"]*"[^>]*>(.*?)(?=<div[^>]+class="[^"]*widok_kurier|\Z)',
        html_clean, re.DOTALL,
    )
    for col in kurier_cols:
        m = re.search(r"name_kurier[^>]*>([^<]+)", col)
        if not m:
            continue
        kname = m.group(1).strip()
        zid_list = re.findall(r'id="zlec_(\d+)"', col)
        zid_list = [z for z in zid_list if _is_valid_order_id(z)]
        if zid_list:
            courier_packs[kname] = zid_list
        m_load = re.search(r"<div>(\d/\d)</div>", col)
        if m_load:
            courier_load[kname] = m_load.group(1)

    # === closed_ids + addresses: identyczna technika jak v1 (already universal) ===
    closed_ids: Set[str] = set()
    pickup_addresses: Dict[str, str] = {}
    delivery_addresses: Dict[str, str] = {}
    for m in re.finditer(r'id="zamowienie_(\d+)"(.*?)(?=id="zamowienie_|\Z)', html, re.DOTALL):
        zid = m.group(1)
        if not _is_valid_order_id(zid):
            continue
        block = m.group(2)
        if "data-idkurier" not in block:
            closed_ids.add(zid)
        mp = re.search(r'data-address_from="([^"]*)"', block)
        if mp:
            pickup_addresses[zid] = mp.group(1).strip()
        md = re.search(r'data-address_to="([^"]*)"', block)
        if md:
            delivery_addresses[zid] = md.group(1).strip()

    # === Cross-source consistency check (Z3 observability) ===
    # Diff DOM (id="zamowienie_X") vs JS (id: X) — should match.
    # Drift >5% = signal że jeden ze źródeł has parser miss.
    try:
        dom_ids = set()
        for m in re.finditer(r'id="zamowienie_(\d+)"', html_clean):
            zid = m.group(1)
            if _is_valid_order_id(zid):
                dom_ids.add(zid)
        js_ids = set(order_ids)
        if len(dom_ids) > 0 and len(js_ids) > 0:
            delta_pct = abs(len(dom_ids) - len(js_ids)) / max(len(dom_ids), len(js_ids)) * 100
            if delta_pct > 5.0 and max(len(dom_ids), len(js_ids)) >= 10:
                only_in_dom = sorted(dom_ids - js_ids)[:5]
                only_in_js = sorted(js_ids - dom_ids)[:5]
                log.warning(
                    f"v2_parser cross-source delta {delta_pct:.1f}%: "
                    f"DOM={len(dom_ids)} JS={len(js_ids)} "
                    f"only_in_dom_sample={only_in_dom} only_in_js_sample={only_in_js}"
                )
    except Exception as e:
        log.warning(f"v2_parser cross-source check failed: {e}")

    return {
        "order_ids": order_ids,
        "assigned_ids": assigned_ids,
        "unassigned_ids": unassigned_ids,
        "rest_names": rest_names,
        "courier_packs": courier_packs,
        "courier_load": courier_load,
        "html_times": html_times,
        "closed_ids": closed_ids,
        "pickup_addresses": pickup_addresses,
        "delivery_addresses": delivery_addresses,
    }


def _empty_result() -> dict:
    return {
        "order_ids": [],
        "assigned_ids": set(),
        "unassigned_ids": [],
        "rest_names": {},
        "courier_packs": {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }


def parse_compare_v1_v2(html: str) -> dict:
    """Diagnostic helper: run both v1 (regex broken) i v2 (regex fixed), return diff metrics.

    Use w shadow phase: log diff jako WARNING jeśli istotne rozbieżności.
    Expected post-470000-rollover: V2 captures more orders than V1.
    """
    from dispatch_v2.panel_client import parse_panel_html as parse_v1
    v1 = parse_v1(html)
    v2 = parse_panel_html_v2(html)
    return {
        "order_ids_v1": len(v1.get("order_ids", [])),
        "order_ids_v2": len(v2.get("order_ids", [])),
        "delta_order_ids": len(v2["order_ids"]) - len(v1["order_ids"]),
        "only_in_v2_sample": sorted(set(v2["order_ids"]) - set(v1["order_ids"]))[:10],
        "only_in_v1_sample": sorted(set(v1["order_ids"]) - set(v2["order_ids"]))[:10],
        "assigned_ids_v1": len(v1.get("assigned_ids", set())),
        "assigned_ids_v2": len(v2.get("assigned_ids", set())),
        "rest_names_v1": len(v1.get("rest_names", {})),
        "rest_names_v2": len(v2.get("rest_names", {})),
        "pickup_addresses_v1": len(v1.get("pickup_addresses", {})),
        "pickup_addresses_v2": len(v2.get("pickup_addresses", {})),
        "delivery_addresses_v1": len(v1.get("delivery_addresses", {})),
        "delivery_addresses_v2": len(v2.get("delivery_addresses", {})),
        "closed_ids_v1": len(v1.get("closed_ids", set())),
        "closed_ids_v2": len(v2.get("closed_ids", set())),
        "courier_packs_v1": len(v1.get("courier_packs", {})),
        "courier_packs_v2": len(v2.get("courier_packs", {})),
        "courier_load_v1": len(v1.get("courier_load", {})),
        "courier_load_v2": len(v2.get("courier_load", {})),
        "html_times_v1": len(v1.get("html_times", {})),
        "html_times_v2": len(v2.get("html_times", {})),
    }
