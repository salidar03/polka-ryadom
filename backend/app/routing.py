"""Маршрут по улицам через открытый сервис OSRM (данные OpenStreetMap).

Если сервис недоступен или отключён (POLKA_ROUTING=off, например в тестах),
строим путь по прямой с поправкой на дороги — система продолжает работать.
"""
import json
import os
import urllib.request

from .geo import ROAD_FACTOR, dist_m

SERVERS = ["https://router.project-osrm.org", "https://routing.openstreetmap.de/routed-car"]
_cache: dict[tuple, dict] = {}


def _straight(points: list[tuple[float, float]]) -> dict:
    d = sum(dist_m(points[i], points[i + 1]) for i in range(len(points) - 1)) * ROAD_FACTOR
    return {"path": [list(p) for p in points], "distance_m": round(d), "duration_s": round(d / 1000 / 25 * 3600), "source": "line"}


def road_route(points: list[tuple[float, float]]) -> dict:
    """points — [(lat, lon), …]. Возвращает {path, distance_m, duration_s, source}."""
    if len(points) < 2 or points[0] == points[-1] and len(points) == 2:
        return {"path": [list(p) for p in points], "distance_m": 0, "duration_s": 0, "source": "line"}
    if os.getenv("POLKA_ROUTING", "on") == "off":
        return _straight(points)
    key = tuple((round(a, 5), round(b, 5)) for a, b in points)
    if key in _cache:
        return _cache[key]
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    for base in SERVERS:
        try:
            req = urllib.request.Request(f"{base}/route/v1/driving/{coords}?overview=full&geometries=geojson",
                                         headers={"User-Agent": "polka-ryadom-hackathon/0.3"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.load(resp)
            if data.get("code") == "Ok":
                r = data["routes"][0]
                res = {"path": [[lat, lon] for lon, lat in r["geometry"]["coordinates"]],
                       "distance_m": round(r["distance"]), "duration_s": round(r["duration"]), "source": "osrm"}
                _cache[key] = res
                return res
        except Exception:  # сеть, таймаут, неожиданный ответ — пробуем следующий сервер
            continue
    return _straight(points)
