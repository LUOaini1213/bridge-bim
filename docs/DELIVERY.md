# 交付入口与原生三维几何校核

从 [README](../README.md) 移来。命令都在仓库根目录运行。

## 完整交付入口

使用已安装 `requirements.txt` 的虚拟环境 Python。入口会把该解释器传给所有子命令，离线检查不启动 Rhino、不改写正式报告。

```powershell
.\.venv\Scripts\python.exe scripts\delivery.py --check
.\.venv\Scripts\python.exe scripts\delivery.py --revalidate
.\.venv\Scripts\python.exe scripts\delivery.py --rebuild
.\.venv\Scripts\python.exe scripts\delivery.py --rebuild --construction-config construction\my-plan.json
```

Linux / macOS 上只有离线的两个入口（`--rebuild` 要 Windows 上的 Rhino 8）：

```bash
.venv/bin/python scripts/delivery.py --check
.venv/bin/python scripts/delivery.py --revalidate
```

`--rebuild` 需要 Windows 和已授权的 Rhino 8，依次刷新数据、IFC/IDS、原生时间轴与空间检查、生命周期和审批反例，再运行独立验收。
默认配置运行全部测试；自定义配置验收实际选定的数据、IFC、原生回放和 profile summary，README 中的默认示例数字保留其参考范围。
日历、工效或审批修改沿用源模型的参考日期；改台座数量或源几何时，明确追加 `--regenerate-source` 重新建源。
常规重建在覆盖任何正式产物前核验实际源模型；独立 `scripts/check_source.py` 核验形状、位置、单位、属性及来源配置。

`model/delivery_index.json` 记录本次通过检查的输入、代码、构造配置、全部交付产物 SHA256 和实际 Python/依赖/Rhino 版本。
文本指纹规范 CRLF 为 LF，二进制模型保持原始 SHA256；改参数、遗漏快照、改源模型或中途失败均不能继续使用旧的通过状态。
无 Rhino 时可运行 `--check` 验收已提交的交付，缺少 Rhino 的重建请求会保留已有产物和索引。

`--revalidate` 用于只改了文档、测试、离线检查脚本、CI 或依赖清单注释的提交：不启动 Rhino，跑完全部离线检查，
确认全部交付产物的 SHA256 与原生构建时一致，再把新的输入指纹追加到索引的 `revalidations` 里（原生构建记录不动）。
只要改动碰到会进入原生构建的输入（`bridge/`、`rhino/`、施工输入 JSON、`quality/profile.json`、`scripts/run_*.py`、
`check_ids.py`、`check_source.py`），它就拒绝并要求 `--rebuild`；`--check` 也按同一规则核对索引里每一次复核记录。
`build_data.py` 与 `export_ifc.py` 不在此列：Rhino 不运行它们，离线检查用它们重算 `data/` 与 IFC 并与已提交文件逐字节比对。

## 原生三维几何校核

结果：120 片梁、642 对候选，实体相交 **0** 处、净距或接触事件 **0** 处、未决 **0** 处（默认净距 25 mm），
见 [`model/quality/native_spatial.json`](../model/quality/native_spatial.json)。以下是方法与边界。

`rhino/check_clearance.py` 读取源模型中全部 120 片预制梁的实际闭合网格，保留各平面面片转成 Brep，
把模型的米转换为毫米后检查梁与梁的实体相交体积及可配置净距。默认净距为 25 mm；
支座与设计现浇连接不在此报告范围内，网格见证点不声称为精确最短距离。
源模型不写回；碰撞、净距/接触、几何内核未决分别记录构件编号、Rhino GUID、零件和位置。
两个实际 Brep 都通过轴对齐盒认证时才记录 `actual_gap_mm`，并区分接触、低于阈值及达到阈值；
达到阈值的 `at_clearance` 是边界记录，不计净距不足。比较的数值 epsilon 不代表施工允许偏差。
其他几何的 MeshClash 见证只列为 `threshold_candidate` 待距离复核，不能当成精确最短距离；
认证的箱体距离、原网格见证点及接触搜索结果分别保留，不用包络距离冒充一般实体距离。
空或失败的布尔结果必须有经验证的几何证明才能判无碰撞，否则列为未决。
原生反例覆盖穿透、完全包含、不同高度、阈值、仅接触及包络重叠但位于孔洞中的情况，
另注入失败的交线 API，要求产生未决而非通过。
原六组反例之外，附加同一模型间距在阈值上方、恰好达到阈值及阈值下方的三次边界检查，
并核对小幅低于阈值、非盒网格候选和未认证曲面无网格见证时仍需复核的反例。

```bash
python scripts/run_spatial_quality.py --clearance-mm 25  # 需要 Rhino 8
python scripts/check_spatial_quality.py                 # 无需 Rhino，核对真实源身份/单位并生成 HTML
```

报告在 `model/quality/native_spatial.{json,html}`；信息交付的 IDS 校核由 `scripts/check_ids.py` 单独执行。
实现参考：[Rhino MeshClash](https://mcneel.github.io/rhinocommon-api-docs/api/RhinoCommon/html/M_Rhino_Geometry_Intersect_MeshClash_Search_4.htm)、
[IfcTester / IDS](https://docs.ifcopenshell.org/ifctester.html)。
