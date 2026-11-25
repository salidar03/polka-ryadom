"""Собирает список ПВЗ Йошкар-Олы для модели: data/pvz.json.

Источники (OpenStreetMap, лицензия ODbL):
  * data/osm_raw.json        — пункты Wildberries, отмеченные в OSM (реальные точки);
  * data/osm_apartments.json — многоквартирные дома (по ним видно, где живут люди).

В OSM отмечена только часть пунктов Wildberries, поэтому недостающие точки
расставляем как «модельные» ПВЗ у самых плотных кварталов, не ближе MIN_GAP_M
друг к другу. Реальные и модельные точки помечены полем source.
Чтобы подставить свой реальный список ПВЗ, положите CSV в data/pvz.csv
(колонки: name,lat,lon) — тогда скрипт возьмёт его вместо модельных точек.

Запуск: python scripts/build_pvz.py
"""
import csv
import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
TARGET_TOTAL = 110      # примерно столько ПВЗ WB в городе (по карте команды)
MIN_GAP_M = 330         # модельные ПВЗ не ставим ближе этого к другим
DENSITY_RADIUS_M = 450  # «вес» точки = сколько домов вокруг


def dist_m(a, b):
    r = 6_371_000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def coords(el):
    return (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])


def main():
    homes = [coords(e) for e in json.loads((DATA / "osm_apartments.json").read_text(encoding="utf-8"))["elements"]]

    def weight(p):
        return sum(1 for h in homes if dist_m(p, h) <= DENSITY_RADIUS_M)

    points = []
    for el in json.loads((DATA / "osm_raw.json").read_text(encoding="utf-8"))["elements"]:
        if el.get("tags", {}).get("shop") not in ("outpost", None) and "brand" not in el.get("tags", {}):
            continue
        lat, lon = coords(el)
        t = el.get("tags", {})
        addr = ", ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x)
        points.append({"name": "", "address": addr, "lat": lat, "lon": lon, "source": "osm"})

    custom = DATA / "pvz.csv"
    if custom.exists():
        with custom.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                points.append({"name": row["name"], "address": row.get("address", ""), "lat": float(row["lat"]), "lon": float(row["lon"]), "source": "team"})
    else:
        ranked = sorted(homes, key=weight, reverse=True)
        for h in ranked:
            if len(points) >= TARGET_TOTAL:
                break
            if all(dist_m(h, (p["lat"], p["lon"])) >= MIN_GAP_M for p in points):
                points.append({"name": "", "address": "", "lat": round(h[0], 6), "lon": round(h[1], 6), "source": "model"})

    for i, p in enumerate(points, start=1):
        p["id"] = i
        if not p["name"]:
            p["name"] = f"ПВЗ №{i}"
        p["weight"] = max(1, weight((p["lat"], p["lon"])))
    (DATA / "pvz.json").write_text(json.dumps(points, ensure_ascii=False, indent=1), encoding="utf-8")
    by_src = {}
    for p in points:
        by_src[p["source"]] = by_src.get(p["source"], 0) + 1
    print("ПВЗ всего:", len(points), by_src)


if __name__ == "__main__":
    main()
