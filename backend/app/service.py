"""Живой процесс: покупатель → ПВЗ с товаром → курьер → ПВЗ покупателя."""
import json
import random
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import Account, Courier, Notification, Order, Product, Pvz, Stock
from .geo import ROAD_FACTOR, dist_m
from .routing import road_route
from .supplier import incoming_for_pvz

WALK_KMH = 4.5
COURIER_BASE_PAY = 60      # ₽ за доставку: подъезд, погрузка, сдача в ПВЗ
COURIER_PAY_PER_KM = 20    # ₽ за километр от ПВЗ с товаром до ПВЗ покупателя

STATUS_LABEL = {
    "via_hub": "Едет через хаб — 2 дня",
    "ready": "Отложен в ПВЗ — можно забирать",
    "assembling": "ПВЗ собирает заказ",
    "awaiting_courier": "Собран, ищем курьера",
    "courier_assigned": "Курьер едет в ПВЗ за заказом",
    "in_transit": "В пути в ПВЗ покупателя",
    "delivered": "Доставлен — можно забирать",
    "issued": "Выдан",
}


def pickup_discount(price: int) -> int:
    """Скидка за самовывоз из ПВЗ, где товар уже лежит: курьер и рейс не нужны.
    Часть сэкономленной логистики: 7% цены, но не меньше 20 ₽ и не больше 60 ₽."""
    return int(min(60, max(20, round(price * 0.07 / 10) * 10)))


def _pt(x) -> tuple[float, float]:
    return (x.lat, x.lon)


def _km(m: float) -> str:
    """494 м / 1,2 км — по-русски, с запятой."""
    return f"{round(m)} м" if m < 1000 else f"{m / 1000:.1f} км".replace(".", ",")


def _walk_min(m: float) -> int:
    return max(1, round(m * ROAD_FACTOR / 1000 / WALK_KMH * 60))


def _get(s: Session, model, obj_id: int, what: str):
    obj = s.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"Не найден {what} №{obj_id}")
    return obj


def _log(order: Order, text: str) -> None:
    h = json.loads(order.history or "[]")
    h.append({"time": datetime.now().strftime("%H:%M:%S"), "text": text})
    order.history = json.dumps(h, ensure_ascii=False)


def _notify(s: Session, target: str, target_id: int, order_id: int | None, text: str) -> None:
    s.add(Notification(target=target, target_id=target_id, order_id=order_id, text=text))


def pvz_brief(p: Pvz | None) -> dict | None:
    if p is None:
        return None
    return {"id": p.id, "name": p.name, "address": p.address, "lat": p.lat, "lon": p.lon, "source": p.source}


def product_brief(pr: Product) -> dict:
    return {"id": pr.id, "name": pr.name, "price": pr.price, "emoji": pr.emoji, "pickup_discount": pickup_discount(pr.price),
            "category": pr.category, "supplier": pr.supplier_name}


# ---------- покупатель ----------

def products(s: Session) -> list[dict]:
    totals = dict(s.execute(select(Stock.product_id, func.sum(Stock.qty)).group_by(Stock.product_id)).all())
    return [{**product_brief(p), "in_city": int(totals.get(p.id, 0) or 0)} for p in s.scalars(select(Product).order_by(Product.id))]


def offers(s: Session, product_id: int, pvz_id: int) -> dict:
    """Где взять товар: в выбранном ПВЗ, в ближайшем (дешевле, забрать сразу), в других — или через хаб."""
    me = _get(s, Pvz, pvz_id, "ПВЗ")
    prod = _get(s, Product, product_id, "товар")
    rows = s.execute(select(Stock, Pvz).join(Pvz, Stock.pvz_id == Pvz.id)
                     .where(Stock.product_id == product_id, Stock.qty > 0)).all()
    options = []
    for st, p in rows:
        d = dist_m(_pt(me), _pt(p))
        options.append({**pvz_brief(p), "qty": st.qty, "distance_m": round(d), "walk_min": _walk_min(d)})
    options.sort(key=lambda o: o["distance_m"])
    here = next((o for o in options if o["id"] == pvz_id), None)
    disc = pickup_discount(prod.price)
    return {
        "product": product_brief(prod), "my_pvz": pvz_brief(me),
        "here": here, "nearest": here or (options[0] if options else None), "options": options,
        "price": prod.price, "pickup_price": prod.price - disc, "discount": disc,
        "deliver_days": 1, "hub_days": 2,
    }


