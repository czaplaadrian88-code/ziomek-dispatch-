"""Route simulator - symulacja trasy kuriera po dodaniu nowego orderu.

Uzywa osrm.table do pobrania macierzy czasow 1 request,
potem nearest-neighbor do optymalizacji kolejnosci dostaw.

Zwraca RoutePlan z predicted_delivered_at per order.

Pure function, zero state.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass, field

from dispatch_v2 import osrm_client
from dispatch_v2.traffic import traffic_multiplier


@dataclass
class OrderSim:
    """Order w kontekscie simulacji routingu."""
    order_id: str
    pickup_coords: Tuple[float, float]
    delivery_coords: Tuple[float, float]
    picked_up_at: Optional[datetime] = None  # None = jeszcze nie odebrany
    status: str = "assigned"  # assigned | picked_up


@dataclass
class RoutePlan:
    """Wynik symulacji trasy dla baga + new_order."""
    sequence: List[str]  # kolejnosc order_id dostaw
    predicted_delivered_at: Dict[str, datetime]  # oid -> przewidywany delivered_at
    total_duration_min: float
    multiplier_used: float


def simulate_bag_route(
    courier_pos: Tuple[float, float],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: Optional[datetime] = None,
) -> Optional[RoutePlan]:
    """Symuluje trase: courier -> [pickup new_order jesli nie picked_up] -> wszystkie delivery.
    
    Nearest neighbor po aktualnych pozycjach startowych.
    
    Args:
        courier_pos: (lat, lon) aktualnej lokalizacji kuriera
        bag: lista orderow juz w bagu (moga byc assigned lub picked_up)
        new_order: nowy order ktory chcemy dodac
        now: timestamp startu symulacji (None = teraz UTC)
    
    Returns:
        RoutePlan lub None jesli osrm nieosiagalny
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    mult = traffic_multiplier(now)
    
    # Zbierz wszystkie punkty do policzenia matrycy
    # - pos 0: courier current
    # - pos 1..N: delivery points (dla bagu + new_order)
    # - opcjonalnie: pickup new_order jesli status assigned (kurier musi go odebrac)
    
    # Strategia: jesli new_order.status == 'assigned' -> najpierw pickup new_order, potem wszystkie delivery
    # Wszystkie istniejace bag ordery zakladamy juz picked_up (jesli nie, kurier i tak musi do nich wrocic,
    # ale w praktyce reconcile picked_up sprawia ze bag == picked_up)
    
    all_orders = bag + [new_order]
    delivery_points = [(o.delivery_coords, o.order_id) for o in all_orders]
    
    # Build point list
    points: List[Tuple[float, float]] = [courier_pos]
    point_labels: List[str] = ["courier"]
    
    # Jesli new_order nie picked_up, dodaj jego pickup jako pierwszy punkt
    need_pickup = new_order.status != "picked_up"
    if need_pickup:
        points.append(new_order.pickup_coords)
        point_labels.append(f"pickup_{new_order.order_id}")
    
    # Dodaj wszystkie delivery jako kolejne punkty
    for coords, oid in delivery_points:
        points.append(coords)
        point_labels.append(f"delivery_{oid}")
    
    # OSRM table - matryca czasow
    matrix = osrm_client.table(points, points)
    if matrix is None:
        return None
    
    def dur_min(i: int, j: int) -> float:
        """Czas minut miedzy punktem i a j z multiplier."""
        cell = matrix[i][j]
        if cell is None:
            return 9999.0
        dur_s = cell.get("duration_s", 0)
        if dur_s is None:
            dur_s = 0
        return (dur_s / 60.0) * mult
    
    # Simulacja: courier -> (pickup new_order) -> nearest neighbor przez delivery points
    trip_start = now
    sequence: List[str] = []
    predicted: Dict[str, datetime] = {}
    
    current_idx = 0  # courier
    
    if need_pickup:
        pickup_idx = 1  # pickup new_order
        trip_start += timedelta(minutes=dur_min(current_idx, pickup_idx))
        current_idx = pickup_idx
        # Zakladamy 2 min na wejscie do restauracji i odbior
        trip_start += timedelta(minutes=2)
    
    # Nearest neighbor po delivery points
    delivery_start_idx = 2 if need_pickup else 1
    remaining = list(range(delivery_start_idx, len(points)))
    
    while remaining:
        best_next = None
        best_dur = float("inf")
        for idx in remaining:
            d = dur_min(current_idx, idx)
            if d < best_dur:
                best_dur = d
                best_next = idx
        if best_next is None:
            break
        trip_start += timedelta(minutes=best_dur)
        oid = point_labels[best_next].replace("delivery_", "")
        predicted[oid] = trip_start
        sequence.append(oid)
        current_idx = best_next
        remaining.remove(best_next)
    
    total_minutes = (trip_start - now).total_seconds() / 60.0
    return RoutePlan(
        sequence=sequence,
        predicted_delivered_at=predicted,
        total_duration_min=round(total_minutes, 1),
        multiplier_used=mult,
    )
