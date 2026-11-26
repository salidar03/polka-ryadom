"""Живой процесс: покупатель → ПВЗ с товаром упаковывает → рейс машины → ПВЗ покупателя."""
import json
import random
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import runs
from .db import Account, Courier, Notification, Order, Product, Pvz, Run, Stock
from .economics import Params
from .geo import ROAD_FACTOR, dist_m
from .similar import similar_products
from .supplier import incoming_for_pvz

WALK_KMH = 4.5

STATUS_LABEL = {
    "via_hub": "Едет через хаб — 2 дня",
    "ready": "Отложен в ПВЗ — можно забирать",
    "assembling": "ПВЗ собирает и упаковывает заказ",
    "awaiting_run": "Упакован — ждёт рейса",
    "in_run": "В рейсе — машина едет за заказом",
    "in_transit": "Взят с точки — едет в ПВЗ покупателя",
    "delivered": "Доставлен — можно забирать",
    "issued": "Выдан",
}


def pickup_discount(price: int) -> int:
    """Скидка за самовывоз из ПВЗ, где товар уже лежит: рейс не нужен.
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


def _shelves(s: Session, product_id: int, me: Pvz) -> list[dict]:
    """ПВЗ, где товар лежит на полке, — от ближайшего к ПВЗ покупателя."""
    rows = s.execute(select(Stock, Pvz).join(Pvz, Stock.pvz_id == Pvz.id)
                     .where(Stock.product_id == product_id, Stock.qty > 0)).all()
    out = []
    for st, p in rows:
        d = dist_m(_pt(me), _pt(p))
        out.append({**pvz_brief(p), "qty": st.qty, "distance_m": round(d), "walk_min": _walk_min(d)})
    return sorted(out, key=lambda o: o["distance_m"])


def alternatives(s: Session, prod: Product, me: Pvz) -> list[dict]:
    """Такой же товар у других продавцов, который уже лежит на полке: можно забрать сразу."""
    out = []
    for other in similar_products(s, prod):
        shelves = _shelves(s, other.id, me)
        if not shelves:
            continue
        best, disc = shelves[0], pickup_discount(other.price)
        out.append({"product": product_brief(other), "pvz": best, "shelves": len(shelves),
                    "price": other.price, "pickup_price": other.price - disc, "discount": disc,
                    "here": best["id"] == me.id})
    return sorted(out, key=lambda a: (a["pvz"]["distance_m"], a["pickup_price"]))[:3]


def offers(s: Session, product_id: int, pvz_id: int) -> dict:
    """Где взять товар: в выбранном ПВЗ, в ближайшем (дешевле, забрать сразу), в других — или через хаб.
    Плюс такой же товар у других продавцов, если он лежит на полках рядом."""
    me = _get(s, Pvz, pvz_id, "ПВЗ")
    prod = _get(s, Product, product_id, "товар")
    options = _shelves(s, product_id, me)
    here = next((o for o in options if o["id"] == pvz_id), None)
    disc = pickup_discount(prod.price)
    slot = runs.next_slot()
    return {
        "product": product_brief(prod), "my_pvz": pvz_brief(me),
        "here": here, "nearest": here or (options[0] if options else None), "options": options,
        "price": prod.price, "pickup_price": prod.price - disc, "discount": disc,
        "deliver_days": 1, "hub_days": 2,
        "next_run": slot.strftime("%H:%M"), "schedule": runs.schedule_text(),
        "alternatives": alternatives(s, prod, me),
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
        _log(o, f"Самовывоз из «{src.name}» со скидкой {disc} ₽ — рейс не нужен. Код {code}")
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
    _notify(s, "pvz", src.id, o.id, f"Новый заказ №{o.id}: соберите и упакуйте «{prod.name}» для доставки в «{buyer_pvz.name}» ({_km(d)})")
    s.commit()
    return order_view(s, o)


def my_orders(s: Session, buyer: Account) -> list[dict]:
    runs.auto_form(s)
    rows = s.scalars(select(Order).where(Order.buyer_account_id == buyer.id).order_by(Order.id.desc()).limit(30))
    return [order_view(s, o) for o in rows]


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
    """ПВЗ подтвердил сбор и упаковку → заказ ждёт ближайшего рейса машины."""
    o = _get(s, Order, order_id, "заказ")
    if o.source_pvz_id != pvz_id:
        raise HTTPException(403, "Этот заказ собирает другой ПВЗ")
    if o.status != "assembling":
        raise HTTPException(409, f"Заказ №{o.id} уже не на сборке: {STATUS_LABEL.get(o.status, o.status)}")
    slot = runs.next_slot()
    o.status, o.ready_at = "awaiting_run", datetime.now()
    _log(o, f"ПВЗ собрал и упаковал заказ — уедет ближайшим рейсом в {slot:%H:%M}")
    for c in s.scalars(select(Courier).where(Courier.busy.is_(False))):
        _notify(s, "courier", c.id, o.id, f"Заказ №{o.id} упакован в «{s.get(Pvz, pvz_id).name}» — ждёт рейса в {slot:%H:%M}")
    s.commit()
    return order_view(s, o)


def issue_order(s: Session, pvz_id: int, order_id: int, code: str) -> dict:
    o = _get(s, Order, order_id, "заказ")
    if o.dest_pvz_id != pvz_id:
        raise HTTPException(403, "Этот заказ выдаёт другой ПВЗ")
    if o.status not in ("ready", "delivered"):
        raise HTTPException(409, f"Заказ №{o.id}: {STATUS_LABEL.get(o.status, o.status)}")
    if code.strip() != o.code:
        raise HTTPException(400, "Код выдачи не совпал")
    o.status = "issued"
    _log(o, f"Выдан покупателю {o.buyer_name}")
    s.commit()
    return order_view(s, o)


# ---------- водитель ----------

CITY_BOUNDS = (56.5, 56.75, 47.7, 48.1)  # Йошкар-Ола с запасом


def in_city(lat: float, lon: float) -> bool:
    return CITY_BOUNDS[0] < lat < CITY_BOUNDS[1] and CITY_BOUNDS[2] < lon < CITY_BOUNDS[3]


def set_courier_location(s: Session, courier_id: int, lat: float, lon: float) -> dict:
    """«Где я сейчас»: водитель отмечает своё место. От него система выбирает, кому отдать рейс,
    и с какой точки начать объезд."""
    if not in_city(lat, lon):
        raise HTTPException(400, "Точка вне Йошкар-Олы — отметьте место на карте города")
    c = _get(s, Courier, courier_id, "курьер")
    c.lat, c.lon = lat, lon
    s.commit()
    return {"lat": c.lat, "lon": c.lon, "near": nearest_pvz(s, lat, lon)}


def nearest_pvz(s: Session, lat: float, lon: float) -> dict | None:
    best = min(s.scalars(select(Pvz)), key=lambda p: dist_m((lat, lon), _pt(p)), default=None)
    if best is None:
        return None
    return {"id": best.id, "name": best.name, "distance_m": round(dist_m((lat, lon), _pt(best)))}


# ---------- представления ----------

def _status_label(o: Order) -> str:
    if o.status == "awaiting_run":
        return f"Упакован — уедет рейсом в {runs.next_slot():%H:%M}"
    if o.status == "in_run" and o.run_id:
        return f"В рейсе №{o.run_id} — машина едет за заказом"
    if o.status == "in_transit" and o.run_id:
        return f"Взят с точки — рейс №{o.run_id} везёт в ПВЗ покупателя"
    return STATUS_LABEL.get(o.status, o.status)


def order_view(s: Session, o: Order) -> dict:
    c = s.get(Courier, o.courier_id) if o.courier_id else None
    run = s.get(Run, o.run_id) if o.run_id else None
    return {
        "id": o.id, "product": product_brief(s.get(Product, o.product_id)), "buyer_name": o.buyer_name,
        "mode": o.mode, "status": o.status, "status_label": _status_label(o), "code": o.code,
        "price": o.price, "discount": o.discount, "paid": o.price - o.discount,
        "source": pvz_brief(s.get(Pvz, o.source_pvz_id)) if o.source_pvz_id else None,
        "dest": pvz_brief(s.get(Pvz, o.dest_pvz_id)), "buyer_pvz": pvz_brief(s.get(Pvz, o.buyer_pvz_id)),
        "courier": {"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon} if c else None,
        "run": {"id": run.id, "slot": run.slot, "status": run.status} if run else None,
        "next_run": runs.next_slot().strftime("%H:%M") if o.status == "awaiting_run" else None,
        "distance_m": o.distance_m,
        "created_at": o.created_at.strftime("%H:%M"), "history": json.loads(o.history or "[]"),
    }


def _notes(s: Session, target: str, target_id: int) -> list[dict]:
    rows = s.scalars(select(Notification).where(Notification.target == target, Notification.target_id == target_id)
                     .order_by(Notification.id.desc()).limit(15))
    return [{"id": n.id, "order_id": n.order_id, "text": n.text, "time": n.created_at.strftime("%H:%M:%S")} for n in rows]


def pvz_dashboard(s: Session, pvz_id: int) -> dict:
    runs.auto_form(s)
    p = _get(s, Pvz, pvz_id, "ПВЗ")
    stock = s.execute(select(Stock, Product).join(Product, Stock.product_id == Product.id)
                      .where(Stock.pvz_id == pvz_id).order_by(Product.id)).all()
    orders = list(s.scalars(select(Order).where(or_(Order.source_pvz_id == pvz_id, Order.dest_pvz_id == pvz_id))
                            .order_by(Order.id.desc())))
    v = [order_view(s, o) for o in orders]
    return {
        "pvz": pvz_brief(p),
        "stock": [{**product_brief(pr), "qty": st.qty} for st, pr in stock],
        "to_assemble": [o for o in v if o["source"] and o["source"]["id"] == pvz_id and o["status"] == "assembling"],
        "to_courier": [o for o in v if o["source"] and o["source"]["id"] == pvz_id and o["mode"] == "deliver"
                       and o["status"] in ("awaiting_run", "in_run")],
        "to_issue": [o for o in v if o["dest"]["id"] == pvz_id and o["status"] in ("ready", "delivered")],
        "incoming": [o for o in v if o["dest"]["id"] == pvz_id and o["mode"] == "deliver"
                     and o["status"] in ("assembling", "awaiting_run", "in_run", "in_transit")],
        "supplies": incoming_for_pvz(s, pvz_id),
        "schedule": runs.schedule_text(), "next_run": runs.next_slot().strftime("%H:%M"),
        "notifications": _notes(s, "pvz", pvz_id),
    }


def couriers(s: Session) -> list[dict]:
    return [{"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon, "busy": c.busy} for c in s.scalars(select(Courier).order_by(Courier.id))]


def courier_dashboard(s: Session, courier_id: int) -> dict:
    runs.auto_form(s)
    c = _get(s, Courier, courier_id, "курьер")
    ready = list(s.scalars(select(Order).where(Order.status == "awaiting_run").order_by(Order.ready_at)))
    done = runs.courier_runs_today(s, courier_id)
    p = Params()
    return {
        "courier": {"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon, "busy": c.busy, "near": nearest_pvz(s, c.lat, c.lon)},
        "run": runs.active_run(s, courier_id),
        "ready": [{"id": o.id, "product": f"{s.get(Product, o.product_id).emoji} {s.get(Product, o.product_id).name}",
                   "from": pvz_brief(s.get(Pvz, o.source_pvz_id)), "to": pvz_brief(s.get(Pvz, o.dest_pvz_id)),
                   "ready_at": o.ready_at.strftime("%H:%M") if o.ready_at else ""} for o in ready],
        "schedule": runs.schedule_text(), "next_run": runs.next_slot().strftime("%H:%M"),
        "limits": {"stops": runs.MAX_STOPS, "orders": runs.MAX_ORDERS, "link_m": runs.LINK_M},
        "earnings": {
            "today": sum(r.pay for r in done), "runs": len(done), "orders": sum(r.orders_count for r in done),
            "km": round(sum(r.km for r in done), 1),
            "rate": f"{p.run_cost:.0f} ₽ за рейс + {p.stop_cost:.0f} ₽ за остановку + {p.km_cost:.0f} ₽ за км",
            "items": [{"id": r.id, "slot": r.slot, "stops": len(json.loads(r.stops_json)), "orders": r.orders_count,
                       "km": r.km, "pay": r.pay, "time": r.finished_at.strftime("%H:%M")} for r in done],
        },
        "notifications": _notes(s, "courier", courier_id),
    }
