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