def create_order(s: Session, buyer: Account, product_id: int, buyer_pvz_id: int, mode: str, source_pvz_id: int | None) -> dict:
    prod = _get(s, Product, product_id, "товар")
    buyer_pvz = _get(s, Pvz, buyer_pvz_id, "ПВЗ")
    code = f"{random.randint(1000, 9999)}"
    base = dict(product_id=prod.id, buyer_account_id=buyer.id, buyer_name=buyer.name, buyer_pvz_id=buyer_pvz.id, code=code, price=prod.price)

    if mode == "hub":
        o = Order(**base, source_pvz_id=None, dest_pvz_id=buyer_pvz.id, mode="hub", status="via_hub")
        s.add(o)
        s.flush()
        _log(o, "На полках города товара нет — заказ поехал по обычной схеме через хаб")
        s.commit()
        return order_view(s, o)

    if source_pvz_id is None:
        raise HTTPException(400, "Не указан ПВЗ, где лежит товар")
    src = _get(s, Pvz, source_pvz_id, "ПВЗ")
    st = s.get(Stock, {"pvz_id": src.id, "product_id": prod.id})
    if st is None or st.qty <= 0:
        raise HTTPException(409, f"В «{src.name}» товара «{prod.name}» уже нет — выберите другой ПВЗ")
    st.qty -= 1  # резервируем штуку на полке

    if mode == "pickup" or src.id == buyer_pvz.id:
        disc = pickup_discount(prod.price)
        o = Order(**base, source_pvz_id=src.id, dest_pvz_id=src.id, mode="pickup", status="ready", discount=disc,
                  distance_m=round(dist_m(_pt(buyer_pvz), _pt(src))))
        s.add(o)
        s.flush()
        _log(o, f"Самовывоз из «{src.name}» со скидкой {disc} ₽ — курьер не нужен. Код {code}")
        _notify(s, "pvz", src.id, o.id, f"Заказ №{o.id}: отложите «{prod.name}» — {buyer.name} заберёт сам. Код выдачи {code}")
        s.commit()
        return order_view(s, o)

    if mode != "deliver":
        raise HTTPException(400, "mode: pickup, deliver или hub")
    d = dist_m(_pt(src), _pt(buyer_pvz))
    o = Order(**base, source_pvz_id=src.id, dest_pvz_id=buyer_pvz.id, mode="deliver", status="assembling", distance_m=round(d))
    s.add(o)
    s.flush()
    _log(o, f"Заказ создан: «{prod.name}» с полки «{src.name}» в «{buyer_pvz.name}» ({_km(d)})")
    _notify(s, "pvz", src.id, o.id, f"Новый заказ №{o.id}: соберите «{prod.name}» для доставки в «{buyer_pvz.name}» ({_km(d)})")
    s.commit()
    return order_view(s, o)


def my_orders(s: Session, buyer: Account) -> list[dict]:
    rows = s.scalars(select(Order).where(Order.buyer_account_id == buyer.id).order_by(Order.id.desc()).limit(30))
    return [order_view(s, o, with_route=False) for o in rows]


# ---------- ПВЗ ----------

def register_pvz(s: Session, name: str, address: str, lat: float, lon: float) -> Pvz:
    if not in_city(lat, lon):
        raise HTTPException(400, "Точка вне Йошкар-Олы — поставьте ПВЗ на карте города")
    new_id = (s.scalar(select(func.max(Pvz.id))) or 0) + 1
    p = Pvz(id=new_id, name=name.strip()[:200] or f"ПВЗ №{new_id}", address=address.strip()[:300],
            lat=lat, lon=lon, source="registered", weight=10)
    s.add(p)
    s.flush()
    return p


def add_stock(s: Session, pvz_id: int, product_id: int, qty: int) -> dict:
    _get(s, Product, product_id, "товар")
    if qty <= 0 or qty > 500:
        raise HTTPException(400, "Количество — от 1 до 500")
    st = s.get(Stock, {"pvz_id": pvz_id, "product_id": product_id})
    if st is None:
        st = Stock(pvz_id=pvz_id, product_id=product_id, qty=0)
        s.add(st)
    st.qty += qty
    s.commit()
    return {"pvz_id": pvz_id, "product_id": product_id, "qty": st.qty}


