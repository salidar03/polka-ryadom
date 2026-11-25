"""Экономика городского заказа: хаб против ПВЗ → ПВЗ (те же правила, что в презентации)."""
import math
from dataclasses import dataclass, asdict


@dataclass
class Params:
    orders: int = 1000            # городских заказов в день (весь тестовый набор)
    radius_m: int = 700           # радиус района (круга) вокруг опорного ПВЗ
    run_cost: float = 900         # база рейса: подача машины и водитель, ₽
    stop_cost: float = 120        # плюс за каждую остановку в ПВЗ (погрузка, сканы), ₽
    km_cost: float = 40           # плюс за километр по дорогам, ₽ (5 ПВЗ и 7,5 км → рейс 1 800 ₽, как в презентации)
    capacity: int = 45            # вместимость рейса, заказов
    hub_cost: float = 100         # забор 20 + хаб 35 + магистраль 25 + развоз 20, ₽
    issue: float = 25             # выдача покупателю, ₽ (в обеих схемах)
    pvz_fee: float = 16           # ПВЗ за приёмку 4 + сборку 12, ₽
    storage: float = 0.8          # хранение на полке, ₽ в день
    turnover: float = 10          # сколько дней товар лежит на полке
    restock: float = 6            # пополнение полки, ₽ за единицу
    share_other_city: float = 0.15  # продавец из другого города → только хаб
    share_bulky: float = 0.05       # крупногабарит → только хаб
    share_no_stock: float = 0.10    # товара нет на полке района → хаб
    seed: int = 42

    def as_dict(self):
        return asdict(self)


@dataclass
class UnitCosts:
    mesh_fixed: float   # заказ с полки без учёта рейса
    hub_total: float    # заказ через хаб
    margin: float       # сколько остаётся на рейс
    break_even: float   # заказов в рейсе, с которого рейс окупается


def run_cost_for(p: Params, loop_km: float, stops: int) -> float:
    """Стоимость одного рейса по петле района: база + остановки + километры."""
    return p.run_cost + p.stop_cost * stops + p.km_cost * loop_km


def unit_costs(p: Params, run_cost: float | None = None) -> UnitCosts:
    paid_days = min(p.turnover, 30)  # после 30 дней хранение оплачивает продавец
    mesh_fixed = p.restock + p.pvz_fee + p.storage * paid_days + p.issue
    hub_total = p.hub_cost + p.issue
    margin = hub_total - mesh_fixed
    rc = p.run_cost if run_cost is None else run_cost
    be = rc / margin if margin > 0 else math.inf
    return UnitCosts(mesh_fixed, hub_total, margin, be)


def plan_runs(candidates: int, p: Params, u: UnitCosts, run_cost: float) -> dict:
    """Набираем рейсы в районе. Неполный последний рейс ниже точки безубыточности
    не выходит — его заказы едут через хаб, поэтому в убыток схема не работает."""
    be = run_cost / u.margin if u.margin > 0 else math.inf
    runs = mesh = 0
    if p.capacity > be:
        full = candidates // p.capacity
        rem = candidates - full * p.capacity
        runs, mesh = full, full * p.capacity
        if rem > be:
            runs, mesh = runs + 1, mesh + rem
    return {"runs": runs, "mesh": mesh, "fallback": candidates - mesh, "break_even": be}


def reason_text(kind: str) -> str:
    return {
        "other_city": "продавец в другом городе — едет через хаб",
        "bulky": "крупногабарит — едет через хаб",
        "no_stock": "товара нет на полке района — едет через хаб",
        "fallback": "в рейсе района меньше точки безубыточности — едет через хаб",
        "mesh": "с полки опорного ПВЗ рейсом ПВЗ → ПВЗ",
    }[kind]
