import { useEffect, useState } from 'react';
import { getSession, logout, resetDemo, setSession } from './api.js';
import AnalyticsView from './AnalyticsView.jsx';
import AuthView from './AuthView.jsx';
import BuyerView from './BuyerView.jsx';
import CourierView from './CourierView.jsx';
import PvzView from './PvzView.jsx';
import SupplierView from './SupplierView.jsx';
import { ROLES } from './common.jsx';

export default function App() {
  const [session, setSess] = useState(getSession);
  const [role, setRole] = useState(null);
  const [analytics, setAnalytics] = useState(window.location.hash === '#analytics');
  const [confirmReset, setConfirmReset] = useState(false);

  useEffect(() => {
    const onSession = () => setSess(getSession());
    const onHash = () => setAnalytics(window.location.hash === '#analytics');
    window.addEventListener('polka-session', onSession);
    window.addEventListener('hashchange', onHash);
    return () => { window.removeEventListener('polka-session', onSession); window.removeEventListener('hashchange', onHash); };
  }, []);

  const exit = async () => { await logout(); setSession(null); setRole(null); };
  const reset = async () => {
    await resetDemo();
    window.location.reload();
  };

  if (analytics) {
    return (
      <div className="shell">
        <nav className="topnav">
          <span className="logo">Полка рядом</span><span className="role-tag">Аналитика Wildberries</span>
          <a className="link nav-right" href="#">← К выбору роли</a>
        </nav>
        <main className="content"><AnalyticsView /></main>
      </div>
    );
  }

  if (!session) {
    if (!role) {
      return (
        <div className="landing">
          <header>
            <p className="eyebrow">Wildberries · Йошкар-Ола</p>
            <h1>Полка рядом</h1>
            <p className="muted">Городские заказы едут из ПВЗ в ПВЗ без хаба. Кто вы?</p>
          </header>
          <div className="roles">
            {Object.entries(ROLES).map(([id, r]) => (
              <button type="button" key={id} className="role-card" onClick={() => setRole(id)}>
                <span className="role-icon" aria-hidden="true">{r.icon}</span>
                <span className="role-title">{r.title}</span>
                <span className="role-text">{r.text}</span>
              </button>
            ))}
          </div>
          <footer className="landing-foot">
            <a href="#analytics">Аналитика Wildberries: районы, рейсы, экономика →</a>
            {confirmReset ? (
              <span className="confirm-reset">
                Заказы и уведомления удалятся, полки и курьеры вернутся к исходным. Сбросить?
                <button type="button" className="link" onClick={reset}>Да, сбросить</button>
                <button type="button" className="link" onClick={() => setConfirmReset(false)}>Отмена</button>
              </span>
            ) : (
              <button type="button" className="link" onClick={() => setConfirmReset(true)}>Сбросить демо</button>
            )}
          </footer>
        </div>
      );
    }
    return <AuthView role={role} onBack={() => setRole(null)} />;
  }

  const user = session.user;
  return (
    <div className="shell">
      <nav className="topnav">
        <span className="logo">Полка рядом</span>
        <span className="role-tag">{ROLES[user.role].icon} {ROLES[user.role].title}</span>
        <span className="who">{user.role === 'pvz' ? user.pvz?.name : user.name} · {user.login}</span>
        <button type="button" className="link nav-right" onClick={exit}>Выйти</button>
      </nav>
      <main className="content">
        {user.role === 'buyer' && <BuyerView user={user} />}
        {user.role === 'pvz' && <PvzView user={user} />}
        {user.role === 'courier' && <CourierView user={user} />}
        {user.role === 'supplier' && <SupplierView user={user} />}
      </main>
    </div>
  );
}
