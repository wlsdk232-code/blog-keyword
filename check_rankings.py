#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 블로그(giant7000) 포스팅 검색 순위 자동 점검 스크립트
-------------------------------------------------------------
1) 네이버에서 내 블로그 글 목록(제목/글번호/날짜)을 자동으로 가져옵니다.
2) 각 글의 핵심 키워드로 네이버 검색 API를 돌려, 그 글이 검색결과 몇 위인지 찾습니다.
   (글번호 logNo 로 정확히 매칭)
3) 결과를 report.html (보기 좋은 표) 로 저장하고, data.json 에 이력을 누적합니다.

필요 환경변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET  (.env 파일 또는 환경변수)

사용 예:
  python check_rankings.py              # 최근 24개월 글 점검
  python check_rankings.py --months 1   # 최근 1개월(월간 자동화용)
  python check_rankings.py --all        # 전체 글
  python check_rankings.py --limit 10   # 테스트용 10개만
"""

import os
import re
import sys
import json
import time
import html
import argparse
import datetime
import urllib.parse
import urllib.request

BLOG_ID = "giant7000"
BLOGGER_LINK = "blog.naver.com/giant7000"
SEARCH_API = "https://openapi.naver.com/v1/search/blog.json"
LIST_API = "https://blog.naver.com/PostTitleListAsync.naver"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")
REPORT_FILE = os.path.join(BASE_DIR, "report.html")
OVERRIDE_FILE = os.path.join(BASE_DIR, "keyword_overrides.csv")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 키워드 추출 시, 이 단어가 나오면 '핵심 키워드는 여기까지'로 보고 자릅니다.
STOP_WORDS = {
    "꼭", "필요한", "필요", "무엇", "무엇이", "어떻게", "왜", "이렇게", "이제", "이런", "이거",
    "방법", "이유", "총정리", "가이드", "체크포인트", "체크리스트", "줄이는", "높이는", "늘리는",
    "위한", "위해", "위해서", "할", "하는", "해야", "되는", "안", "못", "진행", "준비", "고려",
    "확인", "알아야", "알아두면", "첫", "성공", "실패", "효율적인", "효율적", "합리적인", "합리적",
    "제대로", "차이", "비교", "핵심", "정리", "노하우", "전략", "포인트", "사항", "요소", "조건",
    "방향", "중요한", "중요성", "그리고", "전", "후", "때", "경우", "싶다면", "한다면", "하려면",
    "이라면", "할까", "할까요", "달라야", "달라지는", "놓치기", "줄이기", "높이기", "막막하다면",
    "지금", "이것만", "이것", "그", "더", "어떤", "낭비", "운영", "운영까지", "고려해야",
    "시대의", "꼭필요한", "않으려면", "하지", "보다", "먼저",
}


def load_env():
    """같은 폴더의 .env 파일을 읽어 환경변수로 올립니다."""
    envpath = os.path.join(BASE_DIR, ".env")
    if os.path.exists(envpath):
        with open(envpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_keys():
    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if not cid or not csec:
        print("[오류] NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 가 설정되지 않았습니다.")
        print("       .env 파일을 만들거나 환경변수로 키를 넣어주세요.")
        sys.exit(1)
    return cid, csec


def strip_tags(s):
    return re.sub(r"<.*?>", "", s or "")


def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_all_posts():
    """네이버 비공개 글목록 엔드포인트로 전체 글(제목/글번호/날짜)을 수집."""
    posts = []
    page = 1
    per = 30
    while True:
        params = urllib.parse.urlencode({
            "blogId": BLOG_ID, "viewdate": "", "currentPage": page,
            "countPerPage": per, "categoryNo": 0, "parentCategoryNo": "",
        })
        try:
            raw = http_get(LIST_API + "?" + params,
                           headers={"Referer": "https://blog.naver.com/" + BLOG_ID})
        except Exception as e:
            print("[경고] 글목록 %d페이지 수집 실패: %s" % (page, e))
            break
        # 네이버 응답이 비표준 JSON 인 경우가 있어 정규식으로 안전하게 파싱
        # (title/addDate 는 URL 인코딩되어 있어 따옴표가 들어있지 않음)
        pairs = re.findall(r'"logNo":"(\d+)","title":"([^"]*)"', raw)
        dates = re.findall(r'"addDate":"([^"]*)"', raw)
        if not pairs:
            break
        for i, (log_no, enc_title) in enumerate(pairs):
            title = urllib.parse.unquote_plus(enc_title).strip()
            date = dates[i].strip() if i < len(dates) else ""
            posts.append({"logNo": log_no, "title": title, "date": date})
        if len(pairs) < per:
            break
        page += 1
        time.sleep(0.2)
    return posts


def parse_date(s):
    """'2026. 6. 12.' -> date"""
    nums = re.findall(r"\d+", s or "")
    if len(nums) >= 3:
        try:
            return datetime.date(int(nums[0]), int(nums[1]), int(nums[2]))
        except ValueError:
            return None
    return None


def months_ago(d, months):
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    return datetime.date(y, m, 1)


def load_overrides():
    """logNo,keyword 형태의 CSV. 자동 추출이 틀린 글은 여기서 키워드를 직접 지정."""
    ov = {}
    if os.path.exists(OVERRIDE_FILE):
        with open(OVERRIDE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.lower().startswith("logno"):
                    continue
                parts = line.split(",", 1)
                if len(parts) == 2 and parts[0].strip():
                    ov[parts[0].strip()] = parts[1].strip()
    return ov


def extract_keyword(title):
    """제목에서 핵심 검색 키워드를 추론 (앞쪽 명사구 위주, 최대 4어절)."""
    t = re.sub(r"[\[\]\(\)\"'·…|,~]", " ", title)
    kw = []
    for tok in t.split():
        bare = re.sub(r"[^0-9A-Za-z가-힣]", "", tok)
        if not bare:
            continue
        is_stop = (bare in STOP_WORDS
                   or re.match(r"^\d+(가지|개|위|단계|탄)$", bare)
                   or bare.endswith(("까지", "부터")))
        has_q = "?" in tok
        if len(kw) >= 2 and (is_stop or has_q):
            break
        kw.append(bare)
        if has_q:
            break
        if len(kw) >= 4:
            break
    return " ".join(kw) if kw else title.strip()


def search_blog(query, cid, csec, display=100, start=1):
    params = urllib.parse.urlencode({
        "query": query, "display": display, "start": start, "sort": "sim"})
    raw = http_get(SEARCH_API + "?" + params,
                   headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec})
    return json.loads(raw)


def find_rank(log_no, keyword, cid, csec, depth=100):
    """keyword 로 검색해 해당 글(logNo)이 몇 위인지. 없으면 (None, total)."""
    needle = "/" + str(log_no)
    base = 0
    start = 1
    total = None
    while start <= depth and start <= 1000:
        try:
            data = search_blog(keyword, cid, csec, display=100, start=start)
        except Exception as e:
            print("    [경고] 검색 실패(%s): %s" % (keyword, e))
            break
        total = data.get("total")
        items = data.get("items", [])
        if not items:
            break
        for i, it in enumerate(items):
            if needle in it.get("link", ""):
                return base + i + 1, total
        if len(items) < 100:
            break
        base += len(items)
        start += 100
        time.sleep(0.15)
    return None, total


def run(args):
    load_env()
    cid, csec = get_keys()
    overrides = load_overrides()

    print("글 목록 수집 중...")
    posts = fetch_all_posts()
    print("  전체 %d개 글 발견" % len(posts))

    today = datetime.date.today()
    if not args.all:
        cutoff = months_ago(today, args.months)
        filtered = []
        for p in posts:
            d = parse_date(p["date"])
            if d is None or d >= cutoff:
                filtered.append(p)
        posts = filtered
        print("  최근 %d개월 기준 %d개 점검 대상" % (args.months, len(posts)))
    if args.limit:
        posts = posts[: args.limit]

    # 기존 이력 로드
    data = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    today_str = today.isoformat()
    for n, p in enumerate(posts, 1):
        log_no = p["logNo"]
        keyword = overrides.get(log_no) or extract_keyword(p["title"])
        rank, total = find_rank(log_no, keyword, cid, csec, depth=args.depth)
        # 못 찾았고 키워드가 길면, 앞 2어절로 한 번 더 시도
        used_kw = keyword
        if rank is None and len(keyword.split()) > 2:
            short_kw = " ".join(keyword.split()[:2])
            r2, t2 = find_rank(log_no, short_kw, cid, csec, depth=args.depth)
            if r2 is not None:
                rank, total, used_kw = r2, t2, short_kw

        url = "https://blog.naver.com/%s/%s" % (BLOG_ID, log_no)
        rec = data.get(log_no, {})
        rec.update({"title": p["title"], "url": url, "date": p["date"], "keyword": used_kw})
        hist = rec.get("history", [])
        # 같은 날 재실행 시 중복 방지
        hist = [h for h in hist if h.get("checked") != today_str]
        hist.append({"checked": today_str, "rank": rank, "total": total})
        rec["history"] = hist[-24:]  # 최근 24회만 보관
        data[log_no] = rec

        rank_txt = ("%d위" % rank) if rank else "100위 밖"
        print("  [%d/%d] %-9s | %-22s | %s" % (n, len(posts), rank_txt, used_kw[:22], p["title"][:30]))
        time.sleep(0.12)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    generate_html(data)
    print("\n완료! report.html 을 열어보세요.")


# ----------------------------- HTML 리포트 -----------------------------

def rank_class(rank):
    if rank is None:
        return "miss"
    if rank <= 10:
        return "top"
    if rank <= 30:
        return "mid"
    return "low"


def trend_html(hist):
    ranks = [h["rank"] for h in hist if h.get("rank") is not None]
    if len(hist) < 2:
        return '<span class="t-new">NEW</span>'
    cur = hist[-1].get("rank")
    prev = hist[-2].get("rank")
    if cur is None and prev is None:
        return "-"
    if prev is None:
        return '<span class="t-up">신규진입</span>'
    if cur is None:
        return '<span class="t-down">이탈</span>'
    if cur < prev:
        return '<span class="t-up">▲ %d</span>' % (prev - cur)
    if cur > prev:
        return '<span class="t-down">▼ %d</span>' % (cur - prev)
    return '<span class="t-same">-</span>'


def generate_html(data):
    rows = list(data.values())

    def sort_key(r):
        d = parse_date(r.get("date", "")) or datetime.date(1900, 1, 1)
        return d
    rows.sort(key=sort_key, reverse=True)

    total = len(rows)
    exposed = sum(1 for r in rows if r["history"] and r["history"][-1].get("rank"))
    page1 = sum(1 for r in rows if r["history"] and (r["history"][-1].get("rank") or 999) <= 10)
    ranks_now = [r["history"][-1]["rank"] for r in rows if r["history"] and r["history"][-1].get("rank")]
    avg = (sum(ranks_now) / len(ranks_now)) if ranks_now else 0

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    tr = []
    for r in rows:
        h = r["history"][-1] if r["history"] else {"rank": None}
        rank = h.get("rank")
        cls = rank_class(rank)
        rank_txt = ("%d위" % rank) if rank else "100위 밖"
        tr.append(
            '<tr>'
            '<td class="date">%s</td>'
            '<td class="title"><a href="%s" target="_blank">%s</a></td>'
            '<td class="kw">%s</td>'
            '<td class="rank %s">%s</td>'
            '<td class="trend">%s</td>'
            '</tr>' % (
                html.escape(r.get("date", "")),
                html.escape(r.get("url", "")),
                html.escape(r.get("title", "")),
                html.escape(r.get("keyword", "")),
                cls, rank_txt,
                trend_html(r["history"]),
            )
        )

    page = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>디자인펀치 블로그 검색순위 리포트</title>
<style>
:root{{--bd:#e5e7eb;--mut:#6b7280;}}
*{{box-sizing:border-box}}
body{{font-family:-apple-system,'Apple SD Gothic Neo',Pretendard,sans-serif;margin:0;background:#f8fafc;color:#111827}}
.wrap{{max-width:1100px;margin:0 auto;padding:32px 20px 80px}}
h1{{font-size:24px;margin:0 0 6px}}
.sub{{color:var(--mut);font-size:14px;margin-bottom:24px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}}
.card{{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:16px}}
.card .n{{font-size:26px;font-weight:700}}
.card .l{{color:var(--mut);font-size:13px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--bd);border-radius:12px;overflow:hidden}}
th,td{{padding:11px 12px;text-align:left;border-bottom:1px solid var(--bd);font-size:14px;vertical-align:top}}
th{{background:#f1f5f9;font-size:13px;color:#374151;position:sticky;top:0}}
td.date{{white-space:nowrap;color:var(--mut);font-size:13px}}
td.title a{{color:#111827;text-decoration:none}}
td.title a:hover{{text-decoration:underline}}
td.kw{{color:#2563eb;font-size:13px;white-space:nowrap}}
td.rank{{font-weight:700;white-space:nowrap}}
.rank.top{{color:#15803d}} .rank.mid{{color:#b45309}} .rank.low{{color:#6b7280}} .rank.miss{{color:#9ca3af;font-weight:400}}
.t-up{{color:#15803d;font-weight:600}} .t-down{{color:#dc2626;font-weight:600}} .t-new{{color:#2563eb;font-weight:600}} .t-same{{color:#9ca3af}}
.note{{margin-top:18px;color:var(--mut);font-size:12px;line-height:1.6}}
</style></head>
<body><div class="wrap">
<h1>디자인펀치 블로그 검색순위 리포트</h1>
<div class="sub">blog.naver.com/giant7000 · 점검일시 {now}</div>
<div class="cards">
  <div class="card"><div class="n">{total}</div><div class="l">점검한 글</div></div>
  <div class="card"><div class="n">{exposed}</div><div class="l">상위 100위 노출</div></div>
  <div class="card"><div class="n">{page1}</div><div class="l">1페이지(10위 내)</div></div>
  <div class="card"><div class="n">{avg}</div><div class="l">평균 순위</div></div>
</div>
<table>
<thead><tr><th>작성일</th><th>글 제목</th><th>검색 키워드</th><th>현재 순위</th><th>변동</th></tr></thead>
<tbody>
{rows}
</tbody></table>
<div class="note">
※ 순위는 네이버 <b>검색 API(유사도 기준)</b>로 측정한 값으로, 실제 통합검색 화면(광고·인플루언서·스마트블록 포함)과는 다소 차이가 있을 수 있습니다.<br>
※ '검색 키워드'가 어색한 글은 keyword_overrides.csv 에 <code>글번호,키워드</code> 형식으로 추가하면 다음 점검부터 정확해집니다.<br>
※ '변동'은 직전 점검 대비 순위 변화입니다.
</div>
</div></body></html>""".format(
        now=now, total=total, exposed=exposed, page1=page1,
        avg=("%.1f위" % avg if avg else "-"),
        rows="\n".join(tr),
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    ap = argparse.ArgumentParser(description="네이버 블로그 검색순위 점검")
    ap.add_argument("--months", type=int, default=24, help="최근 N개월 글만 점검 (기본 24)")
    ap.add_argument("--all", action="store_true", help="전체 글 점검")
    ap.add_argument("--depth", type=int, default=100, help="검색 깊이(기본 100위까지)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만(테스트용)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
