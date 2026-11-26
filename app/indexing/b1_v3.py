"""B1 v3 — B1 구조 분할과 경로(출처)가 **같은 경계 판정**을 쓰도록 묶은 모듈 (2A).

BASE(B1 + path v2)와 달라진 점
  1. 경계 판정 `classify()` 하나를 청커와 경로가 함께 쓴다.
     - 조: 제N조, 제N조의M, 괄호 제목, 괄호 없는 짧은 제목, 제목 없는 조(본문이 같은 줄), 삭제 조.
       괄호·마크다운 제목이 아닌 후보는 '번호가 직전 조보다 크고 +3 이내'일 때만 조로 본다(본문 참조 오인 방지).
       '제3조에 따라'처럼 번호 바로 뒤에 조사·문장부호가 붙거나 '제N조 제M항'이면 참조로 본다.
     - 장(제N장)·부칙·별첨류는 상위 경계. 장은 경로에 넣지 않는다.
     - 절 번호 제목(1. / 1.1 / 2.1)은 마크다운 제목 줄일 때만, 조 안이 아닐 때만 경계로 본다. 일반 목록 '1.'은 경계가 아니다.
     - 번호 없는 마크다운 제목(문서 제목, 표 캡션 등)은 경계로 쓰지 않는다.
  2. 출처: 청크 본문이 실제로 덮는 항·호·목 표지를 모두 경로 끝에 적는다(첫 항 하나로 귀속하지 않음).
  3. 쪽 넘김: 다음 쪽 첫머리의 '같은 조/별첨 … (계속)' 제목 줄은 경계가 아닌 이어짐 표시로 보고 청크 본문에서 뺀다.
     다른 조·다른 별첨의 (계속)이면 지우지 않고 일반 경계 판정을 따른다. 청크의 쪽 범위는 원래 쪽 경계로 계산한다.
  4. 각주: 줄머리 '주N)'(또는 *N, [^N]) 각주는 같은 표지가 본문 줄 안에서 정확히 한 번 확인될 때 그 조항·항에 연결한다.
     확인되지 않으면 '각주 주N)'로만 표시하고 임의 조에 붙이지 않는다.
  5. 대체 경로(당사자 0명)는 첫 구조 경계 줄 앞에서 멈춘다(조·절 제목이 문서 식별자에 섞이지 않게).
  6. 청크 상한: 경로까지 붙인 실제 임베딩 문자열을 다시 세어 500을 넘으면 그 문서의 본문 상한을 줄여 다시 자른다.

파싱 출력(쪽 텍스트)은 바꾸지 않는다. 청크 본문은 원문 조각이며, 지운 것은 (3)의 이어짐 제목 줄뿐이다.
"""
import re

from . import b1_units as r
from . import path_extractor as P

CHUNK_MAX = 500
LEAD = r'^\s*(?:[-*]\s+)?(#+\s*)?(?:\*\*)?\s*'
ART = re.compile(LEAD + r'(제\s*(\d+)\s*조(?:\s*의\s*(\d+))?)(.*)$')
CHAP = re.compile(LEAD + r'제\s*\d+\s*[장편](?=\s|$)')
SUPP = re.compile(LEAD + r'부\s*칙\s*(?:[(（].*[)）])?\s*$')
NUMSEC = re.compile(r'^\s*(#+)\s*(?:\*\*)?\s*(\d{1,2}(?:\.\d{1,2})*)(\.?)\s+(\S.*?)\s*(?:\*\*)?\s*$')
# docling이 제목 표시(#)를 빠뜨린 절 제목: '01. 변경 대상'처럼 두 자리 번호 + 짧은 제목만 있는 줄 (일반 목록 '1.'과 구분)
PADSEC = re.compile(r'^\s*(?:\*\*)?\s*(0[1-9]|[1-9]\d)\.\s+(\S.{0,40}?)\s*(?:\*\*)?\s*$')
# 번호 없는 마크다운 제목: 짧고 문장이 아니면 절 경계 (문서의 첫 제목은 문서 제목이라 제외)
MDSEC = re.compile(r'^\s*#{1,6}\s+(?:\*\*)?\s*(\S.{0,40}?)\s*(?:\*\*)?\s*$')
CONT = re.compile(r'[(（]\s*계\s*속\s*[)）]\s*$')
ITEM = re.compile(r'^\s*(?:[-*]\s+)?([①-⑳]|\(\d{1,2}\)|\d{1,2}\.(?!\d)|[가-하]\.|\d{1,2}\))\s*\S')
FOOT = re.compile(r'^\s*(?:[-*]\s+)?(주\s*\d{1,2}\)|\*\d{1,2}(?=\s)|\[\^?\d{1,2}\])\s*\S')
REF_PARTICLE = re.compile(r'^(?:에|의|를|을|은|는|와|과|및|부터|까지|로|으로|에서|이|가|·|,|，|、|\.|에는|에도)')
CLEAN = P.clean


