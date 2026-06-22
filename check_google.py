#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인블로그 / 티스토리 구글 검색 노출 통계 자동 점검 스크립트
-------------------------------------------------------------
1) 구글 Search Console API(서비스 계정 인증)로 각 블로그의 최근 6개월
   검색 데이터를 가져옵니다. (글 URL × 검색어 단위)
2) 글(URL)별로 '대표 검색어'(노출수가 가장 많은 검색어) 1개를 골라
   그 검색어의 평균 순위 / 노출수 / 클릭수를 정리합니다.
3) 결과를 report-google.html(현재 순위 표) 로 저장하고, data_google.json 에
   이력을 누적합니다. (네이버용 check_rankings.py 와 짝을 이루는 별도 페이지)

인증 방법(둘 중 하나 자동 선택):
  - 환경변수 GOOGLE_SA_JSON  : 서비스 계정 JSON 문자열 (GitHub Actions 용)
  - 로컬 키 파일             : blog-search-stats-*.json (로컬 테스트 용)

사용 예:
  python check_google.py            # 최근 180일
  python check_google.py --days 90  # 최근 90일
"""

import os
import re
import sys
import json
import glob
import html
import time
import argparse
import datetime
import urllib.parse
import urllib.request

from google.oauth2 import service_account
from googleapiclient.discovery import build

# 점검 대상: (Search Console 속성 URL, 화면 표시 이름)
PROPERTIES = [
    ("https://blog.designpunch.co.kr/", "인블로그"),
    ("https://designpunch.tistory.com/", "티스토리"),
]
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data_google.json")
REPORT_FILE = os.path.join(BASE_DIR, "report-google.html")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def get_service():
    """서비스 계정으로 Search Console API 클라이언트를 만든다."""
    raw = os.environ.get("GOOGLE_SA_JSON")
    if raw:
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        keys = glob.glob(os.path.join(BASE_DIR, "blog-search-stats-*.json"))
        if not keys:
            print("[오류] 서비스 계정 키를 찾을 수 없습니다.")
            print("       GOOGLE_SA_JSON 환경변수를 설정하거나, 로컬에 키 파일을 두세요.")
            sys.exit(1)
        creds = service_account.Credentials.from_service_account_file(keys[0], scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def fetch_rows(svc, site, start, end):
    """글 URL × 검색어 단위 데이터를 모두 가져온다(페이지네이션 포함)."""
    rows = []
    start_row = 0
    while True:
        body = {
            "startDate": start,
            "endDate": end,
            "dimensions": ["page", "query"],
            "rowLimit": 25000,
            "startRow": start_row,
        }
        try:
            resp = svc.searchanalytics().query(siteUrl=site, body=body).execute()
        except Exception as e:
            print("    [오류] 데이터 조회 실패(%s): %s" % (site, str(e)[:160]))
            break
        batch = resp.get("rows", [])
        rows.extend(batch)
        if len(batch) < 25000:
            break
        start_row += len(batch)
        time.sleep(0.2)
    return rows


def months_ago(d, months):
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    return datetime.date(y, m, 1)


def parse_pub_date(s):
    nums = re.findall(r"\d+", s or "")
    if len(nums) >= 3:
        try:
            return datetime.date(int(nums[0]), int(nums[1]), int(nums[2]))
        except ValueError:
            return None
    return None


def is_noise_query(q):
    """검색어 노이즈 제거: site: 연산자 등 실제 키워드가 아닌 진단성 쿼리."""
    q = (q or "").strip().lower()
    return q.startswith("site:") or q.startswith("inurl:") or q.startswith("cache:")


def fetch_meta(url, cache):
    """글 페이지에서 제목과 발행일을 가져온다(캐시 사용).
    반환: {"title": 제목, "date": "YYYY-MM-DD" 또는 ""}"""
    cached = cache.get(url)
    if cached and cached.get("title") and cached.get("date") is not None:
        return cached
    title, date = "", ""
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read(300000).decode("utf-8", "ignore")
        # 제목
        m = re.search(r"<title[^>]*>(.*?)</title>", data, re.I | re.S)
        if m:
            title = html.unescape(m.group(1)).strip()
            for sep in [" :: ", " :", " | ", " - "]:
                if sep in title and len(title.split(sep)[0]) >= 4:
                    title = title.split(sep)[0].strip()
                    break
        # 발행일 (여러 형식 시도)
        for pat in [r'article:published_time"[^>]*content="([^"]+)"',
                    r'"datePublished"\s*:\s*"([^"]+)"',
                    r'property="og:regDate"[^>]*content="([^"]+)"']:
            dm = re.search(pat, data, re.I)
            if dm:
                dnums = re.findall(r"\d+", dm.group(1))
                if len(dnums) >= 3:
                    date = "%04d-%02d-%02d" % (int(dnums[0]), int(dnums[1]), int(dnums[2]))
                break
    except Exception:
        pass
    if not title:
        title = url.rstrip("/").split("/")[-1] or url
    meta = {"title": title, "date": date}
    cache[url] = meta
    return meta


def run(args):
    svc = get_service()

    today = datetime.date.today()
    start = (today - datetime.timedelta(days=args.days)).isoformat()
    end = today.isoformat()
    today_str = today.isoformat()
    age_cutoff = months_ago(today, args.max_age_months)   # 발행일 필터 기준

    # 기존 데이터 로드
    raw = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
    posts_db = raw.get("posts", {}) if isinstance(raw, dict) else {}
    snapshots = raw.get("snapshots", []) if isinstance(raw, dict) else []
    # 캐시: {url: {"title":..., "date":...}}
    meta_cache = {k: {"title": v.get("title", ""), "date": v.get("date", "")}
                  for k, v in posts_db.items()}

    print("■ 구글 Search Console 점검 (지표 최근 %d일 / 발행일 최근 %d개월 글만)"
          % (args.days, args.max_age_months))

    seen_keys = set()
    for site, label in PROPERTIES:
        rows = fetch_rows(svc, site, start, end)
        # 글 URL 단위로 묶어 대표 검색어/합계 계산
        by_page = {}
        for r in rows:
            page, query = r["keys"]
            if is_noise_query(query):
                continue
            agg = by_page.setdefault(page, {"clicks": 0, "impr": 0, "top": None})
            agg["clicks"] += r.get("clicks", 0)
            agg["impr"] += r.get("impressions", 0)
            cand = {"query": query, "impr": r.get("impressions", 0),
                    "clicks": r.get("clicks", 0), "pos": r.get("position", 0)}
            if agg["top"] is None or cand["impr"] > agg["top"]["impr"]:
                agg["top"] = cand

        print("  [%s] 글 %d개 (검색 노출 있는 URL 기준)" % (label, len(by_page)))

        skipped_old = 0
        site_root = site.rstrip("/")
        for page, agg in by_page.items():
            top = agg["top"]
            if not top:
                continue
            # 블로그 첫(홈)페이지는 개별 포스팅이 아니므로 제외
            if page.rstrip("/") == site_root:
                continue
            meta = fetch_meta(page, meta_cache)
            pub = parse_pub_date(meta.get("date", ""))
            # 발행일이 6개월보다 오래된 글은 제외 (발행일 모르면 유지)
            if pub is not None and pub < age_cutoff:
                skipped_old += 1
                continue
            key = page
            seen_keys.add(key)
            rank = round(top["pos"], 1)
            rec = posts_db.get(key, {})
            rec.update({
                "blog": label,
                "title": meta.get("title", ""),
                "date": meta.get("date", ""),
                "url": page,
                "keyword": top["query"],
                "impressions": agg["impr"],
                "clicks": agg["clicks"],
            })
            hist = rec.get("history", [])
            hist = [h for h in hist if h.get("checked") != today_str]
            hist.append({"checked": today_str, "rank": rank,
                         "impressions": agg["impr"], "clicks": agg["clicks"]})
            rec["history"] = hist[-24:]
            posts_db[key] = rec

        if skipped_old:
            print("    └ 발행 6개월 초과로 제외: %d개" % skipped_old)

    # 발행일이 6개월 지난 글은 통계에서 정리(제거)
    before = len(posts_db)
    posts_db = {k: v for k, v in posts_db.items()
                if (parse_pub_date(v.get("date", "")) is None)
                or (parse_pub_date(v.get("date", "")) >= age_cutoff)}
    purged = before - len(posts_db)
    if purged:
        print("  기간 지난 글 %d개 정리" % purged)

    # 이번 회차에 노출이 사라진 글: 이력에 '미노출'로 한 줄 기록(추세 '이탈' 판단용)
    for key, rec in posts_db.items():
        if key in seen_keys:
            continue
        hist = rec.get("history", [])
        if hist and hist[-1].get("checked") == today_str:
            continue
        hist = [h for h in hist if h.get("checked") != today_str]
        hist.append({"checked": today_str, "rank": None, "impressions": 0, "clicks": 0})
        rec["history"] = hist[-24:]
        posts_db[key] = rec

    # 회차 스냅샷(카드 추이 그래프용)
    cur = [build_row(v) for v in posts_db.values()]
    rk = [r["rank"] for r in cur if r["exposed"]]
    snap = {
        "checked": today_str,
        "overall": {
            "total": len(cur),
            "exposed": sum(1 for r in cur if r["exposed"]),
            "page1": sum(1 for r in cur if r["top10"]),
            "avg": round(sum(rk) / len(rk), 1) if rk else 0,
        },
        "blogs": {},
    }
    for _, label in PROPERTIES:
        rb = [r for r in cur if r["blog"] == label]
        if rb:
            rkb = [r["rank"] for r in rb if r["exposed"]]
            snap["blogs"][label] = {
                "total": len(rb),
                "exposed": sum(1 for r in rb if r["exposed"]),
                "page1": sum(1 for r in rb if r["top10"]),
                "avg": round(sum(rkb) / len(rkb), 1) if rkb else 0,
            }
    snapshots = [s for s in snapshots if s.get("checked") != today_str]
    snapshots.append(snap)
    snapshots = snapshots[-90:]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"posts": posts_db, "snapshots": snapshots}, f, ensure_ascii=False, indent=2)

    generate_html(posts_db, snapshots, days=args.days)
    print("\n완료! report-google.html 갱신됨.")


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
        t_label, t_val, t_cls = "신규노출", 999, "up"
    elif cur is None:
        t_label, t_val, t_cls = "이탈", -999, "down"
    elif cur < prev:
        t_label, t_val, t_cls = "▲ %.1f" % (prev - cur), (prev - cur), "up"
    elif cur > prev:
        t_label, t_val, t_cls = "▼ %.1f" % (cur - prev), -(cur - prev), "down"
    else:
        t_label, t_val, t_cls = "-", 0, "same"

    kw = rec.get("keyword", "")
    search_url = ("https://www.google.com/search?hl=ko&gl=kr&q="
                  + urllib.parse.quote(kw))
    # 평균 순위가 2페이지 이상이면 해당 검색결과 페이지로 바로 이동(&start=)
    if cur and cur > 10:
        start_off = int((int(cur) - 1) // 10 * 10)
        search_url += "&start=%d" % start_off
    d = parse_pub_date(rec.get("date", ""))
    date_num = (d.year * 10000 + d.month * 100 + d.day) if d else 0
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
        "impressions": rec.get("impressions", 0),
        "clicks": rec.get("clicks", 0),
        "exposed": exposed,
        "top10": bool(exposed and cur <= 10),
        "trendLabel": t_label,
        "trendVal": (t_val if t_val is not None else -100000),
        "trendCls": t_cls,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>디자인펀치 구글 검색노출 리포트</title>
<style>
:root{--bd:#e5e7eb;--mut:#6b7280;}
*{box-sizing:border-box}
body{font-family:-apple-system,'Apple SD Gothic Neo',Pretendard,sans-serif;margin:0;background:#f8fafc;color:#111827}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:24px;margin:0 0 6px}
.sub{color:var(--mut);font-size:14px;margin-bottom:6px}
.nav{margin-bottom:20px;font-size:13px}
.nav a{color:#2563eb;text-decoration:none}
.nav a:hover{text-decoration:underline}
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
td.num{text-align:right;color:#374151;font-size:13px;white-space:nowrap}
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
<h1>디자인펀치 구글 검색노출 리포트</h1>
<div class="sub">인블로그 · 티스토리 (구글 Search Console 기준) · 점검일시 __NOW__</div>
<div class="nav">📊 <a href="report.html">네이버 블로그 검색순위 리포트 보기 →</a></div>
<div class="cards">
  <div class="card"><div class="n" id="c_total">__TOTAL__</div><div class="l">노출된 글</div><div class="spark-box" id="sp_total"></div></div>
  <div class="card"><div class="n" id="c_exposed">__EXPOSED__</div><div class="l">검색 노출 글</div><div class="spark-box" id="sp_exposed"></div></div>
  <div class="card"><div class="n" id="c_page1">__PAGE1__</div><div class="l">1페이지(10위 내)</div><div class="spark-box" id="sp_page1"></div></div>
  <div class="card"><div class="n" id="c_avg">__AVG__</div><div class="l">평균 순위</div><div class="spark-box" id="sp_avg"></div></div>
</div>
<div class="bar">
  <input id="q" type="text" placeholder="제목·검색어 검색">
  <select id="blog">__BLOG_OPTIONS__</select>
  <label class="chk"><input type="checkbox" id="chg"> 변동 있는 글만</label>
  <div class="cnt" id="cnt"></div>
  <select id="per"><option value="25">25개씩</option><option value="50">50개씩</option><option value="100">100개씩</option></select>
</div>
<table>
<thead><tr>
  <th data-key="dateNum" data-type="num">작성일<span class="ar"></span></th>
  <th data-key="title" data-type="str">글 제목<span class="ar"></span></th>
  <th data-key="blog" data-type="str">블로그<span class="ar"></span></th>
  <th data-key="keyword" data-type="str">대표 검색어<span class="ar"></span></th>
  <th data-key="rankSort" data-type="num">평균 순위<span class="ar"></span></th>
  <th data-key="impressions" data-type="num">노출수<span class="ar"></span></th>
  <th data-key="clicks" data-type="num">클릭수<span class="ar"></span></th>
  <th data-key="trendVal" data-type="num">추세<span class="ar"></span></th>
</tr></thead>
<tbody id="tb"></tbody></table>
<div class="pg" id="pg"></div>
<div class="note">
※ 본 데이터는 구글 <b>Search Console</b> 기준이며, 실제 사람들이 검색해 내 글이 노출된 검색어/순위입니다. 데이터는 보통 2~3일 지연됩니다.<br>
※ '대표 검색어'는 해당 글이 가장 많이 노출된 검색어 1개이며, '평균 순위'는 그 검색어에서의 구글 평균 노출 순위입니다.<br>
※ 검색어를 클릭하면 해당 순위의 구글 검색결과 페이지로 이동합니다.<br>
※ 노출수·클릭수·평균순위는 <b>최근 __DAYS__일</b> 기준이며, <b>발행 6개월 이내</b> 글만 표시합니다.
</div>
</div>
<script>
const DATA = __DATA__;
const SNAPS = __SNAPS__;
let sortKey="impressions", sortDir=-1, page=1, perPage=25, query="", changeOnly=false, blogSel="";
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
    return '<tr><td class="date" data-label="작성일">'+esc(r.date)+'</td><td class="title" data-label="글 제목">'+title+'</td><td class="blog" data-label="블로그">'+esc(r.blog)+'</td><td data-label="대표 검색어">'+kw+'</td><td data-label="평균 순위">'+rank+'</td><td class="num" data-label="노출수">'+r.impressions+'</td><td class="num" data-label="클릭수">'+r.clicks+'</td><td data-label="추세">'+tr+'</td></tr>';
  }).join("");
  tb.innerHTML = rowsHtml || ('<tr><td colspan="8" class="empty">'+(changeOnly?'변동 있는 글이 없습니다.':'표시할 데이터가 없습니다. (구글 검색 노출이 쌓이면 표시됩니다)')+'</td></tr>');
  cnt.textContent="총 "+d.length+"개 중 "+(d.length?(start+1):0)+"-"+Math.min(start+perPage,d.length)+" 표시";
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


def generate_html(data, snapshots=None, days=30):
    snapshots = snapshots or []
    rows = [build_row(r) for r in data.values()]
    total = len(rows)
    exposed = sum(1 for r in rows if r["exposed"])
    page1 = sum(1 for r in rows if r["top10"])
    ranks_now = [r["rank"] for r in rows if r["exposed"]]
    avg = (sum(ranks_now) / len(ranks_now)) if ranks_now else 0
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    present = [lbl for _, lbl in PROPERTIES if any(r["blog"] == lbl for r in rows)]
    blog_opts = '<option value="">전체 블로그</option>' + "".join(
        '<option value="%s">%s</option>' % (html.escape(b), html.escape(b)) for b in present)

    page = (HTML_TEMPLATE
            .replace("__NOW__", now)
            .replace("__DAYS__", str(days))
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
    ap = argparse.ArgumentParser(description="구글 Search Console 검색노출 점검")
    ap.add_argument("--days", type=int, default=30,
                    help="지표(노출/클릭/순위) 집계 기간, 최근 N일 (기본 30)")
    ap.add_argument("--max-age-months", type=int, default=6,
                    help="발행일 기준 최근 N개월 이내 글만 포함 (기본 6)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
