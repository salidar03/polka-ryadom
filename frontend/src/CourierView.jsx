import { useState } from 'react';
import { CircleMarker, Polyline, Tooltip, useMapEvents } from 'react-leaflet';
import { fetchCourierDashboard, fetchCouriers, markDelivered, markPicked, setCourierLocation } from './api.js';
import { CityMap, FlyTo, Notes, StatusChip, fmt, km, locateMe, usePoll } from './common.jsx';

function ClickToPlace({ onPick }) {
  useMapEvents({ click: (e) => onPick([e.latlng.lat, e.latlng.lng]) });
  return null;
}

/** Курьер после входа: задание «забрать в ПВЗ А — доставить в ПВЗ Б», маршрут по улицам и заработок. */
export default function CourierView({ user }) {
  const [couriers, setCouriers] = useState([]);
  const [dash, setDash] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false); // ждём щелчка по карте «я здесь»
  const [located, setLocated] = useState(null);  // время последней отметки

  const saveLocation = async ([lat, lon]) => {
    setPicking(false);
    await act(() => setCourierLocation(lat, lon));
    setLocated(new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }));
  };
  const autoLocate = async () => {
    try { await saveLocation(await locateMe()); } catch (e) { setError(e.message); setPicking(true); }
  };

  usePoll(() => {
    fetchCouriers().then(setCouriers).catch(() => {});
    fetchCourierDashboard().then((d) => { setDash(d); setError(''); }).catch((e) => setError(e.message));
  }, []);

  const act = async (fn) => {
    setBusy(true);
    try { await fn(); setDash(await fetchCourierDashboard()); setCouriers(await fetchCouriers()); setError(''); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const me = dash?.courier;
  const task = dash?.tasks?.[0];
  const route = task?.route;
  const earn = dash?.earnings;
  const fly = route ? [...route.approach.path, ...route.delivery.path] : me ? [[me.lat, me.lon]] : [];
  const meId = user.courier?.id;

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <p className="eyebrow">Доставка</p>
          <h1>{user.name}</h1>
          {me && <span className={`chip ${me.busy ? 'work' : 'good'}`}>{me.busy ? 'выполняет заказ' : 'свободен — ждёт задание'}</span>}
        </header>
        {error && <p className="error" role="alert">{error}</p>}

        <section className="block">
          <h2>Где я сейчас</h2>
          {me?.near && (
            <p className="muted small">
              Рядом с «{me.near.name}» · {km(me.near.distance_m)}{located ? ` · отмечено в ${located}` : ''}
            </p>
          )}
          <div className="actions">
            <button type="button" className={picking ? 'primary' : 'secondary'} disabled={busy} onClick={() => setPicking((v) => !v)}>
              {picking ? 'Нажмите на карту…' : 'Отметить на карте'}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={autoLocate}>Определить автоматически</button>
          </div>
          <p className="muted small">От вашей точки система выбирает ближайшего курьера и строит путь до ПВЗ.</p>
        </section>

        <section className="block">
          <h2>Задание</h2>
          {!task && <p className="muted small">Заданий нет. Как только ПВЗ подтвердит сбор заказа, система выберет ближайшего свободного курьера — если это вы, задание появится здесь. Не забудьте отметить, где вы сейчас.</p>}
          {dash?.tasks.map((o) => (
            <div key={o.id} className="card task alert">
              <p><b>Заберите товар из «{o.source.name}» и доставьте в «{o.dest.name}»</b></p>
              <p className="muted small">Заказ №{o.id} · {o.product.emoji} {o.product.name} · оплата <b>{fmt(o.courier_pay)} ₽</b></p>
              <StatusChip status={o.status} label={o.status_label} />
              <ol className="steps">
                <li className={o.status === 'courier_assigned' ? 'now' : 'done'}>
                  Забрать в «{o.source.name}»{o.source.address ? `, ${o.source.address}` : ''}
                  {o.route && ` — ${km(o.route.approach.distance_m)}, ~${Math.max(1, Math.round(o.route.approach.duration_s / 60))} мин`}
                </li>
                <li className={o.status === 'in_transit' ? 'now' : ''}>
                  Доставить в «{o.dest.name}»{o.dest.address ? `, ${o.dest.address}` : ''}
                  {o.route && ` — ${km(o.route.delivery.distance_m)}, ~${Math.max(1, Math.round(o.route.delivery.duration_s / 60))} мин`}
                </li>
              </ol>
              {o.route && <p className="muted small">{o.route.delivery.source === 'osrm' ? 'Маршрут построен по улицам (OpenStreetMap).' : 'Сервис маршрутов не ответил — показан путь по прямой.'}</p>}
              {o.status === 'courier_assigned' && <button type="button" className="primary" disabled={busy} onClick={() => act(() => markPicked(o.id))}>Забрал заказ</button>}
              {o.status === 'in_transit' && <button type="button" className="primary" disabled={busy} onClick={() => act(() => markDelivered(o.id))}>Доставил в ПВЗ</button>}
            </div>
          ))}
        </section>

        {earn && (
          <section className="block">
            <h2>Мой заработок</h2>
            <div className="kpis">
              <div className="kpi wide">
                <p className="kpi-k">Заработано сегодня</p>
                <p className="kpi-v accent">{fmt(earn.today)} ₽</p>
                <p className="kpi-n">{earn.rate}</p>
              </div>
              <div className="kpi"><p className="kpi-k">Доставок</p><p className="kpi-v">{earn.deliveries}</p></div>
              <div className="kpi"><p className="kpi-k">Проехал</p><p className="kpi-v">{new Intl.NumberFormat('ru-RU').format(earn.km)} км</p></div>
            </div>
            {earn.items.length > 0 && (
              <ul className="list">
                {earn.items.map((x) => (
                  <li key={x.id}><div><b>№{x.id} · {x.product}</b><br /><span className="muted small">{x.time} · «{x.from}» → «{x.to}» · {new Intl.NumberFormat('ru-RU').format(x.km)} км</span></div><b>+{fmt(x.pay)} ₽</b></li>
                ))}
              </ul>
            )}
          </section>
        )}

        <section className="block">
          <h2>Уведомления</h2>
          <Notes items={dash?.notifications} />
        </section>
      </aside>

      <CityMap className={picking ? 'map picking' : 'map'}>
        {picking && <ClickToPlace onPick={saveLocation} />}
        <FlyTo points={fly} />
        {couriers.map((c) => (
          <CircleMarker key={c.id} center={[c.lat, c.lon]} radius={c.id === meId ? 10 : 6}
            pathOptions={{ color: '#14213D', weight: c.id === meId ? 4 : 2, fillColor: c.busy ? '#E8742A' : '#3B6FD8', fillOpacity: 1 }}>
            <Tooltip permanent={c.id === meId} direction="top">{c.id === meId ? 'Я здесь' : `${c.name} — ${c.busy ? 'выполняет заказ' : 'свободен'}`}</Tooltip>
          </CircleMarker>
        ))}
        {task && route && (
          <>
            {task.status === 'courier_assigned' && (
              <Polyline positions={route.approach.path} pathOptions={{ color: '#3B6FD8', weight: 4, dashArray: '6 8' }}>
                <Tooltip sticky>К ПВЗ за заказом</Tooltip>
              </Polyline>
            )}
            <Polyline positions={route.delivery.path} pathOptions={{ color: '#E8742A', weight: 5 }}>
              <Tooltip sticky>Доставка в ПВЗ покупателя</Tooltip>
            </Polyline>
            <CircleMarker center={[task.source.lat, task.source.lon]} radius={9} pathOptions={{ color: '#E8742A', weight: 3, fillColor: '#FFFFFF', fillOpacity: 1 }}>
              <Tooltip permanent direction="top">Забрать: {task.source.name}</Tooltip>
            </CircleMarker>
            <CircleMarker center={[task.dest.lat, task.dest.lon]} radius={9} pathOptions={{ color: '#2F7A4D', weight: 3, fillColor: '#FFFFFF', fillOpacity: 1 }}>
              <Tooltip permanent direction="top">Доставить: {task.dest.name}</Tooltip>
            </CircleMarker>
          </>
        )}
      </CityMap>
    </div>
  );
}
