"""Полный путь заказа через API с входом по ролям: покупатель → ПВЗ упаковал → рейс машины → выдача."""
import os
import tempfile
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "flow.db").replace("\\", "/")
os.environ["POLKA_ROUTING"] = "off"  # без сети: маршрут по прямой с поправкой на дороги

from fastapi.testclient import TestClient  # noqa: E402

from app import runs  # noqa: E402
from app.db import Order, SessionLocal  # noqa: E402
from app.geo import dist_m  # noqa: E402
from app.main import app  # noqa: E402


def login(c, role, user, password="1234"):
    r = c.post("/api/auth/login", json={"role": role, "login": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def reg_courier(c, login_, lat=None, lon=None):
    body = {"role": "courier", "name": login_, "login": login_, "password": "secret"}
    if lat is not None:
        body.update(lat=lat, lon=lon)
    r = c.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["courier"]["id"]


def d(a, b):
    return dist_m((a["lat"], a["lon"]), (b["lat"], b["lon"]))


def pick_points(c):
    """A — ПВЗ с товаром, B и C — ПВЗ покупателей в пересекающихся кругах рядом с A, F и G — далеко от них."""
    pts = [p for p in c.get("/api/pvz").json() if p["source"] != "registered"]
    for a in pts:
        near = sorted((p for p in pts if p["id"] != a["id"] and 400 < d(a, p) < 1300), key=lambda p: d(a, p))
        if len(near) < 2:
            continue
        b, cc = near[0], near[1]
        far = [p for p in pts if min(d(p, a), d(p, b), d(p, cc)) > 3000]
        for f in far:
            g = next((p for p in far if p["id"] != f["id"] and 400 < d(f, p) < 1300), None)
            if g:
                return a, b, cc, f, g
    raise AssertionError("не нашли подходящие ПВЗ")


def put_on_shelf(c, pvz, product_id, qty=5):
    h = login(c, "pvz", f"pvz{pvz['id']}")
    assert c.post("/api/pvz/me/stock", headers=h, json={"product_id": product_id, "qty": qty}).status_code == 200
    return h


def order(c, buyer, product_id, buyer_pvz, src, mode="deliver"):
    r = c.post("/api/orders", headers=buyer, json={"product_id": product_id, "buyer_pvz_id": buyer_pvz["id"],
                                                   "mode": mode, "source_pvz_id": src["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_login_rules():
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"role": "buyer", "login": "ivan", "password": "0000"}).status_code == 401
        assert c.post("/api/auth/login", json={"role": "pvz", "login": "ivan", "password": "1234"}).status_code == 403
        assert c.get("/api/my/orders").status_code == 401
        buyer = login(c, "buyer", "ivan")
        assert c.get("/api/pvz/me/dashboard", headers=buyer).status_code == 403


def test_shelves_start_empty():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        assert all(p["in_city"] == 0 for p in c.get("/api/products").json())


def test_run_picks_orders_in_overlapping_circles():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, b, cc, f, g = pick_points(c)
        buyer = login(c, "buyer", "ivan")
        pvz_a = put_on_shelf(c, a, 1)
        pvz_f = put_on_shelf(c, f, 2)

        offer = c.get(f"/api/offers?product_id=1&pvz_id={b['id']}").json()
        assert offer["here"] is None and offer["nearest"]["id"] == a["id"] and offer["next_run"]
        assert 20 <= offer["discount"] <= 60

        o1 = order(c, buyer, 1, b, a)
        o2 = order(c, buyer, 1, cc, a)
        o3 = order(c, buyer, 2, g, f)
        assert o1["status"] == "assembling"
        # чужой ПВЗ не может подтвердить сбор
        assert c.post(f"/api/orders/{o1['id']}/assembled", headers=login(c, "pvz", f"pvz{b['id']}")).status_code == 403
        for o, h in ((o1, pvz_a), (o2, pvz_a), (o3, pvz_f)):
            r = c.post(f"/api/orders/{o['id']}/assembled", headers=h).json()
            assert r["status"] == "awaiting_run" and r["next_run"] and "рейсом" in r["status_label"]
        dash_a = c.get("/api/pvz/me/dashboard", headers=pvz_a).json()
        assert {x["id"] for x in dash_a["to_courier"]} == {o1["id"], o2["id"]}

        # водитель у точки A: ждёт рейса, видит упакованные заказы
        drv, drv_id = reg_courier(c, "drv1", a["lat"] + 0.0005, a["lon"])
        dash = c.get("/api/courier/me/dashboard", headers=drv).json()
        assert dash["run"] is None and len(dash["ready"]) == 3 and dash["schedule"].startswith("10:00")

        # «Выехать в рейс сейчас»: берёт заказы в пересекающихся кругах, дальний остаётся ждать
        run = c.post("/api/courier/me/run/start", headers=drv).json()
        ids = {x["id"] for st in run["stops"] for x in st["pickups"]}
        assert ids == {o1["id"], o2["id"]}
        assert run["stops"][0]["pvz"]["id"] == a["id"] and run["current"] == 0
        assert run["pay"] == round(900 + 120 * len(run["stops"]) + 40 * run["km"]) or abs(run["pay"] - (900 + 120 * len(run["stops"]) + 40 * run["km"])) <= 1
        assert run["route"]["path"]
        assert c.post("/api/courier/me/run/start", headers=drv).status_code == 409   # второй рейс, пока не закончен первый

        # выгрузить, не взяв с точки, нельзя
        drop_idx = next(st["index"] for st in run["stops"] if st["drops"])
        assert c.post(f"/api/runs/{run['id']}/stops/{drop_idx}/unloaded", headers=drv).status_code == 409
        for oid in (o1["id"], o2["id"]):
            run = c.post(f"/api/orders/{oid}/picked", headers=drv).json()
        assert c.post(f"/api/orders/{o1['id']}/picked", headers=drv).status_code == 409
        assert c.post(f"/api/orders/{o3['id']}/picked", headers=drv).status_code == 403    # не из этого рейса
        assert run["current"] == 1
        mine = {x["id"]: x for x in c.get("/api/my/orders", headers=buyer).json()}
        assert mine[o1["id"]]["status"] == "in_transit" and mine[o1["id"]]["run"]["id"] == run["id"]

        for st in run["stops"]:
            if st["drops"]:
                run = c.post(f"/api/runs/{run['id']}/stops/{st['index']}/unloaded", headers=drv).json()
        assert run["status"] == "done"
        dash = c.get("/api/courier/me/dashboard", headers=drv).json()
        assert dash["run"] is None and dash["earnings"]["runs"] == 1 and dash["earnings"]["today"] == run["pay"]
        assert dash["earnings"]["orders"] == 2 and [x["id"] for x in dash["ready"]] == [o3["id"]]

        pvz_b = login(c, "pvz", f"pvz{b['id']}")
        assert c.post(f"/api/orders/{o1['id']}/issued", headers=pvz_b, json={"code": "0000"}).status_code == 400
        assert c.post(f"/api/orders/{o1['id']}/issued", headers=pvz_b, json={"code": o1["code"]}).json()["status"] == "issued"

        # второй водитель забирает дальний заказ
        drv2, _ = reg_courier(c, "drv2", f["lat"], f["lon"] + 0.0005)
        run2 = c.post("/api/courier/me/run/start", headers=drv2).json()
        assert [x["id"] for st in run2["stops"] for x in st["pickups"]] == [o3["id"]]
        assert c.post("/api/courier/me/run/start", headers=drv).status_code == 409   # готовых заказов больше нет


def test_runs_leave_on_schedule():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, b, *_ = pick_points(c)
        buyer = login(c, "buyer", "maria")
        pvz_a = put_on_shelf(c, a, 3)
        o = order(c, buyer, 3, b, a)
        c.post(f"/api/orders/{o['id']}/assembled", headers=pvz_a)
        reg_courier(c, "sched_drv", a["lat"], a["lon"])
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        with SessionLocal() as s:
            s.get(Order, o["id"]).ready_at = today + timedelta(hours=11)    # упакован в 11:00
            s.commit()
            runs.auto_form(s, today + timedelta(hours=12, minutes=30))      # 12:30 — рейса ещё не было
            assert s.get(Order, o["id"]).status == "awaiting_run"
            runs.auto_form(s, today + timedelta(hours=13, minutes=1))       # 13:01 — уехал рейсом 13:00
            got = s.get(Order, o["id"])
            assert got.status == "in_run" and got.run_id


def test_stops_pick_before_drop():
    class O:
        def __init__(self, i, src, dst):
            self.id, self.source_pvz_id, self.dest_pvz_id = i, src, dst

    pos = {1: (56.630, 47.880), 2: (56.636, 47.880), 3: (56.642, 47.880)}
    # заказ 1: 3 → 1, заказ 2: 1 → 2. Водитель стоит у точки 1
    stops = runs.plan_stops((56.6299, 47.880), [O(1, 3, 1), O(2, 1, 2)], pos)
    seen = set()
    for st in stops:
        assert all(oid in seen for oid in st["drops"])   # выгружаем только то, что уже взяли
        seen |= set(st["pickups"])
    assert [st["pvz_id"] for st in stops][0] == 1 and seen == {1, 2}


def test_pickup_gets_discount_and_reserves_stock():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, b, *_ = pick_points(c)
        put_on_shelf(c, a, 4, qty=3)
        buyer = login(c, "buyer", "maria")
        offer = c.get(f"/api/offers?product_id=4&pvz_id={b['id']}").json()
        o = order(c, buyer, 4, b, a, mode="pickup")
        assert o["status"] == "ready" and o["dest"]["id"] == a["id"] and o["paid"] == offer["pickup_price"]
        stock = {x["id"]: x["qty"] for x in c.get("/api/pvz/me/dashboard", headers=login(c, "pvz", f"pvz{a['id']}")).json()["stock"]}
        assert stock[4] == 2


def test_courier_location():
    with TestClient(app) as c:
        h, _ = reg_courier(c, "loc_drv")
        loc = c.post("/api/courier/me/location", headers=h, json={"lat": 56.6345, "lon": 47.8925}).json()
        assert loc["near"]["id"]
        assert c.post("/api/courier/me/location", headers=h, json={"lat": 55.75, "lon": 37.6}).status_code == 400


def test_register_pvz_courier_buyer():
    with TestClient(app) as c:
        r = c.post("/api/auth/register", json={"role": "pvz", "name": "Мой ПВЗ", "login": "my_pvz", "password": "secret",
                                               "address": "ул. Тестовая, 1", "lat": 56.63, "lon": 47.9}).json()
        assert r["user"]["pvz"]["address"] == "ул. Тестовая, 1"
        h = {"Authorization": f"Bearer {r['token']}"}
        assert c.post("/api/pvz/me/stock", headers=h, json={"product_id": 1, "qty": 5}).json()["qty"] == 5
        # списать одну штуку, потом убрать товар с полки целиком
        assert c.delete("/api/pvz/me/stock/1?qty=1", headers=h).json()["qty"] == 4
        assert c.delete("/api/pvz/me/stock/1", headers=h).json()["removed"] is True
        assert all(x["id"] != 1 for x in c.get("/api/pvz/me/dashboard", headers=h).json()["stock"])
        assert c.delete("/api/pvz/me/stock/1", headers=h).status_code == 404
        assert c.post("/api/auth/register", json={"role": "pvz", "name": "Далеко", "login": "far_pvz", "password": "secret",
                                                  "lat": 55.75, "lon": 37.6}).status_code == 400
        assert c.post("/api/auth/register", json={"role": "buyer", "name": "Пётр", "login": "my_pvz", "password": "secret"}).status_code == 409
        cr = c.post("/api/auth/register", json={"role": "courier", "name": "Пётр", "login": "petr_c", "password": "secret"}).json()
        assert cr["user"]["courier"]["name"] == "Пётр"
