"""Полный путь заказа через API с входом по ролям: покупатель → ПВЗ → курьер → выдача."""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "flow.db").replace("\\", "/")
os.environ["POLKA_ROUTING"] = "off"  # без сети: маршрут по прямой с поправкой на дороги

from fastapi.testclient import TestClient  # noqa: E402

from app.geo import dist_m  # noqa: E402
from app.main import app  # noqa: E402


def login(c, role, user, password="1234"):
    r = c.post("/api/auth/login", json={"role": role, "login": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def find_offer(c, product_id):
    """ПВЗ покупателя, где товара нет, но он есть рядом."""
    for p in c.get("/api/pvz").json():
        o = c.get(f"/api/offers?product_id={product_id}&pvz_id={p['id']}").json()
        if o["here"] is None and o["nearest"]:
            return p, o
    raise AssertionError("нет подходящего ПВЗ")


def test_login_rules():
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"role": "buyer", "login": "ivan", "password": "0000"}).status_code == 401
        assert c.post("/api/auth/login", json={"role": "pvz", "login": "ivan", "password": "1234"}).status_code == 403
        assert c.get("/api/my/orders").status_code == 401
        buyer = login(c, "buyer", "ivan")
        assert c.get("/api/pvz/me/dashboard", headers=buyer).status_code == 403


def test_full_delivery_flow_with_courier_pay():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        buyer = login(c, "buyer", "ivan")
        prod = next(p for p in c.get("/api/products").json() if p["in_city"] > 0)
        buyer_pvz, offer = find_offer(c, prod["id"])
        src = offer["nearest"]
        assert offer["pickup_price"] == prod["price"] - offer["discount"] and 20 <= offer["discount"] <= 60

        order = c.post("/api/orders", headers=buyer, json={"product_id": prod["id"], "buyer_pvz_id": buyer_pvz["id"],
                                                           "mode": "deliver", "source_pvz_id": src["id"]}).json()
        assert order["status"] == "assembling" and order["paid"] == prod["price"]

        pvz_src = login(c, "pvz", f"pvz{src['id']}")
        dash = c.get("/api/pvz/me/dashboard", headers=pvz_src).json()
        assert dash["to_assemble"][0]["id"] == order["id"] and "соберите" in dash["notifications"][0]["text"]
        # чужой ПВЗ не может подтвердить сбор
        other = login(c, "pvz", f"pvz{buyer_pvz['id']}")
        assert c.post(f"/api/orders/{order['id']}/assembled", headers=other).status_code == 403

        # курьеров ещё нет — собранный заказ ждёт в очереди
        assert c.get("/api/couriers").json() == []
        order = c.post(f"/api/orders/{order['id']}/assembled", headers=pvz_src).json()
        assert order["status"] == "awaiting_courier"

        # зарегистрировался курьер — заказ из очереди сразу достаётся ему
        courier, cid = reg_courier(c, "far_c", 56.60, 47.95)
        task = c.get("/api/courier/me/dashboard", headers=courier).json()["tasks"][0]
        assert task["id"] == order["id"] and task["courier_pay"] >= 60 and task["route"]["delivery"]["path"]
        order = task
        assert c.post(f"/api/orders/{order['id']}/picked", headers=courier).json()["status"] == "in_transit"
        assert c.post(f"/api/orders/{order['id']}/delivered", headers=courier).json()["status"] == "delivered"
        earn = c.get("/api/courier/me/dashboard", headers=courier).json()["earnings"]
        assert earn["deliveries"] == 1 and earn["today"] == order["courier_pay"]

        dest = login(c, "pvz", f"pvz{buyer_pvz['id']}")
        assert c.post(f"/api/orders/{order['id']}/issued", headers=dest, json={"code": "0000"}).status_code == 400
        assert c.post(f"/api/orders/{order['id']}/issued", headers=dest, json={"code": order["code"]}).json()["status"] == "issued"
        assert c.get("/api/my/orders", headers=buyer).json()[0]["status"] == "issued"

        # «Где я сейчас»: второй курьер отмечает место у ПВЗ с товаром — и следующий заказ достаётся ему, а не дальнему
        near, near_id = reg_courier(c, "near_c")
        loc = c.post("/api/courier/me/location", headers=near, json={"lat": src["lat"] + 0.0004, "lon": src["lon"]}).json()
        assert loc["near"]["id"] == src["id"]
        assert c.post("/api/courier/me/location", headers=near, json={"lat": 55.75, "lon": 37.6}).status_code == 400
        order2 = c.post("/api/orders", headers=buyer, json={"product_id": prod["id"], "buyer_pvz_id": buyer_pvz["id"],
                                                            "mode": "deliver", "source_pvz_id": src["id"]}).json()
        couriers_before = {x["id"]: x for x in c.get("/api/couriers").json()}
        order2 = c.post(f"/api/orders/{order2['id']}/assembled", headers=pvz_src).json()
        assert order2["courier"]["id"] == near_id
        free = [x for x in couriers_before.values() if not x["busy"]]
        assert min(free, key=lambda x: dist_m((x["lat"], x["lon"]), (src["lat"], src["lon"])))["id"] == near_id


def reg_courier(c, login_, lat=None, lon=None):
    body = {"role": "courier", "name": login_, "login": login_, "password": "secret"}
    if lat is not None:
        body.update(lat=lat, lon=lon)
    r = c.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["courier"]["id"]


def test_pickup_gets_discount_and_reserves_stock():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        buyer = login(c, "buyer", "maria")
        prod = next(p for p in c.get("/api/products").json() if p["in_city"] > 0)
        buyer_pvz, offer = find_offer(c, prod["id"])
        src = offer["nearest"]
        o = c.post("/api/orders", headers=buyer, json={"product_id": prod["id"], "buyer_pvz_id": buyer_pvz["id"],
                                                       "mode": "pickup", "source_pvz_id": src["id"]}).json()
        assert o["status"] == "ready" and o["dest"]["id"] == src["id"] and o["paid"] == offer["pickup_price"]
        stock = {x["id"]: x["qty"] for x in c.get("/api/pvz/me/dashboard", headers=login(c, "pvz", f"pvz{src['id']}")).json()["stock"]}
        assert stock[prod["id"]] == src["qty"] - 1


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