def item_level(label):
    if re.fullmatch(r'[①-⑳]|\(\d{1,2}\)', label): return 1
    if re.fullmatch(r'\d{1,2}\.', label): return 2
    if re.fullmatch(r'[가-하]\.', label): return 3
    return 4


def _art_title(num_txt, rest, md):
    """조 제목 줄이면 (경로용 제목, 본문이 같은 줄에 있는지)를, 아니면 None."""
    num = re.sub(r'\s+', '', num_txt)
    r0 = rest.replace('**', '')
    if r0 == '' or r0.isspace(): return num, False, 'plain'
    if CONT.search(r0): return num, False, 'cont'
    if REF_PARTICLE.match(r0): return None                      # 제3조에 따라 / 제5조의 …
    m = re.fullmatch(r'\s*([(（][^)）]{1,40}[)）])\s*', r0)
    if m: return num + m.group(1), False, 'paren'
    if not r0[:1].isspace(): return None
    s = r0.strip()
    if re.match(r'제\s*\d+\s*[항호]', s): return None             # 제5조 제2항 …
    if s.startswith('삭제'): return num + ' 삭제', True, 'deleted'
    if len(s) <= 40 and not s.endswith(('.', '。', '다')): return f'{num} {s}', False, 'short' if not md else 'md'
    return num, True, 'inline'                                   # 제목 없는 조: 본문이 같은 줄


class Classifier:
    """줄 단위 경계 판정. 상태(직전 조 번호, 열린 조·별첨)를 가진다. 청커와 경로가 같은 인스턴스를 쓴다."""

    def __init__(self):
        self.last_art = None; self.top = None; self.top_kind = None; self.seen_md = False

    def classify(self, line):
        """반환: None(경계 아님) 또는 dict(kind, title, inline, cont)."""
        m = ART.match(line)
        if m:
            md = bool(m.group(1)); got = _art_title(m.group(2), m.group(5), md)
            if got:
                title, inline, how = got
                n = (int(m.group(3)), int(m.group(4) or 0))
                if how == 'cont':
                    if self.top_kind == 'article' and self.last_art and n == self.last_art:
                        return dict(kind='cont', title=self.top, inline=False)
                    return None
                strong = md or how in ('paren', 'plain')
                seq_ok = self.last_art is None and n[0] <= 3 or self.last_art is not None and self.last_art < n and n[0] <= self.last_art[0] + 3
                if strong and (self.last_art is None or self.last_art < n or self.top_kind != 'article') or seq_ok:
                    self.last_art = n; self.top = title; self.top_kind = 'article'
                    return dict(kind='article', title=title, inline=inline)
            return None
        if CHAP.match(line):
            self.top = None; self.top_kind = 'chapter'
            return dict(kind='chapter', title=CLEAN(line), inline=False)
        if SUPP.match(line):
            self.top = '부칙'; self.top_kind = 'supp'
            return dict(kind='supp', title='부칙', inline=False)
        a = P.annex_match(line)
        if a:
            if CONT.search(a):
                base = CONT.sub('', a).strip()
                if self.top_kind == 'annex' and self.top and _annex_key(base) == _annex_key(self.top):
                    return dict(kind='cont', title=self.top, inline=False)
            if self.top_kind == 'annex' and self.top and a == self.top:   # 같은 별첨 제목 반복(쪽 머리)
                return dict(kind='cont', title=self.top, inline=False)
            self.top = a; self.top_kind = 'annex'; self.last_art = None
            return dict(kind='annex', title=a, inline=False)
        m = NUMSEC.match(line)
        if m: self.seen_md = True
        if m and self.top_kind != 'article':
            t = m.group(4).replace('**', '').strip()
            if len(t) <= 60 and not t.endswith(('다.', '다', '.')):
                return dict(kind='numsec', title=f'{m.group(2)}{m.group(3)} {t}', depth=m.group(2).count('.') + 1, inline=False)
        m = PADSEC.match(line)
        if m and self.top_kind != 'article':
            t = m.group(2).strip()
            if not t.endswith(('다.', '다', '.', ',', ':')):
                return dict(kind='numsec', title=f'{m.group(1)}. {t}', depth=1, inline=False)
        m = MDSEC.match(line)
        if m:
            first, self.seen_md = not self.seen_md, True
            t = m.group(1).replace('**', '').strip()
            if not first and self.top_kind != 'article' and not t.endswith(('다.', '다', '.', ',', ':')):
                return dict(kind='mdsec', title=t, inline=False)
        return None


