import { useEffect, useState } from 'react';
import { CircleMarker, Polyline, Tooltip } from 'react-leaflet';
import { createOrder, fetchMyOrders, fetchOffers, fetchProducts, fetchPvz } from './api.js';
import { CityMap, FlyTo, StatusChip, WB, fmt, km, usePoll } from './common.jsx';

const PVZ_KEY = 'polka-buyer-pvz';

/** Покупатель: каталог → оформление → выбор ПВЗ → предложение забрать рядом дешевле или доставить в свой ПВЗ. */
export default function BuyerView({ user }) {
  const [pvz, setPvz] = useState([]);
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState(null);
  const [myPvz, setMyPvz] = useState(() => { try { return Number(localStorage.getItem(PVZ_KEY)) || null; } catch { return null; } });
  const [offer, setOffer] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [orders, setOrders] = useState([]);
  const [placed, setPlaced] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchPvz().then(setPvz).catch((e) => setError(e.message));
    fetchProducts().then(setProducts).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { try { if (myPvz) localStorage.setItem(PVZ_KEY, String(myPvz)); } catch { /* приватный режим */ } }, [myPvz]);
  useEffect(() => {
    setShowAll(false);
    if (!product || !myPvz) { setOffer(null); return; }
    fetchOffers(product.id, myPvz).then((o) => { setOffer(o); setError(''); }).catch((e) => setError(e.message));
  }, [product, myPvz]);
  usePoll(() => { fetchMyOrders().then(setOrders).catch(() => {}); }, []);

  const order = async (mode, sourceId) => {
    setBusy(true);
    try {
      const o = await createOrder({ product_id: product.id, buyer_pvz_id: myPvz, mode, source_pvz_id: sourceId });
      setPlaced(o);
      setProduct(null);
      setOrders(await fetchMyOrders());
      fetchProducts().then(setProducts);
      setError('');
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const me = pvz.find((p) => p.id === myPvz);
  const nearest = offer?.nearest;
  const others = (offer?.options || []).filter((o) => o.id !== nearest?.id);
  const stockIds = new Set((offer?.options || []).map((o) => o.id));

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <p className="eyebrow">Здравствуйте, {user.name}</p>
          <h1>{product ? 'Оформление заказа' : 'Каталог'}</h1>
        </header>
        {error && <p className="error" role="alert">{error}</p>}
        {placed && (
          <div className="card good" role="status">
            <h3>Заказ №{placed.id} оформлен</h3>
            <p>{placed.product.emoji} {placed.product.name} · {fmt(placed.paid)} ₽{placed.discount ? ` (скидка ${placed.discount} ₽)` : ''}</p>
            <p className="muted small">{placed.status_label}. {['ready'].includes(placed.status) ? `Код выдачи ${placed.code}.` : 'Статус обновляется в «Моих заказах».'}</p>
            <button type="button" className="link" onClick={() => setPlaced(null)}>Закрыть</button>
          </div>
        )}

        {!product ? (
          <section className="block">
            <div className="products">
              {products.map((p) => (
                <button type="button" key={p.id} className="product" onClick={() => { setProduct(p); setPlaced(null); }}>
                  <span className="emoji" aria-hidden="true">{p.emoji}</span>
                  <span className="pname">{p.name}</span>
                  <span className="pprice">{fmt(p.price)} ₽</span>
                  <span className="pstock">{p.supplier ? `от «${p.supplier}» · ` : ''}{p.in_city ? `в ПВЗ города: ${p.in_city} шт.` : 'в ПВЗ города нет'}</span>
                </button>
              ))}
            </div>
          </section>
        ) : (
          <section className="block">
            <div className="card">
              <p className="row-between"><b>{product.emoji} {product.name}</b><b>{fmt(product.price)} ₽</b></p>
              <button type="button" className="link" onClick={() => setProduct(null)}>← Вернуться в каталог</button>
            </div>
            <label className="field" htmlFor="buyer-pvz"><span>ПВЗ получения (или нажмите на точку на карте)</span>
              <select id="buyer-pvz" value={myPvz || ''} onChange={(e) => setMyPvz(Number(e.target.value) || null)}>
                <option value="">— выберите ПВЗ —</option>
                {pvz.map((p) => <option key={p.id} value={p.id}>{p.name}{p.address ? ` · ${p.address}` : ''}</option>)}
              </select>
            </label>

            {offer && (offer.here ? (
              <div className="card good">
                <h3>Товар уже в выбранном ПВЗ</h3>
                <p>Заберите сегодня за <b>{fmt(offer.pickup_price)} ₽</b> <s className="muted">{fmt(offer.price)} ₽</s> — курьер не нужен, поэтому дешевле.</p>
                <button type="button" className="primary" disabled={busy} onClick={() => order('pickup', offer.here.id)}>Забрать сегодня за {fmt(offer.pickup_price)} ₽</button>
              </div>
            ) : nearest ? (
              <>
                <div className="card good">
                  <h3>Этот товар уже лежит рядом — заберите сегодня дешевле</h3>
                  <p><b>«{nearest.name}»</b>{nearest.address ? `, ${nearest.address}` : ''}</p>
                  <p className="muted">{km(nearest.distance_m)} от выбранного ПВЗ · ~{nearest.walk_min} мин пешком · на полке {nearest.qty} шт.</p>
                  <p className="price-line"><b>{fmt(offer.pickup_price)} ₽</b> <s className="muted">{fmt(offer.price)} ₽</s> <span className="chip good">−{offer.discount} ₽ за самовывоз</span></p>
                  <button type="button" className="primary" disabled={busy} onClick={() => order('pickup', nearest.id)}>Забрать там за {fmt(offer.pickup_price)} ₽</button>
                </div>
                <div className="card">
                  <h3>Или доставим в «{me?.name}»</h3>
                  <p className="muted">Завтра, {fmt(offer.price)} ₽. Курьер привезёт из «{nearest.name}».</p>
                  <button type="button" className="secondary" disabled={busy} onClick={() => order('deliver', nearest.id)}>Заказать в мой ПВЗ за {fmt(offer.price)} ₽</button>
                </div>
              </>
            ) : (
              <div className="card">
                <h3>В ПВЗ города этого товара нет</h3>
                <p className="muted">Заказ поедет со склада через хаб — {offer.hub_days} дня, {fmt(offer.price)} ₽.</p>
                <button type="button" className="primary" disabled={busy} onClick={() => order('hub', null)}>Заказать через хаб</button>
              </div>
            ))}

            {others.length > 0 && (
              <button type="button" className="link" onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Скрыть другие ПВЗ' : `Посмотреть в других ПВЗ (${others.length})`}
              </button>
            )}
            {showAll && (
              <ul className="list">
                {others.map((o) => (
                  <li key={o.id}>
                    <div><b>{o.name}</b><br /><span className="muted small">{km(o.distance_m)} · ~{o.walk_min} мин пешком · {o.qty} шт.</span></div>
                    <button type="button" className="secondary small-btn" disabled={busy} onClick={() => order('pickup', o.id)}>Забрать за {fmt(offer.pickup_price)} ₽</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        <section className="block">
          <h2>Мои заказы</h2>
          {!orders.length && <p className="muted small">Заказов пока нет.</p>}
          <ul className="list">
            {orders.map((o) => (
              <li key={o.id} className="order">
                <div>
                  <b>№{o.id} · {o.product.emoji} {o.product.name}</b> · {fmt(o.paid)} ₽
                  <br /><StatusChip status={o.status} label={o.status_label} />
                  <br /><span className="muted small">
                    {o.mode === 'pickup' ? `Забрать в «${o.dest.name}»` : o.mode === 'hub' ? `Через хаб в «${o.dest.name}»` : `«${o.source.name}» → «${o.dest.name}»`}
                    {o.courier && !['issued', 'delivered'].includes(o.status) ? ` · курьер ${o.courier.name}` : ''}
                  </span>
                </div>
                {['ready', 'delivered'].includes(o.status) && <span className="code" title="Код выдачи">{o.code}</span>}
              </li>
            ))}
          </ul>
        </section>
      </aside>

      <CityMap>
        <FlyTo points={nearest && me ? [[me.lat, me.lon], [nearest.lat, nearest.lon]] : me ? [[me.lat, me.lon]] : []} />
        {pvz.map((p) => {
          const isMe = p.id === myPvz;
          const has = stockIds.has(p.id);
          return (
            <CircleMarker
              key={p.id}
              center={[p.lat, p.lon]}
              radius={isMe ? 10 : has ? 7 : 4}
              pathOptions={{ color: isMe ? '#E8742A' : has ? '#2F7A4D' : WB, weight: isMe ? 4 : 2, fillColor: has ? '#2F7A4D' : isMe ? WB : '#FFFFFF', fillOpacity: 1 }}
              eventHandlers={{ click: () => setMyPvz(p.id) }}
            >
              <Tooltip>{p.name}{p.address ? <><br />{p.address}</> : null}{isMe ? <><br /><b>ПВЗ получения</b></> : null}{has ? <><br />товар на полке</> : null}</Tooltip>
            </CircleMarker>
          );
        })}
        {me && nearest && nearest.id !== me.id && (
          <Polyline positions={[[me.lat, me.lon], [nearest.lat, nearest.lon]]} pathOptions={{ color: '#2F7A4D', weight: 3, dashArray: '6 8' }} />
        )}
      </CityMap>
    </div>
  );
}
