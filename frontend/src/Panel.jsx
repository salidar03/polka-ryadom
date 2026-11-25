import { STATUS } from './MapView.jsx';

const nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const fmt = (n) => nf.format(Math.round(n ?? 0));
const pct = (n) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(n ?? 0);

const SLIDERS = [
  { key: 'orders', label: 'Городских заказов в день', min: 200, max: 5000, step: 100, unit: '' },
  { key: 'radius_m', label: 'Радиус района', min: 400, max: 1500, step: 50, unit: ' м' },
  { key: 'run_cost', label: 'Рейс: база (машина и водитель)', min: 300, max: 2500, step: 50, unit: ' ₽' },
  { key: 'stop_cost', label: 'Рейс: за остановку в ПВЗ', min: 0, max: 300, step: 10, unit: ' ₽' },
  { key: 'km_cost', label: 'Рейс: за километр', min: 0, max: 120, step: 5, unit: ' ₽' },
  { key: 'capacity', label: 'Вместимость рейса', min: 20, max: 80, step: 1, unit: ' заказов' },
  { key: 'hub_cost', label: 'Хаб, магистраль и плечи', min: 50, max: 150, step: 5, unit: ' ₽' },
  { key: 'turnover', label: 'Дней товар лежит на полке', min: 2, max: 60, step: 1, unit: ' дн.' },
];

export default function Panel({ params, defaults, onChange, onReset, plan, error, zone, orders, pvz, onOptimize, optimizing, optimum, onCloseZone }) {
  const t = plan?.totals;
  const u = plan?.unit;
  const name = (id) => pvz.find((p) => p.id === id)?.name || `ПВЗ ${id}`;
  const changed = Object.keys(defaults).some((k) => defaults[k] !== params[k]);

  return (
    <aside className="panel">
      <header className="brand">
        <p className="eyebrow">Wildberries · Йошкар-Ола</p>
        <h1>Полка рядом</h1>
        <p className="muted">Городские заказы едут из ПВЗ в ПВЗ без хаба. Круг — район с опорным ПВЗ и полкой, оранжевая петля — рейс.</p>
      </header>

      {error && <p className="error" role="alert">{error}</p>}

      {t && (
        <section className="kpis" aria-live="polite">
          <div className="kpi wide">
            <p className="kpi-k">Экономия по городу</p>
            <p className="kpi-v accent">−{pct(t.saving_pct)}%</p>
            <p className="kpi-n">{fmt(t.saving_month)} ₽ в месяц · по ходовому товару −{pct(t.saving_pct_candidates)}%</p>
            <span className={`chip ${t.saving_pct_candidates >= 20 ? 'good' : 'warn'}`}>
              критерий ≥ 20% {t.saving_pct_candidates >= 20 ? 'выполнен' : 'пока не выполнен'}
            </span>
          </div>
          <div className="kpi">
            <p className="kpi-k">Заказ в полном рейсе</p>
            <p className="kpi-v"><span className="from">{fmt(u.hub_total)}</span> → {fmt(u.mesh_full_run)} ₽</p>
            <p className="kpi-n">типичный рейс {fmt(u.typical_run_cost)} ₽ · граница {u.break_even ? Math.ceil(u.break_even) : '—'} заказов</p>
          </div>
          <div className="kpi">
            <p className="kpi-k">Районы с рейсами</p>
            <p className="kpi-v">{t.zones_active} из {t.zones}</p>
            <p className="kpi-n">{t.runs} рейсов в день</p>
          </div>
          <div className="kpi">
            <p className="kpi-k">Ушли ПВЗ → ПВЗ</p>
            <p className="kpi-v">{fmt(t.mesh)}</p>
            <p className="kpi-n">из {fmt(t.orders)} заказов · {fmt(t.fallback)} из неполных рейсов — через хаб</p>
          </div>
          <div className="kpi">
            <p className="kpi-k">Доход ПВЗ в районе с рейсами</p>
            <p className="kpi-v">{fmt(t.pvz_income_month)} ₽</p>
            <p className="kpi-n">в месяц на точку за приёмку, хранение, сборку</p>
          </div>
        </section>
      )}

      <section className="controls">
        <div className="row-between">
          <h2>Параметры</h2>
          {changed && <button type="button" className="link" onClick={onReset}>Сбросить</button>}
        </div>
        {SLIDERS.map((s) => (
          <label key={s.key} className="ctrl" htmlFor={`in-${s.key}`}>
            <span className="ctrl-top"><span>{s.label}</span><output>{fmt(params[s.key])}{s.unit}</output></span>
            <input id={`in-${s.key}`} type="range" min={s.min} max={s.max} step={s.step} value={params[s.key]}
              onChange={(e) => onChange(s.key, Number(e.target.value))} />
          </label>
        ))}
        <button type="button" className="primary" onClick={onOptimize} disabled={optimizing}>
          {optimizing ? 'Считаю…' : 'Подобрать радиус района'}
        </button>
        {optimum && (
          <p className="muted small">
            Лучший радиус — {optimum.best_radius_m} м: экономия {fmt(optimum.table.find((x) => x.radius_m === optimum.best_radius_m).saving_day)} ₽ в день.
          </p>
        )}
      </section>

      {zone ? (
        <section className="zone">
          <div className="row-between">
            <h2>Район №{zone.id}</h2>
            <button type="button" className="link" onClick={onCloseZone}>Закрыть</button>
          </div>
          <p className="status" style={{ color: STATUS[zone.status].color }}>● {STATUS[zone.status].label}</p>
          <p className="muted small">Опорный ПВЗ: {name(zone.center_id)} · {zone.pvz_ids.length} ПВЗ · петля {zone.loop_km} км · рейс {fmt(zone.run_cost)} ₽ · граница {zone.break_even ? Math.ceil(zone.break_even) : '—'} заказов</p>
          <dl className="stats">
            <div><dt>Заказов в день</dt><dd>{zone.orders}</dd></div>
            <div><dt>С полки (кандидаты)</dt><dd>{zone.candidates}</dd></div>
            <div><dt>Рейсов</dt><dd>{zone.runs}</dd></div>
            <div><dt>Ушли ПВЗ → ПВЗ</dt><dd>{zone.mesh}</dd></div>
            <div><dt>Через хаб</dt><dd>{zone.hub}</dd></div>
            <div><dt>Экономия в день</dt><dd>{fmt(zone.saving)} ₽</dd></div>
          </dl>
          <h3>Заказы района</h3>
          <ul className="orders">
            {orders.map((o) => (
              <li key={o.id}>
                <span className={`tag ${o.route}`}>{o.route === 'mesh' ? 'ПВЗ → ПВЗ' : 'хаб'}</span>
                <span>№{o.id} · {o.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <section className="legend">
          <h2>Как читать карту</h2>
          <ul>
            {Object.entries(STATUS).map(([k, s]) => (
              <li key={k}><i className="ring" style={{ borderColor: s.color }} />{s.label}</li>
            ))}
            <li><i className="dot wb" />реальный ПВЗ Wildberries из OpenStreetMap</li>
            <li><i className="dot model" />модельный ПВЗ (в OSM отмечены не все)</li>
            <li><i className="dot center" />опорный ПВЗ с полкой ходового товара</li>
            <li><i className="line" />петля рейса ПВЗ → ПВЗ</li>
          </ul>
          <p className="muted small">Нажмите на район, чтобы увидеть его заказы и куда каждый поехал.</p>
        </section>
      )}
    </aside>
  );
}
