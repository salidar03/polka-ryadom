"""Поставщик: загрузка товара, предложение распределить часть партии по городам, поставки в ПВЗ."""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Account, Notification, Order, Product, Pvz, Stock, Supply
from .demand import SPACING_M, category, plan_distribution
from .similar import similar_products

BADGE = ("Если разложить товар по ПВЗ, покупатель с большей вероятностью заберёт именно ваш товар: "
         "когда похожий товар есть у нескольких продавцов, мы предлагаем тот, что уже лежит на полке рядом — "
         "его можно забрать сразу и дешевле.")


def _pvz_points(s: Session) -> list[dict]:
    return [{"id": p.id, "name": p.name, "lat": p.lat, "lon": p.lon, "weight": p.weight}
            for p in s.scalars(select(Pvz).order_by(Pvz.id))]


def _check(name: str, price: int, qty: int, share: float, horizon: int) -> None:
    if not name.strip():
        raise HTTPException(400, "Укажите название товара")
    if not (10 <= price <= 1_000_000):
        raise HTTPException(400, "Цена — от 10 ₽")
    if not (1 <= qty <= 100_000):
        raise HTTPException(400, "Партия — от 1 до 100 000 штук")
    if not (0.05 <= share <= 0.3):
        raise HTTPException(400, "На полки ПВЗ — от 5% до 30% партии")
    if not (7 <= horizon <= 30):
        raise HTTPException(400, "Срок на полке — от 7 до 30 дней")


def competitors(s: Session, acc: Account, name: str, cat_id: str) -> list[dict]:
    """Такой же товар у других продавцов: сколько ПВЗ города уже держат его на полке."""
    if not name.strip():
        return []
    probe = Product(id=None, name=name, price=0, category=category(cat_id)["id"], supplier_account_id=acc.id)
    out = []
    for pr in similar_products(s, probe):
        points = s.scalar(select(func.count()).select_from(Stock).where(Stock.product_id == pr.id, Stock.qty > 0)) or 0
        out.append({"id": pr.id, "name": pr.name, "emoji": pr.emoji, "price": pr.price,
                    "supplier": pr.supplier_name or "Wildberries", "shelf_points": int(points)})
    return sorted(out, key=lambda x: -x["shelf_points"])


def _with_market(s: Session, acc: Account, name: str, cat_id: str, plan: dict) -> dict:
    rivals = competitors(s, acc, name, cat_id)
    plan["competitors"] = rivals
    plan["badge"] = BADGE
    plan["spacing_m"] = SPACING_M
    return plan


def preview(s: Session, acc: Account, name: str, cat_id: str, qty: int, share: float, horizon: int) -> dict:
    return _with_market(s, acc, name, cat_id, plan_distribution(_pvz_points(s), cat_id, qty, share, horizon))


def upload(s: Session, acc: Account, name: str, cat_id: str, price: int, qty: int, share: float, horizon: int, accept_plan: bool) -> dict:
    """Создать товар и поставки: по плану — часть на полки ПВЗ и в города, остальное на склад WB;
    без плана — вся партия на склад WB."""
    _check(name, price, qty, share, horizon)
    cat = category(cat_id)
    new_id = (s.scalar(select(func.max(Product.id))) or 0) + 1
    prod = Product(id=new_id, name=name.strip()[:200], price=price, emoji=cat["emoji"], category=cat["id"],
                   supplier_account_id=acc.id, supplier_name=acc.name)
    s.add(prod)
    s.flush()
    plan = plan_distribution(_pvz_points(s), cat["id"], qty, share, horizon) if accept_plan else None
    if plan:
        _with_market(s, acc, name, cat["id"], plan)
    on_shelves = 0
    if plan:
        for row in plan["cities"]:
            if row["pvz_network"]:
                for p in row["pvz"]:
                    s.add(Supply(product_id=prod.id, supplier_account_id=acc.id, kind="shelf", city=row["city"],
                                 pvz_id=p["id"], qty=p["qty"], status="in_transit"))
                    s.add(Notification(target="pvz", target_id=p["id"], order_id=None,
                                       text=f"Приёмка от поставщика «{acc.name}»: {p['qty']} шт. «{prod.name}» на полку"))
            elif row["qty"] > 0:
                s.add(Supply(product_id=prod.id, supplier_account_id=acc.id, kind="city", city=row["city"],
                             qty=row["qty"], status="in_transit"))
        on_shelves = plan["on_shelves"]
    if qty - on_shelves > 0:
        s.add(Supply(product_id=prod.id, supplier_account_id=acc.id, kind="warehouse", city="Склад WB",
                     qty=qty - on_shelves, status="in_transit"))
    s.commit()
    return {"product_id": prod.id, "plan": plan, "dashboard": dashboard(s, acc)}


