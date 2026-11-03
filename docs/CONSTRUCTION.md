# 完整示例施工计划、Rhino 时间轴与阶段结果

从 [README](../README.md#梁场与-4d) 移来。命令都在仓库根目录运行。

先打开 `model/bridge_bim.3dm`，在 Rhino 8 Python 编辑器运行
[`rhino/replay_construction.py`](../rhino/replay_construction.py)，弹出 **Eto 非模态面板**，可继续操作 Rhino。
输入日期/小时/分钟、拖动时间轴、前后跳任务节点，设置每帧推进小时并播放/暂停。
输入 BIM 编号或在视口选中构件再查询，可见其施工、养护、完成时刻及已有阶段结果。
关闭面板会停止计时器；再次运行只保留一个面板。

[`bridge/construction.py`](../bridge/construction.py) 是 IFC、Rhino、CSV/JSON 共用的完整示例计划。
默认输入保留原预制、架梁时刻；横隔板 → 湿接缝 → 翼缘现浇（每跨）→ 连续段/养护 → 体系转换（每联）
→ 铺装/养护 → 护栏/养护 → 伸缩装置（相邻两联完成后）有明确完成—开始依赖。
正式输入是 [`construction/input.json`](../construction/input.json)，读取和严格校验入口为 [`bridge/construction_input.py`](../bridge/construction_input.py)。
`durations` 配置各工种的有效施工小时和连续养护小时；`crews.L/R` 配置每幅每工种的实际班组池，默认均为一组。
班组池覆盖 JSON 中八类后续工种；预制按台座资源排，架设仍使用原单台架桥机顺序，增加班组不会增加架桥机台数。
每个任务记录 `crew_id`，班组在施工结束释放，可在养护期间转到下一跨；多班组确实并行，同一班组不重叠。
`scheduling.crew_strategy` 明确班组派工策略：默认 `fixed_route` 按原跨序及班组尾部可用时间派工，保留默认日期；
`earliest_gap` 按既有预留工作区间寻找可放入当前任务的最早空档，可先做已放行的后跨，再做迟批的前跨。
这两种策略都是满足依赖、门禁、日历和资源的确定性可行排程，不承诺全局最优或任意重排后的最早完工。
旧完整 schema 1 配置仅缺 `scheduling` 时自动补 `fixed_route`，规范配置指纹按补齐后的内容计算；其他缺键、未知键或未知策略仍拒绝。
任务的 `crew_strategy`、`wait_reasons` 和 `effective_wait_reasons` 记录策略及班组/日历/前置放行等待原因；缺验收仍单列 `hold_reasons`。
`calendar` 的 `weekdays` 为周一 0 至周日 6，`rest_dates` 为停工日，`work_dates` 为例外工作日，两列表不允许重叠。
`start_hour` 与 `day_hours` 定义每日班次；开浇、张拉移梁、架设、过孔、后续工序均遵守日历，养护与换幅转场间隔按连续时间计。
台座释放后才允许同台座重用，架设保留不可拆分的过孔作业及换幅转场时间。
默认横隔板、湿接缝、翼缘每跨各施工 4 h、养护 48 h；铺装、护栏每联各施工 20 h、养护 72 h；
伸缩装置每个施工 4 h，转换作业 2 h；连续段施工 10 h、养护 168 h。
`baseline` 可覆写预制/架梁起始日、台座数、台座周期、最小龄期、架设/过孔小时、换幅转场日数和连续段浇筑滞后日数。
施工排程以 JSON 为输入权威；修改 `config.py` 中旧施工常量不会覆写 JSON。几何、荷载与既有整联力学案例仍由 `config.py` 定义。
Python API `compute(configuration=完整字典或JSON路径)` 同源加载；旧 `build(..., assumptions={...})` 保留局部覆盖兼容。
**这些工效、班组与养护停留是演示假设，非现场实测，也不代表混凝土强度验收。**
默认全部体系转换顺延至 **2027-02-09 18:00**，后续收尾至 **2027-02-20 16:00**；
[`data/construction_tasks.csv`](../data/construction_tasks.csv) 和 [`data/construction_plan.json`](../data/construction_plan.json)
列出每项任务与依赖。`data/conversions.csv` 与 `summary.conversion_last` 是原算法的转换参考：固定原 1 天滞后、7 天养护，不含新增工序和门禁。
这组日期及原支座龄期作对照，不能作为当前 JSON 的最终转换日期；`construction_plan.reference_baseline` 保存该来源说明，完整小时级转换以 `construction_plan` 为准。

`mode=simulation` 将所有任务明确标为演示预测，门禁为 **synthetic release**，没有伪造的验收编号或批准时间。
`mode=project` 不会仅按等待小时放行：预制完成要求 `fabrication_acceptance`；架设还要求 `erection_strength`、`prestress_grout`；混凝土工序完成要求 `concrete_strength`；
转换还要求 `continuity_strength`、`negative_moment_prestress_grout`、`conversion_authorization`，伸缩装置要求 `installation_acceptance`。
每条 `releases` 记录必须含 `task_id/gate/approved_at/reference/source`，缺引用、未知任务/门禁、重复记录、带时区时间都拒绝。
缺批准或前置未放行的任务输出 **HELD** 及原因，批准不能绕过前置依赖。
`start/work_finish/finish` 是排程预测，`effective_*` 与 `release_at` 是满足所选班组策略、输入门禁与其他约束的可行预测，均不是实测进度。
固定路线可能为迟批前跨等待；插空可利用此前的可用区间，但不会绕过门禁或增加架桥机。
迟批会顺延后续工作、过孔及转场，不会在解锁瞬间把下游都算完成；临时支座仅在转换可行预测完成时拆除。
Rhino 的面板、构件查询、UserText、截图和 IFC 任务 Pset 都显示模式、门禁及来源。IFC 仅写 `Schedule*`，不填造假的 `Actual*` 或 `Completion`。
`source` 记录输入文件依据、作者与引用，放行记录应来自项目负责人的强度报告、张拉/压浆记录及转换放行记录；软件不验证签署人的资格或替代现场审批。

修改方法：先复制完整 `construction/input.json` 为自己的 JSON，再修改所需键（以下片段是补丁示意，不能替代完整文件）：

```json
{"crews": {"L": {"diaphragm": 2}},
 "calendar": {"weekdays": [0,1,2,3,4], "rest_dates": ["2026-12-07"], "work_dates": ["2026-12-06"]},
 "durations": {"pavement": {"work_hours": 30, "cure_hours": 96}}}
```

每次重算、IFC 导出、Rhino 回放须用同一输入路径，也可统一设置环境变量 `BRIDGE_CONSTRUCTION_CONFIG`。
保存快照包含完整规范配置和 SHA256，独立读回按快照的配置重建，不会回到默认 simulation。
原 `.3dm` 几何和 `cast/erect` 属性作为源基线保留，配置计划另存 `construction_*`，回放中源梁场峰值几何隐藏。
改变台座布局后要重建完整源模型；仅修改日历、工效、班组或门禁可直接在原源模型回放。

```powershell
python scripts/build_data.py --construction-config construction/my-plan.json
python scripts/export_ifc.py --construction-config construction/my-plan.json
# 仅需更新完整源模型几何（例如台座数变化）时执行下一行：
python scripts/run_rhino.py --construction-config construction/my-plan.json
python scripts/run_rhino.py --jobs rhino/verification_jobs.json --verify-panel --spatial-quality --construction-config construction/my-plan.json
python scripts/build_data.py --check --construction-config construction/my-plan.json
python scripts/export_ifc.py --check --construction-config construction/my-plan.json
python scripts/check_ids.py
python scripts/check_replay.py --all
python scripts/check_project_gates.py
python scripts/check_spatial_quality.py
```

默认输入对应仓库已保存的数值摘要。自定义输入会改变排程和龄期相关结果，随后需更新 README 并运行 `scripts/check_readme.py`，不能沿用默认日期/数量。
[`tests/test_construction_input.py`](../tests/test_construction_input.py) 覆盖真实双班组并行、日历、迟批机器链、未批准/已批准及非法输入；
[`tests/test_construction_delivery.py`](../tests/test_construction_delivery.py) 核对 IFC 回读门禁、稳定 GUID 与空 Actual 字段。
[`tests/test_crew_strategy.py`](../tests/test_crew_strategy.py) 对比迟批前跨/已放行后跨、区间插空、多班组与休息日；
[`tests/test_readme_profile.py`](../tests/test_readme_profile.py) 证明施工数字来自选定 JSON，拒绝旧台座数量或篡改的任务表。

仓库 README 是默认输入的参考说明，原转换参考表仍按原算法，不会用自定义 profile 的日期替换整联结构案例。
`python scripts/check_readme.py` 核对全部默认 README 数字，台座数和施工工效读取实际选定配置，默认文字不能冒充自定义配置的结果。
自定义 profile 使用只读 `python scripts/check_readme.py --construction-config construction/my_project.json --profile-summary`：
独立重建并核对当前 `data/construction_plan.json`、全部任务 CSV、预制/架梁表与基线摘要，打印模式、策略、台座数、日历、HELD 数及预测完成时刻。
这项检查不要求重写默认 README 的几何/整联结果与图片；IFC、原生回放和质量检查由交付流程分别核对。
需要核对另存的完整 README/产物时，可传 `--readme`、`--data-dir`、`--ifc`，没有隐式默认输入回退。
源模型的原始 `cast/erect/age` 与既有结构属性属于来源基线，由可移植的 `model/source_config.json` 记录其输入及源文件 SHA。
原生全源构建会更新该记录；当前已独立验证的原源保留原字节，仅初始化来源说明。
`python scripts/check_source.py` 在写入任何新产物前核对真实源 `.3dm` 的单位、唯一构件 ID、来源属性、几何位置/尺寸、闭合网格顶点及面方向、解析圆柱/方盒和台座布局。
新日历、工效、门禁或班组策略直接复用原源，更新的实际配置由回放 `construction_*` 字段记录，原日期仍为参考。
台座数或几何不匹配时明确要求交付流程的 `--regenerate-source`；源与回放相互一致不能替代这项独立几何核验。
[`tests/test_source_profile.py`](../tests/test_source_profile.py) 包含平移、单位、篡改属性、同包围盒圆柱换方盒、闭合网格反面及来源指纹反例。
`--verify-panel` 还生成两份带 **测试专用批准** 的原生 project 快照在 `model/project_replay/`，用于证明批准前不可见、批准后仅首梁架设而转换仍阻断。

面板可选择 30 条梁位线及 **M1（一期恒载）/ Mc（体系转换增量）/ M2（二期恒载增量）/ MG（恒载合计）**。
紫色弯矩曲线、带符号反力箭头和有效/无效支承标记生成在“施工阶段结果”层；三维用短标号，截图顶部表格列完整ID及数值。
弯矩图比例自适应，反力箭头长度仅示意，精确值以表格为准。Mc 的临时支座拆除作用单独标注，
不冒充剩余支座反力。数据来自原 `bridge.structure.analyse`，保存在 [`data/stage_results.json`](../data/stage_results.json)，
可独立读回核对原生曲线坐标、支座状态与反力。
**这是既有整联载荷工况的查看器；Mc/M2 为增量，日期回放不重新求解部分安装结构。**
原工程结果与原基线支座龄期保持不变；新增示例日历不会自动替换工程分析的施工龄期。
未舍入的阶段 JSON 单独按类型/字段/顺序严格互核。各条 M1/Mc/M2/MG 弯矩曲线沿用 OpenSees 互核的向量门槛：逐点差不超过 `max(2e-9, 1e-9 × max(1, 两份该曲线的最大绝对弯矩))`，覆盖刚度恢复内力时的消去误差；坐标、反力及其他浮点仍按逐值相对门槛 1e-9、绝对下限 2e-9 比对。`build_data.py --check` 输出全部浮点差异的分工况最大绝对误差、门槛比和所有超限位置，供跨平台 CI 核验。
原工程表仍保持原固定小数位的门槛，ID、整数与布尔值没有浮点容差。

也可让本机 Rhino 自动导出指定日期；原完整模型和已发布的结构计算结果不会被覆盖：

```powershell
python scripts/run_rhino.py --replay 2026-12-21
python scripts/check_replay.py model/replay/bridge_2026-12-21.3dm
python scripts/run_rhino.py --replay 2027-02-20T16:00 --stage Mc --line L-U2-G3 --verify-panel
python scripts/check_replay.py model/replay/bridge_2027-02-20_1600_Mc_L-U2-G3.3dm
python scripts/run_rhino.py --dates 2026-12-06 2026-12-21 2027-02-02
python scripts/run_rhino.py --jobs rhino/verification_jobs.json --verify-panel
```

产物位于 `model/replay/`：带日期/时刻/阶段的 `.3dm`、原生视口 PNG 和逐构件状态 JSON；
`--output-dir` 可指定其他目录。独立读回检查核对实际对象显隐、原编号与 BIM 属性、几何包围盒及排程计数。
[`bridge/replay.py`](../bridge/replay.py) 的日期状态计算只用标准库，边界测试纳入 CI。

日期单独输入采用日末语义；带时刻输入可区别安装中、施工中、养护、已完成。
完整几何在任务开始时出现，未模拟吊装运动、混凝土体积渐增。临时支座按新转换任务的实际结束时刻拆除。
下部结构、垫石和永久支座为灰色静态参照，不能据此推断它们在该日期已完成。
梁场峰值快照隐藏，当前在制与存梁数按原排程时刻统计。首次生成阶段数据请执行 `python scripts/build_data.py`。
