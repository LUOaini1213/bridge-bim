"""Read-only independent verification of standard IFC forecast crews/calendars."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import ifcopenshell
import ifcopenshell.util.element as UE
import ifcopenshell.util.date as UD

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bridge import construction_input as CI
from bridge.pipeline import compute


def check(model, configuration=None, plan=None):
    cfg=CI.load(configuration)
    plan=plan or compute(configuration=cfg)['construction']
    crews={v.Identification:v for v in model.by_type('IfcCrewResource')}
    expected={f'{deck}:{cls}:{i}' for deck in ('L','R') for cls in CI.CLASSES for i in range(1,cfg['crews'][deck][cls]+1)}
    if len(crews)!=len(model.by_type('IfcCrewResource')) or set(crews)!=expected:
        raise ValueError('IFC crew identities/count differ from construction input')
    calendars=model.by_type('IfcWorkCalendar')
    if len(calendars)!=1:
        raise ValueError('expected one construction work calendar')
    cal=calendars[0]; settings=cfg['calendar']
    props=UE.get_pset(cal,'BridgeBIM_ConstructionCalendar') or {}
    if props.get('ConfigurationSHA256')!=CI.fingerprint(cfg) or json.loads(props.get('CalendarInput','{}'))!=settings:
        raise ValueError('IFC calendar provenance differs')

    def clock(hours):
        return '24:00:00' if hours==24 else (datetime(2000,1,1)+timedelta(hours=hours)).time().isoformat()

    def verify_window(w, recurrence, weekdays=None):
        r=w.RecurrencePattern
        if r is None or r.RecurrenceType!=recurrence or r.Interval!=1:
            raise ValueError('IFC work recurrence differs')
        if any(getattr(r,field) is not None for field in ('DayComponent','MonthComponent','Position','Occurrences')) or recurrence=='DAILY' and r.WeekdayComponent is not None:
            raise ValueError('IFC work recurrence has unexpected limiting components')
        if weekdays is not None and set(r.WeekdayComponent or ())!={d+1 for d in weekdays}:
            raise ValueError('IFC weekday convention/window differs')
        if len(r.TimePeriods or ())!=1:
            raise ValueError('expected one shift time period')
        t=r.TimePeriods[0]
        if (t.StartTime,t.EndTime)!=(clock(settings['start_hour']),clock(settings['start_hour']+settings['day_hours'])):
            raise ValueError('IFC shift clock differs')

    weekly=[w for w in cal.WorkingTimes or () if w.StartDate is None and w.FinishDate is None]
    extra=[w for w in cal.WorkingTimes or () if w.StartDate is not None]
    if len(weekly)!=1 or len(extra)!=len(settings['work_dates']):
        raise ValueError('IFC working week/exception work dates differ')
    verify_window(weekly[0],'WEEKLY',settings['weekdays'])
    if {w.StartDate for w in extra}!=set(settings['work_dates']):
        raise ValueError('IFC additional work dates differ')
    for w in extra:
        if w.StartDate!=w.FinishDate: raise ValueError('additional work date must be bounded to that day')
        verify_window(w,'DAILY')
    rests=cal.ExceptionTimes or ()
    if len(rests)!=len(settings['rest_dates']) or {w.StartDate for w in rests}!=set(settings['rest_dates']):
        raise ValueError('IFC nonworking exception dates differ')
    for w in rests:
        if w.FinishDate!=w.StartDate or w.RecurrencePattern is not None:
            raise ValueError('rest date must be a whole-day exception')
    controls=[o.id() for rel in model.by_type('IfcRelAssignsToControl') if rel.RelatingControl==cal for o in rel.RelatedObjects]
    controlled=set(controls)
    if len(controls)!=len(controlled): raise ValueError('duplicate calendar controls')
    tasks={t.Identification:t for t in model.by_type('IfcTask')}
    assigned={t.id():[] for t in tasks.values()}
    for rel in model.by_type('IfcRelAssignsToProcess'):
        for obj in rel.RelatedObjects:
            if obj.is_a('IfcConstructionResource'):
                if not obj.is_a('IfcCrewResource'): raise ValueError('unexpected crew allocation type')
                assigned[rel.RelatingProcess.id()].append(obj.Identification)
    wanted_controls={crews[v].id() for v in crews}|{tasks[t['id']].id() for t in plan['tasks'] if t['class']!='precast'}
    if controlled!=wanted_controls: raise ValueError('calendar resource/task control set differs')
    for item in plan['tasks']:
        t=tasks[item['id']]
        wanted={item['crew_id']} if item['crew_id'] else set()
        if sorted(assigned[t.id()])!=sorted(wanted): raise ValueError('forecast task/crew allocation differs: '+item['id'])
        if item['class']!='precast' and t.id() not in controlled:
            raise ValueError('work task missing calendar control: '+item['id'])
        p=UE.get_pset(t,'BridgeBIM_ConstructionTask') or {}
        if p.get('CrewStrategy')!=cfg['scheduling']['crew_strategy'] or json.loads(p.get('WaitReasons','null'))!=item['wait_reasons'] or json.loads(p.get('EffectiveWaitReasons','null'))!=item['effective_wait_reasons']:
            raise ValueError('task allocation strategy/wait provenance differs: '+item['id'])
    for ident,crew in crews.items():
        if crew.id() not in controlled: raise ValueError('crew missing calendar control: '+ident)
        used=[t for t in plan['tasks'] if t['crew_id']==ident]
        usage=crew.Usage
        if used:
            if usage is None or usage.ScheduleUsage!=1.0 or usage.IsOverAllocated is not False:
                raise ValueError('forecast crew usage missing')
            if UD.ifc2datetime(usage.ScheduleWork)!=timedelta(hours=sum(t['work_hours'] for t in used)):
                raise ValueError('crew scheduled work differs')
            if (usage.ScheduleStart,usage.ScheduleFinish)!=(min(t['start'] for t in used).isoformat(),max(t['work_finish'] for t in used).isoformat()):
                raise ValueError('crew scheduled extent differs')
        elif usage is not None:
            raise ValueError('unused crew must not invent usage')
        if usage is not None:
            for field in ('ActualWork','ActualUsage','ActualStart','ActualFinish','Completion'):
                if getattr(usage,field) is not None: raise ValueError('fabricated actual crew progress')
    return {'crews':len(crews),'calendars':1,'assigned_tasks':sum(bool(v) for v in assigned.values()),
            'rest_dates':len(rests),'work_dates':len(extra),'crew_strategy':cfg['scheduling']['crew_strategy']}


def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ifc',default=str(ROOT/'model'/'bridge_bim.ifc'))
    parser.add_argument('--construction-config')
    args=parser.parse_args()
    print('PASS IFC construction resources '+json.dumps(check(ifcopenshell.open(args.ifc),args.construction_config),sort_keys=True))


if __name__=='__main__': main()
