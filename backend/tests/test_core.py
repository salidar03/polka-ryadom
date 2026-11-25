from app.economics import Params, plan_runs, run_cost_for, unit_costs
from app.geo import build_zones, dist_m


def test_unit_costs_match_presentation():
    p = Params()
    rc = run_cost_for(p, 7.5, 5)                    # 5 ПВЗ и 7,5 км → 1 800 ₽, как в презентации
    assert rc == 1800
    u = unit_costs(p, rc)
    assert u.hub_total == 125                       # 100 + выдача 25
    assert u.mesh_fixed == 55                       # 6 + 16 + 0.8×10 + 25
    assert round(u.mesh_fixed + rc / p.capacity) == 95
    assert 25.7 < u.break_even < 25.8               # граница ≈ 26 заказов


def test_partial_run_below_break_even_goes_to_hub():
    p, u = Params(), unit_costs(Params())
    r = plan_runs(55, p, u, 1800)
    assert (r["runs"], r["mesh"], r["fallback"]) == (1, 45, 10)   # 10 < 26 → хаб
    r = plan_runs(80, p, u, 1800)
    assert (r["runs"], r["mesh"], r["fallback"]) == (2, 80, 0)    # 35 > 26 → второй рейс
    assert plan_runs(20, p, u, 1800)["runs"] == 0


def test_longer_loop_needs_more_orders():
    p, u = Params(), unit_costs(Params())
    assert plan_runs(30, p, u, run_cost_for(p, 3, 4))["runs"] == 1    # 1 500 ₽ → граница ≈ 21
    assert plan_runs(30, p, u, run_cost_for(p, 15, 12))["runs"] == 0  # 2 940 ₽ → граница ≈ 42


def test_storage_after_30_days_is_paid_by_seller():
    assert unit_costs(Params(turnover=45)).mesh_fixed == unit_costs(Params(turnover=30)).mesh_fixed


def test_every_pvz_in_exactly_one_zone_within_radius():
    pts = [{"id": i, "lat": 56.63 + (i % 5) * 0.004, "lon": 47.88 + (i // 5) * 0.007, "weight": 1} for i in range(1, 21)]
    zones = build_zones(pts, 600)
    seen = [pid for z in zones for pid in z["pvz_ids"]]
    assert sorted(seen) == list(range(1, 21))
    pos = {p["id"]: (p["lat"], p["lon"]) for p in pts}
    for z in zones:
        for pid in z["pvz_ids"]:
            assert dist_m(pos[pid], pos[z["center_id"]]) <= 600
