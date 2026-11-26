import { useEffect, useState } from 'react';
import { Circle, CircleMarker, Tooltip } from 'react-leaflet';
import { fetchDemand, fetchSupplierDashboard, supplierPlan, supplierUpload } from './api.js';
import { CityMap, FlyTo, fmt, usePoll } from './common.jsx';

const pct = (x) => `${Math.round(x * 100)}%`;
const nf1 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const rub = (x) => `${x < 0 ? '−' : ''}${fmt(Math.abs(x))} ₽`;
const MODE = { pickup: 'самовывоз с полки', deliver: 'рейс ПВЗ → ПВЗ', hub: 'через хаб' };

/** Кабинет продавца: стоимость и прибыль по каждому заказу, начисления, хранение на полках. */
function SellerFinance({ f }) {
  const r = f.rules;
  return (
    <section className="block">
      <h2>Финансы продавца</h2>
      <div className="kpis">
        <div className="kpi wide">
          <p className="kpi-k">К выплате (продажи − комиссия − логистика − платное хранение − возвраты)</p>
          <p className={`kpi-v accent${f.total < 0 ? ' neg' : ''}`}>{rub(f.total)}</p>
          <p className="kpi-n">комиссия {r.commission_pct}% · логистика с полки ПВЗ {r.logistics_shelf} ₽ вместо {r.logistics_hub} ₽ через хаб</p>
        </div>
      </div>
      {f.by_kind.length > 0 && (
        <table className="ptable">
          <tbody>{f.by_kind.map((k) => <tr key={k.kind}><td>{k.name}</td><td className={k.rub < 0 ? 'neg' : ''}><b>{rub(k.rub)}</b></td></tr>)}</tbody>
        </table>
      )}
      <h3>Экономика заказов</h3>
      {!f.sales.length && <p className="muted small">Заказов пока нет.</p>}
      {f.sales.length > 0 && (
        <table className="ptable">
          <thead><tr><th>Заказ</th><th>Цена</th><th>Комиссия</th><th>Логистика</th><th>Вам</th></tr></thead>
          <tbody>
            {f.sales.map((x) => (
              <tr key={x.id}>
                <td>№{x.id} {x.product}<br /><span className="muted small">{MODE[x.mode]}{x.status === 'returned' ? ' · возврат' : x.done ? '' : ' · в пути'}</span></td>
                <td>{fmt(x.price)}</td>
                <td>−{fmt(x.commission)}</td>
                <td>−{fmt(x.logistics)}</td>
                <td><b>{x.status === 'returned' ? '0' : fmt(x.payout)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted small">«Вам» начисляется, когда покупатель получил заказ. При возврате деньги покупателю и комиссия возвращаются, логистика — нет. Хранение первые {r.free_days} дней бесплатно, дальше {nf1.format(r.late_rate)} ₽ за штуку в день; на {r.remove_day}-й день остаток вывозится вам.</p>
      {f.shelves.length > 0 && (
        <>
          <h3>Мой товар на полках ПВЗ</h3>
          <ul className="list">
            {f.shelves.map((x) => (
              <li key={`${x.product}-${x.pvz}`}><div><b>{x.product}</b> · {x.pvz}<br /><span className={`age ${x.age.state}`}>{x.age.text}</span></div><b>{x.qty} шт.</b></li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Где товар продавца: сводка по городам и по ПВЗ Йошкар-Олы (с фильтром по товару). */
function goodsWhere(map, productId) {
  if (!map) return { pvz: [], cities: [], warehouse: 0, yoTotal: 0 };
  const pick = (pid) => !productId || pid === productId;
  const pvz = map.pvz.map((p) => {
    const items = p.items.filter((x) => pick(x.product_id));
    return { ...p, items, on_shelf: items.reduce((a, x) => a + x.on_shelf, 0), in_transit: items.reduce((a, x) => a + x.in_transit, 0) };
  }).filter((p) => p.on_shelf + p.in_transit > 0).sort((a, b) => b.on_shelf + b.in_transit - (a.on_shelf + a.in_transit));
  const byCity = {};
  map.cities.filter((c) => pick(c.product_id)).forEach((c) => {
    byCity[c.city] = byCity[c.city] || { city: c.city, lat: c.lat, lon: c.lon, qty: 0 };
    byCity[c.city].qty += c.qty;
  });
  const yoShelf = pvz.reduce((a, x) => a + x.on_shelf, 0);
  const yoTransit = pvz.reduce((a, x) => a + x.in_transit, 0);
  const cities = [
    ...(yoShelf + yoTransit > 0 ? [{ city: map.base_city.name, lat: map.base_city.lat, lon: map.base_city.lon, qty: yoShelf + yoTransit, shelf: yoShelf, transit: yoTransit, base: true }] : []),
    ...Object.values(byCity).sort((a, b) => b.qty - a.qty),
  ];
  const warehouse = map.warehouse.filter((w) => pick(w.product_id)).reduce((a, w) => a + w.qty, 0);
  return { pvz, cities, warehouse };
}

/** Поставщик: загрузить товар → система по спросу предлагает разложить часть партии по ПВЗ городов. */
export default function SupplierView({ user }) {
  const [demand, setDemand] = useState(null);
  const [form, setForm] = useState({ name: '', category: 'electronics', price: 990, qty: 500, share: 0.15, horizon_days: 14 });
  const [plan, setPlan] = useState(null);
  const [dash, setDash] = useState(null);
  const [done, setDone] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [mapMode, setMapMode] = useState(null);   // goods — где мой товар | plan — план раскладки
  const [focus, setFocus] = useState('cities');   // cities — по городам | pvz — по ПВЗ Йошкар-Олы
  const [productId, setProductId] = useState(0);  // 0 — все товары

  useEffect(() => { fetchDemand().then(setDemand).catch((e) => setError(e.message)); }, []);
  usePoll(() => { fetchSupplierDashboard().then(setDash).catch(() => {}); }, []);

  // предложение пересчитывается, пока поставщик меняет партию, категорию, долю и срок
  useEffect(() => {
    const t = setTimeout(() => {
      if (!form.qty || !form.price) { setPlan(null); return; }
      supplierPlan({ ...form, qty: Number(form.qty), price: Number(form.price) })
        .then((p) => { setPlan(p); setError(''); })
        .catch((e) => setError(e.message));
    }, 250);
    return () => clearTimeout(t);
  }, [form.name, form.category, form.qty, form.price, form.share, form.horizon_days]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k, num) => (e) => setForm({ ...form, [k]: num ? Number(e.target.value) : e.target.value });

  const upload = async (acceptPlan) => {
    if (!form.name.trim()) { setError('Впишите название товара'); return; }
    setBusy(true);
    try {
      const res = await supplierUpload({ ...form, qty: Number(form.qty), price: Number(form.price), accept_plan: acceptPlan });
      setDone({ name: form.name, plan: res.plan, qty: Number(form.qty) });
      setDash(res.dashboard);
      setForm({ ...form, name: '' });
      setError('');
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const cities = plan?.cities || [];
  const yo = cities.find((c) => c.pvz_network);
  const maxQty = Math.max(1, ...cities.map((c) => c.qty));
  const table = demand?.city;
  const rivals = plan?.competitors || [];
  const badge = plan?.badge || dash?.badge;
  const hasGoods = (dash?.products || []).length > 0;
  const mode = mapMode || (hasGoods ? 'goods' : 'plan');
  const where = goodsWhere(dash?.map, productId);
  const maxCity = Math.max(1, ...where.cities.map((c) => c.qty));
  const goodsFly = focus === 'pvz' && where.pvz.length
    ? where.pvz.map((x) => [x.lat, x.lon])
    : where.cities.length ? where.cities.map((c) => [c.lat, c.lon]) : (demand?.cities || []).map((c) => [c.lat, c.lon]);

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <p className="eyebrow">Поставщик</p>
          <h1>{user.name}</h1>
        </header>
        {error && <p className="error" role="alert">{error}</p>}
        {done && (
          <div className="card good" role="status">
            <h3>«{done.name}» загружен</h3>
            <p className="small">{done.plan
              ? `${fmt(done.plan.on_shelves)} шт. едут на полки ПВЗ ${done.plan.cities.filter((c) => c.qty).length} городов, ${fmt(done.plan.to_warehouse)} шт. — на склад WB. ПВЗ Йошкар-Олы получили задания на приёмку.`
              : `Вся партия ${fmt(done.qty)} шт. едет на склад WB.`}</p>
            <button type="button" className="link" onClick={() => setDone(null)}>Закрыть</button>
          </div>
        )}

        {hasGoods && (
          <section className="block">
            <div className="row-between"><h2>Где мой товар</h2></div>
            <div className="seg" role="tablist" aria-label="Что показать на карте">
              <button type="button" role="tab" aria-selected={mode === 'goods'} className={mode === 'goods' ? 'on' : ''} onClick={() => setMapMode('goods')}>На карте: мой товар</button>
              <button type="button" role="tab" aria-selected={mode === 'plan'} className={mode === 'plan' ? 'on' : ''} onClick={() => setMapMode('plan')}>План новой партии</button>
            </div>
            {mode === 'goods' && (
              <>
                <div className="actions">
                  <select aria-label="Товар" value={productId} onChange={(e) => setProductId(Number(e.target.value))}>
                    <option value={0}>Все мои товары</option>
                    {dash.products.map((x) => <option key={x.id} value={x.id}>{x.emoji} {x.name}</option>)}
                  </select>
                  <div className="seg small-seg">
                    <button type="button" className={focus === 'cities' ? 'on' : ''} onClick={() => setFocus('cities')}>Города</button>
                    <button type="button" className={focus === 'pvz' ? 'on' : ''} onClick={() => setFocus('pvz')}>ПВЗ Йошкар-Олы</button>
                  </div>
                </div>
                <table className="ptable">
                  <thead><tr><th>Город</th><th>Штук</th></tr></thead>
                  <tbody>
                    {where.cities.map((c) => (
                      <tr key={c.city}>
                        <td>{c.city}{c.base ? ` — на полках ${fmt(c.shelf)}${c.transit ? `, едет ${fmt(c.transit)}` : ''}` : ' — отправлено в город'}</td>
                        <td><b>{fmt(c.qty)}</b></td>
                      </tr>
                    ))}
                    {where.warehouse > 0 && <tr><td>Склад WB</td><td><b>{fmt(where.warehouse)}</b></td></tr>}
                  </tbody>
                </table>
                <p className="muted small legend-line">
                  <span className="dot-on" /> на полке ПВЗ &nbsp; <span className="dot-transit" /> едет на полку (ПВЗ ещё не принял)
                </p>
                {focus === 'pvz' && (
                  where.pvz.length ? (
                    <ul className="list">
                      {where.pvz.map((x) => (
                        <li key={x.id}>
                          <div><b>{x.name}</b>{x.address ? <span className="muted small"> · {x.address}</span> : null}<br />
                            <span className="muted small">{x.items.map((i) => `${i.emoji} ${i.product}: ${i.on_shelf ? `${i.on_shelf} на полке` : ''}${i.on_shelf && i.in_transit ? ', ' : ''}${i.in_transit ? `${i.in_transit} едет` : ''}`).join(' · ')}</span>
                          </div>
                          <b>{x.on_shelf + x.in_transit} шт.</b>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="muted small">В ПВЗ Йошкар-Олы этого товара нет — разложите часть партии по плану.</p>
                )}
              </>
            )}
          </section>
        )}

        <section className="block card">
          <h2>Загрузить товар</h2>
          <label className="field" htmlFor="s-name"><span>Товар</span>
            <input id="s-name" value={form.name} onChange={set('name')} placeholder="Колонка портативная" maxLength={200} />
          </label>
          <label className="field" htmlFor="s-cat"><span>Категория</span>
            <select id="s-cat" value={form.category} onChange={set('category')}>
              {(demand?.categories || []).map((c) => <option key={c.id} value={c.id}>{c.emoji} {c.name}</option>)}
            </select>
          </label>
          <div className="two">
            <label className="field" htmlFor="s-price"><span>Цена, ₽</span>
              <input id="s-price" type="number" min={10} value={form.price} onChange={set('price', true)} />
            </label>
            <label className="field" htmlFor="s-qty"><span>Партия, шт.</span>
              <input id="s-qty" type="number" min={1} value={form.qty} onChange={set('qty', true)} />
            </label>
          </div>
          <label className="ctrl" htmlFor="s-share">
            <span className="ctrl-top"><span>На полки ПВЗ городов</span><output>{pct(form.share)} партии</output></span>
            <input id="s-share" type="range" min={0.05} max={0.3} step={0.01} value={form.share} onChange={set('share', true)} />
          </label>
          <label className="ctrl" htmlFor="s-horizon">
            <span className="ctrl-top"><span>Запас на полке</span><output>{form.horizon_days} дн. спроса</output></span>
            <input id="s-horizon" type="range" min={7} max={30} step={1} value={form.horizon_days} onChange={set('horizon_days', true)} />
          </label>
        </section>

        {plan && (
          <section className="block">
            <div className="card good">
              <h3>Предложение по спросу</h3>
              <p>Разложить <b>{fmt(plan.on_shelves)} шт.</b> по полкам ПВЗ в {cities.filter((c) => c.qty).length} городах, остальные <b>{fmt(plan.to_warehouse)} шт.</b> — на склад WB, как обычно.</p>
              {plan.capped_by_demand && <p className="muted small">Больше на полки не предлагаем: спрос за {plan.horizon_days} дн. — {fmt(plan.on_shelves)} шт., лишнее будет лежать.</p>}
              <p className="muted small">Заказ с полки дешевле на ~{plan.saving_per_order} ₽ и приходит на день быстрее — для продавца это ниже КТР и выше продажи.</p>
            </div>
            <table className="ptable">
              <thead><tr><th>Город</th><th>Спрос, шт./день</th><th>На полки</th><th>Хватит</th></tr></thead>
              <tbody>
                {cities.map((c) => (
                  <tr key={c.city}>
                    <td>{c.city}{c.pvz_network ? ' ★' : ''}<span className="bar" style={{ width: `${Math.round((c.qty / maxQty) * 60)}px` }} /></td>
                    <td>{nf1.format(c.sku_per_day)}</td>
                    <td><b>{fmt(c.qty)}</b></td>
                    <td>{c.qty ? `${nf1.format(c.days_cover)} дн.` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {yo?.pvz.length > 0 && (
              <p className="muted small">
                ★ Йошкар-Ола: {yo.pvz.map((p) => `${p.name} — ${p.qty} шт.`).join(', ')}. Это ПВЗ с самым высоким спросом,
                и между ними не меньше {plan.spacing_m} м — ваш товар не повторяется в соседних пунктах, каждый обслуживает свой район.
                «Хватит» — на сколько дней спроса хватит товара на полках города.
              </p>
            )}
            {badge && (
              <div className="label-card">
                <span className="label-tag">Метка</span>
                <p>{badge}</p>
                {rivals.length > 0 && (
                  <p className="small">
                    Похожий товар уже продают: {rivals.map((r) => `«${r.name}» (${r.supplier}, ${fmt(r.price)} ₽, на полках ${r.shelf_points} ПВЗ)`).join('; ')}.
                  </p>
                )}
              </div>
            )}
            <div className="actions">
              <button type="button" className="primary" disabled={busy} onClick={() => upload(true)}>Загрузить и разложить по плану</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => upload(false)}>Всё на склад WB</button>
            </div>
          </section>
        )}

        <section className="block">
          <h2>Мои товары</h2>
          {!dash?.products.length && <p className="muted small">Товаров пока нет — загрузите первый.</p>}
          {dash?.products.map((p) => (
            <div key={p.id} className="card">
              <p className="row-between"><b>{p.emoji} {p.name}</b><b>{fmt(p.price)} ₽</b></p>
              <p className="muted small">{p.category} · партия {fmt(p.total)} шт.: на полки ПВЗ {fmt(p.to_pvz)} (принято {fmt(p.accepted)}), в города {fmt(p.to_cities)}, на склад WB {fmt(p.to_warehouse)}</p>
              <p className="small">Сейчас на полках: <b>{fmt(p.on_shelf_now)} шт.</b>{p.shelf_points ? ` в ${p.shelf_points} ПВЗ` : ''} · продано: <b>{p.sold}</b> · выручка: <b>{fmt(p.revenue)} ₽</b></p>
              {p.competitors > 0 && (
                <p className="small"><span className="label-tag">Метка</span> Похожий товар есть ещё у {p.competitors} продавц{p.competitors === 1 ? 'а' : 'ов'}.
                  {p.to_pvz ? ' Ваш лежит на полках — покупателю рядом предложим его.' : ' Ваш товар только на складе — покупатель рядом скорее заберёт чужой с полки.'}</p>
              )}
            </div>
          ))}
        </section>

        {dash?.finance && <SellerFinance f={dash.finance} />}

        {table && (
          <details className="block">
            <summary><b>Примерный спрос: {table.city}</b> — {fmt(table.items_per_day)} товаров в день</summary>
            <table className="ptable">
              <thead><tr><th>Категория</th><th>Доля</th><th>Товаров в день</th><th>Один ходовой товар</th></tr></thead>
              <tbody>
                {table.rows.map((r) => (
                  <tr key={r.id}><td>{r.emoji} {r.name}</td><td>{pct(r.share)}</td><td>{fmt(r.per_day)}</td><td>{nf1.format(r.sku_per_day)}</td></tr>
                ))}
              </tbody>
            </table>
            {table.notes.map((n) => <p key={n} className="muted small">{n}</p>)}
          </details>
        )}
      </aside>

      <CityMap>
        {mode === 'plan' && (
          <>
            <FlyTo points={cities.length ? cities.map((c) => [c.lat, c.lon]) : (demand?.cities || []).map((c) => [c.lat, c.lon])} />
            {cities.map((c) => (
              <CircleMarker key={c.city} center={[c.lat, c.lon]} radius={8 + Math.sqrt(c.qty) * 2}
                pathOptions={{ color: c.pvz_network ? '#E8742A' : '#14213D', weight: 3, fillColor: '#E8742A', fillOpacity: c.qty ? 0.45 : 0.05 }}>
                <Tooltip permanent direction="top">{c.city}: {fmt(c.qty)} шт.</Tooltip>
              </CircleMarker>
            ))}
            {yo?.pvz.map((p) => (
              <Circle key={`z${p.id}`} center={[p.lat, p.lon]} radius={350} pathOptions={{ color: '#7B2D8E', weight: 1, fillOpacity: 0.08 }} />
            ))}
            {yo?.pvz.map((p) => (
              <CircleMarker key={`p${p.id}`} center={[p.lat, p.lon]} radius={4} pathOptions={{ color: '#7B2D8E', weight: 2, fillColor: '#FFFFFF', fillOpacity: 1 }}>
                <Tooltip>{p.name}: {p.qty} шт.</Tooltip>
              </CircleMarker>
            ))}
          </>
        )}
        {mode === 'goods' && (
          <>
            <FlyTo points={goodsFly} />
            {focus === 'cities' && where.cities.map((c) => (
              <CircleMarker key={`${c.city}-${productId}`} center={[c.lat, c.lon]} radius={8 + Math.sqrt(c.qty / maxCity) * 22}
                pathOptions={{ color: c.base ? '#7B2D8E' : '#14213D', weight: 3, fillColor: '#7B2D8E', fillOpacity: 0.4 }}
                eventHandlers={{ click: () => { if (c.base) setFocus('pvz'); } }}>
                <Tooltip permanent direction="top">{c.city}: {fmt(c.qty)} шт.{c.base ? ' — нажмите, чтобы увидеть ПВЗ' : ''}</Tooltip>
              </CircleMarker>
            ))}
            {where.pvz.map((x) => (
              <CircleMarker key={`g${x.id}-${focus}-${productId}`} center={[x.lat, x.lon]} radius={6 + Math.min(10, Math.sqrt(x.on_shelf + x.in_transit) * 2)}
                pathOptions={{ color: x.on_shelf ? '#7B2D8E' : '#E8742A', weight: 3, fillColor: x.on_shelf ? '#7B2D8E' : '#FFFFFF', fillOpacity: 0.85 }}>
                <Tooltip permanent={focus === 'pvz' && where.pvz.length <= 15} direction="top">
                  {x.name}: {x.on_shelf ? `${x.on_shelf} на полке` : ''}{x.on_shelf && x.in_transit ? ', ' : ''}{x.in_transit ? `${x.in_transit} едет` : ''}
                </Tooltip>
              </CircleMarker>
            ))}
          </>
        )}
      </CityMap>
    </div>
  );
}
