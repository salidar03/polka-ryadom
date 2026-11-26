"""Поставщик: загрузка товара, предложение распределить часть партии по городам, поставки в ПВЗ."""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Account, Notification, Order, Product, Pvz, Stock, Supply  # noqa: F401
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
    from . import finance as fin
    from .scenarios import put_on_shelf

    sup = s.get(Supply, supply_id)
    if sup is None or sup.pvz_id != pvz_id or sup.kind != "shelf":
        raise HTTPException(404, "Такой поставки в ваш ПВЗ нет")
    if sup.status != "in_transit":
        raise HTTPException(409, "Поставка уже принята")
    st = put_on_shelf(s, pvz_id, sup.product_id, sup.qty)
    sup.status, sup.accepted_at = "accepted", datetime.now()
    name = s.get(Product, sup.product_id).name
    fin.post(s, "pvz", pvz_id, "accept", fin.PVZ_ACCEPT * sup.qty, f"Приёмка «{name}»: {sup.qty} шт. × {fin.PVZ_ACCEPT} ₽")
    s.commit()
    return {"supply_id": sup.id, "qty": st.qty}


def incoming_for_pvz(s: Session, pvz_id: int) -> list[dict]:
    rows = s.execute(select(Supply, Product).join(Product, Supply.product_id == Product.id)
                     .where(Supply.pvz_id == pvz_id, Supply.kind == "shelf", Supply.status == "in_transit").order_by(Supply.id)).all()
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
    return {"supplier": {"id": acc.id, "name": acc.name, "login": acc.login}, "products": items, "badge": BADGE,
            "finance": seller_finance(s, acc), "map": seller_map(s, acc)}


def seller_map(s: Session, acc: Account) -> dict:
    """Где товар продавца и сколько: по ПВЗ Йошкар-Олы (на полке и едет на полку), по городам и на складе WB."""
    from .demand import demand_data

    prods = {p.id: p for p in s.scalars(select(Product).where(Product.supplier_account_id == acc.id))}
    if not prods:
        return {"pvz": [], "cities": [], "warehouse": []}
    pvz: dict[int, dict] = {}

    def point(p: Pvz) -> dict:
        return pvz.setdefault(p.id, {"id": p.id, "name": p.name, "address": p.address, "lat": p.lat, "lon": p.lon, "items": []})

    for st, p in s.execute(select(Stock, Pvz).join(Pvz, Stock.pvz_id == Pvz.id)
                           .where(Stock.product_id.in_(prods), Stock.qty > 0)).all():
        pr = prods[st.product_id]
        point(p)["items"].append({"product_id": pr.id, "product": pr.name, "emoji": pr.emoji, "on_shelf": st.qty, "in_transit": 0})
    cities: dict[tuple[str, int], int] = {}
    warehouse: dict[int, int] = {}
    for sup in s.scalars(select(Supply).where(Supply.product_id.in_(prods))):
        pr = prods[sup.product_id]
        if sup.kind == "shelf" and sup.status == "in_transit" and sup.pvz_id:
            pt = point(s.get(Pvz, sup.pvz_id))
            row = next((x for x in pt["items"] if x["product_id"] == pr.id), None)
            if row is None:
                row = {"product_id": pr.id, "product": pr.name, "emoji": pr.emoji, "on_shelf": 0, "in_transit": 0}
                pt["items"].append(row)
            row["in_transit"] += sup.qty
        elif sup.kind == "city":
            cities[(sup.city, pr.id)] = cities.get((sup.city, pr.id), 0) + sup.qty
        elif sup.kind == "warehouse":
            warehouse[pr.id] = warehouse.get(pr.id, 0) + sup.qty
    geo = {c["name"]: c for c in demand_data()["cities"]}
    return {
        "pvz": list(pvz.values()),
        "cities": [{"city": c, "product_id": pid, "product": prods[pid].name, "qty": q, "lat": geo[c]["lat"], "lon": geo[c]["lon"]}
                   for (c, pid), q in cities.items() if c in geo],
        "base_city": {"name": demand_data()["base_city"], "lat": geo[demand_data()["base_city"]]["lat"],
                      "lon": geo[demand_data()["base_city"]]["lon"]},
        "warehouse": [{"product_id": pid, "product": prods[pid].name, "qty": q} for pid, q in warehouse.items()],
    }


def seller_finance(s: Session, acc: Account) -> dict:
    """Кабинет продавца: начисления и экономика каждого заказа — цена, комиссия, логистика, к выплате."""
    from . import finance as fin
    from .scenarios import shelf_age

    pids = list(s.scalars(select(Product.id).where(Product.supplier_account_id == acc.id)))
    orders = list(s.scalars(select(Order).where(Order.product_id.in_(pids)).order_by(Order.id.desc()).limit(30))) if pids else []
    sales = []
    for o in orders:
        e = fin.order_economics(s, o)
        sales.append({"id": o.id, "product": s.get(Product, o.product_id).name, "mode": o.mode, "status": o.status,
                      "price": o.price, "commission": e["commission"], "logistics": e["seller_logistics"],
                      "payout": e["seller_payout"], "logistics_cost": e["logistics_cost"], "vs_hub": e["vs_hub"],
                      "done": o.status in ("issued", "returned")})
    shelves = []
    for st, pr, pvz in s.execute(select(Stock, Product, Pvz).join(Product, Stock.product_id == Product.id)
                                 .join(Pvz, Stock.pvz_id == Pvz.id)
                                 .where(Product.supplier_account_id == acc.id, Stock.qty > 0)).all():
        shelves.append({"product": pr.name, "pvz": pvz.name, "qty": st.qty, "age": shelf_age(st)})
    return {**fin.ledger_summary(s, "seller", acc.id), "sales": sales, "shelves": shelves,
            "rules": {"commission_pct": round(fin.COMMISSION * 100), "logistics_hub": fin.SELLER_LOGISTICS_HUB,
                      "logistics_shelf": fin.SELLER_LOGISTICS_SHELF, "free_days": fin.FREE_STORAGE_DAYS,
                      "late_rate": fin.SELLER_STORAGE_DAY, "remove_day": fin.AUTO_REMOVE_DAY}}
