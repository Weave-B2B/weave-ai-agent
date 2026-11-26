"""2B 파싱 출력 정리. Docling·Luna를 다시 돌리지 않고, 저장된 쪽 텍스트와 원본 PDF의 글자 레이어만 쓴다.

정리 항목 (모든 변경은 로그에 쪽·전·후·분류를 남긴다)
  1. html_unescape (표현): Docling 마크다운 내보내기가 만든 &lt; &gt; &amp; &quot; &#39; 를 원래 문자로.
  2. list_wrap_merge (구조 복원): 글자 레이어에서 바로 앞 줄이 오른쪽 끝까지 찬(줄바꿈된) 미완결 줄이고,
     현재 줄 내용이 원문에서는 표지 없이 시작하는데 Docling이 목록 표지(-, 2., 다. 등)를 붙인 경우 → 표지를 떼고 앞 항목의 이어지는 줄로.
  3. marker_restore (표현): 글자 레이어 줄머리 기호(○ 등)가 Docling 출력에서 다른 표지(-)로 바뀐 경우 원문 기호로 되돌림.
     원문에서 확인되지 않는 기호는 만들지 않는다.
  4. noise_remove: 여러 쪽에 반복되는 머리말·꼬리말(숫자만 다른 것, OCR로 몇 글자 다른 것 포함), 쪽 번호 줄,
     라틴 대문자 워터마크 줄을 지운다. 표 행(|)은 건드리지 않는다. 문서 제목으로 뽑힌 줄과 문서 번호 줄은 1쪽 첫 등장을 남긴다.
     조·장·별첨·절 제목과 '(계속)' 줄은 구조 경계라 지우지 않는다('(계속)'은 B1이 처리).
     숫자만 다른 줄을 같은 것으로 보는 것은 아래 구역(꼬리말)에서만 한다. 위 구역(머리말)은 숫자까지 같아야 한다.
     1쪽짜리 문서는 반복을 확인할 수 없어 지우지 않는다(쪽 번호 줄 제외).
  글자 레이어가 없는 쪽(스캔·Luna 처리 쪽)은 2·3을 하지 않는다.
"""
import difflib
import html
import re

from . import path_extractor as P

N = lambda s: re.sub(r'[\s|　]+', '', s or '')
ENT = re.compile(r'&(lt|gt|amp|quot|#39);')
DLINE = re.compile(r'^(\s*)(?:([-*•])\s+)?((?:[①-⑳]|\(\d{1,2}\)|\d{1,2}\.(?!\d)|[가-하]\.|\d{1,2}\))(?=\s|$))?\s*(.*)$')
SMARK = re.compile(r'^([○●◦•▪■□◆◇※\-–·]|[①-⑳]|\(\d{1,2}\)|\d{1,2}\.(?!\d)|[가-하]\.|\d{1,2}\))')
TERMINAL = tuple('.。!?:;)」』]') + ('다', '요')
PAGENUM = re.compile(r'^\s*(?:[-–—]\s*)?(?:페이지\s*)?\d{1,3}(?:\s*/\s*\d{1,3})?(?:\s*[-–—])?\s*$')
WATERMARK = re.compile(r'^[A-Z][A-Z ]{2,14}$')


def _find(src, content):
    """content가 시작하는 원문 줄 번호와 그 줄의 원문 표지(prefix)."""
    k = N(content)[:14]
    if len(k) < 2: return None, None
    for j, s in enumerate(src):
        ns = N(s)
        if ns.startswith(k): return j, ''
        m = SMARK.match(s.strip())
        if m and N(s.strip()[m.end():]).startswith(k): return j, m.group(1)
    return None, None


def fix_lists(page, text, log, src):
    if not src: return text
    lens = sorted(len(s) for s in src); p90 = lens[int(0.9 * (len(lens) - 1))]
    out = []
    for line in text.split('\n'):
        m = DLINE.match(line)
        bullet, label, content = (m.group(2), m.group(3), m.group(4)) if m else (None, None, line)
        if not m or not (bullet or label) or line.lstrip().startswith(('|', '#')):
            out.append(line); continue
        if not content.strip() and label:              # '- 다.' : 표지처럼 보이는 것이 사실 본문 글자
            content, label = label, None
        j, sprefix = _find(src, content)
        if j is None:
            out.append(line); continue
        dl = (label or '').strip()
        if sprefix == '':
            prev = src[j - 1] if j > 0 else ''
            prev_wrapped = j > 0 and len(prev) >= 0.8 * p90 and not prev.rstrip().endswith(TERMINAL)
            prev_out = next((x for x in reversed(out) if x.strip()), '')
            if prev_wrapped and prev_out and not prev_out.lstrip().startswith(('#', '|')) and N(prev_out).endswith(N(prev)[-6:]):
                log.append(dict(page=page, kind='list_wrap_merge', category='구조 훼손 복원', before=line, after=content,
                                evidence=f'원문 앞 줄이 줄 끝까지 차고 미완결: …{prev[-20:]!r} / 원문 이 줄: {src[j][:30]!r}'))
                while out and not out[-1].strip(): out.pop()   # 빈 줄 없이 앞 항목에 잇는다
                out.append(content); continue
            out.append(line); continue
        if sprefix and sprefix != dl and not (dl == '' and sprefix in ('-', '–')):
            new = f"{m.group(1)}{sprefix} {content}"
            log.append(dict(page=page, kind='marker_restore', category='표현', before=line, after=new, evidence=f'원문 줄: {src[j][:30]!r}'))
            out.append(new); continue
        out.append(line)
    return '\n'.join(out)


