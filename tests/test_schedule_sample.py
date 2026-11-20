"""Validate the sample oracle's workload, dependencies and capacity before adding a production scheduler."""
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SAMPLE=json.loads((Path(__file__).resolve().parents[1]/'data/scenarios/schedule-v1-sample.json').read_text())


def test_sample_schedule_has_feasible_allocations_and_explicit_assumptions():
    s=SAMPLE
    assert s['synthetic'] and s['rules']['client_requested_deadline'] is None
    people={p['id']:p for p in s['people']}
    base={t['id']:t for t in s['baseline_tasks']}
    changed={**base,**{t['id']:t for t in s['change']['added_tasks']+s['change']['updated_tasks']}}
    for tasks,allocations,end in [(base,s['expected']['baseline_allocations'],s['expected']['baseline_end']),
                                   (changed,s['expected']['changed_allocations'],s['expected']['changed_end'])]:
        workload=defaultdict(int); capacity=defaultdict(int); starts={}; finishes={}
        for row in allocations:
            task=tasks[row['task_id']]; person=people[task['person_id']]; day=row['date']
            assert date.fromisoformat(day).isoweekday() in s['calendar']['workdays']
            assert day not in s['calendar']['holidays'] and day>=person['available_from']
            workload[task['id']]+=row['hours']; capacity[person['id'],day]+=row['hours']
            assert capacity[person['id'],day]<=person['daily_available_hours']
            starts[task['id']]=min(starts.get(task['id'],day),day)
            finishes[task['id']]=max(finishes.get(task['id'],day),day)
        for key,task in tasks.items():
            assert workload[key]==task['remaining_hours']
            assert all(starts[key]>finishes[p] for p in task['predecessors'])
        assert max(finishes.values())==end
    extra=sum(t['remaining_hours'] for t in changed.values())-sum(t['remaining_hours'] for t in base.values())
    assert extra==s['change']['incremental_hours']==s['change']['incremental_md']*s['hours_per_md']
    # 샘플 기대값 검증만 수행한다. 운영 계산은 Backend에 있다.
    begin, end = map(date.fromisoformat, (s['expected']['baseline_end'], s['expected']['changed_end']))
    days = [begin + timedelta(days=n) for n in range(1, (end-begin).days+1)]
    assert sum(d.isoweekday() in s['calendar']['workdays'] and d.isoformat() not in s['calendar']['holidays']
               for d in days) == s['expected']['impact_business_days']
