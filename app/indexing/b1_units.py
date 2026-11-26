"""Structural splitting primitives from the R2 benchmark."""
import re

from .path_extractor import ENC

def ntok(text):
    return len(ENC.encode(text, disallowed_special=()))

def page_of(doc, off):
    for i, (s, e) in enumerate(doc['bounds']):
        if off < e + 2: return i + 1
    return len(doc['bounds'])

def span_pages(doc, s, e):
    return page_of(doc, s), page_of(doc, max(s, e - 1))

def lines_off(text, base=0):
    out, pos = [], 0
    for l in text.split('\n'):
        out.append((base + pos, base + pos + len(l), l)); pos += len(l) + 1
    return out

def trim(text, s, e):
    while s < e and text[s].isspace(): s += 1
    while e > s and text[e - 1].isspace(): e -= 1
    return s, e

def mk(doc, s, e):
    s, e = trim(doc['text'], s, e)
    return dict(start=s, end=e, text=doc['text'][s:e])

# ============================================================ 구조 패턴
ARTICLE = re.compile(r'^\s*(?:[-*]\s+)?(?:#+\s*)?(?:\*\*)?\s*제\s*\d+\s*조(?:\s*의\s*\d+)?(?=[\s(（*:.]|$)')
ANNEX = re.compile(r'^\s*(?:#+\s*)?(?:\*\*)?\s*(?:\[\s*)?(?:별첨|부록|붙임|별지|별표)')
HEADING = re.compile(r'^\s*#+\s+\S')
ITEM = re.compile(r'^\s*(?:[-*]\s+)?(?:[①-⑳]|\d{1,2}\.(?!\d)|[가-하]\.|\(\d{1,2}\)|\d{1,2}\))\s*\S')
TABLE = re.compile(r'^\s*(\||<table)')
TSEP = re.compile(r'^\s*\|[\s:|-]+\|?\s*$')

SEPS = [r'\n\s*\n', r'\n', r'(?<=다\.)\s+|(?<=요\.)\s+|(?<=[.?!])\s+', r' ']

def split_R(text, s, e, L, level=0):
    s, e = trim(text, s, e)
    if s >= e: return []
    if ntok(text[s:e]) <= L: return [(s, e)]
    if level >= len(SEPS):
        toks = ENC.encode(text[s:e], disallowed_special=()); _, offs = ENC.decode_with_offsets(toks)
        offs = list(offs) + [e - s]; out, i = [], 0
        while i < len(toks):
            j = min(i + L, len(toks))
            while j > i + 1 and ntok(text[s + offs[i]: s + offs[j]]) > L: j -= 1
            out.append((s + offs[i], s + offs[j])); i = j
        return out
    cuts = [m.end() for m in re.finditer(SEPS[level], text[s:e])]
    pieces, prev = [], s
    for c in cuts: pieces.append((prev, s + c)); prev = s + c
    pieces.append((prev, e))
    groups, cur = [], None
    for ps, pe in pieces:
        if cur is None: cur = (ps, pe)
        elif ntok(text[cur[0]:pe].strip()) <= L: cur = (cur[0], pe)
        else: groups.append(cur); cur = (ps, pe)
    if cur: groups.append(cur)
    res = []
    for ps, pe in groups:
        if ntok(text[ps:pe].strip()) <= L: res.append(trim(text, ps, pe))
        else: res.extend(split_R(text, ps, pe, L, level + 1))
    return [r for r in res if r[0] < r[1]]

def chunk_R(doc, L, s=0, e=None):
    return [mk(doc, a, b) for a, b in split_R(doc['text'], s, len(doc['text']) if e is None else e, L)]

def table_groups(doc, b, L):
    rows = b['rows']
    nh = 2 if len(rows) > 1 and TSEP.match(rows[1][2]) else 1
    header, body = rows[:nh], rows[nh:]
    htxt = '\n'.join(r[2] for r in header)
    if not body: return [mk(doc, b['start'], b['end'])]
    out, cur = [], []
    def flush():
        if cur: out.append(dict(start=cur[0][0], end=cur[-1][1], text=(htxt + '\n' + '\n'.join(r[2] for r in cur)).strip(), header_repeated=True))
    for r in body:
        if cur and ntok(htxt + '\n' + '\n'.join(x[2] for x in cur + [r])) > L: flush(); cur = []
        if ntok(htxt + '\n' + r[2]) > L:
            flush(); cur = []
            out.extend(mk(doc, a, z) for a, z in split_R(doc['text'], r[0], r[1], L)); continue
        cur.append(r)
    flush()
    if out and out[0]['start'] == body[0][0]:  # 첫 묶음은 실제 머리글 위치부터
        out[0]['start'] = b['start']
    return out