def remove_stock(s: Session, pvz_id: int, product_id: int, qty: int | None) -> dict:
    """Списать qty штук с полки или, если qty не указан, убрать товар с полки целиком.
    Уже оформленные заказы не страдают: их штуки зарезервированы при заказе."""
    st = s.get(Stock, {"pvz_id": pvz_id, "product_id": product_id})
    if st is None:
        raise HTTPException(404, "Этого товара нет на полке")
    if qty is not None and qty <= 0:
        raise HTTPException(400, "Списать можно от 1 штуки")
    if qty is None or qty >= st.qty:
        s.delete(st)
        s.commit()
        return {"pvz_id": pvz_id, "product_id": product_id, "qty": 0, "removed": True}
    st.qty -= qty
    s.commit()
    return {"pvz_id": pvz_id, "product_id": product_id, "qty": st.qty, "removed": False}


def confirm_assembled(s: Session, pvz_id: int, order_id: int) -> dict:
    """ПВЗ подтвердил сбор заказа → система выбирает ближайшего свободного курьера."""
    o = _get(s, Order, order_id, "заказ")
    if o.source_pvz_id != pvz_id:
        raise HTTPException(403, "Этот заказ собирает другой ПВЗ")
    if o.status != "assembling":
        raise HTTPException(409, f"Заказ №{o.id} уже не на сборке: {STATUS_LABEL[o.status]}")
    o.status = "awaiting_courier"
    _log(o, "ПВЗ подтвердил сбор заказа")
    assign_courier(s, o)
    s.commit()
    return order_view(s, o)


def dispatch_waiting(s: Session) -> None:
    """Раздать заказы из очереди (собраны, но курьера не было) свободным курьерам — старые первыми."""
    for o in list(s.scalars(select(Order).where(Order.status == "awaiting_courier").order_by(Order.created_at))):
        if not s.scalars(select(Courier).where(Courier.busy.is_(False))).first():
            break
        assign_courier(s, o)


def assign_courier(s: Session, o: Order) -> None:
    """Ближайший свободный курьер к ПВЗ с заказом; маршрут по улицам: курьер → ПВЗ А → ПВЗ Б."""
    src, dest = s.get(Pvz, o.source_pvz_id), s.get(Pvz, o.dest_pvz_id)
    free = list(s.scalars(select(Courier).where(Courier.busy.is_(False))))
    if not free:
        _log(o, "Свободных курьеров нет — заказ ждёт в очереди")
        _notify(s, "pvz", src.id, o.id, f"Заказ №{o.id} собран. Свободных курьеров нет — назначим первого, кто появится")
        return
    c = min(free, key=lambda c: dist_m(_pt(c), _pt(src)))
    approach = road_route([_pt(c), _pt(src)])
    delivery = road_route([_pt(src), _pt(dest)])
    o.courier_id, o.status, c.busy = c.id, "courier_assigned", True
    o.road_m = approach["distance_m"] + delivery["distance_m"]
    o.courier_pay = round(COURIER_BASE_PAY + COURIER_PAY_PER_KM * delivery["distance_m"] / 1000)
    o.route_json = json.dumps({"approach": approach, "delivery": delivery})
    mins = max(1, round(approach["duration_s"] / 60))
    _log(o, f"Назначен ближайший курьер {c.name}: {_km(approach['distance_m'])} по дорогам до ПВЗ, ~{mins} мин")
    _notify(s, "courier", c.id, o.id,
            f"Заказ №{o.id}: заберите в «{src.name}» и доставьте в «{dest.name}» — {_km(delivery['distance_m'])} по дорогам, оплата {o.courier_pay} ₽")
    _notify(s, "pvz", src.id, o.id, f"За заказом №{o.id} едет курьер {c.name}, ~{mins} мин")


def issue_order(s: Session, pvz_id: int, order_id: int, code: str) -> dict:
    o = _get(s, Order, order_id, "заказ")
    if o.dest_pvz_id != pvz_id:
        raise HTTPException(403, "Этот заказ выдаёт другой ПВЗ")
    if o.status not in ("ready", "delivered"):
        raise HTTPException(409, f"Заказ №{o.id}: {STATUS_LABEL[o.status]}")
    if code.strip() != o.code:
        raise HTTPException(400, "Код выдачи не совпал")
    o.status = "issued"
    _log(o, f"Выдан покупателю {o.buyer_name}")
    s.commit()
    return order_view(s, o)


