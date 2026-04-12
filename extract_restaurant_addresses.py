#!/usr/bin/env python3
"""
Wyciąga adresy restauracji z panelu + orders_state.json.
Output: /tmp/restaurant_addresses_from_panel.json
Keyed po address_id (int, stabilny klucz z bazy panelu).
"""
import sys, json, re, logging
from pathlib import Path
sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.panel_client import (
    fetch_panel_html, parse_panel_html, fetch_order_details,
    _session, health_check
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("extract_addr")

SKIP_ADDRESS_IDS = {161}  # Nadajesz.pl - firmowe konto
OUT = Path("/tmp/restaurant_addresses_from_panel.json")
STATE = Path("/root/.openclaw/workspace/dispatch_state/orders_state.json")

def norm_postcode(pc):
    if not pc: return None
    pc_clean = re.sub(r'\s+', '', str(pc))
    m = re.match(r'^(\d{2})-?(\d{3})$', pc_clean)
    return f"{m.group(1)}-{m.group(2)}" if m else str(pc).strip()

def norm_city(c):
    if not c: return None
    c = str(c).strip().lower()
    fix = {"bialystok": "Białystok", "białystok": "Białystok"}
    return fix.get(c, c.title())

def norm_company(name):
    if not name: return None
    n = str(name).lstrip("_").strip()
    n = re.sub(r'\s+', ' ', n)
    # zachowaj oryginalną kapitalizację jeśli mixed, bo "KILIŃSKIEGO" != "Kilińskiego"
    if n.isupper(): n = n.title()
    return n

def extract_from_address_dict(a):
    if not isinstance(a, dict): return None
    aid = a.get("id")
    if not aid: return None
    if int(aid) in SKIP_ADDRESS_IDS: return None
    street = (a.get("street") or "").strip()
    if not street: return None
    return {
        "address_id": int(aid),
        "company": norm_company(a.get("company") or a.get("lastname")),
        "company_raw": a.get("company"),
        "street": street,
        "post_code": norm_postcode(a.get("post_code")),
        "city": norm_city(a.get("city")),
    }

def main():
    results = {}  # address_id -> dict
    sources = {}  # address_id -> list of order ids

    # 1. Panel live
    log.info("Login + fetch panel...")
    health_check()
    html = fetch_panel_html()
    parsed = parse_panel_html(html)
    csrf = _session.get("csrf") or ""
    order_ids = parsed.get("order_ids", [])
    log.info(f"Panel ma {len(order_ids)} zleceń, iteruję...")

    for zid in order_ids:
        try:
            raw = fetch_order_details(zid, csrf)
            if not raw: continue
            info = extract_from_address_dict(raw.get("address"))
            if not info: continue
            aid = info["address_id"]
            if aid not in results:
                results[aid] = info
                sources[aid] = []
            sources[aid].append(int(zid))
        except Exception as e:
            log.warning(f"zid={zid} err: {e}")

    panel_count = len(results)
    log.info(f"Z panelu: {panel_count} unikalnych restauracji")

    # 2. Historyczne z orders_state (jeśli mają raw.address)
    hist_added = 0
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text())
            orders = st.get("orders", {})
            for oid, o in orders.items():
                raw = o.get("raw") or {}
                info = extract_from_address_dict(raw.get("address"))
                if not info: continue
                aid = info["address_id"]
                if aid not in results:
                    results[aid] = info
                    sources[aid] = []
                    hist_added += 1
                sources[aid].append(int(oid))
        except Exception as e:
            log.warning(f"orders_state read err: {e}")
    log.info(f"Z historii orders_state: +{hist_added} nowych")

    # 3. Dopisz source_order_ids (dedup, max 5)
    for aid in results:
        uniq = sorted(set(sources[aid]), reverse=True)[:5]
        results[aid]["source_order_ids"] = uniq

    # 4. Zapis (klucze jako stringi bo JSON)
    out = {str(aid): results[aid] for aid in sorted(results.keys())}
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log.info(f"OK: {len(out)} restauracji → {OUT}")

    # 5. Podgląd
    print(f"\n=== {len(out)} restauracji ===")
    for aid, info in out.items():
        print(f"[{aid}] {info['company']:35s} | {info['street']}, {info['post_code']} {info['city']}")

if __name__ == "__main__":
    main()
