"""Нештатные сценарии: возврат, «товара нет на полке», долгое хранение, заказ через хаб, лимит полки."""
import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import finance as fin
from .db import Account, Notification, Order, Product, Pvz, Stock, Supply
from .geo import dist_m


def _log(o: Order, text: str) -> None:
    h = json.loads(o.history or "[]")
    h.append({"time": datetime.now().strftime("%H:%M:%S"), "text": text})
    o.history = json.dumps(h, ensure_ascii=False)


def _note(s: Session, target: str, target_id: int, order_id: int | None, text: str) -> None:
    s.add(Notification(target=target, target_id=target_id, order_id=order_id, text=text))


def _order(s: Session, order_id: int) -> Order:
    o = s.get(Order, order_id)
    if o is None:
        raise HTTPException(404, f"Нет заказа №{order_id}")
    return o


# ---------- лимит полки ----------

def shelf_used(s: Session, pvz_id: int) -> int:
    return int(s.scalar(select(func.coalesce(func.sum(Stock.qty), 0)).where(Stock.pvz_id == pvz_id)) or 0)


def check_capacity(s: Session, pvz_id: int, add: int) -> None:
    used = shelf_used(s, pvz_id)
    if used + add > fin.PVZ_CAPACITY:
        raise HTTPException(409, f"Полка заполнена: занято {used} из {fin.PVZ_CAPACITY} мест, "
                                 f"свободно {fin.PVZ_CAPACITY - used} — примите меньше или освободите место")


def put_on_shelf(s: Session, pvz_id: int, product_id: int, qty: int) -> Stock:
    """Положить товар на полку с учётом лимита; срок хранения считается с первого дня партии."""
    check_capacity(s, pvz_id, qty)
    st = s.get(Stock, {"pvz_id": pvz_id, "product_id": product_id})
    if st is None:
        st = Stock(pvz_id=pvz_id, product_id=product_id, qty=0)
        s.add(st)
    if not st.qty or st.since is None:
        st.since = datetime.now()
    st.qty += qty
    return st


def shelf_age(st: Stock) -> dict:
    days = max(0, (datetime.now() - st.since).days) if st.since else 0
    if days >= fin.FREE_STORAGE_DAYS:
        state, text = "late", f"{days}-й день — хранение платит продавец, вывоз на {fin.AUTO_REMOVE_DAY}-й день"
    elif days >= fin.WARN_DAY:
        state, text = "warn", f"{days}-й день — через {fin.FREE_STORAGE_DAYS - days} дн. хранение станет платным для продавца"
    else:
        state, text = "ok", f"лежит {days} дн." if days else "принят сегодня"
    return {"days": days, "state": state, "text": text}


# ---------- возврат ----------

def request_return(s: Session, buyer: Account, order_id: int, reason: str) -> Order:
    o = _order(s, order_id)
    if o.buyer_account_id != buyer.id:
        raise HTTPException(403, "Это не ваш заказ")
    if o.status != "issued":
        raise HTTPException(409, "Вернуть можно только полученный заказ")
    if reason not in ("ok", "defect"):
        raise HTTPException(400, "Причина: ok — не подошёл, defect — брак")
    o.status, o.return_reason = "return_requested", reason
    why = "не подошёл" if reason == "ok" else "брак"
    _log(o, f"Покупатель оформил возврат ({why}) — отнесёт товар в «{s.get(Pvz, o.dest_pvz_id).name}»")
    _note(s, "pvz", o.dest_pvz_id, o.id, f"Возврат заказа №{o.id} ({why}): осмотрите товар и примите")
    s.commit()
    return o


