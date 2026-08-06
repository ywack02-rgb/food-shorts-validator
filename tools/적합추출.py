#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""평가결과.md에서 「적합」 판정만 뽑아 촬영대기.md를 생성한다.

사용법:
    python3 tools/적합추출.py

동작:
    평가결과.md (읽기)  +  소재메모.md (읽기)  →  촬영대기.md (덮어쓰기)

평가결과.md는 절대 수정하지 않는다. 촬영대기.md는 매번 통째로 다시 만드는
파생 파일이므로, 여기에 손으로 메모를 적으면 다음 실행 때 지워진다.

판정 라인 표기가 회차마다 다르므로(✅/○/이모지 없음 등) 정규식으로 흡수한다.
「부적합」에 「적합」이 포함되므로 판정 분류는 반드시 부적합 → 보류 → 적합 순서로
검사한다.
"""

import io
import os
import re
import sys
from collections import OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_RESULT = os.path.join(ROOT, '평가결과.md')
SRC_MEMO = os.path.join(ROOT, '소재메모.md')
OUT = os.path.join(ROOT, '촬영대기.md')

# ── 판정 ────────────────────────────────────────────────────────────────
VERDICT_LINE = re.compile(r'^\**\s*판정\s*\**\s*[:：].*$', re.M)
# 재검증 회차(R1~)는 「기존: 보류(60점) → 재판정: ✅ 적합(72점)」 형식을 쓴다.
# 이 줄은 「판정:」으로 시작하지 않으므로 별도로 잡고, 분류·점수는 「재판정」 뒤쪽만 본다.
REVERDICT_LINE = re.compile(r'^\**.*?재판정\s*\**\s*[:：].*$', re.M)
# 회차 구분 헤더에서 날짜를 뽑는다 (## 검증 실행: 2026-06-12 (1~10번) 등)
BATCH_HEAD = re.compile(r'^##\s*(?:검증 실행|.*?회차).*$', re.M)
DATE = re.compile(r'(20\d\d-\d\d-\d\d)')

# ── 대본 필드 ───────────────────────────────────────────────────────────
def field(body, *names):
    """■ 필드명: 값  형태에서 값을 뽑는다. 다음 ■ 또는 빈 줄 전까지."""
    for name in names:
        pat = re.compile(
            r'^■\s*\**\s*' + re.escape(name) + r'\s*\**\s*[:：]\s*(.*?)'
            r'(?=^■|^\*\*[^\n]*\*\*\s*$|^---|\Z)', re.M | re.S)
        m = pat.search(body)
        if m:
            v = m.group(1).strip()
            if v:
                return re.sub(r'\n{3,}', '\n\n', v)
    return ''


HEADLINE3 = re.compile(
    r'^\**[★\s]*후킹 헤드라인 3개[^\n]*\n(.*?)(?=^\**[★\s]*(?:대본|점수|사유|운영자|보강)|^■|^---|\Z)',
    re.M | re.S)


def headlines3(body):
    m = HEADLINE3.search(body)
    if not m:
        return []
    out = []
    for ln in m.group(1).split('\n'):
        ln = ln.strip()
        if not ln:
            continue
        ln = re.sub(r'^\**\s*(?:\d+[.)]|[-*•])\s*', '', ln).strip()
        ln = ln.strip('*').strip().strip('"').strip('"').strip('"')
        if ln:
            out.append(ln)
    return out[:3]


def score_of(body, vline):
    """점수를 뽑는다. 판정 라인의 (NN점 → 합계 표 → '합계 NN점' 순으로 신뢰."""
    m = re.search(r'\((\d{1,3})\s*점', vline)
    if m and 0 <= int(m.group(1)) <= 100:
        return int(m.group(1))
    m = re.search(r'\*\*합계\*\*\s*\|\s*\*\*(\d{1,3})\s*점', body)
    if m:
        return int(m.group(1))
    m = re.search(r'합계\s*[:：|]?\s*\**\s*(\d{1,3})\s*점', body)
    if m and 0 <= int(m.group(1)) <= 100:
        return int(m.group(1))
    return None


# ── 제품명 정규화 (중복 판별용) ────────────────────────────────────────
LEAD_NUM = re.compile(r'^(?:R\d+\s+)?(?:#?\d+\s*[.)]?\s*)?(?:—\s*)?')
ORIG_NAME = re.compile(r'\s*—\s*[「『].*$')


def product_name(head):
    s = LEAD_NUM.sub('', head)
    s = ORIG_NAME.sub('', s)
    return s.strip(' —-–').strip()


def norm_key(name):
    s = re.sub(r'[\s(){}\[\]「」『』&+·,./·]', '', name)
    s = re.sub(r'\d+(?:g|kg|ml|개입|매입|봉지?|박스|줄|연|종)', '', s)
    return s.lower()


def similar_groups(entries):
    """제품명이 서로 포함 관계인 항목을 묶는다.

    「가루비 포테이토 팜 쟈가폿쿠루」와 「가루비 포테이토 팜 쟈가폿쿠루 감자스낵」처럼
    사실상 같은 제품이 표기만 달라 따로 적합을 받은 경우를 잡기 위한 것이다.
    완전일치(norm_key)만으로는 안 걸린다. 포함 관계는 추정이므로 결과는 「확인 필요」로
    표시하고 자동으로 지우거나 합치지 않는다.
    """
    keys = [(e, norm_key(e['name'])) for e in entries]
    parent = {id(e): id(e) for e, _ in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, (ea, ka) in enumerate(keys):
        for eb, kb in keys[i + 1:]:
            if len(ka) < 8 or len(kb) < 8:
                continue
            if ka in kb or kb in ka:
                union(id(ea), id(eb))
    buckets = defaultdict(list)
    by_id = {id(e): e for e, _ in keys}
    for e, _ in keys:
        buckets[find(id(e))].append(e)
    return [sorted(g, key=lambda x: x['line'])
            for g in buckets.values() if len(g) > 1]


def parse_entries(path):
    lines = io.open(path, encoding='utf-8').read().split('\n')
    heads = [i for i, l in enumerate(lines) if l.startswith('### ')]
    # 각 엔트리 앞쪽에서 가장 가까운 회차 헤더의 날짜를 찾아둔다
    batch_date = {}
    cur = ''
    for i, l in enumerate(lines):
        if BATCH_HEAD.match(l):
            d = DATE.search(l)
            if d:
                cur = d.group(1)
        batch_date[i] = cur

    bounds = heads + [len(lines)]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        head = lines[a][4:].strip()
        body = '\n'.join(lines[a:b])
        m = REVERDICT_LINE.search(body)
        if m:
            # 「기존: … → 재판정: …」 — 화살표 뒤(재판정 이후)만 현재 판정이다
            vline = m.group(0)
            judged = vline.split('재판정', 1)[1]
        else:
            m = VERDICT_LINE.search(body)
            if not m:
                continue
            vline = m.group(0)
            judged = vline
        head_part = re.sub(r'\(.*', '', judged)
        if '부적합' in head_part:
            cat = '부적합'
        elif '보류' in head_part:
            cat = '보류'
        elif '적합' in head_part:
            cat = '적합'
        else:
            continue
        out.append(dict(
            line=a + 1, head=head, body=body, cat=cat, vline=vline.strip(),
            score=score_of(body, judged), date=batch_date.get(a, ''),
            name=product_name(head),
        ))
    return out


def memo_fits(path):
    """소재메모.md에서 적합으로 표시된 줄을 뽑는다. 재검증(🔁) 결과를 우선한다."""
    rows = []
    for i, raw in enumerate(io.open(path, encoding='utf-8').read().split('\n'), 1):
        if not raw.startswith('- [x] '):
            continue
        # 재검증 화살표가 있으면 그 뒤의 판정이 현재 판정이다
        cur = raw.split('🔁')[-1] if '🔁' in raw else raw
        tail = cur.rsplit('·', 1)[-1].strip() if '·' in cur else ''
        if tail.startswith('적합'):
            name = re.sub(r'^- \[x\]\s*', '', raw).split('✅')[0].strip()
            rows.append((i, name, '🔁' in raw))
    return rows


def main():
    entries = parse_entries(SRC_RESULT)
    fits = [e for e in entries if e['cat'] == '적합']

    # 재검증 엔트리(R로 시작)가 있으면 같은 제품의 옛 엔트리는 구판정으로 표시
    revalidated = {norm_key(e['name']) for e in entries
                   if re.match(r'^R\d+\s', e['head'])}
    for e in fits:
        e['is_reval'] = bool(re.match(r'^R\d+\s', e['head']))
        e['superseded'] = (not e['is_reval']) and norm_key(e['name']) in revalidated

    # 같은 제품이 여러 번 적합을 받은 경우 묶는다 (같은 영상 두 번 찍는 사고 방지)
    groups = defaultdict(list)
    for e in fits:
        groups[norm_key(e['name'])].append(e)
    for k, g in groups.items():
        for e in g:
            e['dupe'] = len(g) if len(g) > 1 else 0

    for e in fits:
        e['headlines'] = headlines3(e['body'])
        e['hook'] = field(e['body'], '첫 3초 후킹 문장', '첫 3초 후킹')
        e['title'] = field(e['body'], '제목(헤드라인)', '제목', '제목(후보)')
        e['script'] = field(e['body'], '대본 (약 300자)', '대본', '대본 (200~400자)')
        if not e['script']:
            m = re.search(r'^■\s*\**\s*대본[^\n]*[:：]?\s*\n(.*?)(?=^■|\Z)',
                          e['body'], re.M | re.S)
            e['script'] = m.group(1).strip() if m else ''
        e['numbers'] = field(e['body'], '사용한 핵심 수치', '핵심 수치')
        e['todo'] = field(e['body'], '[확인 필요] 항목', '[확인 필요]')
        e['has_script'] = bool(e['script'])
        m = re.search(r'^\**[★\s]*운영자 조치[^\n]*[:：]\**\s*(.*?)(?=^---|\Z)',
                      e['body'], re.M | re.S)
        e['action'] = m.group(1).strip() if m else ''
        # 「브랜드 통째 삭제」는 대기열의 그 브랜드 줄을 지우라는 뜻이지, 이 적합 대본을
        # 버리라는 뜻이 아니다. 오히려 이 한 편이 그 브랜드 축을 소진한다는 신호다.
        e['last_of_brand'] = bool(
            re.search(r'브랜드 통째 삭제', e['body'])
            and not re.search(r'브랜드 통째 삭제\s*(?:는|도|를)?\s*(?:이번에도\s*)?'
                              r'(?:절대\s*)?금지', e['body']))

    def sortkey(e):
        return (-(e['score'] if e['score'] is not None else -1), e['line'])

    fits.sort(key=sortkey)
    live = [e for e in fits if not e['superseded']]
    tier_a = [e for e in live if e['score'] is not None and e['score'] >= 70]
    tier_b = [e for e in live if e['score'] is not None and 60 <= e['score'] < 70]
    tier_c = [e for e in live if e['score'] is None or e['score'] < 60]

    W = []
    w = W.append
    w('# 촬영 대기 목록 (적합 판정 전체)')
    w('')
    w('> **이 파일은 `tools/적합추출.py`가 자동 생성한다. 직접 수정하지 말 것 —**')
    w('> **다음 실행 때 통째로 덮어쓴다.** 갱신: `python3 tools/적합추출.py`')
    w('> 원본은 `평가결과.md`이며, 각 항목의 `평가결과.md L####`가 원문 위치다.')
    w('')
    w('## 요약')
    w('')
    w('| 구분 | 건수 |')
    w('|---|---|')
    w('| 전체 판정 엔트리 | %d건 |' % len(entries))
    w('| **적합** | **%d건** |' % len(fits))
    w('| 보류 | %d건 |' % sum(1 for e in entries if e['cat'] == '보류'))
    w('| 부적합 | %d건 |' % sum(1 for e in entries if e['cat'] == '부적합'))
    w('| 적합 중 재검증으로 대체된 구판정 | %d건 |' % sum(1 for e in fits if e['superseded']))
    w('| **촬영 후보(구판정 제외)** | **%d건** |' % len(live))
    w('| ├ 70점 이상 | %d건 |' % len(tier_a))
    w('| ├ 60~69점 | %d건 |' % len(tier_b))
    w('| └ 60점 미만·점수 미기재 | %d건 |' % len(tier_c))
    w('| 대본이 있는 항목 | %d건 |' % sum(1 for e in live if e['has_script']))
    w('')
    w('**기호**')
    w('')
    w('- 🔁 재검증으로 새로 판정된 항목')
    w('- 👥 같은 제품이 여러 번 적합을 받음 — 한 편만 찍고 나머지 줄은 삭제')
    w('- 🎬 이 브랜드에 「통째 삭제」 지시가 발행된 항목 — **대본을 버리라는 뜻이 아니다.**')
    w('  대기열의 그 브랜드 줄을 지우라는 뜻이고, **이 한 편이 그 브랜드의 마지막 영상**이 된다.')
    w('')

    def index_table(title, rows):
        if not rows:
            return
        w('### %s' % title)
        w('')
        w('| 점수 | 제품 | 기호 | 대본 | 판정일 | 원문 |')
        w('|---:|---|---|---|---|---|')
        for e in rows:
            flags = ''.join([
                '🔁' if e['is_reval'] else '',
                '👥%d' % e['dupe'] if e['dupe'] else '',
                '🎬' if e['last_of_brand'] else '',
            ]) or '—'
            w('| %s | %s | %s | %s | %s | L%d |' % (
                e['score'] if e['score'] is not None else '—',
                e['name'][:52], flags,
                '○' if e['has_script'] else '×',
                e['date'] or '—', e['line']))
        w('')

    w('## 색인')
    w('')
    index_table('A. 70점 이상 — 즉시 촬영 후보', tier_a)
    index_table('B. 60~69점 — 적합(보강 1개)', tier_b)
    index_table('C. 60점 미만 / 점수 미기재 — 초기 회차 표기', tier_c)

    dupes = OrderedDict()
    for e in live:
        if e['dupe']:
            dupes.setdefault(norm_key(e['name']), []).append(e)
    if dupes:
        w('## 👥 중복 경고 — 같은 제품이 여러 번 적합을 받았다')
        w('')
        w('한 편만 찍고 나머지 줄은 삭제해야 하는 묶음이다.')
        w('')
        for g in dupes.values():
            w('- **%s** — %s' % (
                g[0]['name'],
                ' / '.join('L%d(%s점, %s)' % (
                    x['line'], x['score'] if x['score'] is not None else '—',
                    x['date'] or '?') for x in g)))
        w('')

    sims = similar_groups(live)
    # 완전일치로 이미 잡힌 묶음은 제외
    dupe_ids = {id(x) for g in dupes.values() for x in g}
    sims = [g for g in sims if not all(id(x) in dupe_ids for x in g)]
    if sims:
        w('## 🔍 유사 제품군 — 표기만 다른 같은 제품인지 확인할 것')
        w('')
        w('제품명이 서로 포함 관계인 항목이다. 추정이므로 자동으로 합치지 않았다.')
        w('')
        for g in sims:
            w('- ' + ' / '.join('**%s** (%s점, L%d)' % (
                x['name'], x['score'] if x['score'] is not None else '—', x['line'])
                for x in g))
        w('')

    w('---')
    w('')
    w('## 상세')
    w('')
    for tier_name, rows in (('A. 70점 이상', tier_a), ('B. 60~69점', tier_b),
                            ('C. 60점 미만 / 미기재', tier_c)):
        if not rows:
            continue
        w('## [%s]' % tier_name)
        w('')
        for e in rows:
            w('### %s%s%s' % (
                '🔁 ' if e['is_reval'] else '',
                e['name'],
                ' 👥중복%d건' % e['dupe'] if e['dupe'] else ''))
            w('')
            w('- **점수** %s / **판정일** %s / **원문** `평가결과.md L%d`' % (
                ('%d점' % e['score']) if e['score'] is not None else '미기재',
                e['date'] or '미기재', e['line']))
            w('- **원문 제목** %s' % e['head'])
            if e['last_of_brand']:
                w('- 🎬 **이 브랜드는 「통째 삭제」 지시 대상 — 이 한 편이 마지막 영상이다.**')
                w('  (대본을 버리라는 뜻이 아니라, 같은 브랜드로 후속편을 기대하지 말라는 뜻이다.)')
            w('')
            if e['headlines']:
                w('**후킹 헤드라인**')
                w('')
                for i, h in enumerate(e['headlines'], 1):
                    w('%d. %s' % (i, h))
                w('')
            if e['hook']:
                w('**첫 3초** %s' % e['hook'].replace('\n', ' '))
                w('')
            if e['script']:
                w('**대본**')
                w('')
                for ln in e['script'].split('\n'):
                    w('> %s' % ln if ln.strip() else '>')
                w('')
            else:
                w('**대본** — 원문에 대본 블록이 없다(초기 회차 형식). `평가결과.md L%d` 확인.' % e['line'])
                w('')
            if e['numbers']:
                w('**핵심 수치** %s' % e['numbers'].replace('\n', ' '))
                w('')
            if e['todo']:
                w('**[확인 필요]**')
                w('')
                w(e['todo'])
                w('')
            w('---')
            w('')

    # ── 소재메모 대조 ────────────────────────────────────────────────
    mf = memo_fits(SRC_MEMO)
    w('## 소재메모.md 대조')
    w('')
    w('소재메모.md에서 적합으로 표시된 줄: **%d건** (그중 재검증 %d건)'
      % (len(mf), sum(1 for _, _, r in mf if r)))
    w('')
    result_keys = {norm_key(e['name']) for e in live}
    memo_keys = {norm_key(n) for _, n, _ in mf}

    def lcs_len(a, b):
        """가장 긴 공통 부분문자열의 길이. 소재메모와 평가결과의 표기 차이를 흡수한다."""
        if not a or not b:
            return 0
        prev = [0] * (len(b) + 1)
        best = 0
        for ca in a:
            cur = [0] * (len(b) + 1)
            for j, cb in enumerate(b, 1):
                if ca == cb:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best:
                        best = cur[j]
            prev = cur
        return best

    def loose(key, pool):
        """완전일치 → 포함관계 → 공통 부분문자열 6자 이상 순으로 대조한다."""
        if key in pool:
            return True
        for other in pool:
            if len(key) >= 6 and len(other) >= 6 and (key in other or other in key):
                return True
            if lcs_len(key, other) >= 6:
                return True
        return False

    only_memo = [(i, n) for i, n, _ in mf if not loose(norm_key(n), result_keys)]
    if only_memo:
        w('### ⚠ 소재메모에는 적합인데 평가결과에서 적합으로 안 잡히는 줄')
        w('')
        w('표기 차이(공백·번역명 등)일 수도 있고, 실제 불일치일 수도 있다. 눈으로 확인할 것.')
        w('')
        for i, n in only_memo:
            w('- 소재메모.md L%d — %s' % (i, n))
        w('')
    only_res = [e for e in live if not loose(norm_key(e['name']), memo_keys)]
    if only_res:
        w('### ⚠ 평가결과는 적합인데 소재메모에 적합 표시가 없는 항목')
        w('')
        for e in only_res:
            w('- %s (`평가결과.md L%d`)' % (e['name'], e['line']))
        w('')

    io.open(OUT, 'w', encoding='utf-8').write('\n'.join(W).rstrip() + '\n')
    print('촬영대기.md 생성 완료')
    print('  전체 판정 %d건 / 적합 %d건 (구판정 제외 %d건)'
          % (len(entries), len(fits), len(live)))
    print('  70점+ %d · 60~69 %d · 그 외 %d · 대본 있음 %d'
          % (len(tier_a), len(tier_b), len(tier_c),
             sum(1 for e in live if e['has_script'])))
    if dupes:
        print('  중복 경고 %d묶음' % len(dupes))
    return 0


if __name__ == '__main__':
    sys.exit(main())
