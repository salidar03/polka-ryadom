"""Деньги в системе: тарифы, начисления ПВЗ и продавцу, экономика одного заказа.

Все ставки — допущения команды (те же, что в расчётной модели и на слайдах):
  через хаб маркетплейс тратит 125 ₽ = забор 20 + хаб 35 + магистраль 25 + развоз 20 + выдача 25;
  с полки ПВЗ — 55 ₽ постоянных (пополнение 6 + приёмка 4 + хранение 0,8 ₽ × 10 дней + сборка 12 + выдача 25)
  и доля рейса машины, если заказ везут в другой ПВЗ (рейс = 900 + 120 ₽/остановка + 40 ₽/км).
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Ledger, Order, Product, Run

# --- ПВЗ: за что платим ---
PVZ_ACCEPT = 4            # ₽ за штуку при приёмке поставки на полку
PVZ_STORAGE_DAY = 0.8     # ₽ за штуку в день хранения
PVZ_ASSEMBLY = 12         # ₽ за сборку и упаковку заказа
PVZ_ISSUE = 25            # ₽ за выдачу покупателю
PVZ_RETURN = 15           # ₽ за приём и осмотр возврата
PVZ_PENALTY_MISSING = 50  # ₽ штраф: заказ оформлен, а товара на полке нет (расхождение остатков)
PVZ_CAPACITY = 300        # штук на полке «Полки рядом» — больше подсобка не вмещает
PEAK_HOURS = (17, 21)     # вечерний пик выдачи: сборка разрешена, но рейсы сюда не ставим

# --- хранение: кто платит ---
FREE_STORAGE_DAYS = 30    # первые 30 дней хранение оплачивает маркетплейс
SELLER_STORAGE_DAY = 1.6  # с 31-го дня — продавец, ₽ за штуку в день (двойной тариф)
AUTO_REMOVE_DAY = 45      # на 45-й день неликвид вывозится с полки продавцу
WARN_DAY = 25             # за 5 дней до платного периода предупреждаем

# --- продавец ---
COMMISSION = 0.20               # комиссия маркетплейса с цены
SELLER_LOGISTICS_HUB = 125      # тариф логистики продавцу через хаб
SELLER_LOGISTICS_SHELF = 110    # с полки ПВЗ — на 15 ₽ дешевле: стимул раскладывать товар

# --- себестоимость логистики маркетплейса ---
HUB_COST = 125
RESTOCK = 6
MODEL_STORAGE_DAYS = 10
SHELF_FIXED = RESTOCK + PVZ_ACCEPT + PVZ_STORAGE_DAY * MODEL_STORAGE_DAYS + PVZ_ASSEMBLY + PVZ_ISSUE  # 55 ₽
RUN_SHARE_ESTIMATE = 40         # 1 800 ₽ полного рейса / 45 заказов — пока рейс не прошёл

KIND_LABEL = {
    "accept": "Приёмка поставок", "storage": "Хранение", "assembly": "Сборка заказов", "issue": "Выдача",
    "return": "Приём возвратов", "penalty": "Штрафы", "sale": "Продажи", "commission": "Комиссия",
    "logistics": "Логистика", "refund": "Возвраты покупателям", "storage_late": "Хранение после 30 дней",
    "removal": "Вывоз неликвида",
}


def post(s: Session, party: str, party_id: int | None, kind: str, amount: float, note: str,
         order_id: int | None = None) -> None:
    """Записать начисление (+) или списание (−) в журнал ПВЗ или продавца."""
    if party_id is None or not amount:
        return
    s.add(Ledger(party=party, party_id=party_id, kind=kind, amount=round(amount, 2), note=note, order_id=order_id))


def seller_of(s: Session, o: Order) -> int | None:
    return s.get(Product, o.product_id).supplier_account_id


def order_economics(s: Session, o: Order) -> dict:
    """Экономика одного заказа: что платит покупатель, что получает продавец и ПВЗ, во что обходится логистика."""
    price = o.price
    commission = round(price * COMMISSION)
    if o.mode == "hub":
        cost, fee, run_share = HUB_COST, SELLER_LOGISTICS_HUB, 0
        parts = [("Забор у продавца", 20), ("Сортировка в хабе", 35), ("Магистраль", 25), ("Развоз по ПВЗ", 20), ("Выдача в ПВЗ", 25)]
    else:
        run = s.get(Run, o.run_id) if o.run_id else None
        run_share = round(run.pay / max(1, run.orders_count)) if run else (RUN_SHARE_ESTIMATE if o.mode == "deliver" else 0)
        cost, fee = round(SHELF_FIXED + run_share), SELLER_LOGISTICS_SHELF
        parts = [("Пополнение полки", RESTOCK), ("Приёмка в ПВЗ", PVZ_ACCEPT),
                 ("Хранение ~10 дней", round(PVZ_STORAGE_DAY * MODEL_STORAGE_DAYS)), ("Сборка в ПВЗ", PVZ_ASSEMBLY),
                 ("Выдача в ПВЗ", PVZ_ISSUE)]
        if o.mode == "deliver":
            parts.append((f"Доля рейса №{run.id}" if run else "Доля рейса (оценка)", run_share))
    returned = o.status == "returned"
    pvz_income = sum(v for k, v in parts if k in ("Приёмка в ПВЗ", "Сборка в ПВЗ", "Выдача в ПВЗ") or k.startswith("Хранение"))
    return {
        "buyer_paid": price - o.discount, "discount": o.discount, "price": price,
        "commission": commission, "seller_logistics": fee,
        "seller_payout": 0 if returned else price - commission - fee,
        "logistics_cost": cost, "logistics_parts": [{"name": k, "rub": v} for k, v in parts],
        "run_share": run_share, "vs_hub": HUB_COST - cost,
        "marketplace_margin": (0 if returned else commission) + fee - cost - o.discount,
        "pvz_income": pvz_income, "returned": returned,
    }


def ledger_summary(s: Session, party: str, party_id: int, limit: int = 12) -> dict:
    rows = list(s.scalars(select(Ledger).where(Ledger.party == party, Ledger.party_id == party_id).order_by(Ledger.id.desc())))
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    by_kind: dict[str, float] = {}
    for r in rows:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + r.amount
    return {
        "total": round(sum(r.amount for r in rows)),
        "today": round(sum(r.amount for r in rows if r.created_at >= today)),
        "by_kind": [{"kind": k, "name": KIND_LABEL.get(k, k), "rub": round(v, 2)} for k, v in sorted(by_kind.items(), key=lambda kv: -abs(kv[1]))],
        "entries": [{"id": r.id, "kind": r.kind, "name": KIND_LABEL.get(r.kind, r.kind), "rub": r.amount, "note": r.note,
                     "order_id": r.order_id, "time": r.created_at.strftime("%d.%m %H:%M")} for r in rows[:limit]],
    }


def pvz_tariffs() -> list[dict]:
    return [
        {"name": "Приёмка поставки", "rate": f"{PVZ_ACCEPT} ₽ за штуку"},
        {"name": "Хранение", "rate": f"{PVZ_STORAGE_DAY:.1f} ₽ за штуку в день".replace(".", ",")},
        {"name": "Сборка и упаковка заказа", "rate": f"{PVZ_ASSEMBLY} ₽"},
        {"name": "Выдача покупателю", "rate": f"{PVZ_ISSUE} ₽"},
        {"name": "Приём возврата", "rate": f"{PVZ_RETURN} ₽"},
        {"name": "Штраф: товара нет на полке", "rate": f"−{PVZ_PENALTY_MISSING} ₽"},
    ]


def pvz_month_estimate(orders_per_day: int = 40) -> int:
    """Дополнительный доход ПВЗ в месяц от «Полки рядом» при orders_per_day заказах с полки в день (как в модели).
    Выдачу не считаем: за неё ПВЗ платят и в обычной схеме через хаб."""
    per_order = PVZ_ACCEPT + PVZ_STORAGE_DAY * MODEL_STORAGE_DAYS + PVZ_ASSEMBLY
    return round(per_order * orders_per_day * 30, -2)