def _exact(l):
    return N(re.sub(r'^[#\s\-*]+', '', l).strip())


def _key(l):
    s = re.sub(r'^[#\s\-*]+', '', l).strip()
    return re.sub(r'\d+', '#', N(s))


def remove_noise(doc_id, pages, protect, log, zone=3):
    n = len(pages)
    lines = [p.split('\n') for p in pages]
    def zone_idx(ls):
        idx = [i for i, l in enumerate(ls) if l.strip()]
        return set(idx[:zone] + idx[-zone:])
    from . import b1_v3
    def structural(l):
        return bool(b1_v3.CONT.search(l.strip())) or bool(b1_v3.Classifier().classify(l))
    cand = []                                         # (page, line idx, key, 구역: 'top'|'bottom'|None)
    for pi, ls in enumerate(lines):
        idx = [i for i, l in enumerate(ls) if l.strip()]
        top, bot = set(idx[:zone]), set(idx[-zone:])
        for i, l in enumerate(ls):
            if not l.strip() or l.lstrip().startswith('|') or structural(l): continue
            cand.append((pi, i, _key(l) if i in bot and i not in top else _exact(l), 'bottom' if i in bot else 'top' if i in top else None))
    drop = {}
    # 쪽 번호 줄(위·아래 구역)
    for pi, i, k, inz in cand:
        if inz and PAGENUM.match(re.sub(r'^#+\s*', '', lines[pi][i])): drop[(pi, i)] = 'page_number'
    if n >= 2:
        # 워터마크: 라틴 대문자만의 짧은 줄이 2쪽 이상 어디에든
        wm = {}
        for pi, i, k, inz in cand:
            t = re.sub(r'^[#\s\-*]+', '', lines[pi][i]).strip()
            if WATERMARK.match(t): wm.setdefault(t, set()).add(pi)
        for pi, i, k, inz in cand:
            t = re.sub(r'^[#\s\-*]+', '', lines[pi][i]).strip()
            if t in wm and len(wm[t]) >= 2: drop[(pi, i)] = 'watermark'
        # 반복 머리말·꼬리말: 위·아래 구역의 줄이 (거의) 같은 꼴로 2쪽 이상
        zc = [(pi, i, (z, k)) for pi, i, k, z in cand if z and len(k) >= 3]
        groups = []
        for pi, i, k in zc:
            for g in groups:
                if k[0] == g['k'][0] and difflib.SequenceMatcher(None, k[1], g['k'][1]).ratio() >= 0.85: g['m'].append((pi, i)); break
            else: groups.append(dict(k=k, m=[(pi, i)]))
        for g in groups:
            if len({pi for pi, _ in g['m']}) >= 2:
                for pi, i in g['m']: drop.setdefault((pi, i), 'running_header_footer')
    # 보호: 문서 제목·번호 줄의 1쪽 첫 등장
    kept = set()
    for (pi, i), why in sorted(drop.items()):
        l = lines[pi][i]
        hit = [j for j, x in enumerate(protect) if x and N(x) and N(x) in N(l) and j not in kept]
        if why == 'running_header_footer' and pi == 0 and hit:
            kept.update(hit); del drop[(pi, i)]
            log.append(dict(page=pi + 1, kind='noise_keep', category='보존', before=l, after=l, evidence='문서 제목·번호 줄의 첫 등장은 남김'))
    for (pi, i), why in sorted(drop.items()):
        log.append(dict(page=pi + 1, kind='noise_remove', category=why, before=lines[pi][i], after=''))
    out = []
    for pi, ls in enumerate(lines):
        keep = [l for i, l in enumerate(ls) if (pi, i) not in drop]
        out.append(re.sub(r'\n{3,}', '\n\n', '\n'.join(keep)).strip('\n'))
    return out


def clean_doc(doc_id, pages, source_lines):
    log = []
    out = []
    for i, t in enumerate(pages, 1):
        t2 = ENT.sub(lambda m: html.unescape(m.group(0)), t)
        if t2 != t:
            for a, b in zip(t.split('\n'), t2.split('\n')):
                if a != b: log.append(dict(page=i, kind='html_unescape', category='표현', before=a, after=b))
        if i in source_lines:
            t2 = fix_lists(i, t2, log, source_lines[i])
        out.append(t2)
    head = '\n\n'.join(out[:P.HEAD_PAGES]); number, _ = P.extract_number(head); title, _ = P.extract_title(head, number)
    out = remove_noise(doc_id, out, [title, number], log)
    return out, log


