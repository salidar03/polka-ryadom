"""Примерный спрос и распределение партии поставщика между городами и ПВЗ.

Спрос города на один товар в день =
    жители / 1000 × товаров в день на 1000 жителей × доля категории × доля одного ходового товара.
Небольшая часть партии (по умолчанию 15%) раскладывается по городам пропорционально спросу,
но не больше спроса за срок «полки» — чтобы товар не залёживался. Остальное едет на склад WB.
В Йошкар-Оле доля города делится между опорными ПВЗ районов по спросу вокруг них.
"""
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "demand.json"
MIN_PER_PVZ = 2          # меньше двух штук на полку не везём
PVZ_ZONE_RADIUS_M = 700  # районы, как в аналитике


@lru_cache(maxsize=1)
def demand_data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def category(cat_id: str) -> dict:
    cats = {c["id"]: c for c in demand_data()["categories"]}
    return cats.get(cat_id, cats["other"])


def city_daily(city: dict, cat_id: str) -> dict:
    d = demand_data()
    cat_day = city["population"] / 1000 * d["items_per_1000_per_day"] * category(cat_id)["share"]
    return {"category_per_day": round(cat_day), "sku_per_day": round(cat_day * d["sku_share"], 1)}


def city_demand_table() -> dict:
    """Спрос базового города по категориям — «примерный спрос для одного города»."""
    d = demand_data()
    base = next(c for c in d["cities"] if c["name"] == d["base_city"])
    total = base["population"] / 1000 * d["items_per_1000_per_day"]
    return {
        "city": base["name"], "population": base["population"], "items_per_day": round(total),
        "items_per_1000_per_day": d["items_per_1000_per_day"], "sku_share": d["sku_share"],
        "rows": [{"id": c["id"], "name": c["name"], "emoji": c["emoji"], "share": c["share"],
                  "per_day": round(total * c["share"]), "sku_per_day": round(total * c["share"] * d["sku_share"], 1)}
                 for c in d["categories"]],
        "notes": [d["about"], d["items_note"], d["sku_note"], d["cities_note"]],
    }


def _largest_remainder(total: int, weights: list[float]) -> list[int]:
    """Разделить total штук пропорционально весам целыми числами без потерь."""
    s = sum(weights)
    if total <= 0 or s <= 0:
        return [0] * len(weights)
    raw = [total * w / s for w in weights]
    base = [int(x) for x in raw]
    left = total - sum(base)
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)[:left]:
        base[i] += 1
    return base


def plan_distribution(pvz_points: list[dict], cat_id: str, qty: int, share: float, horizon_days: int) -> dict:
    """Предложение поставщику: сколько штук в какой город и в какие ПВЗ Йошкар-Олы, остальное — на склад WB."""
    d = demand_data()
    cities = d["cities"]
    forecasts = []
    for c in cities:
        day = city_daily(c, cat_id)
        forecasts.append({**c, **day, "forecast": round(day["sku_per_day"] * horizon_days)})
    want = min(int(qty * share), sum(f["forecast"] for f in forecasts))
    alloc = _largest_remainder(want, [f["forecast"] for f in forecasts])
    alloc = [min(a, f["forecast"]) for a, f in zip(alloc, forecasts)]  # не больше спроса за срок полки

    rows = []
    for f, a in zip(forecasts, alloc):
        rows.append({"city": f["name"], "population": f["population"], "lat": f["lat"], "lon": f["lon"],
                     "sku_per_day": f["sku_per_day"], "forecast": f["forecast"], "qty": a,
                     "days_cover": round(a / f["sku_per_day"], 1) if f["sku_per_day"] else 0,
                     "pvz_network": f["pvz_network"], "pvz": []})

    # Йошкар-Ола: доля города — по опорным ПВЗ районов, где спрос выше
    base = next(r for r in rows if r["pvz_network"])
    if base["qty"] > 0 and pvz_points:
        from .geo import build_zones
        pos = {p["id"]: p for p in pvz_points}
        zones = build_zones(pvz_points, PVZ_ZONE_RADIUS_M)
        zones = sorted(zones, key=lambda z: sum(pos[i]["weight"] for i in z["pvz_ids"]), reverse=True)
        n = max(1, min(len(zones), base["qty"] // MIN_PER_PVZ))
        top = zones[:n]
        split = _largest_remainder(base["qty"], [sum(pos[i]["weight"] for i in z["pvz_ids"]) for z in top])
        for z, q in zip(top, split):
            if q > 0:
                c = pos[z["center_id"]]
                base["pvz"].append({"id": c["id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"], "qty": q,
                                    "zone_pvz": len(z["pvz_ids"])})

    on_shelves = sum(r["qty"] for r in rows)
    return {
        "category": category(cat_id), "qty": qty, "share": share, "horizon_days": horizon_days,
        "on_shelves": on_shelves, "to_warehouse": qty - on_shelves,
        "capped_by_demand": int(qty * share) > on_shelves,
        "cities": rows,
        "saving_per_order": 30,  # экономия логистики на заказе с полки, ₽ (из расчёта 125 → 95)
    }
