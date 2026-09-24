"""变异演练：故意把杆系求解器与荷载加载改错一处，tests/test_structure.py 里必须有测试失败。

篡改演练（tamper_drill.py）改的是产物；这里改的是代码本身——验证的是「测试真的约束了求解器」，
而不是只在它碰巧正确时通过。每个变异都是一个可能真实发生的错误。

    python scripts/mutation_drill.py        # 约 3 分钟；不改动任何文件（变异只在内存里打补丁）
"""
import io
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import math                           # noqa: E402
from bridge import structure as ST   # noqa: E402
import test_structure as T            # noqa: E402

_k, _m, _fe, _lane, _w, _lever, _inf = (ST.Beam._k, ST.Beam._m, ST.Beam._fixed_end, ST.lane_effects, ST._weights,
                                        ST.lever_eta, ST.Beam.influence)
_neg = ST.negative_moment_mode
_smooth, _creep0, _pre, _spec = ST.smooth_extremes, ST.nominal_creep, ST.prestress_estimate, ST.bearing_spec


def k_bad(self, e):
    k = _k(self, e)
    k[1][1] *= 1.001
    return k


def m_bad(self, e):
    m = _m(self, e)
    m[0][0] *= 150 / 156
    m[2][2] *= 150 / 156
    return m


def fe_bad(self, x, P):
    e, r = _fe(self, x, P)
    return e, [r[2], -r[3], r[0], -r[1]]


def lane_bad(il, xs, q, p):
    return _lane(il, xs, q, 0.0)


def weights_bad(L, m0, mc):
    return [mc for _ in L["xs"]]


def lever_bad(secs, k, e):
    return max(0.0, _lever(secs, k, e))


def neg_mode_bad(n_spans):
    return 2


def influence_bad(self):
    IM, IV, IR, IW = _inf(self)
    return IM, [[-v for v in row] for row in IV], IR, IW


def smooth_bad(fn, rel, lo, hi):
    """只粗扫、不细化。"""
    m = max(1, int(math.ceil((hi - lo) / ST.GM_SCAN - 1e-9)))
    vals = [sum(fn(lo + (hi - lo) * j / m + r) for r in rel) for j in range(m + 1)]
    return max(vals), min(vals)


def creep_bad(t0, h_mm, rh):
    return _creep0(t0, h_mm, rh) * (0.1 + t0 ** 0.2) / (0.1 + t0 ** 0.3)


def shrink_dev_bad(dt, h_mm):
    return math.sqrt(dt / (35.0 * (h_mm / 100.0) ** 2 + dt)) if dt > 0 else 0.0


def prestress_bad(res):
    return [dict(q, Np=q["Np"] * 0.85, sigma_pc=q["sigma_pc"] * 0.85) for q in _pre(res)]


def spec_bad(e):
    sp = _spec(e)
    return dict(sp, te_eff=sp["te"])


MUTANTS = [
    ("单元刚度矩阵一个元素 ×1.001", ST.Beam, "_k", k_bad),
    ("一致质量矩阵 156 写成 150", ST.Beam, "_m", m_bad),
    ("单元内集中力的固端力左右对调", ST.Beam, "_fixed_end", fe_bad),
    ("影响线加载漏掉集中力 Pk", ST, "lane_effects", lane_bad),
    ("剪力的 m 沿跨不变（没有 m0 → mc 过渡）", ST, "_weights", weights_bad),
    ("杠杆原理法把负值截成 0", ST, "lever_eta", lever_bad),
    ("剪力影响线反号", ST.Beam, "influence", influence_bad),
    ("负弯矩冲击系数取第二阶频率", ST, "negative_moment_mode", neg_mode_bad),
    ("G-M 找最不利车位只粗扫、不细化", ST, "smooth_extremes", smooth_bad),
    ("徐变 β(t0) 的指数 0.2 写成 0.3", ST, "nominal_creep", creep_bad),
    ("收缩发展系数的 350 写成 35", ST, "shrinkage_development", shrink_dev_bad),
    ("有效预加力漏了抗裂式里的 0.85", ST, "prestress_estimate", prestress_bad),
    ("橡胶层厚没扣上下保护层", ST, "bearing_spec", spec_bad),
]
CLASSES = [T.Sections, T.Distribution, T.GMMethod, T.BeamClosedForm, T.CodeFunctions, T.Bridge, T.AppendixC,
           T.BearingMovements, T.OpenSeesCrossCheck]


def main():
    base = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in CLASSES))
    if base.failures or base.errors:
        sys.exit("未变异时就有测试失败，演练无意义")
    survived = []
    for name, obj, attr, fn in MUTANTS:
        with mock.patch.object(obj, attr, fn):
            res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
                unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in CLASSES))
        bad = [t.id().split(".", 1)[1] for t, _ in res.failures + res.errors]
        print("%-30s %s" % (name, "被 %d 个测试抓到：%s" % (len(bad), "；".join(bad[:3])) if bad else "逃过了"))
        if not bad:
            survived.append(name)
    if ST.Beam._k is not _k or ST.lane_effects is not _lane or ST.bearing_spec is not _spec:
        sys.exit("变异没有还原")
    if survived:
        print("FAIL %d / %d 个变异没被抓到：%s" % (len(survived), len(MUTANTS), "、".join(survived)))
        sys.exit(1)
    print("PASS %d 个变异，每个都有测试失败" % len(MUTANTS))


if __name__ == "__main__":
    main()