# ---------- курьер ----------

def _courier_order(s: Session, courier_id: int, order_id: int) -> Order:
    o = _get(s, Order, order_id, "заказ")
    if o.courier_id != courier_id:
        raise HTTPException(403, "Этот заказ назначен другому курьеру")
    return o


def courier_picked(s: Session, courier_id: int, order_id: int) -> dict:
    o = _courier_order(s, courier_id, order_id)
    if o.status != "courier_assigned":
        raise HTTPException(409, f"Заказ №{o.id}: {STATUS_LABEL[o.status]}")
    c, src, dest = s.get(Courier, o.courier_id), s.get(Pvz, o.source_pvz_id), s.get(Pvz, o.dest_pvz_id)
    c.lat, c.lon = src.lat, src.lon
    o.status = "in_transit"
    route = json.loads(o.route_json or "{}").get("delivery", {})
    mins = max(1, round(route.get("duration_s", 600) / 60))
    _log(o, f"Курьер {c.name} забрал заказ в «{src.name}»")
    _notify(s, "pvz", dest.id, o.id, f"К вам едет заказ №{o.id} (курьер {c.name}), ~{mins} мин")
    s.commit()
    return order_view(s, o)


def courier_delivered(s: Session, courier_id: int, order_id: int) -> dict:
    o = _courier_order(s, courier_id, order_id)
    if o.status != "in_transit":
        raise HTTPException(409, f"Заказ №{o.id}: {STATUS_LABEL[o.status]}")
    c, dest = s.get(Courier, o.courier_id), s.get(Pvz, o.dest_pvz_id)
    c.lat, c.lon, c.busy = dest.lat, dest.lon, False
    o.status, o.delivered_at = "delivered", datetime.now()
    _log(o, f"Курьер {c.name} доставил заказ в «{dest.name}», заработал {o.courier_pay} ₽")
    _notify(s, "pvz", dest.id, o.id, f"Заказ №{o.id} доставлен — выдайте покупателю {o.buyer_name}, код {o.code}")
    _notify(s, "courier", c.id, o.id, f"Заказ №{o.id} доставлен. +{o.courier_pay} ₽ к заработку")
    s.flush()
    dispatch_waiting(s)  # освободившийся курьер может взять заказ из очереди
    s.commit()
    return order_view(s, o)


CITY_BOUNDS = (56.5, 56.75, 47.7, 48.1)  # Йошкар-Ола с запасом


def in_city(lat: float, lon: float) -> bool:
    return CITY_BOUNDS[0] < lat < CITY_BOUNDS[1] and CITY_BOUNDS[2] < lon < CITY_BOUNDS[3]


def set_courier_location(s: Session, courier_id: int, lat: float, lon: float) -> dict:
    """«Где я сейчас»: курьер отмечает своё место. От него система выбирает ближайшего
    и строит путь до ПВЗ; если курьер едет за заказом — путь пересчитывается."""
    if not in_city(lat, lon):
        raise HTTPException(400, "Точка вне Йошкар-Олы — отметьте место на карте города")
    c = _get(s, Courier, courier_id, "курьер")
    c.lat, c.lon = lat, lon
    for o in s.scalars(select(Order).where(Order.courier_id == c.id, Order.status == "courier_assigned")):
        route = json.loads(o.route_json or "{}")
        src = s.get(Pvz, o.source_pvz_id)
        route["approach"] = road_route([(lat, lon), _pt(src)])
        o.route_json = json.dumps(route)
        o.road_m = route["approach"]["distance_m"] + route.get("delivery", {}).get("distance_m", 0)
    s.flush()
    dispatch_waiting(s)  # свободный курьер появился на карте — заберёт заказ из очереди
    s.commit()
    return {"lat": c.lat, "lon": c.lon, "near": nearest_pvz(s, lat, lon)}


def nearest_pvz(s: Session, lat: float, lon: float) -> dict | None:
    best = min(s.scalars(select(Pvz)), key=lambda p: dist_m((lat, lon), _pt(p)), default=None)
    if best is None:
        return None
    return {"id": best.id, "name": best.name, "distance_m": round(dist_m((lat, lon), _pt(best)))}


# ---------- представления ----------

