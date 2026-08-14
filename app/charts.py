"""Геометрия для инлайновых SVG-графиков дашборда (страница «Аналитика»).

Никаких JS-библиотек — сервер сам считает пиксели/координаты, шаблон просто
рисует готовые <path>/<rect>. Подсказки при наведении — через нативный <title>
у каждой фигуры (работает без JS, доступно с клавиатуры через tabindex).
"""
from __future__ import annotations


def _rounded_top_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Path вертикального столбика: скруглены только два верхних угла (data-end),
    у основания — прямой угол, как в спеке (растёт от единой baseline)."""
    r = min(r, w / 2, h) if h > 0 else 0
    if h <= 0:
        return ""
    if r <= 0:
        return f"M {x},{y+h} L {x},{y} L {x+w},{y} L {x+w},{y+h} Z"
    return (
        f"M {x},{y+h} "
        f"L {x},{y+r} Q {x},{y} {x+r},{y} "
        f"L {x+w-r},{y} Q {x+w},{y} {x+w},{y+r} "
        f"L {x+w},{y+h} Z"
    )


def _rounded_end_path_h(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Path горизонтального столбика: скруглён только правый конец (data-end),
    у начала (baseline слева) — прямой угол."""
    r = min(r, h / 2, w) if w > 0 else 0
    if w <= 0:
        return ""
    if r <= 0:
        return f"M {x},{y} L {x+w},{y} L {x+w},{y+h} L {x},{y+h} Z"
    return (
        f"M {x},{y} "
        f"L {x+w-r},{y} Q {x+w},{y} {x+w},{y+r} "
        f"L {x+w},{y+h-r} Q {x+w},{y+h} {x+w-r},{y+h} "
        f"L {x},{y+h} Z"
    )


# ---------- гистограмма оценок (1-10) ----------

_BAR_W = 22        # ширина столбика, px (≤24 по спеке)
_BAR_GAP = 10       # промежуток между столбиками
_CHART_H = 120      # высота области построения (столбиков), px
_TOP_PAD = 16       # запас сверху под подпись значения у самого высокого столбика


def rating_histogram(distribution: list) -> dict:
    """distribution: [{rating, count}, ...] (10 штук, 1..10) -> геометрия для шаблона."""
    max_count = max((d["count"] for d in distribution), default=0) or 1
    bars = []
    for d in distribution:
        h = round((d["count"] / max_count) * _CHART_H) if d["count"] else 0
        rating = d["rating"]
        # тот же порог, что и в export.py/шаблонах: 1-5 красный, 6-7 жёлтый, 8-10 зелёный
        tier = "err" if rating <= 5 else ("warn" if rating <= 7 else "ok")
        x = (rating - 1) * (_BAR_W + _BAR_GAP)
        y = _TOP_PAD + (_CHART_H - h)
        bars.append({
            "rating": rating, "count": d["count"], "tier": tier,
            "path": _rounded_top_path(x, y, _BAR_W, h),
            "label_x": x + _BAR_W / 2, "label_y": y - 4,
        })
    width = len(distribution) * (_BAR_W + _BAR_GAP) - _BAR_GAP
    return {"bars": bars, "bar_w": _BAR_W, "width": width,
            "height": _TOP_PAD + _CHART_H, "max_count": max_count}


# ---------- тренд вопросов по дням ----------

_LINE_W = 640
_LINE_H = 110
_LINE_PAD = 6  # отступ сверху, чтобы верхняя точка не срезалась обводкой


def questions_trend(series: list) -> dict:
    """series: [{date, count}, ...] -> точки/путь для line+area чарта."""
    n = len(series)
    max_count = max((d["count"] for d in series), default=0) or 1
    usable_h = _LINE_H - _LINE_PAD
    step = _LINE_W / max(n - 1, 1)

    points = []
    for i, d in enumerate(series):
        x = round(i * step, 1)
        y = round(_LINE_PAD + usable_h - (d["count"] / max_count) * usable_h, 1)
        points.append({"x": x, "y": y, "date": d["date"], "count": d["count"]})

    line_d = "M " + " L ".join(f"{p['x']},{p['y']}" for p in points)
    area_d = (line_d + f" L {points[-1]['x']},{_LINE_H} L {points[0]['x']},{_LINE_H} Z"
              if points else "")

    # подписи по оси X — первая, средняя, последняя дата (не все 30, тесно).
    # anchor меняется по краям, иначе крайние подписи вылезают за холст.
    tick_idx = sorted({0, n // 2, n - 1}) if n else []
    ticks = []
    for i in tick_idx:
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        ticks.append({"x": points[i]["x"], "date": series[i]["date"], "anchor": anchor})

    return {"points": points, "line_d": line_d, "area_d": area_d, "ticks": ticks,
            "width": _LINE_W, "height": _LINE_H, "max_count": max_count}


# ---------- топ тем по количеству вопросов ----------

_HBAR_W = 480       # ширина области под сам столбик (значение подписывается за её пределами)
_HBAR_H = 14
_LABEL_H = 15       # место под подпись темы НАД столбиком, в пределах своей строки
_ROW_GAP = 8


def topic_bars(topics: list, limit: int = 8) -> dict:
    """topics: topic_stats()-подобный список -> топ по вопросам, горизонтальные столбики.
    Каждая строка: подпись темы сверху, столбик под ней — обе умещаются в своём row_h,
    поэтому даже первая строка не вылезает за верхний край холста."""
    top = sorted(topics, key=lambda t: t["questions"], reverse=True)[:limit]
    max_q = max((t["questions"] for t in top), default=0) or 1
    row_h = _LABEL_H + _HBAR_H
    bars = []
    for i, t in enumerate(top):
        w = round(t["questions"] / max_q * _HBAR_W)
        row_top = i * (row_h + _ROW_GAP)
        bar_y = row_top + _LABEL_H
        bars.append({
            "topic": t["topic"], "questions": t["questions"],
            "path": _rounded_end_path_h(0, bar_y, w, _HBAR_H),
            "label_y": row_top + _LABEL_H - 4,     # подпись темы (baseline текста)
            "value_x": w + 6, "value_y": bar_y + _HBAR_H - 3,  # число сразу за концом столбика
        })
    height = len(top) * (row_h + _ROW_GAP) - _ROW_GAP if top else 0
    return {"bars": bars, "width": _HBAR_W + 40, "height": height, "bar_h": _HBAR_H}
