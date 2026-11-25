"""Планировщик: тестовый набор заказов → районы → рейсы → экономика."""
import random
from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import Pvz, TestOrder
from .economics import Params, plan_runs, reason_text, run_cost_for, unit_costs
from .geo import build_zones

_zone_cache: dict[tuple, list[dict]] = {}


def pvz_list(s: Session) -> list[dict]:
    return [
        {"id": p.id, "name": p.name, "lat": p.lat, "lon": p.lon, "source": p.source, "weight": p.weight}
        for p in s.scalars(select(Pvz).order_by(Pvz.id))
    ]


def zones_for(points: list[dict], radius_m: int) -> list[dict]:
    key = (radius_m, len(points), points[-1]["id"] if points else 0)
    if key not in _zone_cache:
        _zone_cache[key] = build_zones(points, radius_m)
    return _zone_cache[key]


def test_orders(s: Session, p: Params, points: list[dict]) -> list[TestOrder]:
    """Достаём набор из базы; если параметры набора поменялись — генерируем заново (с тем же seed — те же заказы)."""
    batch = f"{p.orders}|{p.seed}|{p.share_other_city}|{p.share_bulky}|{p.share_no_stock}"
    rows = list(s.scalars(select(TestOrder).where(TestOrder.batch == batch).order_by(TestOrder.id)))
    if rows:
        return rows
    s.execute(delete(TestOrder))
    rng = random.Random(p.seed)
    ids = [x["id"] for x in points]
    weights = [x["weight"] for x in points]
    a, b, c = p.share_other_city, p.share_other_city + p.share_bulky, p.share_other_city + p.share_bulky + p.share_no_stock
    for pvz_id in rng.choices(ids, weights=weights, k=p.orders):
        r = rng.random()
        kind = "other_city" if r < a else "bulky" if r < b else "no_stock" if r < c else "candidate"
        s.add(TestOrder(batch=batch, pvz_id=pvz_id, kind=kind))
    s.commit()
    return list(s.scalars(select(TestOrder).where(TestOrder.batch == batch).order_by(TestOrder.id)))


def build_plan(s: Session, p: Params) -> dict:
    points = pvz_list(s)
    pos = {x["id"]: x for x in points}
    zones = zones_for(points, p.radius_m)
    zone_of = {pid: z["id"] for z in zones for pid in z["pvz_ids"]}
    orders = test_orders(s, p, points)
    u = unit_costs(p)

    by_zone: dict[int, Counter] = {z["id"]: Counter() for z in zones}
    for o in orders:
        by_zone[zone_of[o.pvz_id]][o.kind] += 1

    out_zones, T = [], Counter()
    for z in zones:
        cnt = by_zone[z["id"]]
        total = sum(cnt.values())
        cand = cnt["candidate"]
        zone_run_cost = run_cost_for(p, z["loop_km"], len(z["pvz_ids"]))
        r = plan_runs(cand, p, u, zone_run_cost)
        hub_orders = total - r["mesh"]
        cost_hub = total * u.hub_total
        cost_mix = r["mesh"] * u.mesh_fixed + r["runs"] * zone_run_cost + hub_orders * u.hub_total
        status = "hub" if r["runs"] == 0 else ("partial" if r["fallback"] else "mesh")
        center = pos[z["center_id"]]
        out_zones.append({
            "id": z["id"], "center_id": z["center_id"], "center": [center["lat"], center["lon"]],
            "radius_m": p.radius_m, "pvz_ids": z["pvz_ids"], "loop_km": z["loop_km"],
            "loop": [[pos[i]["lat"], pos[i]["lon"]] for i in z["loop"]] + ([[center["lat"], center["lon"]]] if len(z["loop"]) > 1 else []),
            "orders": total, "candidates": cand, "other_city": cnt["other_city"], "bulky": cnt["bulky"], "no_stock": cnt["no_stock"],
            "runs": r["runs"], "mesh": r["mesh"], "fallback": r["fallback"], "hub": hub_orders,
            "run_cost": round(zone_run_cost), "break_even": None if r["break_even"] == float("inf") else round(r["break_even"], 1),
            "cost_hub": round(cost_hub), "cost_mix": round(cost_mix), "saving": round(cost_hub - cost_mix),
            "status": status,
        })
        T.update({"orders": total, "candidates": cand, "mesh": r["mesh"], "fallback": r["fallback"], "hub": hub_orders,
                  "runs": r["runs"], "cost_hub": cost_hub, "cost_mix": cost_mix, "run_spend": r["runs"] * zone_run_cost})

    saving = T["cost_hub"] - T["cost_mix"]
    # только заказы ходового товара с полки (так считается критерий в презентации)
    cand_hub = T["candidates"] * u.hub_total
    cand_mix = T["mesh"] * u.mesh_fixed + T["run_spend"] + T["fallback"] * u.hub_total
    costs = sorted(z["run_cost"] for z in out_zones)
    typical_run = costs[len(costs) // 2] if costs else p.run_cost
    ut = unit_costs(p, typical_run)
    active = [z for z in out_zones if z["runs"] > 0]
    pvz_active = sum(len(z["pvz_ids"]) for z in active)
    pvz_month = (T["mesh"] * (p.pvz_fee + p.storage * min(p.turnover, 30)) / pvz_active * 30) if pvz_active else 0
    return {
        "params": p.as_dict(),
        "unit": {"hub_total": u.hub_total, "mesh_fixed": round(u.mesh_fixed, 1),
                 "typical_run_cost": round(typical_run),
                 "mesh_full_run": round(u.mesh_fixed + typical_run / p.capacity, 1),
                 "break_even": None if ut.break_even == float("inf") else round(ut.break_even, 1)},
        "totals": {
            "pvz": len(points), "zones": len(out_zones), "zones_active": len(active),
            "orders": T["orders"], "candidates": T["candidates"], "mesh": T["mesh"], "fallback": T["fallback"], "hub": T["hub"],
            "runs": T["runs"], "cost_hub": round(T["cost_hub"]), "cost_mix": round(T["cost_mix"]),
            "saving_day": round(saving), "saving_month": round(saving * 30),
            "saving_pct": round(saving / T["cost_hub"] * 100, 1) if T["cost_hub"] else 0,
            "saving_pct_candidates": round((cand_hub - cand_mix) / cand_hub * 100, 1) if cand_hub else 0,
            "avg_cost": round(T["cost_mix"] / T["orders"], 1) if T["orders"] else 0,
            "pvz_income_month": round(pvz_month),
        },
        "zones": out_zones,
    }


def order_decisions(s: Session, p: Params, zone_id: int | None, limit: int) -> list[dict]:
    """Решение по каждому заказу набора: куда он поехал и почему."""
    plan = build_plan(s, p)
    zinfo = {z["id"]: z for z in plan["zones"]}
    zone_of = {pid: z["id"] for z in plan["zones"] for pid in z["pvz_ids"]}
    used = Counter()
    out = []
    for o in test_orders(s, p, pvz_list(s)):
        zid = zone_of[o.pvz_id]
        kind = o.kind
        if kind == "candidate":
            used[zid] += 1
            kind = "mesh" if used[zid] <= zinfo[zid]["mesh"] else "fallback"
        if zone_id is None or zid == zone_id:
            out.append({"id": o.id, "pvz_id": o.pvz_id, "zone_id": zid, "route": "mesh" if kind == "mesh" else "hub",
                        "reason": reason_text(kind)})
            if len(out) >= limit:
                break
    return out