def _annex_key(t):
    m = re.match(r'\s*\[?\s*(별첨|별표|부록|붙임|별지)\s*([0-9A-Za-z가-하]+)?', t)
    return (m.group(1), m.group(2)) if m else t


# ------------------------------------------------------------------ 문서 → 논리 텍스트 + 구조
def build_view(pages):
    """(계속) 제목 줄을 뺀 쪽 텍스트로 논리 문서를 만든다. 지운 줄은 removed에 남긴다."""
    clf = Classifier(); out_pages, removed = [], []
    for pi, p in enumerate(pages):
        keep = []
        for l in p.split('\n'):
            c = clf.classify(l)
            if c and c['kind'] == 'cont':
                removed.append(dict(page=pi + 1, line=l)); continue
            keep.append(l)
        out_pages.append('\n'.join(keep).strip('\n'))
    text, bounds = P.join_pages(out_pages)
    return dict(text=text, bounds=bounds, pages=out_pages, removed=removed)


def parse_structure(doc):
    """B1의 parse_structure와 같은 꼴(섹션·블록)을 만들되 경계는 Classifier로 판정하고 경로 체인·항 표지를 함께 기록."""
    clf = Classifier(); secs, cur = [], None
    chain_top, numstack = None, []
    feet = []
    pos = 0
    lines = []
    for l in doc['text'].split('\n'):
        lines.append((pos, pos + len(l), l)); pos += len(l) + 1
    in_foot = False
    for s, e, l in lines:
        c = clf.classify(l)
        fm = FOOT.match(l) if not c else None
        if c and c['kind'] != 'cont':
            k = c['kind']
            if k in ('article', 'annex', 'supp'): chain_top, numstack = c['title'], []
            elif k == 'chapter': chain_top, numstack = None, []
            elif k == 'numsec':
                d = c['depth']; numstack = [x for x in numstack if x[0] < d] + [(d, c['title'])]
            elif k == 'mdsec':                                  # 번호 절 아래 한 단계, 앞선 번호 없는 절과는 형제
                numstack = [x for x in numstack if x[0] < 99] + [(99, c['title'])]
            chain = ([chain_top] if chain_top else []) + [t for _, t in numstack]
            cur = dict(kind='article' if k == 'article' else 'heading', skind=k, title=c['title'], start=s, end=e, head=(s, e),
                       lines=[], chain=chain, inline=c['inline']); secs.append(cur); in_foot = False
            if c['inline']: cur['lines'].append((s, e, l))      # 제목 없는 조·삭제 조: 제목 줄이 곧 본문
            continue
        if fm:
            feet.append(dict(marker=re.sub(r'\s+', '', fm.group(1)), start=s))
            prev_chain = cur['chain'] if cur else []
            cur = dict(kind='heading', skind='footnote', title=fm.group(1), start=s, end=e, head=None, lines=[(s, e, l)],
                       chain=prev_chain, inline=True, marker=re.sub(r'\s+', '', fm.group(1)), resume=prev_chain); secs.append(cur)
            in_foot = True; continue
        if in_foot and not l.strip():                       # 각주 문단이 끝나면 앞 섹션 경로로 되돌아간다
            in_foot = False
            cur = dict(kind='heading', skind='resume', title='', start=e, end=e, head=None, lines=[], chain=cur['resume'], inline=False)
            secs.append(cur); continue
        if cur is None:
            cur = dict(kind='front', skind='front', title='', start=s, end=e, head=None, lines=[], chain=[], inline=False); secs.append(cur)
        cur['lines'].append((s, e, l)); cur['end'] = e
    # 제목만 있는 섹션은 다음 섹션과 합친다 (B1과 같음). 본문이 제목 줄에 있는 섹션은 합치지 않는다.
    merged, carry = [], None
    for sec in secs:
        body = [x for x in sec['lines'] if x[2].strip()]
        if carry:
            sec = dict(sec, start=carry['start'], pre=carry.get('pre', []) + ([carry['head']] if carry['head'] else []),
                       kind=sec['kind'] if sec['kind'] != 'heading' or sec['skind'] in ('footnote',) else carry['kind'])
            carry = None
        if not body and (sec['head'] is not None or sec['skind'] == 'resume'):
            if sec['skind'] == 'resume': continue
            carry = sec; continue
        merged.append(sec)
    if carry: merged.append(carry)
    for sec in merged:
        blocks, b = [], None
        for s, e, l in sec['lines']:
            if r.TABLE.match(l):
                if not (b and b['kind'] == 'table'): b = dict(kind='table', rows=[]); blocks.append(b)
                b['rows'].append((s, e, l)); continue
            im = ITEM.match(l)
            if im:
                b = dict(kind='item', start=s, end=e, label=im.group(1)); blocks.append(b); continue
            if not l.strip():
                if b and b['kind'] == 'table': b = None
                continue
            if b is None or b['kind'] == 'table':
                b = dict(kind='lead', start=s, end=e); blocks.append(b)
            else: b['end'] = e
        for b in blocks:
            if b['kind'] == 'table':
                b['start'], b['end'] = b['rows'][0][0], b['rows'][-1][1]
                b['records'] = r.table_records(b)
        sec['blocks'] = blocks
    # 각주 연결: 같은 표지가 본문 줄 안(줄머리 제외)에서 정확히 한 번 나오면 그 위치의 섹션·항으로
    for sec in merged:
        if sec.get('skind') != 'footnote': continue
        mk_ = sec['marker']; hits = []
        for s, e, l in lines:
            if FOOT.match(l): continue
            for m in re.finditer(re.escape(mk_).replace(r'\ ', r'\s*'), l):
                if m.start() > 0: hits.append(s + m.start())
        if len(hits) == 1:
            host = section_at(merged, hits[0])
            labs = item_labels(host, hits[0], hits[0] + 1) if host else []
            sec['chain'] = (host['chain'] if host else []) + ([', '.join(labs)] if labs else []) + [mk_]
            sec['foot_link'] = 'linked'
        else:
            sec['chain'] = ['각주 ' + mk_]; sec['foot_link'] = f'unlinked({len(hits)})'
    found = any(s['kind'] != 'front' or any(b['kind'] in ('item', 'table') for b in s['blocks']) for s in merged)
    return merged, found


