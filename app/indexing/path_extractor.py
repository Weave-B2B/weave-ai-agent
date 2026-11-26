"""문서 메타데이터·조항 경로 추출기 (규칙 기반).

입력은 고정 파싱 텍스트(쪽 문자열 목록)뿐이다. 정답 파일, manifest의 name, 질문은 읽지 않는다.

extract(document_id, pages) -> dict
  document, title, parties[{role, name, raw}], number, section_paths[...], status, warnings, path_source
"""
import re
import tiktoken

ENC = tiktoken.get_encoding('cl100k_base')
ntok = lambda s: len(ENC.encode(s, disallowed_special=()))

SECTION_MAX_TOKENS = 30
HEAD_PAGES = 2

# ------------------------------------------------------------------ 당사자
ROLE_WORDS = ['공급받는 자', '공급하는 자', '공급받는자', '공급하는자', '발주자', '발주사', '수행사', '수급인', '도급인', '위탁자', '수탁자',
              '구매자', '공급자', '매도인', '매수인', '고객', '운영사', '갑', '을', '병']
ROLE = '(' + '|'.join(re.escape(r).replace(r'\ ', r'\s*') for r in ROLE_WORDS) + ')'
CORP = r'(?:주식회사|유한회사|㈜|\(주\)|\(유\))'
# 역할어 뒤에 오면 당사자명이 아닌 정보
NOT_NAME = re.compile(r'^(?:권한자|서명자|전화|전화번호|주소|대표자|대표|대표이사|담당자|담당|사업자|사업자등록번호|연락처|서명|\(인\)|이메일|팩스|계좌|은행|정산|및|또는|와|과|은|는|이|가|의|에게|에|측)(?:\s|$|[:：])')
SEPARATORS = r'\s*(?:·|/|\||,|，|·)\s*'
# 한 줄에 문장으로 이어 쓴 표지('발주자: 주식회사 A. 수행사: 주식회사 B. …')도 조각으로 나눈다
SENTENCE = r'(?<=[^\d\s][.。])\s+'   # '06. 발주사 제공 사항'처럼 번호 뒤에서는 나누지 않는다
# 쌍점·괄호 없이 '역할어 이름'만 쓴 조각은 이 길이 이하일 때만 당사자로 본다 (본문 문장 오인 방지)
IMPLICIT_SEG_MAX = 30


def normalize_name(raw):
    """법인 표기를 앞뒤에서 제거한 첫 이름 토큰. 2~20자, 숫자·기호만이면 None."""
    s = re.sub(r'\s+', ' ', raw).strip(' "\'“”‘’:：')
    s = re.sub(r'^' + CORP + r'\s*', '', s)
    tok = s.split(' ')[0] if s else ''
    tok = re.sub(r'\s*' + CORP + r'$', '', tok)
    tok = re.sub(CORP, '', tok).strip(' ,.')
    if not (2 <= len(tok) <= 20): return None
    if not re.search(r'[가-힣A-Za-z]', tok): return None
    return tok


def _raw_name(rest):
    """역할어 뒤 문자열에서 이름 부분(법인 표기 포함)만 원문 그대로 잘라낸다."""
    m = re.match(r'\s*((?:' + CORP + r'\s*)?[가-힣A-Za-z0-9][가-힣A-Za-z0-9&.\-]*(?:\s*' + CORP + r')?)', rest)
    return m.group(1).strip() if m else ''


def _add(out, role, raw):
    if not raw or NOT_NAME.match(raw): return
    name = normalize_name(raw)
    # 같은 이름(또는 이미 찾은 이름의 앞부분만 남은 끊긴 표기)은 처음 발견한 것 하나만
    if not name or any(p['name'] == name or p['name'].startswith(name) for p in out): return
    if name in ROLE_WORDS: return
    out.append({'role': re.sub(r'\s+', ' ', role).strip(), 'name': name, 'raw': raw})


def _name_like(rest):
    """쌍점 없는 '역할어 이름'의 이름 부분: 세 단어 이하이고 문장이 아니어야 한다 ('추가 요청은 … 기록한다.' 제외)."""
    r = rest.strip().rstrip('.')
    return len(r.split()) <= 3 and not r.endswith('다')