def accept_supply(s: Session, pvz_id: int, supply_id: int) -> dict:
    """ПВЗ принял поставку на полку — товар появляется у покупателей."""
    sup = s.get(Supply, supply_id)
    if sup is None or sup.pvz_id != pvz_id:
        raise HTTPException(404, "Такой поставки в ваш ПВЗ нет")
    if sup.status != "in_transit":
        raise HTTPException(409, "Поставка уже принята")
    st = s.get(Stock, {"pvz_id": pvz_id, "product_id": sup.product_id})
    if st is None:
        st = Stock(pvz_id=pvz_id, product_id=sup.product_id, qty=0)
        s.add(st)
    st.qty += sup.qty
    sup.status, sup.accepted_at = "accepted", datetime.now()
    s.commit()
    return {"supply_id": sup.id, "qty": st.qty}


def incoming_for_pvz(s: Session, pvz_id: int) -> list[dict]:
    rows = s.execute(select(Supply, Product).join(Product, Supply.product_id == Product.id)
                     .where(Supply.pvz_id == pvz_id, Supply.status == "in_transit").order_by(Supply.id)).all()
    return [{"id": sp.id, "qty": sp.qty, "product": {"id": pr.id, "name": pr.name, "emoji": pr.emoji},
             "supplier": pr.supplier_name or "Wildberries", "created_at": sp.created_at.strftime("%H:%M")} for sp, pr in rows]


def dashboard(s: Session, acc: Account) -> dict:
    """Мои товары: сколько куда отправлено, сколько на полках сейчас, продано и выручка."""
    items = []
    for pr in s.scalars(select(Product).where(Product.supplier_account_id == acc.id).order_by(Product.id.desc())):
        sup = list(s.scalars(select(Supply).where(Supply.product_id == pr.id)))
        on_shelf = s.scalar(select(func.coalesce(func.sum(Stock.qty), 0)).where(Stock.product_id == pr.id))
        sold = list(s.scalars(select(Order).where(Order.product_id == pr.id)))
        items.append({
            "id": pr.id, "name": pr.name, "emoji": pr.emoji, "price": pr.price, "category": category(pr.category)["name"],
            "total": sum(x.qty for x in sup),
            "to_pvz": sum(x.qty for x in sup if x.kind == "shelf"),
            "accepted": sum(x.qty for x in sup if x.kind == "shelf" and x.status == "accepted"),
            "to_cities": sum(x.qty for x in sup if x.kind == "city"),
            "to_warehouse": sum(x.qty for x in sup if x.kind == "warehouse"),
            "on_shelf_now": int(on_shelf or 0),
            "sold": len(sold), "revenue": sum(o.price - o.discount for o in sold),
            "cities": sorted({x.city for x in sup if x.kind in ("shelf", "city")}),
            "shelf_points": len({x.pvz_id for x in sup if x.kind == "shelf"}),
            "competitors": len(similar_products(s, pr)),
        })
    return {"supplier": {"id": acc.id, "name": acc.name, "login": acc.login}, "products": items, "badge": BADGE}
