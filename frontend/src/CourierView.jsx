import { useState } from 'react';
import L from 'leaflet';
import { Circle, CircleMarker, Marker, Polyline, Tooltip, useMapEvents } from 'react-leaflet';
import { fetchCourierDashboard, fetchCouriers, markPicked, markUnloaded, setCourierLocation, startRun } from './api.js';
import { CityMap, FlyTo, Notes, fmt, km, locateMe, plural, usePoll } from './common.jsx';

const nf1 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });

function ClickToPlace({ onPick }) {
  useMapEvents({ click: (e) => onPick([e.latlng.lat, e.latlng.lng]) });
  return null;
}

/** Номер остановки на карте: оранжевый — забрать, зелёный — только выгрузить, серый — пройдена. */
function stopIcon(n, kind) {
  return L.divIcon({ className: '', html: `<span class="stop-pin ${kind}">${n}</span>`, iconSize: [28, 28], iconAnchor: [14, 14] });
}

function stopKind(st) {
  if (st.done) return 'done';
  return st.pickups.some((x) => x.status === 'in_run') ? 'pick' : 'drop';
}

/** Водитель: рейсы по расписанию, объезд ПВЗ в пересекающихся кругах, «Заказ взят с точки», заработок за рейсы. */
export default function CourierView({ user }) {
  const [couriers, setCouriers] = useState([]);
  const [dash, setDash] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false); // ждём щелчка по карте «я здесь»
  const [located, setLocated] = useState(null);  // время последней отметки

  const act = async (fn) => {
    setBusy(true);
    try { await fn(); setDash(await fetchCourierDashboard()); setCouriers(await fetchCouriers()); setError(''); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
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

  const me = dash?.courier;
  const run = dash?.run;
  const ready = dash?.ready || [];
  const earn = dash?.earnings;
  const limits = dash?.limits;
  const meId = user.courier?.id;
  const fly = run ? run.route.path : ready.length ? [...ready.map((o) => [o.from.lat, o.from.lon]), ...(me ? [[me.lat, me.lon]] : [])] : me ? [[me.lat, me.lon]] : [];

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <p className="eyebrow">Доставка рейсами</p>
          <h1>{user.name}</h1>
          {me && <span className={`chip ${me.busy ? 'work' : 'good'}`}>{run ? `в рейсе №${run.id}` : 'свободен — ждёт рейса'}</span>}
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
          <p className="muted small">Рейс начинается с точки, ближайшей к вам, — отметьте, где стоит машина.</p>
        </section>

        {!run && dash && (
          <section className="block">
            <h2>Следующий рейс — в {dash.next_run}</h2>
            <p className="muted small">
              Расписание: {dash.schedule}. Машина объезжает ПВЗ, круги которых по 700 м пересекаются
              (соседние точки не дальше {nf1.format(limits.link_m / 1000)} км), — до {limits.stops} ПВЗ и {limits.orders} заказов за рейс.
              Маршрут строится по тем ПВЗ, где заказы уже упакованы.
            </p>
            <div className="card">
              <div className="row-between">
                <b>Упаковано и ждёт рейса</b>
                <span className="badge">{ready.length}</span>
              </div>
              {ready.length === 0 && <p className="muted small">Пока ничего: заказ появится здесь, когда ПВЗ подтвердит сбор и упаковку.</p>}
              {ready.length > 0 && (
                <ul className="list">
                  {ready.slice(0, 8).map((o) => (
                    <li key={o.id}>
                      <div><b>№{o.id} · {o.product}</b><br /><span className="muted small">«{o.from.name}» → «{o.to.name}» · упакован в {o.ready_at}</span></div>
                    </li>
                  ))}
                </ul>
              )}
              <button type="button" className="primary" disabled={busy || ready.length === 0} onClick={() => act(startRun)}>
                Выехать в рейс сейчас
              </button>
              <p className="muted small">Для показа: не ждать {dash.next_run} — собрать рейс из готовых заказов прямо сейчас.</p>
            </div>
          </section>
        )}

        {run && (
          <section className="block">
            <h2>Рейс №{run.id} · {run.slot === 'сейчас' ? 'внеплановый' : `в ${run.slot}`}</h2>
            <div className="stats-row">
              <span><b>{run.stops.length}</b> {plural(run.stops.length, 'остановка', 'остановки', 'остановок')}</span>
              <span><b>{run.orders}</b> {plural(run.orders, 'заказ', 'заказа', 'заказов')}</span>
              <span><b>{nf1.format(run.km)}</b> км</span>
              <span><b>{fmt(run.pay)} ₽</b> за рейс</span>
            </div>
            <p className="muted small">
              {run.route.source === 'osrm' ? 'Маршрут построен по улицам (OpenStreetMap).' : 'Сервис маршрутов не ответил — показан путь по прямой.'}
              {' '}Логистика на заказ — {fmt(run.cost_per_order)} ₽.
            </p>
            <ol className="run-stops">
              {run.stops.map((st) => {
                const kind = stopKind(st);
                const canUnload = st.drops.length > 0 && st.drops.some((x) => x.status === 'in_transit');
                const waiting = st.drops.filter((x) => x.status === 'in_run');
                return (
                  <li key={st.index} className={`run-stop ${kind}${run.current === st.index ? ' now' : ''}`}>
                    <span className={`stop-pin ${kind}`}>{st.index + 1}</span>
                    <div className="run-stop-body">
                      <b>{st.pvz.name}</b>
                      {st.pvz.address && <span className="muted small">{st.pvz.address}</span>}
                      {st.pickups.map((x) => (
                        <div key={x.id} className="run-order">
                          <span>Забрать №{x.id} · {x.product}</span>
                          {x.status === 'in_run'
                            ? <button type="button" className="primary small-btn" disabled={busy} onClick={() => act(() => markPicked(x.id))}>Заказ взят с точки</button>
                            : <span className="chip good">взят</span>}
                        </div>
                      ))}
                      {st.drops.length > 0 && (
                        <div className="run-order">
                          <span>Выгрузить: {st.drops.map((x) => `№${x.id}`).join(', ')}</span>
                          {st.drops.every((x) => x.status === 'delivered' || x.status === 'issued')
                            ? <span className="chip good">выгружено</span>
                            : <button type="button" className="secondary small-btn" disabled={busy || !canUnload || waiting.length > 0}
                                title={waiting.length ? `Сначала возьмите с точки №${waiting[0].id}` : ''}
                                onClick={() => act(() => markUnloaded(run.id, st.index))}>Выгрузил заказы</button>}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>
        )}

        {earn && (
          <section className="block">
            <h2>Мой заработок</h2>
            <div className="kpis">
              <div className="kpi wide">
                <p className="kpi-k">Заработано сегодня</p>
                <p className="kpi-v accent">{fmt(earn.today)} ₽</p>
                <p className="kpi-n">{earn.rate}</p>
              </div>
              <div className="kpi"><p className="kpi-k">Рейсов</p><p className="kpi-v">{earn.runs}</p></div>
              <div className="kpi"><p className="kpi-k">Заказов развёз</p><p className="kpi-v">{earn.orders}</p></div>
              <div className="kpi wide"><p className="kpi-k">Проехал</p><p className="kpi-v">{nf1.format(earn.km)} км</p></div>
            </div>
            {earn.items.length > 0 && (
              <ul className="list">
                {earn.items.map((x) => (
                  <li key={x.id}>
                    <div><b>Рейс №{x.id} · {x.slot === 'сейчас' ? 'внеплановый' : x.slot}</b><br /><span className="muted small">завершён в {x.time} · {x.stops} {plural(x.stops, 'остановка', 'остановки', 'остановок')} · {x.orders} {plural(x.orders, 'заказ', 'заказа', 'заказов')} · {nf1.format(x.km)} км</span></div>
                    <b>+{fmt(x.pay)} ₽</b>
                  </li>
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

        {/* без рейса: где упакованы заказы и куда их везти */}
        {!run && ready.map((o) => (
          <Polyline key={`r${o.id}`} positions={[[o.from.lat, o.from.lon], [o.to.lat, o.to.lon]]} pathOptions={{ color: '#E8742A', weight: 2, dashArray: '4 6', opacity: 0.8 }} />
        ))}
        {!run && ready.map((o) => (
          <Circle key={`c${o.id}`} center={[o.from.lat, o.from.lon]} radius={700} pathOptions={{ color: '#E8742A', weight: 1, fillOpacity: 0.06 }} />
        ))}
        {!run && ready.map((o) => (
          <CircleMarker key={`f${o.id}`} center={[o.from.lat, o.from.lon]} radius={7} pathOptions={{ color: '#E8742A', weight: 3, fillColor: '#FFFFFF', fillOpacity: 1 }}>
            <Tooltip direction="top">Упакован №{o.id}: «{o.from.name}»</Tooltip>
          </CircleMarker>
        ))}

        {/* рейс: круги 700 м вокруг остановок, путь по улицам и номера остановок */}
        {run && run.stops.map((st) => (
          <Circle key={`z${st.index}`} center={[st.pvz.lat, st.pvz.lon]} radius={700}
            pathOptions={{ color: '#7B2D8E', weight: 1, fillColor: '#7B2D8E', fillOpacity: 0.05 }} />
        ))}
        {run && (
          <Polyline positions={run.route.path} pathOptions={{ color: '#E8742A', weight: 5 }}>
            <Tooltip sticky>Рейс №{run.id}: {nf1.format(run.km)} км</Tooltip>
          </Polyline>
        )}
        {run && run.stops.map((st) => (
          <Marker key={`s${st.index}`} position={[st.pvz.lat, st.pvz.lon]} icon={stopIcon(st.index + 1, stopKind(st))}>
            <Tooltip direction="top" offset={[0, -12]}>
              {st.index + 1}. {st.pvz.name}
              {st.pickups.length > 0 && ` · забрать ${st.pickups.length}`}
              {st.drops.length > 0 && ` · выгрузить ${st.drops.length}`}
            </Tooltip>
          </Marker>
        ))}

        {couriers.map((c) => (
          <CircleMarker key={c.id} center={[c.lat, c.lon]} radius={c.id === meId ? 10 : 6}
            pathOptions={{ color: '#14213D', weight: c.id === meId ? 4 : 2, fillColor: c.busy ? '#E8742A' : '#3B6FD8', fillOpacity: 1 }}>
            <Tooltip permanent={c.id === meId} direction="bottom">{c.id === meId ? 'Я здесь' : `${c.name} — ${c.busy ? 'в рейсе' : 'свободен'}`}</Tooltip>
          </CircleMarker>
        ))}
      </CityMap>
    </div>
  );
}
