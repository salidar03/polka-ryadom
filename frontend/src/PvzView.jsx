import { useEffect, useState } from 'react';
import { CircleMarker, Polyline, Tooltip } from 'react-leaflet';
import {
  acceptReturn, acceptSupply, addStock, confirmAssembled, demoDays, fetchProducts, fetchPvzDashboard, handoverSupply,
  hubArrived, issueOrder, removeStock, reportMissing,
} from './api.js';
import { CityMap, FlyTo, Notes, StatusChip, WB, fmt, km, usePoll } from './common.jsx';

const rub = (x) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Math.abs(x))} ₽`;

/** «Финансы ПВЗ»: сколько начислено за приёмку, хранение, сборку, выдачу и возвраты, штрафы и тарифы. */
function PvzFinance({ f }) {
  return (
    <>
      <section className="block">
        <h2>Доход ПВЗ от «Полки рядом»</h2>
        <div className="kpis">
          <div className="kpi wide">
            <p className="kpi-k">Начислено всего</p>
            <p className="kpi-v accent">{rub(f.total)}</p>
            <p className="kpi-n">сегодня {rub(f.today)}</p>
          </div>
          <div className="kpi wide">
            <p className="kpi-k">Оценка в месяц при 40 заказах с полки в день</p>
            <p className="kpi-v">≈ {fmt(f.month_estimate)} ₽</p>
            <p className="kpi-n">приёмка 4 + хранение 8 + сборка 12 = 24 ₽ на заказ сверх обычной выдачи</p>
          </div>
        </div>
        {f.by_kind.length > 0 ? (
          <table className="ptable">
            <thead><tr><th>Статья</th><th>Сумма</th></tr></thead>
            <tbody>{f.by_kind.map((k) => <tr key={k.kind}><td>{k.name}</td><td className={k.rub < 0 ? 'neg' : ''}><b>{rub(k.rub)}</b></td></tr>)}</tbody>
          </table>
        ) : <p className="muted small">Начислений пока нет: они появятся после приёмки, сборки и выдачи.</p>}
      </section>
      <section className="block">
        <h2>Тарифы, бонусы и штрафы</h2>
        <table className="ptable">
          <tbody>{f.tariffs.map((x) => <tr key={x.name}><td>{x.name}</td><td><b>{x.rate}</b></td></tr>)}</tbody>
        </table>
        <ul className="muted small rules">
          <li>Хранение первые {f.free_days} дней оплачивает маркетплейс, дальше — продавец по двойному тарифу; на {f.remove_day}-й день неликвид вывозится.</li>
          <li>Полка ограничена {f.capacity} штуками — больше подсобка не вмещает, лишнее не принимаем.</li>
          <li>Вечером (17–21) выдача в приоритете: рейсы забирают упакованное днём, сборку можно оставить до утреннего рейса.</li>
          <li>Штраф только за расхождение остатков: оформлен заказ, а товара на полке нет.</li>
        </ul>
      </section>
      <section className="block">
        <h2>Последние начисления</h2>
        {!f.entries.length && <p className="muted small">Пока пусто.</p>}
        <ul className="list">
          {f.entries.map((x) => (
            <li key={x.id}><div><b>{x.name}</b><br /><span className="muted small">{x.time} · {x.note}</span></div><b className={x.rub < 0 ? 'neg' : 'pos'}>{rub(x.rub)}</b></li>
          ))}
        </ul>
      </section>
    </>
  );
}

/** ПВЗ после входа: уведомления, сборка заказов, выдача по коду, полка с товарами. */
export default function PvzView({ user }) {
  const [products, setProducts] = useState([]);
  const [dash, setDash] = useState(null);
  const [error, setError] = useState('');
  const [stockForm, setStockForm] = useState({ product_id: '', qty: 5 });
  const [codes, setCodes] = useState({});
  const [confirmDel, setConfirmDel] = useState(null); // id товара, который ждёт подтверждения удаления
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState('work');
  const [confirmMissing, setConfirmMissing] = useState(null); // заказ, по которому ждём подтверждения «товара нет»
  const [daysNote, setDaysNote] = useState('');

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
        <div className="seg" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'work'} className={tab === 'work' ? 'on' : ''} onClick={() => setTab('work')}>Заказы и полка</button>
          <button type="button" role="tab" aria-selected={tab === 'money'} className={tab === 'money' ? 'on' : ''} onClick={() => setTab('money')}>
            Финансы{dash ? ` · ${rub(dash.finance.total)}` : ''}
          </button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {dash && tab === 'money' && <PvzFinance f={dash.finance} />}

        {dash && tab === 'work' && (
          <>
            {dash.peak && <p className="card small">Вечерний пик (17–21): сначала выдача. Упакованные заказы заберёт ближайший рейс.</p>}
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

            {dash.returns.length > 0 && (
              <section className="block">
                <h2>Возвраты <span className="badge">{dash.returns.length}</span></h2>
                {dash.returns.map((o) => (
                  <div key={o.id} className="card task alert">
                    <p><b>№{o.id} · {o.product.emoji} {o.product.name}</b> — {o.buyer_name}</p>
                    <p className="muted small">Причина: {o.return_reason === 'defect' ? 'брак' : 'не подошёл'}. Осмотрите товар. Целый остаётся на вашей полке и снова продаётся в городе — везти его через хаб не нужно. За приём возврата +15 ₽.</p>
                    <div className="actions">
                      <button type="button" className="primary" disabled={busy} onClick={() => act(() => acceptReturn(o.id, 'ok'))}>Целый — на полку</button>
                      <button type="button" className="secondary" disabled={busy} onClick={() => act(() => acceptReturn(o.id, 'defect'))}>Брак — продавцу</button>
                    </div>
                  </div>
                ))}
              </section>
            )}

            {dash.to_seller.length > 0 && (
              <section className="block">
                <h2>На вывоз продавцу <span className="badge">{dash.to_seller.length}</span></h2>
                {dash.to_seller.map((sp) => (
                  <div key={sp.id} className="card task">
                    <p><b>{sp.product.emoji} {sp.product.name} — {sp.qty} шт.</b></p>
                    <p className="muted small">Продавец «{sp.supplier}». Брак из возврата или товар пролежал {dash.finance.remove_day} дней — освобождаем полку.</p>
                    <button type="button" className="secondary" disabled={busy} onClick={() => act(() => handoverSupply(sp.id))}>Передал на вывоз</button>
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
                  <p className="muted small">Возьмите с полки и упакуйте. Поедет в «{o.dest.name}» ({km(o.distance_m)}), покупатель {o.buyer_name}. Ближайший рейс — в {dash.next_run}.</p>
                  <div className="actions">
                    <button type="button" className="primary" disabled={busy} onClick={() => act(() => confirmAssembled(o.id))}>Подтвердить сбор заказа</button>
                    {confirmMissing === o.id ? (
                      <>
                        <button type="button" className="danger small-btn" disabled={busy}
                          onClick={() => { setConfirmMissing(null); act(() => reportMissing(o.id)); }}>Да, товара нет (штраф 50 ₽)</button>
                        <button type="button" className="secondary small-btn" onClick={() => setConfirmMissing(null)}>Отмена</button>
                      </>
                    ) : (
                      <button type="button" className="danger small-btn" disabled={busy} onClick={() => setConfirmMissing(o.id)}>Товара нет на полке</button>
                    )}
                  </div>
                  {confirmMissing === o.id && <p className="muted small">Заказ перенаправим в ближайший ПВЗ, где товар есть, а если его нет нигде — через хаб.</p>}
                </div>
              ))}
            </section>

            {dash.to_courier.length > 0 && (
              <section className="block">
                <h2>Упаковано — ждёт рейса</h2>
                <p className="muted small">Рейсы: {dash.schedule}. Отдайте пакет водителю — он нажмёт «Заказ взят с точки».</p>
                <ul className="list">
                  {dash.to_courier.map((o) => (
                    <li key={o.id}><div><b>№{o.id} · {o.product.name}</b> → «{o.dest.name}»<br /><StatusChip status={o.status} label={o.courier ? `${o.status_label}, водитель ${o.courier.name}` : o.status_label} /></div></li>
                  ))}
                </ul>
              </section>
            )}

            {dash.from_hub.length > 0 && (
              <section className="block">
                <h2>Едут к нам через хаб</h2>
                <ul className="list">
                  {dash.from_hub.map((o) => (
                    <li key={o.id}>
                      <div><b>№{o.id} · {o.product.name}</b> — {o.buyer_name}<br /><span className="muted small">Хаб и магистраль, 2 дня · логистика {fmt(o.economics.logistics_cost)} ₽</span></div>
                      <button type="button" className="secondary small-btn" disabled={busy} onClick={() => act(() => hubArrived(o.id))}>Пришло с хаба</button>
                    </li>
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
                  <p className="muted small">{o.mode === 'pickup' ? `Самовывоз с нашей полки, оплачено ${o.paid} ₽` : `Привёз рейс №${o.run?.id || ''}, водитель ${o.courier?.name || ''}`}</p>
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
              <div className="row-between">
                <h2>Товары на полке</h2>
                <span className={`chip ${dash.capacity.used >= dash.capacity.total * 0.9 ? 'warn' : 'muted'}`}>занято {dash.capacity.used} из {dash.capacity.total}</span>
              </div>
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
                    <span className="stock-name">{s.emoji} {s.name}<br /><span className={`age ${s.age.state}`}>{s.age.text}</span></span>
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
              <div className="card demo-days">
                <p className="small"><b>Демо «долгое хранение»</b>: промотать время для всех ПВЗ города. Хранение начислится ПВЗ, после 30-го дня его оплатит продавец, на 45-й день товар уйдёт на вывоз.</p>
                <div className="actions">
                  {[10, 30].map((d) => (
                    <button key={d} type="button" className="secondary small-btn" disabled={busy}
                      onClick={() => act(async () => {
                        const r = await demoDays(d);
                        setDaysNote(`Прошло ${d} дн.: хранение ПВЗ +${fmt(r.storage_rub)} ₽, продавцам −${fmt(r.seller_late_rub)} ₽${r.removed.length ? `, на вывоз: ${r.removed.map((x) => `«${x.product}» ${x.qty} шт.`).join(', ')}` : ''}`);
                      })}>+{d} дней</button>
                  ))}
                </div>
                {daysNote && <p className="muted small" role="status">{daysNote}</p>}
              </div>
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