def section_at(secs, off):
    best = None
    for s in secs:
        if s['start'] <= off: best = s
        else: break
    return best


def item_labels(sec, s, e):
    """청크 [s, e)가 실제로 덮는 항·호·목 표지. 가장 높은 단계만, 등장 순서대로."""
    keys = [rec['key'] for b in sec.get('blocks', []) if b['kind'] == 'table' and b.get('records')
            for rec in b['records']['records']
            if rec['key'] and rec['rows'][0][0] < e and rec['rows'][-1][1] > s]
    if keys: return list(dict.fromkeys(keys))                # 표 항목 식별자가 항·호보다 우선
    its = [b for b in sec.get('blocks', []) if b['kind'] == 'item' and b['start'] < e and b['end'] > s]
    if not its: return []
    lv = min(item_level(b['label']) for b in its)
    out = []
    for b in its:
        if item_level(b['label']) == lv and b['label'] not in out: out.append(b['label'])
    return out


MAX_LABELS = 20


def section_path(sec, s, e):
    if sec is None: return ''
    chain = list(sec.get('chain', []))
    if sec.get('skind') != 'footnote':
        labs = item_labels(sec, s, e)
        if labs:
            chain.append(', '.join(labs[:MAX_LABELS]) + (' …' if len(labs) > MAX_LABELS else ''))
    return ' > '.join(x for x in chain if x)


