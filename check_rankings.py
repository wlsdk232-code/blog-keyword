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

BLOG_IDS = ["giant7000", "front_loveme", "math1004love"]   # 추적할 블로그 아이디들
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


def fetch_all_posts(blog_id):
    """네이버 비공개 글목록 엔드포인트로 한 블로그의 전체 글(제목/글번호/날짜)을 수집."""
    posts = []
    page = 1
    per = 30
    while True:
        params = urllib.parse.urlencode({
            "blogId": blog_id, "viewdate": "", "currentPage": page,
            "countPerPage": per, "categoryNo": 0, "parentCategoryNo": "",
        })
        try:
            raw = http_get(LIST_API + "?" + params,
                           headers={"Referer": "https://blog.naver.com/" + blog_id})
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
            posts.append({"logNo": log_no, "title": title, "date": date, "blog": blog_id})
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


def find_rank(blog_id, log_no, keyword, cid, csec, depth=100):
    """keyword 로 검색해 해당 글이 몇 위인지. 없으면 (None, total)."""
    needle = "/" + blog_id + "/" + str(log_no)
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

    today = datetime.date.today()
    cutoff = months_ago(today, args.months)

    print("글 목록 수집 중... (블로그 %d곳)" % len(BLOG_IDS))
    posts = []
    for bid in BLOG_IDS:
        bp = fetch_all_posts(bid)
        kept = [p for p in bp
                if args.all or parse_date(p["date"]) is None or parse_date(p["date"]) >= cutoff]
        print("  [%s] 전체 %d개 / 최근 %d개월 %d개" % (bid, len(bp), args.months, len(kept)))
        posts.extend(kept)
    if args.limit:
        posts = posts[: args.limit]
    print("  점검 대상 총 %d개" % len(posts))

    # 기존 데이터 로드 (posts: 글별 이력, snapshots: 회차별 요약 추이)
    raw = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
    if isinstance(raw, dict) and "posts" in raw:
        posts_db = raw.get("posts", {})
        snapshots = raw.get("snapshots", [])
    else:
        posts_db = raw if isinstance(raw, dict) else {}
        snapshots = []
    # 구버전(단일 블로그·평면 키) 마이그레이션 -> "blogid/logNo" 키
    mig = {}
    for k, v in posts_db.items():
        if "/" not in k:
            v.setdefault("blog", "giant7000")
            mig["giant7000/" + k] = v
        else:
            mig[k] = v
    posts_db = mig

    today_str = today.isoformat()
    for n, p in enumerate(posts, 1):
        bid = p["blog"]
        log_no = p["logNo"]
        key = bid + "/" + log_no
        keyword = overrides.get(key) or overrides.get(log_no) or extract_keyword(p["title"])
        rank, total = find_rank(bid, log_no, keyword, cid, csec, depth=args.depth)
        # 못 찾았고 키워드가 길면, 앞 2어절로 한 번 더 시도
        used_kw = keyword
        if rank is None and len(keyword.split()) > 2:
            short_kw = " ".join(keyword.split()[:2])
            r2, t2 = find_rank(bid, log_no, short_kw, cid, csec, depth=args.depth)
            if r2 is not None:
                rank, total, used_kw = r2, t2, short_kw

        url = "https://blog.naver.com/%s/%s" % (bid, log_no)
        rec = posts_db.get(key, {})
        rec.update({"blog": bid, "title": p["title"], "url": url, "date": p["date"], "keyword": used_kw})
        hist = rec.get("history", [])
        hist = [h for h in hist if h.get("checked") != today_str]   # 같은 날 중복 방지
        hist.append({"checked": today_str, "rank": rank, "total": total})
        rec["history"] = hist[-24:]
        posts_db[key] = rec

        rank_txt = ("%d위" % rank) if rank else "100위 밖"
        print("  [%d/%d] %-13s %-7s | %-18s | %s" % (n, len(posts), bid, rank_txt, used_kw[:18], p["title"][:24]))
        time.sleep(0.12)

    # 기간 지난 글 자동 삭제 (--all 이 아니면 최근 N개월만 유지)
    if not args.all:
        cutoff = months_ago(today, args.months)
        before = len(posts_db)
        posts_db = {k: v for k, v in posts_db.items()
                    if (parse_date(v.get("date", "")) or today) >= cutoff}
        removed = before - len(posts_db)
        if removed:
            print("  기간 지난 글 %d개 삭제 (최근 %d개월만 유지)" % (removed, args.months))

    # 이번 회차 요약 스냅샷 적립 (전체 + 블로그별, 상단 카드 추이 그래프용)
    cur = [build_row(v) for v in posts_db.values()]

    def _metrics(rows):
        rk = [r["rank"] for r in rows if r["exposed"]]
        return {"total": len(rows),
                "exposed": sum(1 for r in rows if r["exposed"]),
                "page1": sum(1 for r in rows if r["top10"]),
                "avg": round(sum(rk) / len(rk), 1) if rk else 0}

    snap = {"checked": today_str, "overall": _metrics(cur), "blogs": {}}
    for b in BLOG_IDS:
        rb = [r for r in cur if r["blog"] == b]
        if rb:
            snap["blogs"][b] = _metrics(rb)
    snapshots = [s for s in snapshots if s.get("checked") != today_str]
    snapshots.append(snap)
    snapshots = snapshots[-90:]   # 최근 90회 보관

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"posts": posts_db, "snapshots": snapshots}, f, ensure_ascii=False, indent=2)

    generate_html(posts_db, snapshots)
    print("\n완료! report.html 을 열어보세요.")