def extract_parties(head_text):
    out = []
    for line in head_text.split('\n'):
        s = line.strip()
        if not s: continue
        is_heading = bool(re.match(r'^\s*#+\s', s))
        s = re.sub(r'^\s*(?:[-*]\s+|#+\s*)', '', s).replace('**', '')
        # 1) 표 행: | 역할 | 이름 |
        mt = re.match(r'^\|\s*' + ROLE + r'\s*(?:\((?:갑|을|병)\))?\s*\|\s*([^|]+)\|', s)
        if mt:
            _add(out, mt.group(1), _raw_name(mt.group(2))); continue
        # 2) 이름(이하 "갑") / 이름(이하 '을')
        for mm in re.finditer(r'((?:' + CORP + r'\s*)?[가-힣A-Za-z0-9]+(?:\s*' + CORP + r')?)\s*\(\s*이하\s*["\'“‘]?\s*(갑|을|병)\s*["\'”’]?', s):
            _add(out, mm.group(2), mm.group(1).strip())
        # 3) 구분자로 나눈 조각마다 '역할어: 이름', '역할어(갑): 이름', '역할어 이름'
        segs = [x for part in re.split(SENTENCE, s) for x in re.split(SEPARATORS, part) if x]
        role_segs = []
        for seg in segs:
            m = re.match(r'^' + ROLE + r'\s*(\((?:갑|을|병)\))?\s*([:：])?\s*(.+)$', seg)
            if not m: continue
            role, colon, rest = m.group(1), m.group(3), m.group(4)
            if not colon and not m.group(2) and not re.match(r'\s', seg[len(m.group(1)):len(m.group(1)) + 1] or 'x'):
                continue  # '을은', '갑과' 같은 조사 결합은 역할어가 아님
            role_segs.append((role, bool(colon or m.group(2)), rest, len(seg)))
        # 4) 구분자 없이 공백으로 이어진 표지 줄: '발주자 주식회사 A 수행사 주식회사 B 문서번호 …'
        #    법인 표기가 붙은 '역할어 이름' 쌍이 한 줄에 둘 이상일 때만 인정
        pairs = list(re.finditer(r'(?:^|\s)' + ROLE + r'\s+((?:' + CORP + r'\s*[가-힣A-Za-z0-9]+)|(?:[가-힣A-Za-z0-9]+\s*' + CORP + r'))', s))
        if len(pairs) >= 2:
            for mm in pairs: _add(out, mm.group(1), mm.group(2).strip())
        for role, explicit, rest, seg_len in role_segs:
            # 쌍점·괄호 역할 표기가 없으면, 조각 자체가 짧고(본문 문장이 아님) 구분자로 나뉜 줄이거나 짧은 독립 줄만 인정
            # 짧은 독립 줄 허용은 마크다운 제목 줄('## 고객 문의 시스템 운영 계약')에는 적용하지 않음
            if not explicit and not (seg_len <= IMPLICIT_SEG_MAX and not is_heading and _name_like(rest)
                                     and (len(segs) >= 2 or len(s) <= 30)):
                continue
            _add(out, role, _raw_name(rest))
    return out


# ------------------------------------------------------------------ 문서번호
NUM_LABEL = re.compile(r'(?:계약\s*번호|문서\s*번호|관리\s*번호|발주\s*번호|문서\s*No\.?|No\.)\s*[:：|]?\s*\|?\s*([A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9])')
NUM_PATTERN = re.compile(r'(?<![A-Za-z0-9\-])([A-Z]{2,}(?:-[A-Za-z0-9.]+)+)(?![A-Za-z0-9\-])')
DATE = re.compile(r'^\d{4}[-.]\d{1,2}[-.]\d{1,2}$')


def extract_number(head_text):
    for m in NUM_LABEL.finditer(head_text):
        v = m.group(1).rstrip('.')
        if DATE.match(v) or not re.search(r'\d', v) or '-' not in v and not re.search(r'[A-Za-z]', v): continue
        return v, 'label'
    for m in NUM_PATTERN.finditer(head_text):
        v = m.group(1).rstrip('.')
        if re.search(r'\d', v): return v, 'pattern'
    return None, None


