import { useEffect, useState } from 'react';
import { CircleMarker, Polyline, Tooltip } from 'react-leaflet';
import { acceptSupply, addStock, confirmAssembled, fetchProducts, fetchPvzDashboard, issueOrder, removeStock } from './api.js';
import { CityMap, FlyTo, Notes, StatusChip, WB, km, usePoll } from './common.jsx';

/** ПВЗ после входа: уведомления, сборка заказов, выдача по коду, полка с товарами. */
export default function PvzView({ user }) {
  const [products, setProducts] = useState([]);
  const [dash, setDash] = useState(null);
  const [error, setError] = useState('');
  const [stockForm, setStockForm] = useState({ product_id: '', qty: 5 });
  const [codes, setCodes] = useState({});
  const [confirmDel, setConfirmDel] = useState(null); // id товара, который ждёт подтверждения удаления
  const [busy, setBusy] = useState(false);

  useEffect(() => { fetchProducts().then(setProducts).catch(() => {}); }, []);
  usePoll(() => { fetchPvzDashboard().then((d) => { setDash(d); setError(''); }).catch((e) => setError(e.message)); }, []);

  const act = async (fn) => {
    setBusy(true);
    try { await fn(); setDash(await fetchPvzDashboard()); setError(''); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const me = dash?.pvz || user.pvz;
  const outgoing = dash ? [...dash.to_assemble, ...dash.to_courier] : [];

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <p className="eyebrow">Пункт выдачи</p>
          <h1>{me?.name}</h1>
          <p className="muted">{me?.address || 'Адрес не указан'}</p>
        </header>
        {error && <p className="error" role="alert">{error}</p>}

        {dash && (
          <>
            <section className="block">
              <h2>Уведомления</h2>
              <Notes items={dash.notifications} />
            </section>

            {dash.supplies.length > 0 && (
              <section className="block">
                <h2>Приёмка от поставщиков <span className="badge">{dash.supplies.length}</span></h2>
                {dash.supplies.map((sp) => (
                  <div key={sp.id} className="card task">
                    <p><b>{sp.product.emoji} {sp.product.name} — {sp.qty} шт.</b></p>
                    <p className="muted small">Поставщик «{sp.supplier}» · отправлено в {sp.created_at}. Проверьте количество и поставьте на полку.</p>
                    <button type="button" className="primary" disabled={busy} onClick={() => act(() => acceptSupply(sp.id))}>Принять на полку</button>
                  </div>
                ))}
              </section>
            )}

            <section className="block">
              <h2>Собрать заказ {dash.to_assemble.length > 0 && <span className="badge">{dash.to_assemble.length}</span>}</h2>
              {!dash.to_assemble.length && <p className="muted small">Новых заказов на сборку нет.</p>}
              {dash.to_assemble.map((o) => (
                <div key={o.id} className="card task">
                  <p><b>№{o.id} · {o.product.emoji} {o.product.name}</b></p>
                  <p className="muted small">Возьмите с полки и упакуйте. Поедет в «{o.dest.name}» ({km(o.distance_m)}), покупатель {o.buyer_name}.</p>
                  <button type="button" className="primary" disabled={busy} onClick={() => act(() => confirmAssembled(o.id))}>Подтвердить сбор заказа</button>
                </div>
              ))}
            </section>

            {dash.to_courier.length > 0 && (
              <section className="block">
                <h2>Передать курьеру</h2>
                <ul className="list">
                  {dash.to_courier.map((o) => (
                    <li key={o.id}><div><b>№{o.id} · {o.product.name}</b><br /><StatusChip status={o.status} label={o.courier ? `${o.status_label}: ${o.courier.name}` : o.status_label} /></div></li>
                  ))}
                </ul>
              </section>
            )}

            <section className="block">
              <h2>Выдать покупателю {dash.to_issue.length > 0 && <span className="badge">{dash.to_issue.length}</span>}</h2>
              {!dash.to_issue.length && <p className="muted small">Сейчас выдавать нечего.</p>}
              {dash.to_issue.map((o) => (
                <form key={o.id} className="card task" onSubmit={(e) => { e.preventDefault(); act(() => issueOrder(o.id, codes[o.id] || '')); }}>
                  <p><b>№{o.id} · {o.product.emoji} {o.product.name}</b> — {o.buyer_name}</p>
                  <p className="muted small">{o.mode === 'pickup' ? `Самовывоз с нашей полки, оплачено ${o.paid} ₽` : `Привёз курьер ${o.courier?.name || ''}`}</p>
                  <div className="actions">
                    <input aria-label="Код выдачи" className="code-input" placeholder="Код" inputMode="numeric" maxLength={4}
                      value={codes[o.id] || ''} onChange={(e) => setCodes({ ...codes, [o.id]: e.target.value })} />
                    <button type="submit" className="primary" disabled={busy}>Выдать</button>
                  </div>
                </form>
              ))}
            </section>

            {dash.incoming.length > 0 && (
              <section className="block">
                <h2>Едут к нам</h2>
                <ul className="list">
                  {dash.incoming.map((o) => (
                    <li key={o.id}><div><b>№{o.id} · {o.product.name}</b> из «{o.source.name}»<br /><StatusChip status={o.status} label={o.status_label} /></div></li>
                  ))}
                </ul>
              </section>
            )}

            <section className="block">
              <h2>Товары на полке</h2>
              <form className="actions" onSubmit={(e) => { e.preventDefault(); if (stockForm.product_id) act(() => addStock(Number(stockForm.product_id), Number(stockForm.qty))); }}>
                <select aria-label="Товар" value={stockForm.product_id} onChange={(e) => setStockForm({ ...stockForm, product_id: e.target.value })}>
                  <option value="">— какой товар есть —</option>
                  {products.map((p) => <option key={p.id} value={p.id}>{p.emoji} {p.name}</option>)}
                </select>
                <input aria-label="Количество" type="number" min={1} max={500} className="qty-input" value={stockForm.qty}
                  onChange={(e) => setStockForm({ ...stockForm, qty: e.target.value })} />
                <button type="submit" className="primary" disabled={busy || !stockForm.product_id}>Добавить</button>
              </form>
              {!dash.stock.length && <p className="muted small">Полка пуста — добавьте товары, которые у вас есть.</p>}
              <ul className="list">
                {dash.stock.map((s) => (
                  <li key={s.id} className="stock-row">
                    <span className="stock-name">{s.emoji} {s.name}</span>
                    <b className="stock-qty">{s.qty} шт.</b>
                    {confirmDel === s.id ? (
                      <span className="actions">
                        <span className="small">Убрать все {s.qty} шт.?</span>
                        <button type="button" className="danger small-btn" disabled={busy} aria-label={`Да, удалить с полки: ${s.name}`}
                          onClick={() => { setConfirmDel(null); act(() => removeStock(s.id)); }}>Да, удалить</button>
                        <button type="button" className="secondary small-btn" onClick={() => setConfirmDel(null)}>Отмена</button>
                      </span>
                    ) : (
                      <span className="actions">
                        <button type="button" className="secondary small-btn" disabled={busy} title="Списать одну штуку"
                          aria-label={`Списать одну штуку: ${s.name}`} onClick={() => act(() => removeStock(s.id, 1))}>−1</button>
                        <button type="button" className="danger small-btn" disabled={busy}
                          aria-label={`Удалить с полки: ${s.name}`} onClick={() => setConfirmDel(s.id)}>Удалить</button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </aside>

      <CityMap>
        <FlyTo points={me ? [[me.lat, me.lon]] : []} />
        {me && (
          <CircleMarker center={[me.lat, me.lon]} radius={11} pathOptions={{ color: '#E8742A', weight: 4, fillColor: WB, fillOpacity: 1 }}>
            <Tooltip permanent direction="top">{me.name}</Tooltip>
          </CircleMarker>
        )}
        {outgoing.map((o) => (
          <Polyline key={`o${o.id}`} positions={[[o.source.lat, o.source.lon], [o.dest.lat, o.dest.lon]]} pathOptions={{ color: '#E8742A', weight: 3, dashArray: '6 8' }}>
            <Tooltip sticky>Заказ №{o.id} → «{o.dest.name}»</Tooltip>
          </Polyline>
        ))}
        {outgoing.map((o) => (
          <CircleMarker key={`d${o.id}`} center={[o.dest.lat, o.dest.lon]} radius={6} pathOptions={{ color: WB, weight: 2, fillColor: '#FFFFFF', fillOpacity: 1 }}>
            <Tooltip>{o.dest.name}</Tooltip>
          </CircleMarker>
        ))}
        {dash?.incoming.map((o) => (
          <Polyline key={`i${o.id}`} positions={[[o.source.lat, o.source.lon], [o.dest.lat, o.dest.lon]]} pathOptions={{ color: '#2F7A4D', weight: 3, dashArray: '6 8' }} />
        ))}
      </CityMap>
    </div>
  );
}
