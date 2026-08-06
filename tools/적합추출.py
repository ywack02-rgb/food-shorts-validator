#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""평가결과.md에서 적합 항목을 뽑아 촬영대기.md(목록)를 생성한다.

사용법:
    python3 tools/적합추출.py

동작:
    평가결과.md (읽기)  +  소재메모.md (읽기)  →  촬영대기.md (덮어쓰기)

파일 역할 (2026-08-06 확정):
    소재메모.md   중복 검증 여부를 판단하는 원장. 검증한 제품은 `- [x]` + 판정 표시.
    평가결과.md   적합 판정된 제품의 정보와 결과(대본 등) 본문.
    촬영대기.md   적합 목록. 제품명 · 적합여부 · 검증날짜 세 항목만. 자동 생성.

평가결과.md는 절대 수정하지 않는다. 촬영대기.md는 매번 통째로 다시 만드는 파생
파일이므로 손으로 적은 메모는 다음 실행 때 지워진다.

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

VERDICT_LINE = re.compile(r'^\**\s*판정\s*\**\s*[:：].*$', re.M)
# 재검증 회차(R1~)는 「기존: 보류(60점) → 재판정: ✅ 적합(72점)」 형식을 쓴다.
# 이 줄은 「판정:」으로 시작하지 않으므로 별도로 잡고, 분류는 「재판정」 뒤쪽만 본다.
REVERDICT_LINE = re.compile(r'^\**.*?재판정\s*\**\s*[:：].*$', re.M)
BATCH_HEAD = re.compile(r'^##\s*(?:검증 실행|.*?회차).*$', re.M)
DATE = re.compile(r'(20\d\d-\d\d-\d\d)')

# 제품명 정규화 — 「152. 」 「#464 」 「R2 #464 」 같은 머리번호와 「— 「원어명」」 꼬리를 뗀다
LEAD_NUM = re.compile(r'^(?:R\d+\s+)?(?:#?\d+\s*[.)]?\s*)?(?:—\s*)?')
ORIG_NAME = re.compile(r'\s*—\s*[「『].*$')


def product_name(head):
    return ORIG_NAME.sub('', LEAD_NUM.sub('', head)).strip(' —-–').strip()


def norm_key(name):
    s = re.sub(r'[\s(){}\[\]「」『』&+·,./·]', '', name)
    s = re.sub(r'\d+(?:g|kg|ml|개입|매입|봉지?|박스|줄|연|종)', '', s)
    return s.lower()


def parse_entries(path):
    lines = io.open(path, encoding='utf-8').read().split('\n')
    heads = [i for i, l in enumerate(lines) if l.startswith('### ')]
    batch_date, cur = {}, ''
    for i, l in enumerate(lines):
        if BATCH_HEAD.match(l):
            d = DATE.search(l)
            if d:
                cur = d.group(1)
        batch_date[i] = cur

    bounds = heads + [len(lines)]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        body = '\n'.join(lines[a:b])
        m = REVERDICT_LINE.search(body)
        if m:
            judged = m.group(0).split('재판정', 1)[1]   # 화살표 뒤가 현재 판정
            is_reval = True
        else:
            m = VERDICT_LINE.search(body)
            if not m:
                continue
            judged = m.group(0)
            is_reval = False
        head_part = re.sub(r'\(.*', '', judged)
        if '부적합' in head_part:
            cat = '부적합'
        elif '보류' in head_part:
            cat = '보류'
        elif '적합' in head_part:
            cat = '적합'
        else:
            continue
        head = lines[a][4:].strip()
        out.append(dict(line=a + 1, head=head, cat=cat, is_reval=is_reval,
                        date=batch_date.get(a, ''), name=product_name(head)))
    return out


def memo_fits(path):
    """소재메모.md에서 현재 판정이 적합인 줄을 뽑는다. 재검증(🔁) 결과를 우선한다."""
    rows = []
    for i, raw in enumerate(io.open(path, encoding='utf-8').read().split('\n'), 1):
        if not raw.startswith('- [x] '):
            continue
        cur = raw.split('🔁')[-1] if '🔁' in raw else raw
        tail = cur.rsplit('·', 1)[-1].strip() if '·' in cur else ''
        if tail.startswith('적합'):
            name = re.sub(r'^- \[x\]\s*', '', raw).split('✅')[0].strip()
            rows.append((i, name, '🔁' in raw))
    return rows


def lcs_len(a, b):
    """가장 긴 공통 부분문자열 길이. 두 파일의 표기 차이를 흡수하는 데 쓴다."""
    if not a or not b:
        return 0
    prev, best = [0] * (len(b) + 1), 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def loose(key, pool):
    """완전일치 → 포함관계 → 공통 부분문자열 6자 이상 순으로 대조."""
    if key in pool:
        return True
    for other in pool:
        if len(key) >= 6 and len(other) >= 6 and (key in other or other in key):
            return True
        if lcs_len(key, other) >= 6:
            return True
    return False