# ----------------------------- HTML 리포트 -----------------------------

def build_row(rec):
    h = rec.get("history", [])
    cur = h[-1].get("rank") if h else None
    prev = h[-2].get("rank") if len(h) >= 2 else None
    exposed = cur is not None

    if len(h) < 2:
        t_label, t_val, t_cls = "NEW", None, "new"
    elif cur is None and prev is None:
        t_label, t_val, t_cls = "-", 0, "same"
    elif prev is None:
        t_label, t_val, t_cls = "신규진입", 999, "up"
    elif cur is None:
        t_label, t_val, t_cls = "이탈", -999, "down"
    elif cur < prev:
        t_label, t_val, t_cls = "▲ %d" % (prev - cur), (prev - cur), "up"
    elif cur > prev:
        t_label, t_val, t_cls = "▼ %d" % (cur - prev), -(cur - prev), "down"
    else:
        t_label, t_val, t_cls = "-", 0, "same"

    d = parse_date(rec.get("date", ""))
    date_num = (d.year * 10000 + d.month * 100 + d.day) if d else 0
    kw = rec.get("keyword", "")
    search_url = ("https://search.naver.com/search.naver?ssc=tab.blog.all&query="
                  + urllib.parse.quote(kw))
    return {
        "blog": rec.get("blog", ""),
        "date": rec.get("date", ""),
        "dateNum": date_num,
        "title": rec.get("title", ""),
        "url": rec.get("url", ""),
        "keyword": kw,
        "searchUrl": search_url,
        "rank": cur,
        "rankSort": cur if exposed else 100000,
        "exposed": exposed,
        "top10": bool(exposed and cur <= 10),
        "trendLabel": t_label,
        "trendVal": (t_val if t_val is not None else -100000),
        "trendCls": t_cls,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>디자인펀치 블로그 검색순위 리포트</title>
<style>
:root{--bd:#e5e7eb;--mut:#6b7280;}
*{box-sizing:border-box}
body{font-family:-apple-system,'Apple SD Gothic Neo',Pretendard,sans-serif;margin:0;background:#f8fafc;color:#111827}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:24px;margin:0 0 6px}
.sub{color:var(--mut);font-size:14px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.card{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:16px}
.card .n{font-size:26px;font-weight:700}
.card .l{color:var(--mut);font-size:13px;margin-top:4px}
.card .spark{margin-top:8px;display:block;overflow:visible}
.bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:10px;flex-wrap:wrap}
.bar input{padding:8px 12px;border:1px solid var(--bd);border-radius:8px;font-size:14px;width:230px}
.bar select{padding:8px;border:1px solid var(--bd);border-radius:8px;font-size:13px}
.bar .chk{font-size:13px;color:#374151;display:flex;align-items:center;gap:5px;cursor:pointer;white-space:nowrap}
.bar .chk input{width:15px;height:15px;margin:0;cursor:pointer}
td.empty{text-align:center;color:#9ca3af;padding:34px 12px;font-size:14px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--bd);border-radius:12px;overflow:hidden}
th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--bd);font-size:14px;vertical-align:middle}
th{background:#f1f5f9;font-size:13px;color:#374151;cursor:pointer;user-select:none;white-space:nowrap}
th .ar{color:#9ca3af;font-size:11px;margin-left:3px}
td.date{white-space:nowrap;color:var(--mut);font-size:13px}
td.blog{color:#6b7280;font-size:13px;white-space:nowrap}
td.title a{color:#111827;text-decoration:none}
td.title a:hover{text-decoration:underline}
a.kw{color:#2563eb;font-size:13px;text-decoration:none}
a.kw:hover{text-decoration:underline}
span.kw-off{color:#b0b6c0;font-size:13px}
.rank{font-weight:700;white-space:nowrap}
.rank.norm{color:#374151}
.rank.miss{color:#9ca3af;font-weight:500;font-size:13px}
.badge-top{display:inline-block;background:#dcfce7;color:#15803d;border:1px solid #86efac;border-radius:999px;padding:2px 10px;font-weight:700}
.t-up{color:#15803d;font-weight:600}.t-down{color:#dc2626;font-weight:600}.t-new{color:#2563eb;font-weight:600}.t-same{color:#9ca3af}
.pg{display:flex;gap:6px;justify-content:center;align-items:center;margin-top:18px;flex-wrap:wrap}
.pg button{min-width:34px;padding:6px 10px;border:1px solid var(--bd);background:#fff;border-radius:8px;cursor:pointer;font-size:13px}
.pg button.cur{background:#111827;color:#fff;border-color:#111827}
.pg button:disabled{opacity:.4;cursor:default}
.cnt{color:var(--mut);font-size:13px}
.note{margin-top:18px;color:var(--mut);font-size:12px;line-height:1.6}
.card .spark{max-width:100%;height:auto}
.card .spark-box{margin-top:8px;min-height:34px}
@media(max-width:680px){
  .wrap{padding:20px 12px 60px}
  h1{font-size:20px}
  .cards{grid-template-columns:repeat(2,1fr);gap:8px}
  .card{padding:13px}
  .card .n{font-size:22px}
  .bar{align-items:stretch}
  .bar input{width:100%}
  thead{display:none}
  table,tbody,tr,td{display:block;width:100%}
  table{border:none;background:transparent;border-radius:0}
  tr{border:1px solid var(--bd);border-radius:10px;margin-bottom:10px;padding:8px 12px;background:#fff}
  td{border:none;padding:7px 0;display:flex;justify-content:space-between;align-items:center;gap:14px;text-align:right}
  td::before{content:attr(data-label);color:var(--mut);font-weight:600;font-size:12px;text-align:left;flex:0 0 auto}
  td.title{display:block;text-align:left}
  td.title::before{display:block;margin-bottom:4px}
  td.title a{font-weight:600}
  td.empty{display:block;text-align:center}
  td.empty::before{display:none}
}
</style></head>
<body><div class="wrap">
<h1>디자인펀치 블로그 검색순위 리포트</h1>
<div class="sub">blog.naver.com/giant7000 · 점검일시 __NOW__</div>
<div style="margin-bottom:20px;font-size:13px"><a href="report-google.html" style="color:#2563eb;text-decoration:none">🔍 인블로그·티스토리 구글 검색노출 리포트 보기 →</a></div>
<div class="cards">
  <div class="card"><div class="n" id="c_total">__TOTAL__</div><div class="l">점검한 글</div><div class="spark-box" id="sp_total"></div></div>
  <div class="card"><div class="n" id="c_exposed">__EXPOSED__</div><div class="l">상위 100위 노출</div><div class="spark-box" id="sp_exposed"></div></div>
  <div class="card"><div class="n" id="c_page1">__PAGE1__</div><div class="l">1페이지(10위 내)</div><div class="spark-box" id="sp_page1"></div></div>
  <div class="card"><div class="n" id="c_avg">__AVG__</div><div class="l">평균 순위</div><div class="spark-box" id="sp_avg"></div></div>
</div>
<div class="bar">
  <input id="q" type="text" placeholder="제목·키워드 검색">
  <select id="blog">__BLOG_OPTIONS__</select>
  <label class="chk"><input type="checkbox" id="chg"> 변동 있는 글만</label>
  <div class="cnt" id="cnt"></div>
  <select id="per"><option value="25">25개씩</option><option value="50">50개씩</option><option value="100">100개씩</option></select>
</div>
<table>
<thead><tr>
  <th data-key="dateNum" data-type="num">작성일<span class="ar"></span></th>
  <th data-key="blog" data-type="str">블로그<span class="ar"></span></th>
  <th data-key="title" data-type="str">글 제목<span class="ar"></span></th>
  <th data-key="keyword" data-type="str">검색 키워드<span class="ar"></span></th>
  <th data-key="rankSort" data-type="num">현재 순위<span class="ar"></span></th>
  <th data-key="trendVal" data-type="num">변동<span class="ar"></span></th>
</tr></thead>
<tbody id="tb"></tbody></table>
<div class="pg" id="pg"></div>
<div class="note">
※ 순위는 네이버 <b>검색 API(유사도 기준)</b> 값으로, 실제 통합검색 화면(광고·인플루언서·스마트블록 포함)과는 다소 차이가 있을 수 있습니다.<br>
※ 검색 키워드를 클릭하면 네이버 블로그 검색 결과로 이동합니다. (미노출 글은 비활성)<br>
※ '검색 키워드'가 어색하면 keyword_overrides.csv 에 <code>글번호,키워드</code> 로 추가하세요. · '변동'은 직전 점검 대비 순위 변화입니다.
</div>
</div>
<script>
const DATA = __DATA__;
const SNAPS = __SNAPS__;
let sortKey="dateNum", sortDir=-1, page=1, perPage=25, query="", changeOnly=false, blogSel="";
const tb=document.getElementById("tb"), pg=document.getElementById("pg"), cnt=document.getElementById("cnt");
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\\"/g,"&quot;");}
function scoped(){
  let d=DATA.slice();
  if(blogSel){d=d.filter(r=>r.blog===blogSel);}
  if(query){const q=query.toLowerCase();d=d.filter(r=>(r.title||"").toLowerCase().includes(q)||(r.keyword||"").toLowerCase().includes(q));}
  return d;
}
function filtered(){
  let d=scoped();
  if(changeOnly){d=d.filter(r=>r.trendCls==="up"||r.trendCls==="down");}
  d.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
    if(typeof x==="string"){return x.localeCompare(y,"ko")*sortDir;}
    return ((x||0)-(y||0))*sortDir;});
  return d;
}
function sparkSvg(vals,color){
  vals=vals.filter(function(v){return v!=null;});
  if(!vals.length)return "";
  var w=150,h=34,pad=4,n=vals.length,lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),rng=(hi-lo)||1;
  if(n===1){return '<svg class="spark" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'"><circle cx="'+(w/2)+'" cy="'+(h/2)+'" r="3" fill="'+color+'"/></svg>';}
  var pts=vals.map(function(v,i){return [(pad+(w-2*pad)*i/(n-1)).toFixed(1),(pad+(h-2*pad)*(1-(v-lo)/rng)).toFixed(1)];});
  var path="M"+pts.map(function(p){return p[0]+" "+p[1];}).join(" L");
  var lst=pts[n-1];
  return '<svg class="spark" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'"><path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="2" stroke-linejoin="round"/><circle cx="'+lst[0]+'" cy="'+lst[1]+'" r="2.5" fill="'+color+'"/></svg>';
}
function seriesFor(blog,metric){
  return SNAPS.map(function(s){var m=blog?(s.blogs&&s.blogs[blog]):(s.overall||s);return m?m[metric]:null;});
}
function updateCards(){
  var base=scoped();
  var exp=base.filter(function(r){return r.exposed;});
  document.getElementById("c_total").textContent=base.length;
  document.getElementById("c_exposed").textContent=exp.length;
  document.getElementById("c_page1").textContent=base.filter(function(r){return r.top10;}).length;
  var rks=exp.map(function(r){return r.rank;});
  document.getElementById("c_avg").textContent=rks.length?((rks.reduce(function(a,b){return a+b;},0)/rks.length).toFixed(1)+"위"):"-";
  document.getElementById("sp_total").innerHTML=sparkSvg(seriesFor(blogSel,"total"),"#2563eb");
  document.getElementById("sp_exposed").innerHTML=sparkSvg(seriesFor(blogSel,"exposed"),"#15803d");
  document.getElementById("sp_page1").innerHTML=sparkSvg(seriesFor(blogSel,"page1"),"#b45309");
  document.getElementById("sp_avg").innerHTML=sparkSvg(seriesFor(blogSel,"avg"),"#7c3aed");
}
function render(){
  updateCards();
  const d=filtered();
  const pages=Math.max(1,Math.ceil(d.length/perPage));
  if(page>pages)page=pages;
  const start=(page-1)*perPage;
  const slice=d.slice(start,start+perPage);
  const rowsHtml=slice.map(function(r){
    const title='<a href="'+esc(r.url)+'" target="_blank">'+esc(r.title)+'</a>';
    const kw=r.exposed?('<a class="kw" href="'+esc(r.searchUrl)+'" target="_blank">'+esc(r.keyword)+'</a>'):('<span class="kw-off">'+esc(r.keyword)+'</span>');
    let rank;
    if(!r.exposed){rank='<span class="rank miss">미노출</span>';}
    else if(r.top10){rank='<span class="badge-top">'+r.rank+'위</span>';}
    else{rank='<span class="rank norm">'+r.rank+'위</span>';}
    const tr='<span class="t-'+r.trendCls+'">'+esc(r.trendLabel)+'</span>';
    return '<tr><td class="date" data-label="작성일">'+esc(r.date)+'</td><td class="blog" data-label="블로그">'+esc(r.blog)+'</td><td class="title" data-label="글 제목">'+title+'</td><td data-label="검색 키워드">'+kw+'</td><td data-label="현재 순위">'+rank+'</td><td data-label="변동">'+tr+'</td></tr>';
  }).join("");
  tb.innerHTML = rowsHtml || ('<tr><td colspan="6" class="empty">'+(changeOnly?'아직 순위 변동 데이터가 없어요. 다음 자동 점검부터 ▲▼ 변동이 표시됩니다.':'표시할 글이 없습니다.')+'</td></tr>');
  cnt.textContent="총 "+d.length+"개 · "+(d.length?(start+1):0)+"-"+Math.min(start+perPage,d.length)+" 표시";
  let hh='<button '+(page<=1?'disabled':'')+' data-p="'+(page-1)+'">이전</button>';
  const win=2;
  for(let i=1;i<=pages;i++){
    if(i===1||i===pages||(i>=page-win&&i<=page+win)){
      hh+='<button class="'+(i===page?'cur':'')+'" data-p="'+i+'">'+i+'</button>';
    }else if(i===page-win-1||i===page+win+1){hh+='<span class="cnt">…</span>';}
  }
  hh+='<button '+(page>=pages?'disabled':'')+' data-p="'+(page+1)+'">다음</button>';
  pg.innerHTML=hh;
  pg.querySelectorAll("button[data-p]").forEach(function(b){b.onclick=function(){page=parseInt(b.dataset.p);render();window.scrollTo(0,0);};});
  document.querySelectorAll("th[data-key]").forEach(function(th){
    th.querySelector(".ar").textContent = th.dataset.key===sortKey ? (sortDir===1?"▲":"▼") : "";
  });
}
document.querySelectorAll("th[data-key]").forEach(function(th){
  th.onclick=function(){const k=th.dataset.key;
    if(sortKey===k){sortDir=-sortDir;}else{sortKey=k;sortDir=1;}
    page=1;render();};
});
document.getElementById("q").addEventListener("input",function(e){query=e.target.value;page=1;render();});
document.getElementById("per").addEventListener("change",function(e){perPage=parseInt(e.target.value);page=1;render();});
document.getElementById("blog").addEventListener("change",function(e){blogSel=e.target.value;page=1;render();});
document.getElementById("chg").addEventListener("change",function(e){changeOnly=e.target.checked;page=1;render();});
render();
</script>
</body></html>"""


def spark(values, color):
    """회차별 값 리스트로 미니 추이 그래프(SVG) 생성."""
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    w, h, pad = 150, 34, 4
    n = len(vals)
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    if n == 1:
        return ('<svg class="spark" width="%d" height="%d" viewBox="0 0 %d %d">'
                '<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/></svg>'
                % (w, h, w, h, w / 2.0, h / 2.0, color))
    pts = []
    for i, v in enumerate(vals):
        xx = pad + (w - 2 * pad) * (i / float(n - 1))
        yy = pad + (h - 2 * pad) * (1 - (v - lo) / float(rng))
        pts.append((xx, yy))
    path = "M" + " L".join("%.1f %.1f" % p for p in pts)
    lx, ly = pts[-1]
    return ('<svg class="spark" width="%d" height="%d" viewBox="0 0 %d %d">'
            '<path d="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round"/>'
            '<circle cx="%.1f" cy="%.1f" r="2.5" fill="%s"/></svg>'
            % (w, h, w, h, path, color, lx, ly, color))


def generate_html(data, snapshots=None):
    snapshots = snapshots or []
    rows = [build_row(r) for r in data.values()]
    total = len(rows)
    exposed = sum(1 for r in rows if r["exposed"])
    page1 = sum(1 for r in rows if r["top10"])
    ranks_now = [r["rank"] for r in rows if r["exposed"]]
    avg = (sum(ranks_now) / len(ranks_now)) if ranks_now else 0
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    present = [b for b in BLOG_IDS if any(r["blog"] == b for r in rows)]
    blog_opts = '<option value="">전체 블로그</option>' + "".join(
        '<option value="%s">%s</option>' % (html.escape(b), html.escape(b)) for b in present)

    page = (HTML_TEMPLATE
            .replace("__NOW__", now)
            .replace("__BLOG_OPTIONS__", blog_opts)
            .replace("__TOTAL__", str(total))
            .replace("__EXPOSED__", str(exposed))
            .replace("__PAGE1__", str(page1))
            .replace("__AVG__", ("%.1f위" % avg if avg else "-"))
            .replace("__SNAPS__", json.dumps(snapshots, ensure_ascii=False))
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False)))

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    ap = argparse.ArgumentParser(description="네이버 블로그 검색순위 점검")
    ap.add_argument("--months", type=int, default=6, help="최근 N개월 글만 점검 (기본 6)")
    ap.add_argument("--all", action="store_true", help="전체 글 점검")
    ap.add_argument("--depth", type=int, default=100, help="검색 깊이(기본 100위까지)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만(테스트용)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