def accept_return(s: Session, pvz_id: int, order_id: int, condition: str) -> Order:
    """ПВЗ принял возврат. Целый товар остаётся на полке этого ПВЗ — обратно через хаб его не везём;
    брак уходит продавцу. Покупателю возвращаются деньги, продавцу — комиссия, логистика не возвращается."""
    o = _order(s, order_id)
    if o.dest_pvz_id != pvz_id:
        raise HTTPException(403, "Этот возврат принимает другой ПВЗ")
    if o.status != "return_requested":
        raise HTTPException(409, f"По заказу №{o.id} возврат не оформлен")
    if condition not in ("ok", "defect"):
        raise HTTPException(400, "Состояние: ok — целый, defect — брак")
    prod, pvz = s.get(Product, o.product_id), s.get(Pvz, pvz_id)
    o.status = "returned"
    fin.post(s, "pvz", pvz_id, "return", fin.PVZ_RETURN, f"Приём возврата №{o.id}", o.id)
    seller = prod.supplier_account_id
    fin.post(s, "seller", seller, "refund", -o.price, f"Возврат №{o.id}: деньги покупателю", o.id)
    fin.post(s, "seller", seller, "commission", round(o.price * fin.COMMISSION), f"Возврат №{o.id}: комиссия возвращается", o.id)
    if condition == "ok":
        put_on_shelf(s, pvz_id, prod.id, 1)
        _log(o, f"Возврат принят: товар целый — остался на полке «{pvz.name}» и снова продаётся в городе")
    else:
        s.add(Supply(product_id=prod.id, supplier_account_id=seller or 0, kind="to_seller", city="Йошкар-Ола",
                     pvz_id=pvz_id, qty=1, status="in_transit"))
        _log(o, f"Возврат принят: брак — «{pvz.name}» передаст товар продавцу")
    _log(o, f"Покупателю возвращено {o.price - o.discount} ₽")
    s.commit()
    return o


# ---------- сбой: товара нет на полке ----------

def report_missing(s: Session, pvz_id: int, order_id: int) -> Order:
    """ПВЗ не нашёл товар на полке. Штраф за расхождение остатков, остаток обнуляется,
    заказ перенаправляется в ближайший ПВЗ, где товар есть, а если его нет нигде — через хаб."""
    o = _order(s, order_id)
    if o.source_pvz_id != pvz_id or o.status not in ("assembling", "ready"):
        raise HTTPException(409, "Сообщить можно только о заказе, который вы сейчас собираете")
    old = s.get(Pvz, pvz_id)
    fin.post(s, "pvz", pvz_id, "penalty", -fin.PVZ_PENALTY_MISSING, f"Заказ №{o.id}: товара не оказалось на полке", o.id)
    st = s.get(Stock, {"pvz_id": pvz_id, "product_id": o.product_id})
    if st is not None:
        st.qty = 0  # инвентаризация: на полке этого товара нет
    dest = s.get(Pvz, o.dest_pvz_id)
    rows = s.execute(select(Stock, Pvz).join(Pvz, Stock.pvz_id == Pvz.id)
                     .where(Stock.product_id == o.product_id, Stock.qty > 0, Stock.pvz_id != pvz_id)).all()
    if not rows:
        o.mode, o.status, o.source_pvz_id = "hub", "via_hub", None
        _log(o, f"Сбой: в «{old.name}» товара не оказалось, на полках города его нет — заказ перенаправлен через хаб")
        s.commit()
        return o
    st2, new = min(rows, key=lambda r: dist_m((r[1].lat, r[1].lon), (dest.lat, dest.lon)))
    st2.qty -= 1
    o.source_pvz_id = new.id
    o.distance_m = round(dist_m((new.lat, new.lon), (dest.lat, dest.lon)))
    if new.id == dest.id:
        o.status = "ready"
        _log(o, f"Сбой: в «{old.name}» товара не оказалось — он нашёлся прямо в ПВЗ получения «{dest.name}»")
    else:
        o.mode, o.status = "deliver", "assembling"
        _log(o, f"Сбой: в «{old.name}» товара не оказалось — заказ перенаправлен в «{new.name}» и приедет рейсом. "
                f"Скидка покупателя сохраняется")
        _note(s, "pvz", new.id, o.id, f"Перенаправленный заказ №{o.id}: соберите и упакуйте для «{dest.name}»")
    s.commit()
    return o


# ---------- заказ через хаб ----------

