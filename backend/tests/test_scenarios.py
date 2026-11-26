"""Деньги и нештатные сценарии: начисления ПВЗ и продавцу, возврат, «товара нет», долгое хранение, хаб, лимит полки."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "scen.db").replace("\\", "/"))
os.environ["POLKA_ROUTING"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

from app import finance as fin  # noqa: E402
from app.geo import dist_m  # noqa: E402
from app.main import app  # noqa: E402

N = 0


def h(token):
    return {"Authorization": f"Bearer {token}"}


def login(c, role, user, password="1234"):
    r = c.post("/api/auth/login", json={"role": role, "login": user, "password": password})
    assert r.status_code == 200, r.text
    return h(r.json()["token"])


def register(c, role, **kw):
    global N
    N += 1
    body = {"role": role, "login": f"{role}_sc{N}", "name": kw.pop("name", f"{role} {N}"), "password": "secret", **kw}
    r = c.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    return h(r.json()["token"])


def near_pair(c):
    pts = [p for p in c.get("/api/pvz").json() if p["source"] != "registered"]
    for a in pts:
        for b in pts:
            if a["id"] != b["id"] and 500 < dist_m((a["lat"], a["lon"]), (b["lat"], b["lon"])) < 1200:
                return a, b
    raise AssertionError


def supplier_on_shelf(c, pvz, name="Термокружка 450 мл", price=1000, qty=None):
    """Поставщик загружает товар, одна поставка принимается в ПВЗ pvz (или ПВЗ сам ставит товар)."""
    sup = register(c, "supplier", name="ООО Полка")
    r = c.post("/api/supplier/products", headers=sup, json={"name": name, "category": "home", "price": price,
                                                            "qty": 100, "accept_plan": False}).json()
    pid = r["product_id"]
    p = login(c, "pvz", f"pvz{pvz['id']}")
    assert c.post("/api/pvz/me/stock", headers=p, json={"product_id": pid, "qty": qty or 5}).status_code == 200
    return sup, p, pid


def finance(c, headers, role):
    path = "/api/pvz/me/dashboard" if role == "pvz" else "/api/supplier/me/dashboard"
    return c.get(path, headers=headers).json()["finance"]


def test_order_money_flow_pvz_and_seller():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, b = near_pair(c)
        sup, pvz_a, pid = supplier_on_shelf(c, a)
        buyer = login(c, "buyer", "ivan")
        o = c.post("/api/orders", headers=buyer, json={"product_id": pid, "buyer_pvz_id": b["id"], "mode": "deliver",
                                                       "source_pvz_id": a["id"]}).json()
        c.post(f"/api/orders/{o['id']}/assembled", headers=pvz_a)
        assert finance(c, pvz_a, "pvz")["total"] == fin.PVZ_ASSEMBLY

        drv = register(c, "courier", lat=a["lat"], lon=a["lon"])
        run = c.post("/api/courier/me/run/start", headers=drv).json()
        c.post(f"/api/orders/{o['id']}/picked", headers=drv)
        stop = next(st["index"] for st in run["stops"] if st["drops"])
        c.post(f"/api/runs/{run['id']}/stops/{stop}/unloaded", headers=drv)
        pvz_b = login(c, "pvz", f"pvz{b['id']}")
        c.post(f"/api/orders/{o['id']}/issued", headers=pvz_b, json={"code": o["code"]})
        assert finance(c, pvz_b, "pvz")["total"] == fin.PVZ_ISSUE

        e = c.get(f"/api/orders/{o['id']}/economics").json()
        assert e["run_share"] == run["pay"]                       # рейс вёз один заказ — вся оплата на нём
        assert e["logistics_cost"] == round(fin.SHELF_FIXED + run["pay"])
        sf = finance(c, sup, "seller")
        assert sf["total"] == 1000 - 200 - fin.SELLER_LOGISTICS_SHELF  # цена − комиссия 20% − логистика с полки
        assert sf["sales"][0]["payout"] == sf["total"]


def test_return_whole_item_goes_back_on_shelf():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, _ = near_pair(c)
        sup, pvz_a, pid = supplier_on_shelf(c, a, qty=1)
        buyer = login(c, "buyer", "maria")
        o = c.post("/api/orders", headers=buyer, json={"product_id": pid, "buyer_pvz_id": a["id"], "mode": "pickup",
                                                       "source_pvz_id": a["id"]}).json()
        c.post(f"/api/orders/{o['id']}/issued", headers=pvz_a, json={"code": o["code"]})
        assert c.post(f"/api/orders/{o['id']}/return/accept", headers=pvz_a, json={"condition": "ok"}).status_code == 409
        r = c.post(f"/api/orders/{o['id']}/return", headers=buyer, json={"reason": "ok"}).json()
        assert r["status"] == "return_requested"
        dash = c.get("/api/pvz/me/dashboard", headers=pvz_a).json()
        assert [x["id"] for x in dash["returns"]] == [o["id"]]
        r = c.post(f"/api/orders/{o['id']}/return/accept", headers=pvz_a, json={"condition": "ok"}).json()
        assert r["status"] == "returned"
        dash = c.get("/api/pvz/me/dashboard", headers=pvz_a).json()
        assert next(x for x in dash["stock"] if x["id"] == pid)["qty"] == 1      # целый товар снова на полке
        assert dash["finance"]["total"] == fin.PVZ_ASSEMBLY + fin.PVZ_ISSUE + fin.PVZ_RETURN
        sf = finance(c, sup, "seller")
        assert sf["total"] == -fin.SELLER_LOGISTICS_SHELF                        # деньги и комиссия вернулись, логистика — нет


def test_missing_item_redirects_then_goes_via_hub():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, b = near_pair(c)
        sup, pvz_a, pid = supplier_on_shelf(c, a, qty=1)
        pvz_b = login(c, "pvz", f"pvz{b['id']}")
        c.post("/api/pvz/me/stock", headers=pvz_b, json={"product_id": pid, "qty": 1})
        pts = [p for p in c.get("/api/pvz").json() if p["id"] not in (a["id"], b["id"]) and p["source"] != "registered"]
        dest = min(pts, key=lambda p: dist_m((p["lat"], p["lon"]), (a["lat"], a["lon"])))
        buyer = login(c, "buyer", "ivan")
        o = c.post("/api/orders", headers=buyer, json={"product_id": pid, "buyer_pvz_id": dest["id"], "mode": "deliver",
                                                       "source_pvz_id": a["id"]}).json()
        r = c.post(f"/api/orders/{o['id']}/missing", headers=pvz_a).json()
        assert r["source"]["id"] == b["id"] and r["status"] == "assembling"
        assert finance(c, pvz_a, "pvz")["total"] == -fin.PVZ_PENALTY_MISSING
        r = c.post(f"/api/orders/{o['id']}/missing", headers=pvz_b).json()
        assert r["mode"] == "hub" and r["status"] == "via_hub" and "хаб" in r["history"][-1]["text"]
        # заказ пришёл с хаба — ПВЗ покупателя выдаёт; продавцу логистика по тарифу хаба
        pvz_d = login(c, "pvz", f"pvz{dest['id']}")
        assert [x["id"] for x in c.get("/api/pvz/me/dashboard", headers=pvz_d).json()["from_hub"]] == [o["id"]]
        assert c.post(f"/api/orders/{o['id']}/hub-arrived", headers=pvz_d).json()["status"] == "delivered"
        c.post(f"/api/orders/{o['id']}/issued", headers=pvz_d, json={"code": o["code"]})
        assert c.get(f"/api/orders/{o['id']}/economics").json()["seller_logistics"] == fin.SELLER_LOGISTICS_HUB


def test_long_storage_seller_pays_after_30_days_and_removal_on_45():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, _ = near_pair(c)
        sup, pvz_a, pid = supplier_on_shelf(c, a, qty=10)
        r = c.post("/api/demo/days", json={"days": 35}).json()
        assert r["storage_rub"] == round(0.8 * 10 * 35) and r["seller_late_rub"] == round(1.6 * 10 * 5)
        dash = c.get("/api/pvz/me/dashboard", headers=pvz_a).json()
        assert next(x for x in dash["stock"] if x["id"] == pid)["age"]["state"] == "late"
        assert finance(c, sup, "seller")["total"] == -round(1.6 * 10 * 5)
        r = c.post("/api/demo/days", json={"days": 15}).json()
        assert r["removed"] and r["removed"][0]["qty"] == 10
        dash = c.get("/api/pvz/me/dashboard", headers=pvz_a).json()
        assert all(x["id"] != pid for x in dash["stock"])
        out = dash["to_seller"][0]
        assert out["qty"] == 10 and c.post(f"/api/supplies/{out['id']}/handover", headers=pvz_a).json()["status"] == "accepted"
        # хранение ПВЗ — за все 45 дней, продавец — за дни с 31-го по 45-й
        assert round(dash["finance"]["total"]) == round(0.8 * 10 * 45)
        assert finance(c, sup, "seller")["total"] == -round(1.6 * 10 * 15)


def test_shelf_capacity_limit():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        a, _ = near_pair(c)
        p = login(c, "pvz", f"pvz{a['id']}")
        assert c.post("/api/pvz/me/stock", headers=p, json={"product_id": 1, "qty": 290}).status_code == 200
        r = c.post("/api/pvz/me/stock", headers=p, json={"product_id": 2, "qty": 20})
        assert r.status_code == 409 and "Полка заполнена" in r.json()["detail"]
        assert c.get("/api/pvz/me/dashboard", headers=p).json()["capacity"] == {"used": 290, "total": fin.PVZ_CAPACITY}
