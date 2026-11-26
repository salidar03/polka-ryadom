"""Поставщик: загрузка товара, распределение части партии по городам и ПВЗ, приёмка, продажа."""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "supplier.db").replace("\\", "/")
os.environ["POLKA_ROUTING"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

import json  # noqa: E402
from itertools import combinations  # noqa: E402

from app.db import DATA_DIR  # noqa: E402
from app.demand import city_demand_table, plan_distribution, spread_over_pvz  # noqa: E402
from app.geo import dist_m  # noqa: E402
from app.main import app  # noqa: E402


def auth(c, body):
    r = c.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_city_demand_is_consistent():
    t = city_demand_table()
    assert t["city"] == "Йошкар-Ола" and t["items_per_day"] == 28100
    assert abs(sum(r["share"] for r in t["rows"]) - 1) < 1e-9


def test_plan_small_share_by_demand():
    pts = [{"id": i, "name": f"П{i}", "lat": 56.62 + (i % 6) * 0.006, "lon": 47.86 + (i // 6) * 0.01, "weight": 10 + i} for i in range(1, 37)]
    p = plan_distribution(pts, "electronics", 1000, 0.15, 14)
    assert p["on_shelves"] <= 150 and p["to_warehouse"] == 1000 - p["on_shelves"]
    big = {r["city"]: r for r in p["cities"]}
    assert big["Казань"]["qty"] > big["Йошкар-Ола"]["qty"] > 0               # больше жителей — больше штук
    assert all(r["qty"] <= r["forecast"] for r in p["cities"])                 # не больше спроса за срок полки
    assert sum(x["qty"] for x in big["Йошкар-Ола"]["pvz"]) == big["Йошкар-Ола"]["qty"]
    assert all(x["qty"] >= 2 for x in big["Йошкар-Ола"]["pvz"])
    for x, y in combinations(big["Йошкар-Ола"]["pvz"], 2):                     # товар не повторяется ближе 700 м
        assert dist_m((x["lat"], x["lon"]), (y["lat"], y["lon"])) >= 700


def test_spacing_on_real_city():
    pts = json.loads((DATA_DIR / "pvz.json").read_text(encoding="utf-8"))
    out = spread_over_pvz(pts, 60)
    assert sum(x["qty"] for x in out) == 60 and len(out) >= 10
    assert all(x["qty"] >= 2 for x in out)
    assert min(dist_m((x["lat"], x["lon"]), (y["lat"], y["lon"])) for x, y in combinations(out, 2)) >= 700
    assert all(x["nearest_same_m"] >= 700 for x in out)


def test_supplier_flow_upload_accept_buy():
    with TestClient(app) as c:
        sup = auth(c, {"role": "supplier", "name": "ООО Звук", "login": "zvuk", "password": "secret"})
        assert c.post("/api/supplier/plan", headers={}, json={"category": "electronics", "price": 990, "qty": 100}).status_code == 401
        plan = c.post("/api/supplier/plan", headers=sup, json={"category": "electronics", "price": 990, "qty": 800}).json()
        assert plan["on_shelves"] > 0
        res = c.post("/api/supplier/products", headers=sup, json={"name": "Колонка мини", "category": "electronics",
                                                                   "price": 990, "qty": 800}).json()
        pid = res["product_id"]
        item = res["dashboard"]["products"][0]
        assert item["total"] == 800 and item["to_pvz"] + item["to_cities"] + item["to_warehouse"] == 800 and item["on_shelf_now"] == 0

        # ПВЗ видит приёмку и принимает её на полку
        first = res["plan"]["cities"][0]["pvz"][0]
        pvz = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"role": "pvz", "login": f"pvz{first['id']}", "password": "1234"}).json()["token"]}
        dash = c.get("/api/pvz/me/dashboard", headers=pvz).json()
        supply = next(x for x in dash["supplies"] if x["product"]["id"] == pid)
        assert supply["qty"] == first["qty"] and supply["supplier"] == "ООО Звук"
        assert c.post(f"/api/supplies/{supply['id']}/accept", headers=pvz).json()["qty"] == first["qty"]
        assert c.post(f"/api/supplies/{supply['id']}/accept", headers=pvz).status_code == 409

        # товар поставщика в каталоге и его можно купить с полки
        prod = next(p for p in c.get("/api/products").json() if p["id"] == pid)
        assert prod["supplier"] == "ООО Звук" and prod["in_city"] == first["qty"]
        buyer = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"role": "buyer", "login": "ivan", "password": "1234"}).json()["token"]}
        o = c.post("/api/orders", headers=buyer, json={"product_id": pid, "buyer_pvz_id": first["id"], "mode": "pickup", "source_pvz_id": first["id"]}).json()
        assert o["status"] == "ready"
        item = c.get("/api/supplier/me/dashboard", headers=sup).json()["products"][0]
        assert item["sold"] == 1 and item["revenue"] == o["paid"] and item["accepted"] == first["qty"]


