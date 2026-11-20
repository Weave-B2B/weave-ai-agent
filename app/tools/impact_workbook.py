"""Agent 3 전용 XLSX 구조화 입력. RAG·청크·임베딩을 호출하지 않는다.

백엔드가 승인한 버전과 SHA-256을 고정한다. 단가표는 참고자료이며
LLM 입력과 공식 금액 계산에는 전달하지 않는다. 완료일 계산기는 별도 후속 작업이다.
"""
from __future__ import annotations

import hashlib
import io
import math
import posixpath
import re
from datetime import date, timedelta
from uuid import UUID
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

import httpx

from app.tools.backend_calc import BackendCalcError
from app.tools.errors import ToolInputError

MAX_BYTES = 10_485_760
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _xml(data):
    if len(data) > 4_194_304 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ToolInputError('허용되지 않는 엑셀 XML입니다.')
    return ET.fromstring(data)


def _sheets(data):
    if len(data) > MAX_BYTES:
        raise ToolInputError('일정·공수 XLSX는 10MB 이하여야 합니다.')
    with ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > 1000 or sum(i.file_size for i in infos) > 26_214_400:
            raise ToolInputError('엑셀 압축 해제 크기 제한을 초과했습니다.')
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or any(n.startswith('/') or '..' in n.split('/') or 'vbaproject' in n.lower() for n in names):
            raise ToolInputError('허용되지 않는 엑셀 파일 구성입니다.')
        for n in names:
            if n.endswith('.rels') and any(r.get('TargetMode') == 'External' for r in _xml(z.read(n))):
                raise ToolInputError('외부 연결이 있는 엑셀은 사용할 수 없습니다.')
        strings = []
        if 'xl/sharedStrings.xml' in names:
            strings = [''.join(e.itertext()) for e in _xml(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        book = _xml(z.read('xl/workbook.xml'))
        props = book.find('s:workbookPr', NS)
        if props is not None and props.get('date1904') in ('1', 'true'):
            raise ToolInputError('1904 날짜 체계는 지원하지 않습니다. 표준 XLSX로 저장해주세요.')
        rels = {r.get('Id'): r.get('Target') for r in _xml(z.read('xl/_rels/workbook.xml.rels'))}
        result = {}
        for sheet in book.findall('s:sheets/s:sheet', NS):
            target = rels[sheet.get(f'{{{REL}}}id')]
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            rows = []
            for row in _xml(z.read(path)).findall('s:sheetData/s:row', NS):
                cells = {}
                for c in row.findall('s:c', NS):
                    col = re.sub(r'\d', '', c.get('r', ''))
                    v = c.find('s:v', NS)
                    raw = v.text if v is not None else ''
                    if c.get('t') == 's': raw = strings[int(raw)]
                    elif c.get('t') == 'inlineStr': raw = ''.join(c.find('s:is', NS).itertext())
                    cells[col] = (raw or '', c.find('s:f', NS) is not None)
                rows.append((int(row.get('r')), cells))
            result[sheet.get('name')] = rows
        return result


def _value(cells, col):
    value, formula = cells.get(col, ('', False))
    if formula:
        raise ToolInputError(f'{col}열 입력에 수식이 있습니다. 값으로 입력해주세요.')
    return str(value).strip()


def _number(value, label, max_value=None):
    try: n = float(value)
    except (ValueError, TypeError): raise ToolInputError(f'{label}: 숫자를 입력해주세요.') from None
    if not math.isfinite(n) or n < 0 or (max_value is not None and n > max_value):
        raise ToolInputError(f'{label}: 허용 범위 밖의 값입니다.')
    return n


def _date(value, label, required=False):
    if not value:
        if required: raise ToolInputError(f'{label}을 입력해주세요.')
        return None
    try:
        if re.fullmatch(r'\d+(\.\d+)?', value):
            n = float(value)
            if n != int(n): raise ValueError()
            return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
        return date.fromisoformat(value).isoformat()
    except (ValueError, OverflowError): raise ToolInputError(f'{label}: 날짜 형식이 올바르지 않습니다.') from None


def parse_workbook(data: bytes) -> dict:
    """공유한 간소화 양식만 지원. 모르는 양식을 빈 자료로 오인하지 않는다."""
    try: sheets = _sheets(data)
    except ToolInputError: raise
    except (BadZipFile, ET.ParseError, KeyError, ValueError, IndexError, TypeError, AttributeError) as e:
        raise ToolInputError('XLSX 내용을 읽을 수 없습니다. 일정·공수 양식을 확인해주세요.') from e
    if not {'기존 작업', '사람과 근무일'} <= sheets.keys():
        raise ToolInputError('기존 작업 / 사람과 근무일 시트가 필요합니다.')
    tasks, people, exceptions = [], [], []
    start, planned_end, weekdays = None, None, None
    mode = None
    for row, cells in sheets['기존 작업']:
        first = _value(cells, 'A')
        if first == '작업명':
            if not all(word in _value(cells, col) for word, col in [('담당자','B'),('남은','C'),('먼저','D')]):
                raise ToolInputError('기존 작업 시트의 열 구성이 다릅니다.')
            mode = 'tasks'; continue
        if mode == 'tasks':
            if first == '기존 작업의 남은 시간 합계(h)': break
            if not first:
                if any(_value(cells, c) for c in ['B','C','D']): raise ToolInputError(f'작업 {row}행: 작업명이 없습니다.')
                continue
            owner = _value(cells, 'B')
            if not owner: raise ToolInputError(f'작업 {row}행: 담당자가 없습니다.')
            tasks.append({'name': first, 'owner': owner, 'remaining_hours': _number(_value(cells,'C'), f'작업 {row}행 남은 시간'),
                          'predecessors': [x.strip() for x in re.split(r'[;\n]', _value(cells,'D')) if x.strip()]})
        else:
            key = re.sub(r'\s+', '', first)
            if '시작일' in key: start = _date(_value(cells,'B'), '일정 계산 시작일', True)
            elif '현재계획완료일' in key: planned_end = _date(_value(cells,'B'), '현재 계획 완료일')
    mode = None
    for row, cells in sheets['사람과 근무일']:
        first = _value(cells, 'A')
        if first == '담당자':
            if '하루' not in _value(cells,'B'): raise ToolInputError('인력 시트의 가용시간 열이 없습니다.')
            mode = 'people'; continue
        if first.startswith('대상'):
            mode = 'exceptions'; continue
        if first.startswith('휴무·개인 휴가'):
            mode = None; continue
        if re.sub(r'\s+', '', first).startswith('근무요일'): weekdays = _value(cells,'B'); continue
        if not first:
            if mode and any(_value(cells,c) for c in ['B','C','D']): raise ToolInputError(f'인력 {row}행: 대상이 없습니다.')
            continue
        if mode == 'people':
            if not re.fullmatch(r'\d+(\.\d+)?', _value(cells,'B')):
                raise ToolInputError(f'인력 {row}행: 하루 투입 가능 시간을 입력해주세요.')
            people.append({'name': first, 'daily_hours': _number(_value(cells,'B'), f'인력 {row}행',24),
                           'available_from': _date(_value(cells,'C'),'투입 가능일') or start})
        elif mode == 'exceptions':
            if not _value(cells,'B'): break  # 하단 양식 안내문
            exceptions.append({'person': first, 'date': _date(_value(cells,'B'),'예외 근무일',True),
                               'hours': _number(_value(cells,'C'),'예외 근무시간',24)})
    if not tasks or not people or not start or not weekdays:
        raise ToolInputError('작업·담당자·시작일·근무요일은 필수입니다.')
    if len(tasks) > 200 or len(people) > 200 or len(exceptions) > 1000:
        raise ToolInputError('작업·인력은 각각 200행, 예외 근무일은 1000행까지 지원합니다.')
    if not set(re.split(r'[,\s]+', weekdays)) <= {'월','화','수','목','금','토','일'}:
        raise ToolInputError('근무요일은 월,화,수,목,금 형태로 입력해주세요.')
    names = {t['name'] for t in tasks}; owners = {p['name'] for p in people}
    if len(names) != len(tasks) or len(owners) != len(people): raise ToolInputError('작업명과 담당자는 중복될 수 없습니다.')
    for task in tasks:
        if task['owner'] not in owners or not set(task['predecessors']) <= names:
            raise ToolInputError(f"{task['name']}: 담당자 또는 선행 작업을 확인해주세요.")
    seen, active = set(), set()
    graph = {t['name']: t['predecessors'] for t in tasks}
    def visit(name):
        if name in active: raise ToolInputError('선행 작업이 순환합니다.')
        if name in seen: return
        active.add(name)
        for p in graph[name]: visit(p)
        active.remove(name); seen.add(name)
    for name in names: visit(name)
    if any(e['person'] not in owners | {'전체'} for e in exceptions): raise ToolInputError('예외 근무일의 담당자를 확인해주세요.')
    if len({(e['person'],e['date']) for e in exceptions}) != len(exceptions): raise ToolInputError('예외 근무일이 중복되었습니다.')
    return {'tasks': tasks, 'people': people, 'calendar': {'start_date': start, 'planned_end': planned_end,
            'working_weekdays': weekdays, 'exceptions': exceptions},
            'missing': [] if planned_end else ['현재 계획 완료일 미입력: 지연 폭 미산정'],
            'note': '업로드된 운영 자료. 계약 근거 아님. 단가표 시트는 참고용이며 공식 백엔드 단가표를 사용. 완료일 계산은 아직 미연결.'}


def current_source(project_id: str):
    try: UUID(project_id)
    except ValueError: return None  # 합성 프로젝트는 외부 조회하지 않음
    from app.core.config import settings
    from app.indexing.backend import TOKEN_HEADER
    if not settings.backend_base_url or not settings.backend_internal_token:
        raise BackendCalcError('산정 자료 조회에는 백엔드 연결 설정이 필요합니다.')
    try:
        with httpx.Client(base_url=settings.backend_base_url, timeout=settings.backend_timeout_sec) as http:
            r = http.get(f'/internal/v1/projects/{project_id}/impact-workbook', headers={TOKEN_HEADER: settings.backend_internal_token})
            r.raise_for_status(); body = r.json()
        source = body['workbook']
        if source is not None:
            if source.get('projectId') != project_id or source.get('mimeType') != MIME or not 0 < source.get('sizeBytes',0) <= MAX_BYTES:
                raise ValueError('source identity')
            UUID(source['documentVersionId'])
            if not re.fullmatch(r'[0-9a-f]{64}', source['sha256']): raise ValueError('source hash')
            if not source.get('objectKey'): raise ValueError('source object')
        return source
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise BackendCalcError('승인된 일정·공수 자료를 확인하지 못했습니다.') from e


def load_workbook(project_id: str):
    source = current_source(project_id)
    if source is None: return None
    from app.core.config import settings
    from app.indexing.objects import S3Store
    try:
        store = S3Store(settings.s3_endpoint or None, settings.s3_region, settings.s3_access_key_id or None, settings.s3_secret_access_key or None)
        body = store.s3.get_object(Bucket=settings.s3_source_bucket, Key=source['objectKey'])
        stream = body['Body']
        try: data = stream.read(MAX_BYTES + 1)
        finally: stream.close()
    except Exception as e:
        raise BackendCalcError('일정·공수 원본 파일을 읽지 못했습니다.') from e
    if len(data) != source['sizeBytes'] or hashlib.sha256(data).hexdigest() != source['sha256']:
        raise ToolInputError('산정 자료의 파일 크기·해시가 등록 정보와 다릅니다. 다시 업로드해주세요.')
    return {'source': {k: source[k] for k in ('documentId','documentVersionId','version','sha256','title')}, 'data': parse_workbook(data)}


def assert_workbook_current(output):
    project_id = output.context.project_id
    source = current_source(project_id)
    reference = getattr(output,'impact_workbook',None) or (output.meta.debug or {}).get('impact_workbook')
    expected = (reference or {}).get('source') or {}
    if (source or {}).get('documentVersionId') != expected.get('documentVersionId') or (source or {}).get('sha256') != expected.get('sha256'):
        raise ToolInputError('일정·공수 자료가 변경되었습니다. 재산정 후 검토해주세요.')
