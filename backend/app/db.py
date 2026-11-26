"""База данных: PostgreSQL в Docker, SQLite для быстрого локального запуска."""
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'polka.db').as_posix()}")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Pvz(Base):
    __tablename__ = "pvz"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))   # osm | model | team | registered
    weight: Mapped[int] = mapped_column(Integer)      # спрос вокруг точки


class TestOrder(Base):
    """Тестовый набор заказов для аналитики: одни и те же заказы считаем в обеих схемах."""
    __tablename__ = "test_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch: Mapped[str] = mapped_column(String(100), index=True)
    pvz_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(20))     # candidate | other_city | bulky | no_stock


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[int] = mapped_column(Integer)
    emoji: Mapped[str] = mapped_column(String(8), default="")
    category: Mapped[str] = mapped_column(String(40), default="other")
    supplier_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None — товар Wildberries
    supplier_name: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Supply(Base):
    """Поставка от поставщика: часть партии на полку ПВЗ, в другой город или на склад WB."""
    __tablename__ = "supplies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    supplier_account_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(12))        # shelf — полка ПВЗ Йошкар-Олы | city — другой город | warehouse — склад WB
    city: Mapped[str] = mapped_column(String(60))
    pvz_id: Mapped[int | None] = mapped_column(ForeignKey("pvz.id"), nullable=True)
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))      # in_transit — едет | accepted — принята
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Stock(Base):
    """Полка ПВЗ: сколько штук товара лежит в пункте."""
    __tablename__ = "stock"
    pvz_id: Mapped[int] = mapped_column(ForeignKey("pvz.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)


class Courier(Base):
    __tablename__ = "couriers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    home_lat: Mapped[float] = mapped_column(Float)   # где курьер начинает смену (для сброса демо)
    home_lon: Mapped[float] = mapped_column(Float)
    busy: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(Base):
    """Рейс машины: в определённое время объезжает ПВЗ в пересекающихся кругах, забирает упакованные заказы и развозит их."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    courier_id: Mapped[int] = mapped_column(ForeignKey("couriers.id"), index=True)
    status: Mapped[str] = mapped_column(String(10))            # active — в пути | done — завершён
    slot: Mapped[str] = mapped_column(String(10))              # «13:00» или «сейчас»
    stops_json: Mapped[str] = mapped_column(Text)              # [{pvz_id, pickups: [id], drops: [id]}] по порядку
    route_json: Mapped[str] = mapped_column(Text, default="")  # путь по улицам через все остановки
    km: Mapped[float] = mapped_column(Float, default=0)
    pay: Mapped[int] = mapped_column(Integer, default=0)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Account(Base):
    """Учётная запись: покупатель, ПВЗ, водитель (курьер) или поставщик."""
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(10))          # buyer | pvz | courier | supplier
    login: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    pass_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(32))
    pvz_id: Mapped[int | None] = mapped_column(ForeignKey("pvz.id"), nullable=True)
    courier_id: Mapped[int | None] = mapped_column(ForeignKey("couriers.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    buyer_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    buyer_name: Mapped[str] = mapped_column(String(100))
    buyer_pvz_id: Mapped[int] = mapped_column(ForeignKey("pvz.id"))
    source_pvz_id: Mapped[int | None] = mapped_column(ForeignKey("pvz.id"), nullable=True)  # где лежит товар
    dest_pvz_id: Mapped[int] = mapped_column(ForeignKey("pvz.id"))                         # где выдадут
    mode: Mapped[str] = mapped_column(String(20))       # pickup | deliver | hub
    status: Mapped[str] = mapped_column(String(30))
    code: Mapped[str] = mapped_column(String(8))        # код выдачи
    price: Mapped[int] = mapped_column(Integer, default=0)
    discount: Mapped[int] = mapped_column(Integer, default=0)
    courier_id: Mapped[int | None] = mapped_column(ForeignKey("couriers.id"), nullable=True)
    courier_pay: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # рейс, который везёт заказ
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)      # ПВЗ упаковал — ждёт рейса
    picked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)     # водитель взял с точки
    distance_m: Mapped[int] = mapped_column(Integer, default=0)   # по прямой между ПВЗ
    road_m: Mapped[int] = mapped_column(Integer, default=0)       # по дорогам: подъезд + доставка
    route_json: Mapped[str] = mapped_column(Text, default="")     # маршрут курьера по улицам
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    history: Mapped[str] = mapped_column(Text, default="[]")      # JSON: [{time, text}]


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target: Mapped[str] = mapped_column(String(10))      # pvz | courier
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


PRODUCTS = [
    ("Беспроводные наушники", 1490, "🎧", "electronics"), ("Зарядка USB-C 20 Вт", 690, "🔌", "electronics"),
    ("Чехол для телефона", 390, "📱", "electronics"), ("Пауэрбанк 10 000 мА·ч", 1290, "🔋", "electronics"),
    ("Футболка базовая", 590, "👕", "clothes"), ("Носки, 5 пар", 350, "🧦", "clothes"),
    ("Крем для рук", 290, "🧴", "beauty"), ("Шампунь 400 мл", 420, "🧼", "beauty"), ("Кружка керамическая", 450, "☕", "home"),
    ("Настольная игра", 990, "🎲", "hobby"), ("Детский конструктор", 1190, "🧱", "kids"), ("Корм для кошек 1,5 кг", 640, "🐈", "pets"),
]
# Демо-курьеры из ранних версий: при запуске удаляем их — курьеров команда создаёт сама через регистрацию.
OLD_DEMO_COURIERS = ["Алексей", "Мария", "Дмитрий", "Ольга", "Иван", "Светлана", "Артём", "Наталья"]
DEMO_PASSWORD = "1234"
DEMO_BUYERS = [("ivan", "Иван"), ("maria", "Мария")]


def _seed_base(s: Session) -> None:
    """Товары и демо-аккаунты покупателей и ПВЗ (пароль у всех 1234). Курьеров нет — их регистрируют сами.
    Полки пустые: товар в ПВЗ привозят поставщики (или ПВЗ добавляет сам)."""
    from .auth import hash_password

    for i, (name, price, emoji, cat) in enumerate(PRODUCTS, start=1):
        s.add(Product(id=i, name=name, price=price, emoji=emoji, category=cat))
    h, salt = hash_password(DEMO_PASSWORD, "0" * 32)  # одна соль для демо-аккаунтов, чтобы старт был быстрым
    for p in s.scalars(select(Pvz).order_by(Pvz.id)):
        s.add(Account(role="pvz", login=f"pvz{p.id}", name=p.name, pass_hash=h, salt=salt, pvz_id=p.id))
    for login, name in DEMO_BUYERS:
        s.add(Account(role="buyer", login=login, name=name, pass_hash=h, salt=salt))
    s.flush()


def _ensure_columns() -> None:
    """Добавить в уже существующую базу столбцы, которые появились в новых версиях (без потери данных)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = insp.get_table_names()
    adds = {
        "products": {"category": "VARCHAR(40) DEFAULT 'other'", "supplier_account_id": "INTEGER", "supplier_name": "VARCHAR(100)"},
        "orders": {"run_id": "INTEGER", "ready_at": "TIMESTAMP", "picked_at": "TIMESTAMP"},
    }
    with engine.begin() as conn:
        for table, cols in adds.items():
            if table not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in cols.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            if table == "products" and "category" not in have:  # проставить категории товарам, которые уже были в базе
                for i, (_, _, _, cat) in enumerate(PRODUCTS, start=1):
                    conn.execute(text("UPDATE products SET category = :c WHERE id = :i"), {"c": cat, "i": i})


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_columns()
    with Session(engine) as s:
        if s.scalar(select(func.count()).select_from(Pvz)) == 0:
            for p in json.loads((DATA_DIR / "pvz.json").read_text(encoding="utf-8")):
                s.add(Pvz(id=p["id"], name=p["name"], address=p.get("address", ""), lat=p["lat"], lon=p["lon"],
                          source=p["source"], weight=p["weight"]))
            s.commit()
        if s.scalar(select(func.count()).select_from(Product)) == 0:
            _seed_base(s)
            s.commit()
        _remove_old_demo_couriers(s)
        _migrate_to_runs(s)


def _migrate_to_runs(s: Session) -> None:
    """Версия 1.0 возила каждый заказ отдельным курьером. Теперь — рейсами: собранные заказы,
    у которых ещё нет рейса, становятся «упакован, ждёт рейса», курьеры без рейса — свободны."""
    old = list(s.scalars(select(Order).where(Order.run_id.is_(None),
                                             Order.status.in_(("awaiting_courier", "courier_assigned", "in_transit")))))
    for o in old:
        o.status, o.courier_id, o.courier_pay, o.route_json, o.road_m = "awaiting_run", None, 0, "", 0
        o.ready_at = o.ready_at or datetime.now()
    in_run = set(s.scalars(select(Run.courier_id).where(Run.status == "active")))
    for c in s.scalars(select(Courier).where(Courier.busy.is_(True))):
        if c.id not in in_run:
            c.busy = False
    s.commit()


def _remove_old_demo_couriers(s: Session) -> None:
    """Убрать демо-курьеров courier1…courier8 из ранних версий. Курьеров, которых
    зарегистрировали сами, не трогаем. Их активные заказы возвращаются в очередь."""
    demo = [a for a in s.scalars(select(Account).where(Account.role == "courier"))
            if a.login in {f"courier{i}" for i in range(1, 9)} and a.name in OLD_DEMO_COURIERS]
    if not demo:
        return
    ids = [a.courier_id for a in demo]
    for o in s.scalars(select(Order).where(Order.courier_id.in_(ids))):
        if o.status in ("courier_assigned", "in_transit", "in_run"):
            o.status, o.route_json, o.road_m, o.courier_pay, o.run_id = "awaiting_run", "", 0, 0, None
            o.ready_at = o.ready_at or datetime.now()
        o.courier_id = None
    s.execute(delete(Run).where(Run.courier_id.in_(ids)))
    s.execute(delete(Notification).where(Notification.target == "courier", Notification.target_id.in_(ids)))
    s.execute(delete(AuthToken).where(AuthToken.account_id.in_([a.id for a in demo])))
    for a in demo:
        s.delete(a)
    s.flush()
    s.execute(delete(Courier).where(Courier.id.in_(ids)))
    s.commit()


def reset_demo() -> None:
    """Сбросить демо: заказы, рейсы, поставки и уведомления удаляются, полки пустеют,
    водители возвращаются туда, где начинали смену. Учётные записи и ПВЗ остаются."""
    with Session(engine) as s:
        s.execute(delete(Notification))
        s.execute(delete(Order))
        s.execute(delete(Run))
        s.execute(delete(Supply))
        s.execute(delete(Stock))  # товары поставщиков остаются в каталоге, их поставки и полки — заново
        for c in s.scalars(select(Courier)):
            c.lat, c.lon, c.busy = c.home_lat, c.home_lon, False
        s.commit()


def get_session():
    with SessionLocal() as s:
        yield s
