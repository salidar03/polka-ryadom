import { useEffect, useState } from 'react';
import { CircleMarker, Tooltip, useMapEvents } from 'react-leaflet';
import { fetchPvz, login, register, setSession } from './api.js';
import { CityMap, FlyTo, ROLES, WB, locateMe } from './common.jsx';

// Демо-вход для показа. Курьеров по умолчанию нет — их регистрирует команда.
const DEMO = {
  buyer: [['ivan', 'Иван'], ['maria', 'Мария']],
  pvz: [['pvz2', 'ПВЗ №2'], ['pvz50', 'ПВЗ №50'], ['pvz10', 'ПВЗ №10']],
  courier: [],
  supplier: [],
};

function ClickToPlace({ onPick }) {
  useMapEvents({ click: (e) => onPick([e.latlng.lat, e.latlng.lng]) });
  return null;
}

/** Вход и регистрация для выбранной роли. ПВЗ при регистрации задаёт адрес и точку на карте. */
export default function AuthView({ role, onBack }) {
  const [mode, setMode] = useState(DEMO[role].length ? 'login' : 'register');
  const [form, setForm] = useState({ name: '', login: '', password: '', address: '' });
  const [point, setPoint] = useState(null);
  const [pvz, setPvz] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const r = ROLES[role];

  useEffect(() => { fetchPvz().then(setPvz).catch(() => {}); }, []);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const done = (res) => setSession({ token: res.token, user: res.user });
  const run = async (fn) => {
    setBusy(true);
    setError('');
    try { done(await fn()); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const submit = (e) => {
    e.preventDefault();
    if (mode === 'login') return run(() => login(role, form.login, form.password));
    if (role === 'pvz' && !point) { setError('Поставьте точку ПВЗ на карте — щелчком или кнопкой «Найти адрес»'); return undefined; }
    if (role === 'courier' && !point) { setError('Отметьте, где вы сейчас: нажмите на карту или «Определить автоматически»'); return undefined; }
    return run(() => register({ role, name: form.name, login: form.login, password: form.password, address: form.address,
      lat: point?.[0], lon: point?.[1] }));
  };

  const findAddress = async () => {
    if (!form.address.trim()) { setError('Сначала впишите адрес'); return; }
    try {
      const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(`${form.address}, Йошкар-Ола`)}`);
      const [hit] = await res.json();
      if (hit) { setPoint([Number(hit.lat), Number(hit.lon)]); setError(''); } else setError('Адрес не нашёлся — поставьте точку на карте вручную');
    } catch {
      setError('Поиск адреса не ответил — поставьте точку на карте вручную');
    }
  };

  const registeringPvz = role === 'pvz' && mode === 'register';
  const registeringCourier = role === 'courier' && mode === 'register';
  const locate = async () => {
    try { setPoint(await locateMe()); setError(''); } catch (e) { setError(e.message); }
  };

  return (
    <div className="shell">
      <nav className="topnav">
        <span className="logo">Полка рядом</span>
        <span className="role-tag">{r.icon} {r.title}</span>
        <button type="button" className="link nav-right" onClick={onBack}>← Другая роль</button>
      </nav>
      <main className="content">
        <div className="app">
          <aside className="panel">
            <header className="brand">
              <p className="eyebrow">{r.title}</p>
              <h1>{mode === 'login' ? 'Вход' : 'Регистрация'}</h1>
              <p className="muted">{r.text}</p>
            </header>
            <div className="seg" role="tablist">
              <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'on' : ''} onClick={() => { setMode('login'); setError(''); }}>Вход</button>
              <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'on' : ''} onClick={() => { setMode('register'); setError(''); }}>Регистрация</button>
            </div>
            {error && <p className="error" role="alert">{error}</p>}

            <form className="block" onSubmit={submit}>
              {mode === 'register' && (
                <label className="field" htmlFor="f-name"><span>{{ pvz: 'Название ПВЗ', supplier: 'Компания или ваше имя' }[role] || 'Имя'}</span>
                  <input id="f-name" required value={form.name} onChange={set('name')} placeholder={{ pvz: 'ПВЗ на Ленинском', supplier: 'ООО «Звук»' }[role] || 'Пётр'} />
                </label>
              )}
              {registeringPvz && (
                <>
                  <label className="field" htmlFor="f-addr"><span>Адрес ПВЗ</span>
                    <input id="f-addr" required value={form.address} onChange={set('address')} placeholder="Ленинский проспект, 24" />
                  </label>
                  <button type="button" className="link" onClick={findAddress}>Найти адрес на карте</button>
                  <p className="muted small">{point ? `Точка: ${point[0].toFixed(5)}, ${point[1].toFixed(5)} — можно поправить щелчком по карте` : 'Или нажмите на карту, чтобы поставить точку ПВЗ.'}</p>
                </>
              )}
              {registeringCourier && (
                <>
                  <p className="field-title">Где вы сейчас</p>
                  <div className="actions">
                    <button type="button" className="secondary small-btn" onClick={locate}>Определить автоматически</button>
                  </div>
                  <p className="muted small">{point ? `Отмечено: ${point[0].toFixed(5)}, ${point[1].toFixed(5)} — можно поправить щелчком по карте` : 'Или нажмите на карту, где вы находитесь. От этой точки система выбирает ближайшего курьера.'}</p>
                </>
              )}
              <label className="field" htmlFor="f-login"><span>Логин</span>
                <input id="f-login" required autoComplete="username" value={form.login} onChange={set('login')} placeholder="латиницей, от 3 символов" />
              </label>
              <label className="field" htmlFor="f-pass"><span>Пароль</span>
                <input id="f-pass" required type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={form.password} onChange={set('password')} />
              </label>
              <button type="submit" className="primary" disabled={busy}>{mode === 'login' ? 'Войти' : 'Зарегистрироваться'}</button>
            </form>

            {mode === 'login' && DEMO[role].length > 0 && (
              <section className="block demo">
                <h2>Демо-вход для показа</h2>
                <p className="muted small">Пароль у демо-аккаунтов — 1234. Любой ПВЗ: логин pvz и номер, например pvz37.</p>
                <div className="actions">
                  {DEMO[role].map(([l, name]) => (
                    <button type="button" key={l} className="secondary small-btn" disabled={busy} onClick={() => run(() => login(role, l, '1234'))}>{name}</button>
                  ))}
                </div>
              </section>
            )}
          </aside>

          <CityMap>
            {(registeringPvz || registeringCourier) && <ClickToPlace onPick={setPoint} />}
            <FlyTo points={point ? [point] : []} />
            {pvz.map((p) => (
              <CircleMarker key={p.id} center={[p.lat, p.lon]} radius={4} pathOptions={{ color: WB, weight: 2, fillColor: '#FFFFFF', fillOpacity: 1 }}>
                <Tooltip>{p.name}{p.address ? <><br />{p.address}</> : null}<br />логин pvz{p.id}</Tooltip>
              </CircleMarker>
            ))}
            {point && (
              <CircleMarker center={point} radius={11} pathOptions={{ color: '#E8742A', weight: 4, fillColor: '#E8742A', fillOpacity: 0.5 }}>
                <Tooltip permanent direction="top">{role === 'courier' ? 'Я здесь' : 'Новый ПВЗ'}</Tooltip>
              </CircleMarker>
            )}
          </CityMap>
        </div>
      </main>
    </div>
  );
}
