import { Circle, CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from 'react-leaflet';

const CENTER = [56.6345, 47.8925]; // Йошкар-Ола

export const STATUS = {
  mesh: { color: '#2F7A4D', label: 'рейсы окупаются, все заказы с полки' },
  partial: { color: '#E8742A', label: 'рейсы есть, хвост заказов — через хаб' },
  hub: { color: '#8A94A6', label: 'мало заказов, район работает через хаб' },
};
const WB = '#7B2D8E';

export default function MapView({ pvz, plan, selectedId, onSelect }) {
  const zones = plan?.zones || [];
  const zoneOf = {};
  const centers = new Set();
  zones.forEach((z) => { z.pvz_ids.forEach((id) => { zoneOf[id] = z.id; }); centers.add(z.center_id); });

  return (
    <MapContainer center={CENTER} zoom={13} className="map" preferCanvas>
      <TileLayer
        attribution='&copy; участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {zones.map((z) => (
        <Circle
          key={`c${z.id}-${z.radius_m}`}
          center={z.center}
          radius={z.radius_m}
          pathOptions={{
            color: STATUS[z.status].color,
            weight: z.id === selectedId ? 4 : 2,
            fillColor: STATUS[z.status].color,
            fillOpacity: z.id === selectedId ? 0.18 : 0.06,
          }}
          eventHandlers={{ click: () => onSelect(z.id) }}
        />
      ))}

      {zones.filter((z) => z.runs > 0 && z.loop.length > 1).map((z) => (
        <Polyline
          key={`r${z.id}-${z.radius_m}`}
          positions={z.loop}
          pathOptions={{ color: '#E8742A', weight: z.id === selectedId ? 5 : 3, opacity: 0.9 }}
          eventHandlers={{ click: () => onSelect(z.id) }}
        />
      ))}

      {pvz.map((p) => {
        const isCenter = centers.has(p.id);
        return (
          <CircleMarker
            key={p.id}
            center={[p.lat, p.lon]}
            radius={isCenter ? 8 : 5}
            pathOptions={{
              color: isCenter ? '#E8742A' : WB,
              weight: isCenter ? 3 : 2,
              fillColor: p.source === 'model' ? '#FFFFFF' : WB,
              fillOpacity: 1,
            }}
            eventHandlers={{ click: () => zoneOf[p.id] && onSelect(zoneOf[p.id]) }}
          >
            <Tooltip>
              <b>{p.name}</b>
              <br />
              {p.source === 'osm' ? 'реальный пункт (OpenStreetMap)' : p.source === 'team' ? 'пункт из списка команды' : 'модельный пункт'}
              {zoneOf[p.id] && <><br />район №{zoneOf[p.id]}{isCenter ? ' · опорный ПВЗ с полкой' : ''}</>}
            </Tooltip>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}
