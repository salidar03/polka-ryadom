import { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, useMap } from 'react-leaflet';

export const CITY_CENTER = [56.6345, 47.8925]; // Йошкар-Ола

export const ROLES = {
  buyer: { title: 'Покупатель', text: 'Заказать товар и забрать его в ПВЗ рядом — сразу и дешевле, или с доставкой в свой ПВЗ.', icon: '🛍️' },
  pvz: { title: 'ПВЗ', text: 'Пункт выдачи: адрес, полка с товарами, сборка заказов и выдача по коду.', icon: '🏬' },
  courier: { title: 'Доставка', text: 'Курьер: задания «забрать в одном ПВЗ — отвезти в другой», маршрут и заработок.', icon: '🚗' },
  supplier: { title: 'Поставщик', text: 'Загрузить товар — система по спросу предложит разложить часть партии по ПВЗ городов.', icon: '🏭' },
};
export const WB = '#7B2D8E';
export const nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
export const fmt = (n) => nf.format(Math.round(n ?? 0));
export const km = (m) => (m < 1000 ? `${fmt(m)} м` : `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(m / 1000)} км`);

/** Повторяет fn каждые ms миллисекунд, пока компонент на экране. */
export function usePoll(fn, deps, ms = 3000) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    saved.current();
    const t = setInterval(() => saved.current(), ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/** Карта города с подложкой OpenStreetMap. */
export function CityMap({ children, className = 'map' }) {
  return (
    <MapContainer center={CITY_CENTER} zoom={13} className={className} preferCanvas>
      <TileLayer
        attribution='&copy; участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {children}
    </MapContainer>
  );
}

/** Плавно переводит карту на точку или набор точек. */
export function FlyTo({ points }) {
  const map = useMap();
  const key = JSON.stringify(points);
  useEffect(() => {
    const pts = (points || []).filter(Boolean);
    if (pts.length === 1) map.flyTo(pts[0], Math.max(map.getZoom(), 15), { duration: 0.6 });
    else if (pts.length > 1) map.flyToBounds(pts, { padding: [60, 60], duration: 0.6, maxZoom: 16 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}

/** Геолокация браузера. Работает только на localhost или https — по адресу http://192.168… браузер её не даст. */
export function locateMe() {
  return new Promise((resolve, reject) => {
    if (!window.isSecureContext || !navigator.geolocation) {
      reject(new Error('Автоматически определить место здесь нельзя (нужен localhost или https) — нажмите на карту.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve([p.coords.latitude, p.coords.longitude]),
      () => reject(new Error('Браузер не дал определить место — нажмите на карту, чтобы отметить его вручную.')),
      { enableHighAccuracy: true, timeout: 8000 },
    );
  });
}

export function StatusChip({ status, label }) {
  const tone = { issued: 'done', delivered: 'good', ready: 'good', via_hub: 'muted' }[status] || 'work';
  return <span className={`chip ${tone}`}>{label}</span>;
}

export function Notes({ items }) {
  if (!items?.length) return <p className="muted small">Пока уведомлений нет.</p>;
  return (
    <ul className="notes">
      {items.slice(0, 6).map((n) => (
        <li key={n.id}><span className="time">{n.time}</span>{n.text}</li>
      ))}
    </ul>
  );
}
