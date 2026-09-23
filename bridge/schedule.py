"""梁场预制、存梁与架桥机架梁的 4D 计划。

- 架梁顺序：左幅第 1 跨 → 第 12 跨（每跨 1→5 号梁），架桥机转场，右幅第 1 跨 → 第 12 跨。
  预制顺序与架梁顺序一致。
- 预制：N_BEDS 个台座错峰轮转，第 j 片梁在 YARD_START + ⌊j·BED_CYCLE/N_BEDS⌋ 天开浇，
  占台座 BED_CYCLE 天后张拉、移梁进存梁区；开浇满 MIN_AGE_DAYS 天才可架设。
- 架梁：架桥机按工时排，每片梁 ERECT_HOURS，每跨架完过孔 LAUNCH_HOURS，每日 DAY_HOURS，
  一片梁或一次过孔不跨日拆分；下一片梁未到龄期就原地等（顺序不能跳）。
- 存梁：进存梁区当天起算，架设当天离开。
- 体系转换：每幅每联最后一片梁架完后 CONT_CAST_LAG_DAYS 天浇墩顶现浇连续段，
  再过 CONT_CURE_DAYS 天张拉负弯矩钢束、拆除临时支座。
每周 7 天连续施工，按日历天计。
"""
from datetime import timedelta

from . import config as C
from .model import support_kind, support_name, unit_bounds


def sequence():
    """架梁顺序：[(幅, 跨, 梁位)]。"""
    seq = []
    for d, _ in C.DECKS:
        for k in range(1, C.N_SPANS + 1):
            for i in range(1, len(C.GIRDER_OFFSETS) + 1):
                seq.append((d, k, i))
    return seq


def plan(n_beds=None, yard_start=None, erect_start=None, ignore_ready=False):
    """ignore_ready=True：假定梁全部备齐，只受架桥机工时约束——得到架梁的机械极限工期。"""
    n_beds = n_beds or C.N_BEDS
    yard_start = yard_start or C.YARD_START
    erect_start = erect_start or C.ERECT_START
    seq = sequence()
    rows = []
    for j, (d, k, i) in enumerate(seq):
        cast = yard_start + timedelta(days=(j * C.BED_CYCLE_DAYS) // n_beds)
        rows.append({"girder": "G-%s%02d-%d" % (d, k, i), "deck": d, "span": k, "pos": i, "order": j + 1,
                     "bed": j % n_beds + 1, "cast": cast,
                     "to_storage": cast + timedelta(days=C.BED_CYCLE_DAYS),
                     "ready": cast + timedelta(days=C.MIN_AGE_DAYS)})
    # 架桥机：日期 + 当日已用工时
    day, used, idle_days = erect_start, 0.0, set()
    decks = [d for d, _ in C.DECKS]
    for r in rows:
        if r["pos"] == 1 and r["span"] == 1 and r["deck"] != decks[0]:
            # 转场：从下一个整日起算 TRANSFER_DAYS 天
            day, used = day + timedelta(days=1 + C.TRANSFER_DAYS), 0.0
        if not ignore_ready and day < r["ready"]:
            d0 = day + timedelta(days=1) if used > 0 else day
            while d0 < r["ready"]:
                idle_days.add(d0)
                d0 += timedelta(days=1)
            day, used = r["ready"], 0.0
        if used + C.ERECT_HOURS > C.DAY_HOURS + 1e-9:
            day, used = day + timedelta(days=1), 0.0
        r["erect"], r["erect_hour"] = day, used      # 当天第几个工时开始架这一片
        used += C.ERECT_HOURS
        last_in_span = r["pos"] == len(C.GIRDER_OFFSETS)
        if last_in_span and r["span"] < C.N_SPANS:
            if used + C.LAUNCH_HOURS > C.DAY_HOURS + 1e-9:
                day, used = day + timedelta(days=1), 0.0
            used += C.LAUNCH_HOURS
    for r in rows:
        r["storage_days"] = (r["erect"] - r["to_storage"]).days
        r["age_at_erect"] = (r["erect"] - r["cast"]).days
    return rows, sorted(idle_days)


def machine_bound_days(erect_start=None):
    rows, _ = plan(erect_start=erect_start, ignore_ready=True)
    first = min(r["erect"] for r in rows)
    return (max(r["erect"] for r in rows) - first).days + 1


def storage_curve(rows):
    """[(日期, 存梁数)]：存梁区里的梁 = 已进存梁区、尚未架设。"""
    start = min(r["to_storage"] for r in rows)
    end = max(r["erect"] for r in rows)
    out, d = [], start
    while d <= end:
        out.append((d, sum(1 for r in rows if r["to_storage"] <= d < r["erect"])))
        d += timedelta(days=1)
    return out


def conversions(rows):
    """每幅每联的体系转换节点：[{幅, 联, 跨, 连续墩, 架完, 浇连续段, 体系转换}]。"""
    out = []
    for d, _ in C.DECKS:
        for u, (a, b) in enumerate(unit_bounds(), 1):
            piers = [support_name(k) for k in range(a + 1, b) if support_kind(k) == "C"]
            if not piers:
                continue
            last = max(r["erect"] for r in rows if r["deck"] == d and a < r["span"] <= b)
            cast = last + timedelta(days=C.CONT_CAST_LAG_DAYS)
            out.append({"deck": d, "unit": u, "spans": (a + 1, b), "piers": piers, "erected": last, "cast": cast,
                        "conversion": cast + timedelta(days=C.CONT_CURE_DAYS)})
    return out


def yard_state(rows, day):
    """某一天梁场里的梁：台座上在制的 [(台座号, 梁)]，存梁区的 [(存梁位, 层, 梁)]。
    存梁位按进场先后依次占用，每位叠 STORAGE_LAYERS 层。"""
    beds = sorted((r["bed"], r["girder"]) for r in rows if r["cast"] <= day < r["to_storage"])
    stored = sorted((r for r in rows if r["to_storage"] <= day < r["erect"]), key=lambda r: r["order"])
    return beds, [(i // C.STORAGE_LAYERS, i % C.STORAGE_LAYERS, r["girder"]) for i, r in enumerate(stored)]


def summarize(rows, idle):
    curve = storage_curve(rows)
    peak_day, peak = max(curve, key=lambda x: (x[1], x[0]))
    first = min(r["erect"] for r in rows)
    last = max(r["erect"] for r in rows)
    return {
        "cast_first": min(r["cast"] for r in rows), "cast_last": max(r["cast"] for r in rows),
        "erect_first": first, "erect_last": last, "erect_days": (last - first).days + 1,
        "machine_bound_days": machine_bound_days(first),
        "wait_days": (last - first).days + 1 - machine_bound_days(first),
        "storage_peak": peak, "storage_peak_day": peak_day,
        "storage_max_days": max(r["storage_days"] for r in rows),
        "age_min": min(r["age_at_erect"] for r in rows),
        "conversion_last": max(c["conversion"] for c in conversions(rows)),
    }
