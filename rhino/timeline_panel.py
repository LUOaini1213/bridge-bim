"""Nonmodal Eto time/stage controls. UI timer and callbacks stay on UI thread."""
from datetime import timedelta
import Eto.Forms as EF
import Eto.Drawing as ED
import Rhino
import System
import scriptcontext as sc
from bridge import replay as R, construction as CP, stage_results as SR

KEY = "bridge_bim_timeline_panel"


def control(kind, **properties):
    # CPython/pythonnet does not support IronPython's property kwargs in
    # Eto constructors. Construct first, then assign typed properties.
    item = kind()
    for key, value in properties.items():
        if key == "DataStore":
            value = System.Array[System.String](value)
        if key == "Wrap" and kind == EF.Label:
            value = EF.WrapMode.Word
        setattr(item, key, value)
    return item


class Timeline(EF.Form):
    def __init__(self, doc, result, initial, apply, show_stage, export, query):
        super().__init__()
        self.Title = "桥梁 BIM · 完整示例施工时间轴 / 既有阶段结果"
        self.ClientSize = ED.Size(650, 680)
        self.Padding = ED.Padding(12)
        self.doc, self.result = doc, result
        self.document_serial = doc.RuntimeSerialNumber
        self.apply_callback, self.stage_callback = apply, show_stage
        self.export_callback, self.query_callback = export, query
        self.current = R.parse_moment(initial)
        self.start = CP.at(min(row["cast"] for row in result["rows"]))
        self.finish = result["construction"]["finish"]
        self.events = sorted({t[k] for t in result["construction"]["tasks"] for k in ("start", "work_finish", "finish")})
        self.updating = False
        self.syncing_widgets = False
        self.pending = None
        self.busy_rejections = 0
        self.is_closed = False
        self.stage_index, self.line_index = 0, 7
        self.date = control(EF.TextBox, Text=self.current.date().isoformat(), Width=130)
        self.hour = control(EF.NumericStepper, MinValue=0, MaxValue=23, DecimalPlaces=0, Value=self.current.hour, Width=70)
        self.minute = control(EF.NumericStepper, MinValue=0, MaxValue=59, DecimalPlaces=0, Value=self.current.minute, Width=70)
        self.speed = control(EF.NumericStepper, MinValue=1, MaxValue=168, DecimalPlaces=0, Value=12, Width=80)
        self.slider = control(EF.Slider, MinValue=0, MaxValue=int((self.finish - self.start).total_seconds() / 3600), Width=590)
        self.slider.ValueChanged += self.slide
        self.play = control(EF.Button, Text="播放")
        self.play.Click += self.toggle
        self.timer = control(EF.UITimer, Interval=0.6)
        self.timer.Elapsed += self.tick
        self.stage = control(EF.DropDown, DataStore=["施工日期状态", "M1", "Mc", "M2", "MG"], SelectedIndex=0, Width=140)
        self.line = control(EF.DropDown, DataStore=[x["id"] for x in result["stage_data"]["lines"]], SelectedIndex=7, Width=150)
        self.stage.SelectedIndexChanged += self.change_stage
        self.line.SelectedIndexChanged += self.change_stage
        self.eid = control(EF.TextBox, Text="G-L05-3", Width=180)
        self.details = control(EF.TextArea, ReadOnly=True, Height=160, Wrap=True)
        self.status = control(EF.TextArea, ReadOnly=True, Height=100, Wrap=True)
        layout = control(EF.DynamicLayout, Spacing=ED.Size(6, 8))
        layout.AddRow(control(EF.Label, Text="项目当地日期"), self.date, control(EF.Label, Text="时"), self.hour, control(EF.Label, Text="分"), self.minute, self.button("应用", self.enter))
        layout.AddRow(self.slider)
        layout.AddRow(self.button("上个任务节点", lambda s, e: self.adjacent(-1)), self.play,
                      self.button("下个任务节点", lambda s, e: self.adjacent(1)), control(EF.Label, Text="小时 / 帧"), self.speed)
        layout.AddRow(control(EF.Label, Text="既有整联载荷工况"), self.stage, control(EF.Label, Text="梁位线"), self.line)
        layout.AddRow(control(EF.Label, Text="构件编号"), self.eid, self.button("查询编号", self.lookup), self.button("查询所选", self.selected))
        layout.AddRow(self.status)
        layout.AddRow(self.details)
        layout.AddRow(control(EF.Label, Text="紫色曲线：弯矩（正值向下）；绿/灰：工况有效/无效支承。\nMc、M2 为增量；切换工况不会重新计算日历当天的部分施工体系。", Wrap=True))
        layout.AddRow(control(EF.Label, Text=CP.NOTICE, Wrap=True))
        layout.AddRow(self.button("导出当前 .3dm / PNG / JSON", self.save), self.button("关闭面板", lambda s, e: self.Close()))
        layout.Add(None)
        self.Content = layout
        self.Closed += self.closed
        self.update(self.current)
        Rhino.RhinoDoc.CloseDocument += self.document_closed

    def button(self, text, action):
        button = control(EF.Button, Text=text)
        button.Click += action
        return button

    def safe(self, action):
        if self.is_closed:
            return
        try:
            action()
        except Exception as error:
            # A redraw/export can pump CloseDocument while action is running.
            # Closed has already disposed the timer in that case.
            if not self.is_closed:
                self.timer.Stop()
                self.play.Text = "播放"
                self.status.Text = str(error)
            Rhino.RhinoApp.WriteLine(str(error))

    def active_document(self):
        if self.is_closed:
            raise RuntimeError("时间轴面板已关闭；请在当前桥梁文档重新运行脚本")
        live = Rhino.RhinoDoc.FromRuntimeSerialNumber(self.document_serial)
        active = Rhino.RhinoDoc.ActiveDoc
        if live is None or active is None or active.RuntimeSerialNumber != self.document_serial:
            raise RuntimeError("请先切回此面板的桥梁文档；文档关闭后请重新运行时间轴脚本")
        return live

    def update(self, moment):
        # Native redraw/capture can pump Eto messages. Cover the full apply
        # and widget synchronization, retaining only the last user request.
        if self.is_closed:
            return
        if self.updating:
            self.pending = moment
            return
        self.updating = True
        try:
            while True:
                self.pending = None
                self.active_document()
                requested = min(self.finish, max(self.start, moment))
                state = self.apply_callback(requested)
                self.active_document()
                if R.parse_moment(state["datetime"]) != requested:
                    raise RuntimeError("施工状态与请求时刻不一致，更新已停止")
                self.current, self.state = requested, state
                if self.is_closed:
                    break
                self.syncing_widgets = True
                self.date.Text = self.current.date().isoformat()
                self.hour.Value, self.minute.Value = self.current.hour, self.current.minute
                self.slider.Value = int((self.current - self.start).total_seconds() / 3600)
                self.syncing_widgets = False
                c = self.state["counts"]
                self.status.Text = "%s\n已架 %d/%d（安装中 %d）；转换 %d/%d；临时支座 %d\n后续完成 %d/%d 构件；任务 %d/%d；示例收尾 %s" % (
                    self.current.isoformat(" "), c["girders_erected"], c["girders_total"], c["girders_installing"],
                    c["units_converted"], c["units_total"], c["temporary_supports_active"], c["follow_on_completed"],
                    c["follow_on_total"], c["tasks_completed"], c["tasks_total"], self.finish.isoformat(" "))
                self.lookup(None, None)
                if self.pending is None:
                    break
                moment = self.pending
        finally:
            self.updating = False
            self.syncing_widgets = False
            self.pending = None

    def guarded_action(self, action):
        if self.is_closed:
            return False
        if self.updating:
            self.busy_rejections += 1
            Rhino.RhinoApp.WriteLine("时间轴正在更新；本次保存/工况切换未执行，请更新完成后重试。")
            return False
        self.active_document()
        self.updating = True
        try:
            action()
        finally:
            self.updating = False
            pending, self.pending = self.pending, None
            if pending is not None:
                self.update(pending)
        return True

    def enter(self, sender, event):
        self.safe(lambda: self.update(R.parse_moment("%s %02d:%02d" % (self.date.Text, self.hour.Value, self.minute.Value))))

    def slide(self, sender, event):
        if not self.syncing_widgets:
            self.safe(lambda: self.update(self.start + timedelta(hours=self.slider.Value)))

    def toggle(self, sender, event):
        if self.is_closed:
            return
        if self.play.Text == "播放":
            self.timer.Start()
            self.play.Text = "暂停"
        else:
            self.timer.Stop()
            self.play.Text = "播放"

    def tick(self, sender, event):
        if self.updating or self.is_closed:
            return
        self.safe(lambda: self.update(self.current + timedelta(hours=float(self.speed.Value))))
        if self.current >= self.finish:
            self.timer.Stop()
            self.play.Text = "播放"

    def adjacent(self, direction):
        candidates = [t for t in self.events if (t - self.current).total_seconds() * direction > 0]
        if candidates:
            self.safe(lambda: self.update(candidates[0] if direction == 1 else candidates[-1]))

    def change_stage(self, sender, event):
        if not hasattr(self, "status") or self.syncing_widgets:
            return
        if self.updating:
            self.busy_rejections += 1
            self.syncing_widgets = True
            try:
                self.stage.SelectedIndex, self.line.SelectedIndex = self.stage_index, self.line_index
            finally:
                self.syncing_widgets = False
            Rhino.RhinoApp.WriteLine("时间轴正在更新，工况切换未执行。")
            return
        def change():
            self.stage_callback(None if self.stage.SelectedIndex == 0 else str(self.stage.SelectedValue), str(self.line.SelectedValue))
            self.stage_index, self.line_index = self.stage.SelectedIndex, self.line.SelectedIndex
        self.safe(lambda: self.guarded_action(change))

    def lookup(self, sender, event):
        if self.is_closed:
            return
        try:
            self.active_document()
        except RuntimeError as error:
            self.details.Text = str(error)
            return
        self.details.Text = self.query_callback(self.eid.Text.strip(), self.state)

    def selected(self, sender, event):
        if self.is_closed:
            return
        try:
            live = self.active_document()
        except RuntimeError as error:
            self.details.Text = str(error)
            return
        selected = live.Objects.GetSelectedObjects(False, False)
        eids = [obj.Attributes.GetUserString("eid") or obj.Attributes.GetUserString("result_element") for obj in selected]
        if not any(eids):
            self.details.Text = "请在 Rhino 中选中一个 BIM 构件，然后点击查询所选。"
            return
        self.eid.Text = next(eid for eid in eids if eid)
        self.lookup(sender, event)

    def save(self, sender, event):
        self.safe(lambda: self.guarded_action(lambda: self.export_callback(self.state)))

    def document_closed(self, sender, event):
        if event.DocumentSerialNumber == self.document_serial:
            self.Close()

    def closed(self, sender, event):
        if self.is_closed:
            return
        self.is_closed = True
        self.pending = None
        self.timer.Stop()
        self.timer.Dispose()
        self.play.Text = "播放"
        Rhino.RhinoDoc.CloseDocument -= self.document_closed
        if sc.sticky.get(KEY) is self:
            sc.sticky.pop(KEY, None)


def show(doc, result, initial, apply, show_stage, export, query):
    old = sc.sticky.get(KEY)
    if old and not old.is_closed:
        old.Close()
    panel = Timeline(doc, result, initial, apply, show_stage, export, query)
    sc.sticky[KEY] = panel
    panel.Owner = Rhino.UI.RhinoEtoApp.MainWindowForDocument(doc)
    panel.Show()
    return panel
