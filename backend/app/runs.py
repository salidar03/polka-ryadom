"""Доставка рейсами: машина выходит по расписанию и объезжает ПВЗ в пересекающихся кругах.

Как собирается рейс:
1. Заказы, которые ПВЗ собрал и упаковал, ждут ближайшего рейса (10:00, 13:00, 16:00, 19:00).
2. В время рейса (или по кнопке водителя «Выехать в рейс сейчас») система берёт готовый заказ,
   ближайший к свободному водителю, и добавляет к нему другие готовые заказы, если их ПВЗ лежат
   в пересекающихся кругах радиусом 700 м — не дальше 1,4 км от уже взятой точки.
   В рейсе не больше 10 ПВЗ и 45 заказов.
3. Порядок объезда: от водителя к ближайшей точке, где есть что забрать или уже можно выгрузить
   (выгрузить заказ можно только после того, как он взят с точки). Путь — по улицам (OSRM).
4. Водитель на каждой точке жмёт «Заказ взят с точки», в ПВЗ покупателя — «Выгрузил заказы».
   Оплата рейса — как в аналитике: база + за остановку + за километр.
"""
import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Courier, Notification, Order, Product, Pvz, Run
from .economics import Params, run_cost_for
from .geo import dist_m
from .routing import road_route

RUN_SLOTS = [(10, 0), (13, 0), (16, 0), (19, 0)]
MAX_STOPS = 10
MAX_ORDERS = 45
LINK_M = 1400  # два круга по 700 м пересекаются, если центры ближе 1,4 км


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 остановка, 3 остановки, 5 остановок."""
    a, b = abs(n) % 100, abs(n) % 10
    if 10 < a < 20:
        return many
    return one if b == 1 else few if 2 <= b <= 4 else many


def next_slot(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    for h, m in RUN_SLOTS:
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t > now:
            return t
    h, m = RUN_SLOTS[0]
    return (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)


def last_slot(now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    passed = [now.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in RUN_SLOTS]
    passed = [t for t in passed if t <= now]
    return passed[-1] if passed else None


def schedule_text() -> str:
    return ", ".join(f"{h:02d}:{m:02d}" for h, m in RUN_SLOTS)


def _pos(s: Session) -> dict[int, tuple[float, float]]:
    return {p.id: (p.lat, p.lon) for p in s.scalars(select(Pvz))}


def _note(s: Session, target: str, target_id: int, order_id: int | None, text: str) -> None:
    s.add(Notification(target=target, target_id=target_id, order_id=order_id, text=text))


def _log(o: Order, text: str) -> None:
    h = json.loads(o.history or "[]")
    h.append({"time": datetime.now().strftime("%H:%M:%S"), "text": text})
    o.history = json.dumps(h, ensure_ascii=False)


def _grow(seed: Order, pool: list[Order], pos: dict) -> list[Order]:
    """Добавить к рейсу заказы, чьи ПВЗ лежат в пересекающихся кругах с уже взятыми точками."""
    chosen, stops = [seed], {seed.source_pvz_id, seed.dest_pvz_id}

    def gap(pid: int) -> float:
        return min(dist_m(pos[pid], pos[q]) for q in stops)

    for o in sorted((o for o in pool if o.id != seed.id), key=lambda o: gap(o.source_pvz_id)):
        if len(chosen) >= MAX_ORDERS:
            break
        new = {o.source_pvz_id, o.dest_pvz_id} - stops
        if len(stops | new) > MAX_STOPS:
            continue
        if all(gap(p) <= LINK_M for p in new):
            chosen.append(o)
            stops |= new
    return chosen


def plan_stops(start: tuple[float, float], orders: list[Order], pos: dict) -> list[dict]:
    """Порядок объезда: ближайшая точка, где есть что забрать или уже можно выгрузить."""
    to_pick = {o.id: o.source_pvz_id for o in orders}
    to_drop = {o.id: o.dest_pvz_id for o in orders}
    cur, stops = start, []
    while to_pick or to_drop:
        cand = set(to_pick.values()) | {d for oid, d in to_drop.items() if oid not in to_pick}
        nxt = min(cand, key=lambda p: dist_m(cur, pos[p]))
        picks = sorted(oid for oid, p in to_pick.items() if p == nxt)
        drops = sorted(oid for oid, d in to_drop.items() if d == nxt and oid not in to_pick)
        for oid in picks:
            del to_pick[oid]
        for oid in drops:
            del to_drop[oid]
        stops.append({"pvz_id": nxt, "pickups": picks, "drops": drops})
        cur = pos[nxt]
    return stops


def _create_run(s: Session, c: Courier, orders: list[Order], slot: str, pos: dict) -> Run:
    stops = plan_stops((c.lat, c.lon), orders, pos)
    route = road_route([(c.lat, c.lon)] + [pos[st["pvz_id"]] for st in stops])
    km = route["distance_m"] / 1000
    pay = round(run_cost_for(Params(), km, len(stops)))
    run = Run(courier_id=c.id, status="active", slot=slot, stops_json=json.dumps(stops), route_json=json.dumps(route),
              km=round(km, 2), pay=pay, orders_count=len(orders))
    s.add(run)
    s.flush()
    c.busy = True
    names = {p.id: p.name for p in s.scalars(select(Pvz).where(Pvz.id.in_([st["pvz_id"] for st in stops])))}
    for o in orders:
        o.run_id, o.courier_id, o.status = run.id, c.id, "in_run"
        _log(o, f"Включён в рейс №{run.id} ({slot}), водитель {c.name}")
        _note(s, "pvz", o.source_pvz_id, o.id, f"Заказ №{o.id} заберёт рейс №{run.id}, водитель {c.name}")
    km_text = f"{km:.1f}".replace(".", ",")
    _note(s, "courier", c.id, None,
          f"Рейс №{run.id} ({slot}): {len(stops)} {plural(len(stops), 'остановка', 'остановки', 'остановок')}, "
          f"{len(orders)} {plural(len(orders), 'заказ', 'заказа', 'заказов')}, {km_text} км, оплата {pay} ₽. "
          f"Первая точка — «{names[stops[0]['pvz_id']]}»")
    return run


def form_runs(s: Session, pool: list[Order], slot: str, courier: Courier | None = None) -> list[Run]:
    """Собрать рейсы из готовых заказов. Водитель — ближайший свободный к первой точке
    (или тот, кто нажал «Выехать в рейс сейчас»)."""
    pos = _pos(s)
    pool = sorted(pool, key=lambda o: (o.ready_at or o.created_at))
    runs = []
    while pool:
        free = [courier] if courier is not None else list(s.scalars(select(Courier).where(Courier.busy.is_(False))))
        free = [c for c in free if not c.busy]
        if not free:
            break
        seed, c = min(((o, c) for o in pool for c in free), key=lambda oc: dist_m((oc[1].lat, oc[1].lon), pos[oc[0].source_pvz_id]))
        chosen = _grow(seed, pool, pos)
        runs.append(_create_run(s, c, chosen, slot, pos))
        ids = {o.id for o in chosen}
        pool = [o for o in pool if o.id not in ids]
        if courier is not None:
            break
    return runs


def auto_form(s: Session, now: datetime | None = None) -> None:
    """В время рейса собрать рейсы из заказов, которые были готовы к этому времени."""
    ls = last_slot(now)
    if ls is None:
        return
    due = list(s.scalars(select(Order).where(Order.status == "awaiting_run", Order.ready_at <= ls)))
    if due and form_runs(s, due, ls.strftime("%H:%M")):
        s.commit()


def start_now(s: Session, courier_id: int) -> dict:
    """«Выехать в рейс сейчас»: водитель забирает готовые заказы, не дожидаясь расписания."""
    c = s.get(Courier, courier_id)
    if c.busy:
        raise HTTPException(409, "У вас уже есть рейс — сначала завершите его")
    pool = list(s.scalars(select(Order).where(Order.status == "awaiting_run")))
    if not pool:
        raise HTTPException(409, "Готовых к отправке заказов пока нет")
    runs = form_runs(s, pool, "сейчас", courier=c)
    s.commit()
    return run_view(s, runs[0])


def _active_run(s: Session, courier_id: int) -> Run | None:
    return s.scalars(select(Run).where(Run.courier_id == courier_id, Run.status == "active")).first()


def pick(s: Session, courier_id: int, order_id: int) -> dict:
    """«Заказ взят с точки»."""
    o = s.get(Order, order_id)
    run = _active_run(s, courier_id)
    if o is None or run is None or o.run_id != run.id:
        raise HTTPException(403, "Этого заказа нет в вашем рейсе")
    if o.status != "in_run":
        raise HTTPException(409, f"Заказ №{o.id} уже взят")
    c, src = s.get(Courier, courier_id), s.get(Pvz, o.source_pvz_id)
    o.status, o.picked_at = "in_transit", datetime.now()
    c.lat, c.lon = src.lat, src.lon
    _log(o, f"Водитель {c.name} взял заказ с точки «{src.name}»")
    _note(s, "pvz", o.dest_pvz_id, o.id, f"К вам едет заказ №{o.id} — рейс №{run.id}, водитель {c.name}")
    s.commit()
    return run_view(s, run)


def unload(s: Session, courier_id: int, run_id: int, stop_index: int) -> dict:
    """«Выгрузил заказы» в ПВЗ покупателя на этой остановке рейса."""
    run = _active_run(s, courier_id)
    if run is None or run.id != run_id:
        raise HTTPException(403, "Это не ваш текущий рейс")
    stops = json.loads(run.stops_json)
    if not (0 <= stop_index < len(stops)):
        raise HTTPException(404, "Нет такой остановки")
    stop = stops[stop_index]
    orders = [s.get(Order, oid) for oid in stop["drops"]]
    waiting = [o.id for o in orders if o.status == "in_run"]
    if waiting:
        raise HTTPException(409, f"Сначала возьмите с точки заказ №{waiting[0]}")
    c, pvz = s.get(Courier, courier_id), s.get(Pvz, stop["pvz_id"])
    c.lat, c.lon = pvz.lat, pvz.lon
    for o in orders:
        if o.status == "in_transit":
            o.status, o.delivered_at = "delivered", datetime.now()
            _log(o, f"Рейс №{run.id}: выгружен в «{pvz.name}»")
            _note(s, "pvz", pvz.id, o.id, f"Заказ №{o.id} доставлен — выдайте покупателю {o.buyer_name}, код {o.code}")
    all_orders = list(s.scalars(select(Order).where(Order.run_id == run.id)))
    if all(o.status in ("delivered", "issued") for o in all_orders):
        run.status, run.finished_at, c.busy = "done", datetime.now(), False
        _note(s, "courier", c.id, None, f"Рейс №{run.id} завершён. +{run.pay} ₽ к заработку")
    s.commit()
    return run_view(s, run)


def run_view(s: Session, run: Run) -> dict:
    stops = json.loads(run.stops_json)
    orders = {o.id: o for o in s.scalars(select(Order).where(Order.run_id == run.id))}
    products = {p.id: p for p in s.scalars(select(Product).where(Product.id.in_([o.product_id for o in orders.values()])))}

    def brief(oid: int) -> dict:
        o = orders[oid]
        pr = products[o.product_id]
        return {"id": o.id, "product": f"{pr.emoji} {pr.name}", "status": o.status}

    out, current = [], None
    for i, st in enumerate(stops):
        p = s.get(Pvz, st["pvz_id"])
        picks = [brief(x) for x in st["pickups"]]
        drops = [brief(x) for x in st["drops"]]
        done = all(x["status"] != "in_run" for x in picks) and all(x["status"] in ("delivered", "issued") for x in drops)
        if not done and current is None:
            current = i
        out.append({"index": i, "pvz": {"id": p.id, "name": p.name, "address": p.address, "lat": p.lat, "lon": p.lon},
                    "pickups": picks, "drops": drops, "done": done})
    return {"id": run.id, "status": run.status, "slot": run.slot, "km": run.km, "pay": run.pay,
            "orders": run.orders_count, "stops": out, "current": current, "route": json.loads(run.route_json),
            "cost_per_order": round(run.pay / max(1, run.orders_count))}


def courier_runs_today(s: Session, courier_id: int) -> list[Run]:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return list(s.scalars(select(Run).where(Run.courier_id == courier_id, Run.status == "done", Run.finished_at >= today)
                          .order_by(Run.finished_at.desc())))


def active_run(s: Session, courier_id: int) -> dict | None:
    run = _active_run(s, courier_id)
    return run_view(s, run) if run else None
