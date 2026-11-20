"""No external model, embedding or storage needed. Exercise real OOXML parser and tool boundaries."""
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile
from xml.sax.saxutils import escape

import pytest

from app.tools.errors import ToolInputError
from app.tools.impact_workbook import parse_workbook, assert_workbook_current, load_workbook


def workbook(*, hours='8', predecessor='', owner='FE', external=False, formula=False):
    def sheet(rows):
        content = []
        for r, cells in enumerate(rows,1):
            cs=[]
            for col,value in cells.items():
                if value is None: continue
                if value == '__formula__': cs.append(f'<c r="{col}{r}"><f>8*2</f><v>16</v></c>')
                else: cs.append(f'<c r="{col}{r}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
            content.append(f'<row r="{r}">'+''.join(cs)+'</row>')
        return '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(content)+'</sheetData></worksheet>'
    b=io.BytesIO()
    with ZipFile(b,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="기존 작업" r:id="r1"/><sheet name="사람과 근무일" r:id="r2"/><sheet name="단가표" r:id="r3"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships>'+''.join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1,4)) + ('<Relationship Id="external" Target="https://example.com" TargetMode="External"/>' if external else '') + '</Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',sheet([
            {'A':'일정 계산 시작일','B':'2026-11-19'}, {'A':'현재 계획 완료일'},
            {'A':'작업명','B':'담당자','C':'남은 시간(h)','D':'먼저 끝나야 할 작업'},
            {'A':'기존 화면','B':owner,'C':'__formula__' if formula else hours,'D':predecessor},
            {'A':'기존 작업의 남은 시간 합계(h)','B':'__formula__'}]))
        z.writestr('xl/worksheets/sheet2.xml',sheet([
            {'A':'근무 요일 (다르면 수정)','B':'월,화,수,목,금'},
            {'A':'담당자','B':'하루 투입 가능 시간(h)','C':'투입 가능일'},
            {'A':'FE','B':8}, {'A':'휴무·개인 휴가가 있을 때만 입력'},
            {'A':'대상 (전체 또는 담당자)','B':'날짜','C':'그날 투입 가능 시간(h)'},
            {'A':'전체','B':'2026-11-20','C':0}]))
        z.writestr('xl/worksheets/sheet3.xml', sheet([{'A':'FE','B':'고객 단가','C':999999999}]))
    return b.getvalue()


def test_structure_no_rates_or_invented_dates():
    value=parse_workbook(workbook())
    assert value['tasks'][0]['remaining_hours']==8
    assert value['people'][0]['daily_hours']==8
    assert value['calendar']['planned_end'] is None
    assert value['calendar']['exceptions'][0]['hours']==0
    assert value['missing']
    assert '999999999' not in str(value)


@pytest.mark.parametrize('kwargs',[
    {'hours':''},{'hours':-1},{'hours':'nan'},{'predecessor':'확인 필요'},
    {'predecessor':'기존 화면'}, {'owner':'unknown'}, {'external':True},{'formula':True},
])
def test_bad_inputs_fail_closed(kwargs):
    with pytest.raises(ToolInputError): parse_workbook(workbook(**kwargs))


def test_bad_zip_fails_closed():
    with pytest.raises(ToolInputError): parse_workbook(b'not excel')


def test_changed_or_newly_added_workbook_blocks_review(monkeypatch):
    import app.tools.impact_workbook as m
    out=SimpleNamespace(context=SimpleNamespace(project_id='project'),meta=SimpleNamespace(debug={}), impact_workbook={'source':{'documentVersionId':'v1','sha256':'old'}})
    monkeypatch.setattr(m,'current_source',lambda _: {'documentVersionId':'v2','sha256':'new'})
    with pytest.raises(ToolInputError,match='재산정'): assert_workbook_current(out)
    out.impact_workbook=None
    with pytest.raises(ToolInputError): assert_workbook_current(out)
    out.impact_workbook={'source':{'documentVersionId':'v2','sha256':'new'}}
    assert_workbook_current(out)


def test_load_checks_bytes_and_hash_without_embedding(monkeypatch):
    import app.tools.impact_workbook as m
    import app.indexing.objects as objects
    data=workbook()
    source={'documentId':'d','documentVersionId':'v','version':1,'title':'일정','sha256':hashlib.sha256(data).hexdigest(),'sizeBytes':len(data),'objectKey':'x'}
    monkeypatch.setattr(m,'current_source',lambda _: source)
    storage=Mock(); storage.s3.get_object.side_effect=lambda **_: {'Body':io.BytesIO(data)}
    monkeypatch.setattr(objects,'S3Store',lambda *args: storage)
    value=load_workbook('project')
    assert value['source']['sha256']==source['sha256']
    assert len(value['data']['tasks'])==1
    source['sha256']='0'*64
    with pytest.raises(ToolInputError,match='해시'): load_workbook('project')


def test_synthetic_project_never_queries_backend():
    from app.tools.impact_workbook import current_source
    assert current_source('project-demo') is None


def test_ingest_rejects_workbook_before_background_or_embedder(monkeypatch):
    from uuid import uuid4
    from fastapi import BackgroundTasks, HTTPException
    from app.api import internal_ingest_routes as routes
    from app.indexing.messages import IndexRequest
    monkeypatch.setattr(routes,'_require_token',lambda _:None)
    req=IndexRequest(request_id=uuid4(),project_id=uuid4(),document_version_id=uuid4(),object_key='x',document_type='IMPACT_WORKBOOK')
    background=BackgroundTasks()
    with pytest.raises(HTTPException) as e: routes.request_ingest(req,background,'token')
    assert e.value.status_code==422 and not background.tasks
    report=Mock(); monkeypatch.setattr(routes,'_report',report)
    routes.run_ingest(req)
    report.assert_not_called()