def order_view(s: Session, o: Order, with_route: bool = True) -> dict:
    c = s.get(Courier, o.courier_id) if o.courier_id else None
    v = {
        "id": o.id, "product": product_brief(s.get(Product, o.product_id)), "buyer_name": o.buyer_name,
        "mode": o.mode, "status": o.status, "status_label": STATUS_LABEL[o.status], "code": o.code,
        "price": o.price, "discount": o.discount, "paid": o.price - o.discount,
        "source": pvz_brief(s.get(Pvz, o.source_pvz_id)) if o.source_pvz_id else None,
        "dest": pvz_brief(s.get(Pvz, o.dest_pvz_id)), "buyer_pvz": pvz_brief(s.get(Pvz, o.buyer_pvz_id)),
        "courier": {"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon} if c else None,
        "courier_pay": o.courier_pay, "distance_m": o.distance_m, "road_m": o.road_m,
        "created_at": o.created_at.strftime("%H:%M"), "history": json.loads(o.history or "[]"),
    }
    if with_route and o.route_json:
        v["route"] = json.loads(o.route_json)
    return v


def _notes(s: Session, target: str, target_id: int) -> list[dict]:
    rows = s.scalars(select(Notification).where(Notification.target == target, Notification.target_id == target_id)
                     .order_by(Notification.id.desc()).limit(15))
    return [{"id": n.id, "order_id": n.order_id, "text": n.text, "time": n.created_at.strftime("%H:%M:%S")} for n in rows]


def pvz_dashboard(s: Session, pvz_id: int) -> dict:
    p = _get(s, Pvz, pvz_id, "ПВЗ")
    stock = s.execute(select(Stock, Product).join(Product, Stock.product_id == Product.id)
                      .where(Stock.pvz_id == pvz_id).order_by(Product.id)).all()
    orders = list(s.scalars(select(Order).where(or_(Order.source_pvz_id == pvz_id, Order.dest_pvz_id == pvz_id))
                            .order_by(Order.id.desc())))
    v = [order_view(s, o, with_route=False) for o in orders]
    return {
        "pvz": pvz_brief(p),
        "stock": [{**product_brief(pr), "qty": st.qty} for st, pr in stock],
        "to_assemble": [o for o in v if o["source"] and o["source"]["id"] == pvz_id and o["status"] == "assembling"],
        "to_courier": [o for o in v if o["source"] and o["source"]["id"] == pvz_id and o["status"] in ("awaiting_courier", "courier_assigned")],
        "to_issue": [o for o in v if o["dest"]["id"] == pvz_id and o["status"] in ("ready", "delivered")],
        "incoming": [o for o in v if o["dest"]["id"] == pvz_id and o["mode"] == "deliver" and o["status"] in ("assembling", "awaiting_courier", "courier_assigned", "in_transit")],
        "supplies": incoming_for_pvz(s, pvz_id),
        "notifications": _notes(s, "pvz", pvz_id),
    }


def couriers(s: Session) -> list[dict]:
    return [{"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon, "busy": c.busy} for c in s.scalars(select(Courier).order_by(Courier.id))]


def courier_dashboard(s: Session, courier_id: int) -> dict:
    c = _get(s, Courier, courier_id, "курьер")
    active = s.scalars(select(Order).where(Order.courier_id == courier_id, Order.status.in_(("courier_assigned", "in_transit")))
                       .order_by(Order.id))
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    done = list(s.scalars(select(Order).where(Order.courier_id == courier_id, Order.status.in_(("delivered", "issued")),
                                              Order.delivered_at >= today).order_by(Order.delivered_at.desc())))
    return {
        "courier": {"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon, "busy": c.busy, "near": nearest_pvz(s, c.lat, c.lon)},
        "tasks": [order_view(s, o) for o in active],
        "earnings": {
            "today": sum(o.courier_pay for o in done), "deliveries": len(done),
            "km": round(sum(o.road_m for o in done) / 1000, 1),
            "rate": f"{COURIER_BASE_PAY} ₽ за доставку + {COURIER_PAY_PER_KM} ₽ за км",
            "items": [{"id": o.id, "product": s.get(Product, o.product_id).name, "pay": o.courier_pay,
                       "km": round(o.road_m / 1000, 1), "time": o.delivered_at.strftime("%H:%M"),
                       "from": s.get(Pvz, o.source_pvz_id).name, "to": s.get(Pvz, o.dest_pvz_id).name} for o in done],
        },
        "notifications": _notes(s, "courier", courier_id),
    }
