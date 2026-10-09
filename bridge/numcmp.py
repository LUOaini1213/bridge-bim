"""两份文本比对：先要求逐字节相同；不同时退一步，按「数字 / 非数字」切成记号再比——
非数字部分必须完全相同，数字只允许末位上的差异。

用途：产物在 Windows 上生成、在 Linux CI 上复算，两边数学库（sin、cos、exp、sqrt）的结果
可能在最后一位二进制上不同。这种差异不该让检查变红，别的任何差异都应该。

- decimal：CSV / JSON 里按固定小数位打印的数。只有 3 位及以上小数（坐标、高程这类 0.1–1 mm 分辨率）
  允许末位差 1 个单位（四舍五入恰好落在进位边界上时，末位会差 1）；两位及以下小数（梁长、体积、重量）
  必须完全相同——否则把 28.78 m 的梁改成 28.79 m 也会被当成舍入差放过（篡改演练里真出现过）。
- relative：IFC 里按完整精度打印的浮点数，差值不超过 1e-9·max(1, |x|)。
不带小数点的整数（编号、计数、实体号）两种模式下都必须完全相同。
"""
import re
import json
import math

_NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _tokens(text):
    out, pos = [], 0
    for m in _NUM.finditer(text):
        out.append(("s", text[pos:m.start()]))
        out.append(("n", m.group()))
        pos = m.end()
    out.append(("s", text[pos:]))
    return out


def _decimals(s):
    mant = s.lower().split("e")[0]
    return len(mant.split(".")[1]) if "." in mant else 0


def compare(a, b, mode="decimal"):
    """返回 (是否一致, 仅数字末位不同的记号数, 第一处不一致的说明)。"""
    if a == b:
        return True, 0, None
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) != len(tb):
        return False, 0, "记号数不同（%d vs %d）" % (len(ta), len(tb))
    diffs = 0
    for (ka, va), (kb, vb) in zip(ta, tb):
        if va == vb:
            continue
        if ka != "n" or kb != "n":
            return False, diffs, "文字不同：%r vs %r" % (va[:60], vb[:60])
        if not any(ch in va + vb for ch in ".eE"):
            return False, diffs, "整数不同：%s vs %s" % (va, vb)
        x, y = float(va), float(vb)
        if mode == "decimal":
            da, db = _decimals(va), _decimals(vb)
            if da != db or "e" in (va + vb).lower():
                return False, diffs, "数字格式不同：%s vs %s" % (va, vb)
            if da < 3:
                return False, diffs, "两位及以下小数必须完全相同：%s vs %s" % (va, vb)
            if abs(x - y) > 1.000001 * 10.0 ** (-da):
                return False, diffs, "数值差超过末位 1 个单位：%s vs %s" % (va, vb)
        elif abs(x - y) > 1e-9 * max(1.0, abs(x), abs(y)):
            return False, diffs, "数值差超过 1e-9（相对）：%s vs %s" % (va, vb)
        diffs += 1
    return True, diffs, None


def compare_json(a, b, float_atol=0.0, float_scales=None, diagnostics=None):
    """Strict JSON schema/value comparison, using the existing 1e-9 gate
    only for finite float leaves. A caller may supply a small, explicit
    absolute floor for cancellation near zero. IDs, integer indices/counts,
    booleans, list order/length and dictionary keys remain exact. This is
    for unrounded structural results, not fixed-decimal engineering tables.
    """
    if not math.isfinite(float_atol) or float_atol < 0:
        raise ValueError("JSON float absolute tolerance must be finite and nonnegative")
    float_scales = float_scales or {}
    if any(not math.isfinite(scale) or scale < 0 for scale in float_scales.values()):
        raise ValueError("JSON float comparison scales must be finite and nonnegative")
    try:
        left, right = json.loads(a), json.loads(b)
    except (ValueError, TypeError) as error:
        return False, 0, "JSON 无效：" + str(error)
    diffs = [0]

    def visit(x, y, path):
        if type(x) is not type(y):
            return "%s 类型不同：%s vs %s" % (path, type(x).__name__, type(y).__name__)
        if isinstance(x, dict):
            if set(x) != set(y):
                return path + " 字段不同"
            first_failure = None
            for key in x:
                failure = visit(x[key], y[key], path + "." + key)
                first_failure = first_failure or failure
            return first_failure
        elif isinstance(x, list):
            if len(x) != len(y):
                return path + " 列表长度不同"
            first_failure = None
            for index, (first, second) in enumerate(zip(x, y)):
                failure = visit(first, second, "%s[%d]" % (path, index))
                first_failure = first_failure or failure
            return first_failure
        elif isinstance(x, float):
            if not math.isfinite(x) or not math.isfinite(y):
                return path + " 包含非有限浮点数"
            limit = max(float_atol, 1e-9 * max(1.0, abs(x), abs(y), float_scales.get(path, 0.0)))
            if diagnostics is not None and x != y:
                diagnostics.append({"path": path, "difference": abs(x - y), "limit": limit,
                                    "ratio": abs(x - y) / limit, "left": x, "right": y})
            if abs(x - y) > limit:
                return "%s 浮点差超过容差 %g（1e-9相对，绝对下限%g）：%r vs %r" % (path, limit, float_atol, x, y)
            if x != y:
                diffs[0] += 1
        elif x != y:
            return "%s 值不同：%r vs %r" % (path, x, y)
        return None

    failure = visit(left, right, "$")
    return failure is None, diffs[0], failure


