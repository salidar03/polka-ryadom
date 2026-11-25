import { useEffect, useMemo, useState } from 'react';
import { fetchOptimize, fetchPlan, fetchPlanOrders, fetchPvz } from './api.js';
import MapView from './MapView.jsx';
import Panel from './Panel.jsx';

const DEFAULTS = { orders: 1000, radius_m: 700, run_cost: 900, stop_cost: 120, km_cost: 40, capacity: 45, hub_cost: 100, turnover: 10 };

/** Аналитика для Wildberries: районы, рейсы и экономика «хаб против полок» по всему городу. */
export default function AnalyticsView() {
  const [params, setParams] = useState(DEFAULTS);
  const [pvz, setPvz] = useState([]);
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState('');
  const [zoneId, setZoneId] = useState(null);
  const [orders, setOrders] = useState([]);
  const [optimizing, setOptimizing] = useState(false);
  const [optimum, setOptimum] = useState(null);

  useEffect(() => {
    fetchPvz().then(setPvz).catch((e) => setError(e.message));
  }, []);

  // Пересчёт плана при изменении параметров (с небольшой задержкой, пока тянут ползунок).
  useEffect(() => {
    const t = setTimeout(() => {
      fetchPlan(params)
        .then((p) => { setPlan(p); setError(''); })
        .catch((e) => setError(`Не удалось посчитать план: ${e.message}. Запущен ли сервер?`));
    }, 250);
    return () => clearTimeout(t);
  }, [params]);

  const zone = useMemo(() => plan?.zones.find((z) => z.id === zoneId) || null, [plan, zoneId]);

  useEffect(() => {
    if (!zone) { setOrders([]); return; }
    fetchPlanOrders(params, zone.id).then(setOrders).catch(() => setOrders([]));
  }, [zone, params]);

  const optimize = async () => {
    setOptimizing(true);
    try {
      const res = await fetchOptimize(params);
      setOptimum(res);
      setParams((p) => ({ ...p, radius_m: res.best_radius_m }));
      setZoneId(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setOptimizing(false);
    }
  };

  return (
    <div className="app">
      <Panel
        params={params}
        defaults={DEFAULTS}
        onChange={(k, v) => { setParams((p) => ({ ...p, [k]: v })); if (k === 'radius_m') setZoneId(null); }}
        onReset={() => { setParams(DEFAULTS); setZoneId(null); setOptimum(null); }}
        plan={plan}
        error={error}
        zone={zone}
        orders={orders}
        pvz={pvz}
        onOptimize={optimize}
        optimizing={optimizing}
        optimum={optimum}
        onCloseZone={() => setZoneId(null)}
      />
      <MapView pvz={pvz} plan={plan} selectedId={zoneId} onSelect={setZoneId} />
    </div>
  );
}