# 표 행 안의 항목 식별자: [A-F01][IN] 회원 이메일 로그인: … → 'A-F01 회원 이메일 로그인'
ROW_KEY = re.compile(r'\[([A-Z]{1,4}-[A-Z]{0,3}\d{1,4})\](?:\[[A-Za-z]{2,4}\])?\s*([^:：|\[\]\n]{1,40}?)\s*[:：]')


def row_key(line):
    m = ROW_KEY.search(line)
    return f"{m.group(1)} {m.group(2).strip()}" if m else None


# 첫 칸이 식별자인 표: | REQ-001 | 요구사항명 | … | (FR-1.2, SR-01, 기능-01 등)
ID_CELL = re.compile(r'^[A-Za-z가-힣]{1,6}(?:[-_][A-Za-z가-힣]{1,6})?[-_]?\d{1,4}(?:[.-]\d{1,3})*$')  # REQ-001, A-F01, FR-1.2


def _cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _id(cell):
    c = re.sub(r'\s+', '', cell)                       # docling이 'A- F02'처럼 띄운 경우
    return c if ID_CELL.match(c) else None


def id_column(body):
    """앞 두 칸 중 본문 행 60% 이상이 식별자인 칸 번호 (첫 칸이 순번 '1.1'인 표 포함). 없으면 None."""
    rows = [_cells(r[2]) for r in body]
    for j in (0, 1):
        ids = sum(1 for c in rows if len(c) > j + 1 and _id(c[j]))
        if body and ids >= max(2, 0.6 * len(body)): return j
    return None


def id_column_key(line, j=0):
    cells = _cells(line)
    if len(cells) < j + 2: return None
    fid = _id(cells[j])
    if not fid: return None
    name = cells[j + 1] if len(cells[j + 1]) <= 40 else ''
    return f"{fid} {name}".strip()


def table_records(b):
    """식별자가 있는 행부터 다음 식별자 행 전까지를 한 항목으로 묶는다 (PDF 한 행이 표 두 행으로 나뉜 경우 포함).
    식별자는 본문 안 '[A-F01][IN] 이름:' 또는 첫 칸의 ID(본문 행 60% 이상이 ID일 때).
    식별자 행이 없으면 None — 기존처럼 토큰 수로만 나눈다."""
    rows = b['rows']
    nh = 2 if len(rows) > 1 and TSEP.match(rows[1][2]) else 1
    body = rows[nh:]
    key_of = row_key
    if not any(row_key(r[2]) for r in body):
        j = id_column(body)
        if j is None: return None
        key_of = lambda line: id_column_key(line, j)  # noqa: E731
    recs = []
    for r in body:
        k = key_of(r[2])
        if k or not recs: recs.append(dict(key=k, rows=[r]))
        else: recs[-1]['rows'].append(r)
    return dict(header=rows[:nh], records=recs)


def record_units(doc, b, L):
    """항목 하나를 청크 하나로. 다른 항목과 합치지 않아 경로가 항목 하나를 가리킨다.
    머리글은 반복하지 않는다 — 모든 항목 청크에 같은 글자가 붙어 검색 점수를 떨어뜨렸다 (Agent2 정답 세트 측정).
    항목 행에 식별자·이름·설명이 모두 있어 머리글 없이도 뜻이 통한다."""
    out = []
    for rec in b['records']['records']:
        s, e = rec['rows'][0][0], rec['rows'][-1][1]
        text = '\n'.join(r[2] for r in rec['rows']).strip()
        if ntok(text) <= L:
            out.append(dict(start=s, end=e, text=text, header_repeated=True))
        else:
            out.extend(dict(mk(doc, a, z), header_repeated=True) for a, z in split_R(doc['text'], s, e, L))
    return out                                              # 머리글은 어느 항목 청크에도 넣지 않는다 (위치도 항목 행부터)


def block_units(doc, b, L):
    if b['kind'] == 'table':
        if b.get('records'): return record_units(doc, b, L)
        if ntok(doc['text'][b['start']:b['end']]) <= L: return [mk(doc, b['start'], b['end'])]
        return table_groups(doc, b, L)
    return chunk_R(doc, L, b['start'], b['end'])

def greedy(doc, units, L):
    """원문에서 연속인 단위를 상한까지 묶는다(표 머리글 반복 청크는 단독)."""
    out = []
    for u in units:
        if out and not u.get('header_repeated') and not out[-1].get('header_repeated'):
            s, e = out[-1]['start'], u['end']
            if ntok(doc['text'][s:e].strip()) <= L:
                out[-1] = mk(doc, s, e); continue
        out.append(u)
    return out

