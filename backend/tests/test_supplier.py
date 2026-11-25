"""Поставщик: загрузка товара, распределение части партии по городам и ПВЗ, приёмка, продажа."""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "supplier.db").replace("\\", "/")
os.environ["POLKA_ROUTING"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

from app.demand import city_demand_table, plan_distribution  # noqa: E402
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