# ------------------------------------------------------------------ 제목
HEADING = re.compile(r'^\s*#+\s+(.+?)\s*#*\s*$')


def clean(line):
    return re.sub(r'\s+', ' ', re.sub(r'\*\*', '', re.sub(r'^\s*(?:[-*]\s+)?#*\s*', '', line))).strip()


def extract_title(head_text, number):
    lines = [l for l in head_text.split('\n') if l.strip()]
    for l in lines:
        m = HEADING.match(l)
        if not m: continue
        t = clean(l)
        if not t or not re.search(r'[가-힣A-Za-z]', t): continue
        if article_match(l) or (number and number in t): continue
        return t, 'heading'
    return (clean(lines[0]), 'first_line') if lines else (None, None)


# ------------------------------------------------------------------ 조항 경로
ART = re.compile(r'^\s*(?:[-*]\s+)?(#+\s*)?(?:\*\*)?\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?)(.*)$')
ANNEX = re.compile(r'^\s*(?:[-*]\s+)?(#+\s*)?(?:\*\*)?\s*\[?\s*(별첨|별표|부록|붙임|별지)(.*)$')


def article_match(line):
    m = ART.match(line)
    if not m: return None
    rest = m.group(3).replace('**', '').rstrip()
    if rest == '' or re.fullmatch(r'\s*[(（][^)）]{1,40}[)）]\s*', rest):
        return clean(line)
    if rest[:1].isspace():
        r = rest.strip()
        if r and len(r) <= 40 and not r.endswith(('.', '。', '다')):
            return clean(line)
        if m.group(1) and r:  # 마크다운 제목 줄은 길이와 관계없이 인정
            return clean(line)
    return None


def annex_match(line):
    m = ANNEX.match(line)
    if not m: return None
    c = clean(line)
    if m.group(1):  # 마크다운 제목
        return c
    rest = m.group(3)
    if len(c) > 40 or c.endswith(('.', '。')): return None
    # 표지어 뒤: 번호 또는 짧은 명칭. 번호 뒤에 조사가 붙으면 본문 참조
    if re.match(r'^\s*[0-9A-Za-z가-하]+\s*(?:의|에서|에|을|를|과|와|에는|으로|로)(?:\s|$)', rest): return None
    if re.match(r'^\s*([0-9A-Za-z]+[.)]?(?:\s+\S.*)?|[가-힣A-Za-z·\s]{1,20})\s*$', rest) or rest.strip() == '':
        return c
    return None


def page_of(bounds, off):
    for i, (s, e) in enumerate(bounds):
        if off < e + 2: return i + 1
    return len(bounds)


def extract_sections(text, bounds):
    out, pos = [], 0
    for line in text.split('\n'):
        t = article_match(line) or annex_match(line)
        if t:
            toks = ENC.encode(t, disallowed_special=())
            trunc = len(toks) > SECTION_MAX_TOKENS
            title = ENC.decode(toks[:SECTION_MAX_TOKENS]) if trunc else t
            if trunc:
                while ntok(title) > SECTION_MAX_TOKENS or '�' in title: title = title[:-1]
            out.append({'start': pos, 'title': title, 'raw': t, 'page': page_of(bounds, pos), 'tokens': ntok(title),
                        'truncated': trunc, 'kind': 'article' if article_match(line) else 'annex'})
        pos += len(line) + 1
    return out


def section_at(sections, off):
    t = ''
    for s in sections:
        if s['start'] <= off: t = s['title']
        else: break
    return t


# ------------------------------------------------------------------ 전체
def join_pages(pages):
    text, bounds = '', []
    for i, p in enumerate(pages):
        if i: text += '\n\n'
        s = len(text); text += p; bounds.append((s, len(text)))
    return text, bounds


