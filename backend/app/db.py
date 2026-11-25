"""База данных: PostgreSQL в Docker, SQLite для быстрого локального запуска."""
import json
import os
import random
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


class Account(Base):
    """Учётная запись: покупатель, ПВЗ или курьер."""
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(10))          # buyer | pvz | courier
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


def _seed_stock(s: Session) -> None:
    """Ходовой товар на полках опорных ПВЗ (центров районов) и немного — в обычных."""
    from .geo import build_zones

    rng = random.Random(7)
    pvz = [{"id": p.id, "lat": p.lat, "lon": p.lon, "weight": p.weight}
           for p in s.scalars(select(Pvz).where(Pvz.source != "registered").order_by(Pvz.id))]
    centers = {z["center_id"] for z in build_zones(pvz, 700)}
    for p in pvz:
        chance = 0.65 if p["id"] in centers else 0.08
        for prod_id in range(1, len(PRODUCTS) + 1):
            if rng.random() < chance:
                s.add(Stock(pvz_id=p["id"], product_id=prod_id, qty=rng.randint(2, 8)))


def _seed_base(s: Session) -> None:
    """Товары и демо-аккаунты покупателей и ПВЗ (пароль у всех 1234). Курьеров нет — их регистрируют сами."""
    from .auth import hash_password

    for i, (name, price, emoji, cat) in enumerate(PRODUCTS, start=1):
        s.add(Product(id=i, name=name, price=price, emoji=emoji, category=cat))
    h, salt = hash_password(DEMO_PASSWORD, "0" * 32)  # одна соль для демо-аккаунтов, чтобы старт был быстрым
    for p in s.scalars(select(Pvz).order_by(Pvz.id)):
        s.add(Account(role="pvz", login=f"pvz{p.id}", name=p.name, pass_hash=h, salt=salt, pvz_id=p.id))
    for login, name in DEMO_BUYERS:
        s.add(Account(role="buyer", login=login, name=name, pass_hash=h, salt=salt))
    s.flush()
    _seed_stock(s)


def _ensure_columns() -> None:
    """Добавить в уже существующую базу столбцы, которые появились в новых версиях (без потери данных)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "products" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("products")}
    adds = {"category": "VARCHAR(40) DEFAULT 'other'", "supplier_account_id": "INTEGER", "supplier_name": "VARCHAR(100)"}
    with engine.begin() as conn:
        for col, ddl in adds.items():
            if col not in have:
                conn.execute(text(f"ALTER TABLE products ADD COLUMN {col} {ddl}"))
        if "category" not in have:  # проставить категории товарам, которые уже были в базе
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


def _remove_old_demo_couriers(s: Session) -> None:
    """Убрать демо-курьеров courier1…courier8 из ранних версий. Курьеров, которых
    зарегистрировали сами, не трогаем. Их активные заказы возвращаются в очередь."""
    demo = [a for a in s.scalars(select(Account).where(Account.role == "courier"))
            if a.login in {f"courier{i}" for i in range(1, 9)} and a.name in OLD_DEMO_COURIERS]
    if not demo:
        return
    ids = [a.courier_id for a in demo]
    for o in s.scalars(select(Order).where(Order.courier_id.in_(ids))):
        if o.status in ("courier_assigned", "in_transit"):
            o.status, o.route_json, o.road_m, o.courier_pay = "awaiting_courier", "", 0, 0
        o.courier_id = None
    s.execute(delete(Notification).where(Notification.target == "courier", Notification.target_id.in_(ids)))
    s.execute(delete(AuthToken).where(AuthToken.account_id.in_([a.id for a in demo])))
    for a in demo:
        s.delete(a)
    s.flush()
    s.execute(delete(Courier).where(Courier.id.in_(ids)))
    s.commit()
    from .service import dispatch_waiting  # заказы из очереди — оставшимся свободным курьерам
    dispatch_waiting(s)
    s.commit()


def reset_demo() -> None:
    """Сбросить демо: заказы и уведомления удаляются, полки и курьеры — как в начале.
    Учётные записи и зарегистрированные ПВЗ остаются."""
    with Session(engine) as s:
        s.execute(delete(Notification))
        s.execute(delete(Order))
        s.execute(delete(Supply))
        s.execute(delete(Stock))  # товары поставщиков остаются в каталоге, их поставки и полки — заново
        for c in s.scalars(select(Courier)):
            c.lat, c.lon, c.busy = c.home_lat, c.home_lon, False
        _seed_stock(s)
        s.commit()


def get_session():
    with SessionLocal() as s:
        yield s