def compare_stage_json(a, b, selected=False, diagnostics=None):
    """Stage schema 1 moments use the existing OpenSees vector-norm gate.

    A stiffness solution recovers moments by cancellation in each k_e*u_e
    dot product. Its near-zero ordinates need a normwise rather than a
    pointwise relative comparison. Only the four identified moment curves
    get the 1e-9 infinity-norm scale. Selected extrema keep the scalar gate.
    Coordinates, forces, identities and all other leaves keep their gate.
    """
    try:
        left, right = json.loads(a), json.loads(b)
    except (ValueError, TypeError) as error:
        return False, 0, "JSON 无效：" + str(error)
    stages = ("M1", "Mc", "M2", "MG")
    curves = []
    if selected:
        if any(not isinstance(data, dict) or data.get("stage") not in stages for data in (left, right)):
            return False, 0, "不支持的阶段选择 schema"
        curves = [("$.moments", left.get("moments"), right.get("moments"))]
    else:
        if any(not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1
               or not isinstance(data.get("lines"), list) for data in (left, right)):
            return False, 0, "不支持的阶段结果 schema"
        for index, (x, y) in enumerate(zip(left["lines"], right["lines"])):
            if not isinstance(x, dict) or not isinstance(y, dict):
                continue
            xm, ym = x.get("moments"), y.get("moments")
            if isinstance(xm, dict) and isinstance(ym, dict):
                curves.extend(("$.lines[%d].moments.%s" % (index, stage), xm.get(stage), ym.get(stage)) for stage in stages)
    scales = {}
    for path, x, y in curves:
        # Malformed/nonfinite/type-altered arrays must fail strict comparison;
        # do not derive a larger scale from an invalid curve.
        if not isinstance(x, list) or not isinstance(y, list) or not x or len(x) != len(y):
            continue
        if any(type(value) is not float or not math.isfinite(value) for value in x + y):
            continue
        scale = max(1.0, max(abs(value) for value in x + y))
        scales.update(("%s[%d]" % (path, index), scale) for index in range(len(x)))
    return compare_json(a, b, float_atol=2e-9, float_scales=scales, diagnostics=diagnostics)


def stage_roundoff_summary(diagnostics):
    """Full-leaf diagnostics, including every failing float, for Linux CI."""
    groups = {}
    for item in diagnostics:
        match = re.match(r"\$\.lines\[\d+\]\.moments\.(M1|Mc|M2|MG)\[", item["path"])
        group = match.group(1) if match else "other_float_fields"
        stats = groups.setdefault(group, {"changed": 0, "max_absolute": 0.0, "max_gate_ratio": 0.0})
        stats["changed"] += 1
        if item["difference"] > stats["max_absolute"]:
            stats.update(max_absolute=item["difference"], max_absolute_path=item["path"])
        if item["ratio"] > stats["max_gate_ratio"]:
            stats.update(max_gate_ratio=item["ratio"], max_ratio_path=item["path"])
    return {"changed_float_leaves": len(diagnostics), "groups": groups,
            "violations": [item for item in diagnostics if item["ratio"] > 1.0]}