def test_upload_without_plan_goes_to_warehouse():
    with TestClient(app) as c:
        sup = auth(c, {"role": "supplier", "name": "ИП Петров", "login": "petrov_sup", "password": "secret"})
        res = c.post("/api/supplier/products", headers=sup, json={"name": "Плед", "category": "home", "price": 1500,
                                                                   "qty": 200, "accept_plan": False}).json()
        item = res["dashboard"]["products"][0]
        assert item["to_warehouse"] == 200 and item["to_pvz"] == 0 and res["plan"] is None


def test_same_product_from_other_seller_is_offered():
    with TestClient(app) as c:
        a = auth(c, {"role": "supplier", "name": "ООО Свет", "login": "svet", "password": "secret"})
        b = auth(c, {"role": "supplier", "name": "ИП Лампов", "login": "lampov", "password": "secret"})
        res_a = c.post("/api/supplier/products", headers=a, json={"name": "Настольная лампа LED", "category": "home",
                                                                 "price": 1200, "qty": 300, "accept_plan": False}).json()
        # второй продавец видит конкурента и метку про раскладку по ПВЗ
        plan = c.post("/api/supplier/plan", headers=b, json={"name": "Лампа настольная LED", "category": "home",
                                                             "price": 1100, "qty": 600}).json()
        assert [x["name"] for x in plan["competitors"]] == ["Настольная лампа LED"] and "ваш товар" in plan["badge"]
        res_b = c.post("/api/supplier/products", headers=b, json={"name": "Лампа настольная LED", "category": "home",
                                                                 "price": 1100, "qty": 600}).json()
        spot = res_b["plan"]["cities"][0]["pvz"][0]
        pvz = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"role": "pvz", "login": f"pvz{spot['id']}", "password": "1234"}).json()["token"]}
        sup = next(x for x in c.get("/api/pvz/me/dashboard", headers=pvz).json()["supplies"] if x["product"]["id"] == res_b["product_id"])
        c.post(f"/api/supplies/{sup['id']}/accept", headers=pvz)

        # покупатель смотрит лампу первого продавца — её нет на полках, а такая же лежит в ПВЗ рядом
        offer = c.get(f"/api/offers?product_id={res_a['product_id']}&pvz_id={spot['id']}").json()
        assert offer["nearest"] is None
        alt = offer["alternatives"][0]
        assert alt["product"]["id"] == res_b["product_id"] and alt["here"] and alt["pickup_price"] < 1100
        # другая категория или тот же продавец — не «такой же товар»
        assert c.get(f"/api/offers?product_id=9&pvz_id={spot['id']}").json()["alternatives"] == []
        item = next(x for x in c.get("/api/supplier/me/dashboard", headers=b).json()["products"] if x["id"] == res_b["product_id"])
        assert item["competitors"] == 1 and item["shelf_points"] >= 1
