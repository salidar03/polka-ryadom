// Обращения к API. Сессия (токен и пользователь) хранится в sessionStorage —
// своя в каждой вкладке браузера, поэтому в соседних вкладках можно войти разными ролями.
const KEY = 'polka-session';

export function getSession() {
  try { return JSON.parse(sessionStorage.getItem(KEY)); } catch { return null; }
}
export function setSession(s) {
  try { if (s) sessionStorage.setItem(KEY, JSON.stringify(s)); else sessionStorage.removeItem(KEY); } catch { /* приватный режим */ }
  window.dispatchEvent(new Event('polka-session'));
}

const toQuery = (params) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => [k, String(v)])).toString();

async function request(url, options = {}) {
  const token = getSession()?.token;
  const headers = { ...(options.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401 && token) setSession(null); // сессия устарела — на экран входа
  if (!res.ok) {
    let msg = `Сервер ответил ${res.status}`;
    try {
      const body = await res.json();
      msg = typeof body.detail === 'string' ? body.detail : 'Проверьте введённые данные';
    } catch { /* тело не JSON */ }
    throw new Error(msg);
  }
  return res.json();
}
const post = (url, body) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });

// вход
export const login = (role, loginName, password) => post('/api/auth/login', { role, login: loginName, password });
export const register = (body) => post('/api/auth/register', body);
export const logout = () => post('/api/auth/logout').catch(() => {});

// общее
export const fetchPvz = () => request('/api/pvz');
export const fetchProducts = () => request('/api/products');
export const fetchCouriers = () => request('/api/couriers');
export const resetDemo = () => post('/api/demo/reset');

// покупатель
export const fetchOffers = (productId, pvzId) => request(`/api/offers?${toQuery({ product_id: productId, pvz_id: pvzId })}`);
export const createOrder = (body) => post('/api/orders', body);
export const fetchMyOrders = () => request('/api/my/orders');

// ПВЗ
export const fetchPvzDashboard = () => request('/api/pvz/me/dashboard');
export const addStock = (productId, qty) => post('/api/pvz/me/stock', { product_id: productId, qty });
// qty — списать столько штук; без qty — убрать товар с полки целиком
export const removeStock = (productId, qty) => request(`/api/pvz/me/stock/${productId}${qty ? `?qty=${qty}` : ''}`, { method: 'DELETE' });
export const confirmAssembled = (orderId) => post(`/api/orders/${orderId}/assembled`);
export const issueOrder = (orderId, code) => post(`/api/orders/${orderId}/issued`, { code });

// сценарии: возврат, сбой, хаб, вывоз, долгое хранение
export const requestReturn = (orderId, reason) => post(`/api/orders/${orderId}/return`, { reason });
export const acceptReturn = (orderId, condition) => post(`/api/orders/${orderId}/return/accept`, { condition });
export const reportMissing = (orderId) => post(`/api/orders/${orderId}/missing`);
export const hubArrived = (orderId) => post(`/api/orders/${orderId}/hub-arrived`);
export const handoverSupply = (supplyId) => post(`/api/supplies/${supplyId}/handover`);
export const demoDays = (days) => post('/api/demo/days', { days });

// водитель рейсов
export const fetchCourierDashboard = () => request('/api/courier/me/dashboard');
export const setCourierLocation = (lat, lon) => post('/api/courier/me/location', { lat, lon });
export const startRun = () => post('/api/courier/me/run/start');
export const markPicked = (orderId) => post(`/api/orders/${orderId}/picked`);           // «Заказ взят с точки»
export const markUnloaded = (runId, stopIndex) => post(`/api/runs/${runId}/stops/${stopIndex}/unloaded`);

// поставщик
export const fetchDemand = () => request('/api/demand');
export const supplierPlan = (body) => post('/api/supplier/plan', body);
export const supplierUpload = (body) => post('/api/supplier/products', body);
export const fetchSupplierDashboard = () => request('/api/supplier/me/dashboard');
export const acceptSupply = (supplyId) => post(`/api/supplies/${supplyId}/accept`);

// аналитика
export const fetchPlan = (params) => request(`/api/plan?${toQuery(params)}`);
export const fetchOptimize = (params) => request(`/api/optimize?${toQuery(params)}`);
export const fetchPlanOrders = (params, zoneId, limit = 12) => request(`/api/plan/orders?${toQuery({ ...params, zone_id: zoneId, limit })}`);
