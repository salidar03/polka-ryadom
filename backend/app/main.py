"""API «Полка рядом»: городские заказы Wildberries из ПВЗ в ПВЗ без хаба.

Документация API открывается на /docs. Разделы покупателя, ПВЗ и курьера
требуют входа: токен из /api/auth/login передаётся в заголовке
`Authorization: Bearer <токен>`.
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import service, supplier
from .auth import check_password, current_account, find_account, hash_password, issue_token, require, user_view
from .demand import city_demand_table, demand_data
from .db import Account, AuthToken, Courier, get_session, init_db, reset_demo
from .economics import Params
from .planner import build_plan, order_decisions, pvz_list


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Полка рядом API", version="0.4.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CITY_CENTER = (56.6345, 47.8925)  # Йошкар-Ола

buyer_only = require("buyer")
pvz_only = require("pvz")
courier_only = require("courier")
supplier_only = require("supplier")


# ================= Вход и регистрация =================

class RegisterRequest(BaseModel):
    role: str = Field(..., pattern="^(buyer|pvz|courier|supplier)$")
    name: str = Field(..., min_length=1, max_length=100)
    login: str = Field(..., min_length=3, max_length=60, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password: str = Field(..., min_length=4, max_length=100)
    address: str = Field("", max_length=300)   # для ПВЗ
    lat: float | None = None                   # для ПВЗ — точка на карте; для курьера — где начинает смену
    lon: float | None = None


@app.post("/api/auth/register")
def register(req: RegisterRequest, s: Session = Depends(get_session)):
    if find_account(s, req.login):
        raise HTTPException(409, "Такой логин уже занят")
    h, salt = hash_password(req.password)
    acc = Account(role=req.role, login=req.login.strip().lower(), name=req.name.strip(), pass_hash=h, salt=salt)
    if req.role == "pvz":
        if req.lat is None or req.lon is None:
            raise HTTPException(400, "Поставьте точку ПВЗ на карте")
        acc.pvz_id = service.register_pvz(s, req.name, req.address, req.lat, req.lon).id
    if req.role == "courier":
        # где курьер сейчас: точка с карты; если не отметил — центр города (потом поправит в «Где я сейчас»)
        lat, lon = (req.lat, req.lon) if req.lat is not None and req.lon is not None else CITY_CENTER
        if not service.in_city(lat, lon):
            raise HTTPException(400, "Точка вне Йошкар-Олы — отметьте, где вы, на карте города")
        c = Courier(name=req.name.strip(), lat=lat, lon=lon, home_lat=lat, home_lon=lon, busy=False)
        s.add(c)
        s.flush()
        acc.courier_id = c.id
    s.add(acc)
    s.flush()
    if req.role == "courier":
        service.dispatch_waiting(s)  # новый свободный курьер сразу получает заказ из очереди, если он есть
    token = issue_token(s, acc)
    return {"token": token, "user": user_view(s, acc)}


class LoginRequest(BaseModel):
    role: str = Field(..., pattern="^(buyer|pvz|courier|supplier)$")
    login: str
    password: str


@app.post("/api/auth/login")
def login(req: LoginRequest, s: Session = Depends(get_session)):
    acc = find_account(s, req.login)
    if acc is None or not check_password(req.password, acc):
        raise HTTPException(401, "Неверный логин или пароль")
    if acc.role != req.role:
        raise HTTPException(403, "Эта учётная запись другой роли — выберите правильную роль на первом экране")
    return {"token": issue_token(s, acc), "user": user_view(s, acc)}


@app.get("/api/auth/me")
def me(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return user_view(s, acc)


@app.post("/api/auth/logout")
def logout(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    s.query(AuthToken).filter(AuthToken.account_id == acc.id).delete()
    s.commit()
    return {"status": "ok"}


# ================= Покупатель =================

@app.get("/api/products")
def get_products(s: Session = Depends(get_session)):
    return service.products(s)


@app.get("/api/offers")
def get_offers(product_id: int, pvz_id: int, s: Session = Depends(get_session)):
    """Где взять товар: в выбранном ПВЗ, в ближайшем (дешевле, забрать сразу), в других — или через хаб."""
    return service.offers(s, product_id, pvz_id)


class OrderRequest(BaseModel):
    product_id: int
    buyer_pvz_id: int
    mode: str = Field(..., description="pickup — забрать самому со скидкой; deliver — доставить в свой ПВЗ; hub — через хаб")
    source_pvz_id: int | None = None


@app.post("/api/orders")
def post_order(req: OrderRequest, acc: Account = Depends(buyer_only), s: Session = Depends(get_session)):
    return service.create_order(s, acc, req.product_id, req.buyer_pvz_id, req.mode, req.source_pvz_id)


@app.get("/api/my/orders")
def get_my_orders(acc: Account = Depends(buyer_only), s: Session = Depends(get_session)):
    return service.my_orders(s, acc)


# ================= ПВЗ =================

@app.get("/api/pvz/me/dashboard")
def get_pvz_dashboard(acc: Account = Depends(pvz_only), s: Session = Depends(get_session)):
    return service.pvz_dashboard(s, acc.pvz_id)


class StockRequest(BaseModel):
    product_id: int
    qty: int = Field(..., ge=1, le=500)


@app.post("/api/pvz/me/stock")
def post_stock(req: StockRequest, acc: Account = Depends(pvz_only), s: Session = Depends(get_session)):
    return service.add_stock(s, acc.pvz_id, req.product_id, req.qty)


@app.delete("/api/pvz/me/stock/{product_id}")
def delete_stock(product_id: int, qty: int | None = Query(None, ge=1, le=500, description="сколько списать; без qty — убрать товар целиком"),
                 acc: Account = Depends(pvz_only), s: Session = Depends(get_session)):
    """Списать часть товара с полки или убрать его с полки полностью."""
    return service.remove_stock(s, acc.pvz_id, product_id, qty)


@app.post("/api/orders/{order_id}/assembled")
def post_assembled(order_id: int, acc: Account = Depends(pvz_only), s: Session = Depends(get_session)):
    """ПВЗ подтвердил сбор заказа → назначается ближайший свободный курьер."""
    return service.confirm_assembled(s, acc.pvz_id, order_id)


class IssueRequest(BaseModel):
    code: str


@app.post("/api/orders/{order_id}/issued")
def post_issued(order_id: int, req: IssueRequest, acc: Account = Depends(pvz_only), s: Session = Depends(get_session)):
    return service.issue_order(s, acc.pvz_id, order_id, req.code)


# ================= Курьер =================

@app.get("/api/couriers")
def get_couriers(s: Session = Depends(get_session)):
    return service.couriers(s)


@app.get("/api/courier/me/dashboard")
def get_courier_dashboard(acc: Account = Depends(courier_only), s: Session = Depends(get_session)):
    return service.courier_dashboard(s, acc.courier_id)


class LocationRequest(BaseModel):
    lat: float
    lon: float


@app.post("/api/courier/me/location")
def post_location(req: LocationRequest, acc: Account = Depends(courier_only), s: Session = Depends(get_session)):
    """«Где я сейчас»: курьер отмечает своё место на карте или по геолокации."""
    return service.set_courier_location(s, acc.courier_id, req.lat, req.lon)


@app.post("/api/orders/{order_id}/picked")
def post_picked(order_id: int, acc: Account = Depends(courier_only), s: Session = Depends(get_session)):
    return service.courier_picked(s, acc.courier_id, order_id)


@app.post("/api/orders/{order_id}/delivered")
def post_delivered(order_id: int, acc: Account = Depends(courier_only), s: Session = Depends(get_session)):
    return service.courier_delivered(s, acc.courier_id, order_id)


# ================= Поставщик =================

@app.get("/api/demand")
def get_demand():
    """Примерный спрос базового города по категориям, список городов и допущения."""
    d = demand_data()
    return {"city": city_demand_table(), "categories": d["categories"],
            "cities": [{"name": c["name"], "population": c["population"], "lat": c["lat"], "lon": c["lon"],
                        "pvz_network": c["pvz_network"]} for c in d["cities"]]}


class SupplyPlanRequest(BaseModel):
    name: str = Field("", max_length=200)
    category: str = "other"
    price: int = Field(..., ge=10, le=1_000_000)
    qty: int = Field(..., ge=1, le=100_000)
    share: float = Field(0.15, ge=0.05, le=0.3, description="какую часть партии разложить по полкам ПВЗ городов")
    horizon_days: int = Field(14, ge=7, le=30, description="на сколько дней спроса везти на полки")
    accept_plan: bool = True


@app.post("/api/supplier/plan")
def post_supplier_plan(req: SupplyPlanRequest, acc: Account = Depends(supplier_only), s: Session = Depends(get_session)):
    """Предложение: сколько штук в какой город и ПВЗ по спросу (ничего не сохраняет)."""
    return supplier.preview(s, req.category, req.qty, req.share, req.horizon_days)


@app.post("/api/supplier/products")
def post_supplier_product(req: SupplyPlanRequest, acc: Account = Depends(supplier_only), s: Session = Depends(get_session)):
    """Загрузить товар: создать его и поставки — по плану или всю партию на склад WB."""
    return supplier.upload(s, acc, req.name, req.category, req.price, req.qty, req.share, req.horizon_days, req.accept_plan)


@app.get("/api/supplier/me/dashboard")
def get_supplier_dashboard(acc: Account = Depends(supplier_only), s: Session = Depends(get_session)):
    return supplier.dashboard(s, acc)


@app.post("/api/supplies/{supply_id}/accept")
def post_accept_supply(supply_id: int, acc: Account = Depends(pvz_only), s: Session = Depends(get_session)):
    """ПВЗ принял поставку от поставщика на полку."""
    return supplier.accept_supply(s, acc.pvz_id, supply_id)


@app.post("/api/demo/reset")
def post_reset():
    """Сбросить демо: заказы и уведомления удаляются, полки и курьеры — как в начале. Аккаунты остаются."""
    reset_demo()
    return {"status": "ok"}


# ================= Аналитика Wildberries: районы, рейсы, экономика =================

def params(
    orders: int = Query(1000, ge=50, le=20000, description="Городских заказов в день"),
    radius_m: int = Query(700, ge=300, le=2000, description="Радиус района, м"),
    run_cost: float = Query(900, ge=0, le=10000, description="База рейса (машина и водитель), ₽"),
    stop_cost: float = Query(120, ge=0, le=1000, description="Рейс: ₽ за остановку в ПВЗ"),
    km_cost: float = Query(40, ge=0, le=300, description="Рейс: ₽ за километр по дорогам"),
    capacity: int = Query(45, ge=5, le=200, description="Вместимость рейса"),
    hub_cost: float = Query(100, ge=10, le=400, description="Хаб, магистраль и плечи, ₽"),
    issue: float = Query(25, ge=0, le=100, description="Выдача, ₽"),
    pvz_fee: float = Query(16, ge=0, le=100, description="ПВЗ за приёмку и сборку, ₽"),
    storage: float = Query(0.8, ge=0, le=10, description="Хранение, ₽ в день"),
    turnover: float = Query(10, ge=1, le=90, description="Дней на полке"),
    restock: float = Query(6, ge=0, le=50, description="Пополнение полки, ₽"),
    share_other_city: float = Query(0.15, ge=0, le=1),
    share_bulky: float = Query(0.05, ge=0, le=1),
    share_no_stock: float = Query(0.10, ge=0, le=1),
    seed: int = Query(42),
) -> Params:
    return Params(orders=orders, radius_m=radius_m, run_cost=run_cost, stop_cost=stop_cost, km_cost=km_cost, capacity=capacity,
                  hub_cost=hub_cost, issue=issue, pvz_fee=pvz_fee, storage=storage, turnover=turnover, restock=restock,
                  share_other_city=share_other_city, share_bulky=share_bulky, share_no_stock=share_no_stock, seed=seed)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/pvz")
def get_pvz(s: Session = Depends(get_session)):
    return pvz_list(s)


@app.get("/api/plan")
def get_plan(p: Params = Depends(params), s: Session = Depends(get_session)):
    return build_plan(s, p)


@app.get("/api/plan/orders")
def get_plan_orders(zone_id: int | None = None, limit: int = Query(200, ge=1, le=5000),
                    p: Params = Depends(params), s: Session = Depends(get_session)):
    """Заказы тестового набора: куда поехал каждый и почему."""
    return order_decisions(s, p, zone_id, limit)


@app.get("/api/optimize")
def optimize_radius(p: Params = Depends(params), s: Session = Depends(get_session)):
    """Подбирает радиус района с максимальной экономией: перебор 400–1500 м."""
    table = []
    for r in range(400, 1501, 100):
        p.radius_m = r
        t = build_plan(s, p)["totals"]
        table.append({"radius_m": r, "zones": t["zones"], "zones_active": t["zones_active"],
                      "saving_day": t["saving_day"], "saving_pct": t["saving_pct"]})
    best = max(table, key=lambda x: x["saving_day"])
    return {"best_radius_m": best["radius_m"], "table": table}


class RouteRequest(BaseModel):
    pvz_id: int
    other_city: bool = False
    bulky: bool = False
    on_shelf: bool = True


@app.post("/api/route")
def route_order(req: RouteRequest, p: Params = Depends(params), s: Session = Depends(get_session)):
    """Куда поедет заказ по экономике района: решение маршрутизатора с причиной."""
    plan = build_plan(s, p)
    zone = next((z for z in plan["zones"] if req.pvz_id in z["pvz_ids"]), None)
    if zone is None:
        raise HTTPException(404, "Нет такого ПВЗ")
    if req.other_city:
        return {"route": "hub", "zone_id": zone["id"], "reason": "продавец в другом городе — едет через хаб"}
    if req.bulky:
        return {"route": "hub", "zone_id": zone["id"], "reason": "крупногабарит — едет через хаб"}
    if not req.on_shelf:
        return {"route": "hub", "zone_id": zone["id"], "reason": "товара нет на полке района — едет через хаб"}
    if zone["runs"] == 0:
        return {"route": "hub", "zone_id": zone["id"], "reason": "в районе мало заказов для рейса — едет через хаб"}
    return {"route": "mesh", "zone_id": zone["id"], "center_id": zone["center_id"],
            "reason": f"с полки опорного ПВЗ №{zone['center_id']} рейсом ПВЗ → ПВЗ"}