def hub_arrived(s: Session, pvz_id: int, order_id: int) -> Order:
    o = _order(s, order_id)
    if o.dest_pvz_id != pvz_id or o.status != "via_hub":
        raise HTTPException(409, "Этот заказ не едет к вам через хаб")
    o.status, o.delivered_at = "delivered", datetime.now()
    _log(o, "Пришёл с хаба по магистрали — можно выдавать")
    _note(s, "pvz", pvz_id, o.id, f"Заказ №{o.id} пришёл с хаба — выдайте покупателю {o.buyer_name}, код {o.code}")
    s.commit()
    return o


# ---------- долгое хранение ----------

def advance_days(s: Session, days: int) -> dict:
    """Демо: прошло N дней. Начисляем хранение ПВЗ (первые 30 дней — за счёт маркетплейса,
    дальше двойной тариф платит продавец), на 45-й день неликвид вывозится продавцу."""
    if not 1 <= days <= 60:
        raise HTTPException(400, "От 1 до 60 дней")
    now = datetime.now()
    removed, storage_total, late_total = [], 0.0, 0.0
    for st in list(s.scalars(select(Stock).where(Stock.qty > 0))):
        st.since = st.since or now
        age0 = max(0, (now - st.since).days)
        age1 = age0 + days
        stored = max(0, min(age1, fin.AUTO_REMOVE_DAY) - age0)
        late = max(0, min(age1, fin.AUTO_REMOVE_DAY) - max(age0, fin.FREE_STORAGE_DAYS))
        prod, pvz = s.get(Product, st.product_id), s.get(Pvz, st.pvz_id)
        pay = fin.PVZ_STORAGE_DAY * st.qty * stored
        fin.post(s, "pvz", st.pvz_id, "storage", pay, f"Хранение «{prod.name}»: {st.qty} шт. × {stored} дн.")
        storage_total += pay
        if late:
            charge = fin.SELLER_STORAGE_DAY * st.qty * late
            fin.post(s, "seller", prod.supplier_account_id, "storage_late", -charge,
                     f"«{prod.name}» в «{pvz.name}»: {st.qty} шт. × {late} дн. после {fin.FREE_STORAGE_DAYS}-го дня")
            late_total += charge
        st.since -= timedelta(days=days)
        if age1 >= fin.AUTO_REMOVE_DAY:
            s.add(Supply(product_id=prod.id, supplier_account_id=prod.supplier_account_id or 0, kind="to_seller",
                         city="Йошкар-Ола", pvz_id=pvz.id, qty=st.qty, status="in_transit"))
            _note(s, "pvz", pvz.id, None, f"Вывоз неликвида: «{prod.name}» пролежал {fin.AUTO_REMOVE_DAY} дней — "
                                           f"передайте {st.qty} шт. на вывоз продавцу")
            removed.append({"pvz": pvz.name, "product": prod.name, "qty": st.qty})
            st.qty = 0
    s.commit()
    return {"days": days, "storage_rub": round(storage_total), "seller_late_rub": round(late_total), "removed": removed}


def handover_to_seller(s: Session, pvz_id: int, supply_id: int) -> dict:
    sup = s.get(Supply, supply_id)
    if sup is None or sup.pvz_id != pvz_id or sup.kind != "to_seller":
        raise HTTPException(404, "Такого вывоза в вашем ПВЗ нет")
    if sup.status != "in_transit":
        raise HTTPException(409, "Уже передано")
    sup.status, sup.accepted_at = "accepted", datetime.now()
    s.commit()
    return {"supply_id": sup.id, "status": sup.status}


def outgoing_for_pvz(s: Session, pvz_id: int) -> list[dict]:
    rows = s.execute(select(Supply, Product).join(Product, Supply.product_id == Product.id)
                     .where(Supply.pvz_id == pvz_id, Supply.kind == "to_seller", Supply.status == "in_transit")).all()
    return [{"id": sp.id, "qty": sp.qty, "product": {"id": pr.id, "name": pr.name, "emoji": pr.emoji},
             "supplier": pr.supplier_name or "Wildberries"} for sp, pr in rows]