def similar_groups(entries):
    """제품명이 서로 포함 관계인 항목을 묶는다.

    「가루비 포테이토 팜 쟈가폿쿠루」와 「… 쟈가폿쿠루 감자스낵」처럼 사실상 같은
    제품이 표기만 달라 따로 적합을 받은 경우를 잡는다. 추정이므로 자동으로 합치거나
    지우지 않고 「확인 필요」로만 보여준다.
    """
    keys = [(e, norm_key(e['name'])) for e in entries]
    parent = {id(e): id(e) for e, _ in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, (ea, ka) in enumerate(keys):
        for eb, kb in keys[i + 1:]:
            if len(ka) < 8 or len(kb) < 8:
                continue
            if ka in kb or kb in ka:
                ra, rb = find(id(ea)), find(id(eb))
                if ra != rb:
                    parent[rb] = ra
    buckets = defaultdict(list)
    for e, _ in keys:
        buckets[find(id(e))].append(e)
    return [sorted(g, key=lambda x: x['line'])
            for g in buckets.values() if len(g) > 1]


def main():
    entries = parse_entries(SRC_RESULT)
    fits = [e for e in entries if e['cat'] == '적합']
    others = [e for e in entries if e['cat'] != '적합']
    fits.sort(key=lambda e: (e['date'] or '', e['line']))

    # 완전일치 중복
    groups = defaultdict(list)
    for e in fits:
        groups[norm_key(e['name'])].append(e)
    dupes = OrderedDict((k, g) for k, g in groups.items() if len(g) > 1)

    W = []
    w = W.append
    w('# 촬영 대기 목록 (적합 판정)')
    w('')
    w('> **자동 생성 파일 — 직접 수정하지 말 것.** 다음 실행 때 통째로 덮어쓴다.')
    w('> 갱신: `python3 tools/적합추출.py`')
    w('>')
    w('> 제품의 상세 정보·대본은 `평가결과.md`에 있다.')
    w('> 검증했는지 여부(중복 확인)는 `소재메모.md`에서 본다.')
    w('')
    w('**적합 %d건** (재검증으로 승격된 항목 %d건 포함)'
      % (len(fits), sum(1 for e in fits if e['is_reval'])))
    w('')
    w('| 제품명 | 적합여부 | 검증날짜 |')
    w('|---|---|---|')
    for e in fits:
        w('| %s | 적합%s | %s |' % (
            e['name'], ' (재검증)' if e['is_reval'] else '', e['date'] or '미기재'))
    w('')

    if dupes:
        w('---')
        w('')
        w('## 👥 중복 — 같은 제품이 두 번 적합을 받았다')
        w('')
        w('한 편만 찍고 나머지 줄은 삭제할 것.')
        w('')
        for g in dupes.values():
            w('- **%s** — %s' % (g[0]['name'],
                                 ' / '.join('%s' % (x['date'] or '?') for x in g)))
        w('')

    sims = similar_groups(fits)
    dupe_ids = {id(x) for g in dupes.values() for x in g}
    sims = [g for g in sims if not all(id(x) in dupe_ids for x in g)]
    if sims:
        w('---')
        w('')
        w('## 🔍 유사 제품군 — 표기만 다른 같은 제품인지 확인할 것')
        w('')
        w('제품명이 서로 포함 관계인 항목이다. 추정이므로 자동으로 합치지 않았다.')
        w('')
        for g in sims:
            w('- ' + ' / '.join('**%s** (%s)' % (x['name'], x['date'] or '?')
                                for x in g))
        w('')

    # ── 소재메모 대조 ────────────────────────────────────────────────
    mf = memo_fits(SRC_MEMO)
    result_keys = {norm_key(e['name']) for e in fits}
    memo_keys = {norm_key(n) for _, n, _ in mf}
    only_memo = [(i, n) for i, n, _ in mf if not loose(norm_key(n), result_keys)]
    only_res = [e for e in fits if not loose(norm_key(e['name']), memo_keys)]

    w('---')
    w('')
    w('## 소재메모.md 대조')
    w('')
    w('소재메모.md에서 적합으로 표시된 줄: **%d건** (그중 재검증 %d건)'
      % (len(mf), sum(1 for _, _, r in mf if r)))
    w('')
    if only_memo:
        w('### ⚠ 소재메모는 적합인데 평가결과에서 안 잡히는 줄')
        w('')
        w('표기 차이일 수도, 실제 불일치일 수도 있다. 눈으로 확인할 것.')
        w('')
        for i, n in only_memo:
            w('- 소재메모.md L%d — %s' % (i, n))
        w('')
    if only_res:
        w('### ⚠ 평가결과는 적합인데 소재메모에 적합 표시가 없는 항목')
        w('')
        for e in only_res:
            w('- %s' % e['name'])
        w('')
    if not only_memo and not only_res:
        w('두 파일의 적합 판정이 일치한다.')
        w('')

    if others:
        w('---')
        w('')
        w('## ⚠ 평가결과.md에 적합이 아닌 항목이 섞여 있다')
        w('')
        w('`평가결과.md`는 적합 전용이다. 아래 항목은 잘못 들어간 것이므로 확인할 것.')
        w('')
        for e in others:
            w('- [%s] %s (`평가결과.md L%d`)' % (e['cat'], e['name'], e['line']))
        w('')

    io.open(OUT, 'w', encoding='utf-8').write('\n'.join(W).rstrip() + '\n')
    print('촬영대기.md 생성 완료 — 적합 %d건 (재검증 승격 %d건)'
          % (len(fits), sum(1 for e in fits if e['is_reval'])))
    if dupes:
        print('  👥 중복 %d묶음' % len(dupes))
    if sims:
        print('  🔍 유사 제품군 %d묶음' % len(sims))
    if only_memo or only_res:
        print('  ⚠ 소재메모 대조 불일치 %d건' % (len(only_memo) + len(only_res)))
    if others:
        print('  ⚠ 평가결과.md에 적합 아닌 항목 %d건이 섞여 있다' % len(others))
    return 0


if __name__ == '__main__':
    sys.exit(main())