# ------------------------------------------------------------------ 청킹 (B1과 같은 순서·같은 묶음 규칙)
def chunk_B3(doc, L):
    secs, found = parse_structure(doc)
    if not found:
        return [dict(c, sec=None) for c in r.chunk_R(doc, L)], secs, True
    out = []
    for sec in secs:
        s, e = r.trim(doc['text'], sec['start'], sec['end'])
        if s >= e: continue
        keyed = any(b['kind'] == 'table' and b.get('records') for b in sec.get('blocks', []))
        if r.ntok(doc['text'][s:e]) <= L and not keyed:      # 항목 식별자 표는 짧아도 항목별로 나눈다
            out.append(dict(r.mk(doc, s, e), sec=sec)); continue
        units = [r.mk(doc, a, z) for a, z in sec.get('pre', []) + ([sec['head']] if sec['head'] and not sec['inline'] else [])]
        for b in sec['blocks']:
            units.extend(r.block_units(doc, b, L))
        for u in r.greedy(doc, units, L):
            out.append(dict(u, sec=sec))
    return [c for c in out if c['text']], secs, False


# ------------------------------------------------------------------ 대체 경로
def fallback_identity(pages, title):
    """path_extractor.fallback_identity와 같되, 첫 구조 경계(조·장·별첨·절 제목) 줄 앞에서 멈춘다."""
    clf = Classifier()
    lines = [l for l in '\n\n'.join(pages[:P.HEAD_PAGES]).split('\n') if l.strip()]
    start = 0
    if title:
        for i, l in enumerate(lines):
            if P.clean(l) == title: start = i; break
    picked, info = [], {'start_line': lines[start] if lines else '', 'cut': False, 'stopped_at': None}
    for l in lines[start:]:
        boundary = clf.classify(l)                         # 문서 제목 줄도 판정기에 넘겨야 다음 제목을 절로 본다
        if picked and (boundary or r.TABLE.match(l)):      # 표(메타 표 등)는 식별자에 넣지 않는다
            info['stopped_at'] = P.clean(l); break
        c = P.clean(l); cand = ' / '.join(picked + [c])
        if r.ntok(cand) <= P.FALLBACK_MAX_TOKENS:
            picked.append(c); continue
        if not picked:
            c2, _ = P._cut_tokens(c, P.FALLBACK_MAX_TOKENS); picked.append(c2); info['cut'] = True
        break
    return ' / '.join(picked), info


def metas_for(docs_pages):
    metas = [P.extract(d, p) for d, p in docs_pages.items()]
    for m in metas:
        pages = docs_pages[m['document']]
        if not m['parties']:
            fb, info = fallback_identity(pages, m['title'])
            m.update(path_source='fallback', fallback_identity=fb, fallback_info=info)
        else:
            m['path_source'] = 'extracted'
    return {m['document']: m for m in P.mark_collisions(metas)}


def path_string(meta, sec_path):
    if not sec_path:                                    # 표지 등 절이 없는 청크
        return f"[{P.identity(meta)}]\n"
    return f"[{P.identity(meta)} > {sec_path}]\n"


def chunk_document(did, pages, meta):
    """반환: chunks(list), info(dict). chunk = text(임베딩 문자열), body, path, page_start, page_end, labels…"""
    view = build_view(pages)
    doc = dict(id=did, text=view['text'], bounds=view['bounds'], pages=view['pages'])
    base = r.ntok(path_string(meta, '')) + 2
    L = CHUNK_MAX - base - 24                        # 경로 조항 부분 첫 예산(이후 실제 길이로 검사)
    tries = []
    while True:
        cs, secs, fb = chunk_B3(doc, L)
        built = []
        for c in cs:
            sp = section_path(c['sec'], c['start'], c['end']) if c['sec'] else ''
            pre = path_string(meta, sp)
            ps, pe = r.span_pages(doc, c['start'], c['end'])
            built.append(dict(body=c['text'], path=pre.strip(), text=pre + c['text'], page_start=ps, page_end=pe,
                              start=c['start'], end=c['end'], tokens=r.ntok(pre + c['text']),
                              skind=c['sec']['skind'] if c['sec'] else None))
        over = max((b['tokens'] for b in built), default=0) - CHUNK_MAX
        tries.append(dict(L=L, chunks=len(built), max_tokens=max((b['tokens'] for b in built), default=0)))
        if over <= 0 or L < 100: break
        L -= over + 2
    feet = [dict(marker=s.get('marker'), link=s.get('foot_link'), path=' > '.join(s['chain'])) for s in secs if s.get('skind') == 'footnote']
    return built, dict(L=L, tries=tries, removed_cont=view['removed'], fallback_chunker=fb, footnotes=feet)
