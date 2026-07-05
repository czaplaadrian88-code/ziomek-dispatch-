"""Reader ground-truth statusów kuriera z GPS (courier_ground_truth.json).

Plik pisany WYŁĄCZNIE przez courier-api (`status_store.write_ground_truth`) —
jeden writer, żeby uniknąć wyścigu z wieloma writerami orders_state.json.
Ten moduł TYLKO czyta.

Faza 2b: obserwator shadow (`courier_gps_commitment_shadow.py`) używa go do
porównania faktu GPS vs commitment ze skrapowania panelu. Faza 2b-LIVE (osobny
ACK, po kalibracji): konsument w Ziomku ustawi `commitment_level`/`picked_up_at`
z tego źródła zamiast z niejednoznacznego panelu (panel HTML nie rozróżnia
status 3 assigned od 5 picked_up — patrz panel_watcher.py).

Schema wpisu (per order_id):
  {courier_id, last_status_code, last_status_label, last_status_at(epoch),
   picked_up_at(epoch|brak), delivered_at(epoch|brak), source, updated_at(epoch),
   gps_arrived_at(epoch|brak), gps_arrived_accuracy_m(|brak), gps_arrival_source(|brak)}

5b (2026-07-05): `gps_arrived_at` = fizyczny PRZYJAZD pod adres DOSTAWY (geofence
apki, dwell 30 s, earliest-wins) — writer: courier-api `write_gps_arrival`.
UWAGA semantyka: gps_arrived_at = "kurier stoi pod budynkiem", delivered_at =
"kurier potwierdził wręczenie suwakiem" (button-press, ±~3 min szumu).
"""
import json

GROUND_TRUTH_PATH = "/root/.openclaw/workspace/dispatch_state/courier_ground_truth.json"


def load_ground_truth(path: str = GROUND_TRUTH_PATH) -> dict:
    """Cały ground-truth jako dict {order_id: entry}. {} gdy brak/zepsuty."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def get_entry(gt: dict, order_id) -> dict:
    """Wpis dla zlecenia lub None."""
    e = gt.get(str(order_id)) if isinstance(gt, dict) else None
    return e if isinstance(e, dict) else None


def gps_picked_up_at(gt: dict, order_id):
    """Epoch realnego odbioru z GPS (status 5) lub None."""
    e = get_entry(gt, order_id)
    return e.get("picked_up_at") if e else None


def gps_delivered_at(gt: dict, order_id):
    """Epoch realnego doręczenia z GPS (status 7) lub None."""
    e = get_entry(gt, order_id)
    return e.get("delivered_at") if e else None


def gps_arrived_at(gt: dict, order_id):
    """5b: epoch fizycznego PRZYJAZDU pod adres dostawy (geofence apki) lub None.

    Preferuj nad delivered_at gdy potrzebna prawda "kiedy jedzenie dojechało"
    (delivered_at = ręczny klik, ±~3 min; gps_arrived_at = dwell-potwierdzony GPS).
    """
    e = get_entry(gt, order_id)
    return e.get("gps_arrived_at") if e else None