def extract(document_id, pages):
    pages = [p if isinstance(p, str) else p['text'] for p in pages]
    text, bounds = join_pages(pages)
    head = '\n\n'.join(pages[:HEAD_PAGES])
    number, number_src = extract_number(head)
    title, title_src = extract_title(head, number)
    parties = extract_parties(head)
    sections = extract_sections(text, bounds)
    warnings = []
    if len(parties) == 0: warnings.append('parties_0')
    elif len(parties) == 1: warnings.append('parties_1')
    if title_src != 'heading': warnings.append('title_missing' if title is None else 'title_from_first_line')
    for s in sections:
        if s['truncated']: warnings.append(f"section_truncated:p{s['page']}")
    return {'document': document_id, 'title': title, 'parties': parties, 'number': number,
            'number_source': number_src, 'title_source': title_src, 'section_paths': sections,
            'status': 'ok' if not warnings else 'warn', 'warnings': warnings, 'path_source': 'extracted'}


# ------------------------------------------------------------------ 3단계: 대체 경로, 경로 직렬화, 충돌 경고, 보고서
FALLBACK_MAX_TOKENS = 100
RESERVE_MARGIN = 2


def _cut_tokens(s, n):
    toks = ENC.encode(s, disallowed_special=())
    if len(toks) <= n: return s, False
    t = ENC.decode(toks[:n])
    while ntok(t) > n or '�' in t: t = t[:-1]
    return t, True


def fallback_identity(pages, title):
    """당사자 0명일 때: 파싱 원문의 제목 줄부터 최대 100토큰. 완전한 줄 우선, 첫 줄이 넘으면 토큰 경계에서 자름."""
    lines = [l.strip() for l in '\n\n'.join(pages[:HEAD_PAGES]).split('\n') if l.strip()]
    start = 0
    if title:
        for i, l in enumerate(lines):
            if clean(l) == title: start = i; break
    picked, info = [], {'start_line': lines[start] if lines else '', 'cut': False}
    for l in lines[start:]:
        c = clean(l)
        cand = ' / '.join(picked + [c])
        if ntok(cand) <= FALLBACK_MAX_TOKENS:
            picked.append(c); continue
        if not picked:
            c2, _ = _cut_tokens(c, FALLBACK_MAX_TOKENS); picked.append(c2); info['cut'] = True
        break
    return ' / '.join(picked), info


def identity(meta):
    if meta['path_source'] == 'fallback':
        return meta['fallback_identity']
    names = ', '.join(p['name'] for p in meta['parties'])
    return f"{meta['title'] or ''} | {names} | {meta['number'] or ''}"


def path_string(meta, section):
    return f"[{identity(meta)} > {section}]\n"


def reserve_tokens(meta):
    """실제로 쓸 전체 경로 최대 토큰 + 여유 2."""
    secs = [''] + [s['title'] for s in meta['section_paths']]
    return max(ntok(path_string(meta, s)) for s in secs) + RESERVE_MARGIN


def finalize(meta, pages):
    """대체 경로 결정(당사자 0명). extract() 결과에 덧붙인다."""
    if not meta['parties']:
        fb, info = fallback_identity(pages, meta['title'])
        meta.update(path_source='fallback', fallback_identity=fb, fallback_info=info)
        if info['cut']: meta['warnings'].append('fallback_first_line_cut')
    else:
        meta['path_source'] = 'extracted'
    meta['status'] = 'ok' if not meta['warnings'] else 'warn'
    return meta


def mark_collisions(metas):
    """같은 색인 묶음 안에서 (제목, 당사자, 번호) 중복과, 최종 식별 경로 중복을 경고한다."""
    from collections import defaultdict
    by_key, by_id = defaultdict(list), defaultdict(list)
    for m in metas:
        by_key[(m['title'], tuple(sorted(p['name'] for p in m['parties'])), m['number'])].append(m['document'])
        by_id[identity(m)].append(m['document'])
    for m in metas:
        k = (m['title'], tuple(sorted(p['name'] for p in m['parties'])), m['number'])
        if len(by_key[k]) > 1:
            m['warnings'].append('duplicate_meta:' + '+'.join(d for d in by_key[k] if d != m['document']))
        if len(by_id[identity(m)]) > 1:
            m['warnings'].append('identity_collision:' + '+'.join(d for d in by_id[identity(m)] if d != m['document']))
        m['status'] = 'ok' if not m['warnings'] else 'warn'
    return metas


REPORT_FIELDS = ['document', 'title', 'parties', 'number', 'section_name_max_tokens', 'full_path_max_tokens',
                 'status', 'warnings', 'path_source']


