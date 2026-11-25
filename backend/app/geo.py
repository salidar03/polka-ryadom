"""География: расстояния, районы (круги) и петля рейса внутри района."""
import math

EARTH_R = 6_371_000
ROAD_FACTOR = 1.4  # по дорогам примерно в 1,4 раза длиннее, чем по прямой


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Расстояние по прямой между двумя точками (lat, lon) в метрах."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def build_zones(points: list[dict], radius_m: float) -> list[dict]:
    """Делит ПВЗ на районы.

    1. Жадно выбираем опорные ПВЗ: каждый раз берём точку, чей круг радиусом
       radius_m накрывает больше всего ещё не накрытых ПВЗ (при равенстве —
       с большим спросом вокруг).
    2. Каждый ПВЗ прикрепляем к ближайшему опорному: так он попадает ровно
       в один район, и его заказы не считаются дважды.
    """
    pos = {p["id"]: (p["lat"], p["lon"]) for p in points}
    weight = {p["id"]: p.get("weight", 1) for p in points}
    ids = list(pos)
    cover = {i: {j for j in ids if dist_m(pos[i], pos[j]) <= radius_m} for i in ids}

    uncovered, centers = set(ids), []
    while uncovered:
        best = max(ids, key=lambda i: (len(cover[i] & uncovered), sum(weight[j] for j in cover[i] & uncovered), -i))
        centers.append(best)
        uncovered -= cover[best]

    members = {c: [] for c in centers}
    for j in ids:
        nearest = min(centers, key=lambda c: dist_m(pos[j], pos[c]))
        members[nearest].append(j)

    zones = []
    for n, c in enumerate(centers, start=1):
        loop, length = route_loop(c, members[c], pos)
        zones.append({"id": n, "center_id": c, "pvz_ids": sorted(members[c]), "loop": loop,
                      "loop_km": round(length * ROAD_FACTOR / 1000, 2)})
    return zones


def route_loop(start: int, stops: list[int], pos: dict) -> tuple[list[int], float]:
    """Петля рейса: опорный ПВЗ → все ПВЗ района → обратно.

    Ближайший сосед, затем улучшение 2-opt. Для района из 5–15 точек этого достаточно.
    """
    rest = [s for s in stops if s != start]
    order = [start]
    while rest:
        last = pos[order[-1]]
        nxt = min(rest, key=lambda s: dist_m(last, pos[s]))
        order.append(nxt)
        rest.remove(nxt)

    def length(seq):
        return sum(dist_m(pos[seq[i]], pos[seq[(i + 1) % len(seq)]]) for i in range(len(seq))) if len(seq) > 1 else 0.0

    improved = True
    while improved and len(order) > 3:
        improved = False
        for i in range(1, len(order) - 1):
            for k in range(i + 1, len(order)):
                cand = order[:i] + order[i:k + 1][::-1] + order[k + 1:]
                if length(cand) + 1e-6 < length(order):
                    order, improved = cand, True
    return order, length(order)
