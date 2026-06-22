#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인블로그 / 티스토리 구글 검색 노출·클릭 통계 자동 점검 스크립트
-------------------------------------------------------------
1) 각 블로그의 RSS/sitemap 에서 '최근 6개월 발행 글 전체'를 수집합니다.
   (구글 검색 노출이 0인 글도 모두 포함)
2) 구글 Search Console API(서비스 계정)로 최근 30일 노출수·클릭수를
   가져와 글 URL 로 매칭합니다. (노출 없는 글은 0)
3) 결과를 report-google.html(노출수·클릭수 표) 로 저장하고
   data_google.json 에 이력을 누적합니다.

* 평균순위/추세는 신뢰도 한계로 제외하고, 실제 발생 데이터인
  노출수·클릭수 중심으로 구성합니다.

인증: 환경변수 GOOGLE_SA_JSON(서비스 계정 JSON 문자열) 또는
      로컬 blog-search-stats-*.json 키 파일.

사용 예:
  python check_google.py                         # 지표 30일 / 발행 6개월
  python check_google.py --days 28 --max-age-months 6
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
from email.utils import parsedate_to_datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

# (Search Console 속성 URL, 표시 이름, 블로그 베이스 URL)
PROPERTIES = [
    ("https://blog.designpunch.co.kr/", "인블로그", "https://blog.designpunch.co.kr"),
    ("https://designpunch.tistory.com/", "티스토리", "https://designpunch.tistory.com"),
]
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data_google.json")
REPORT_FILE = os.path.join(BASE_DIR, "report-google.html")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ----------------------------- 공통 유틸 -----------------------------

def http_get(url, timeout=20, limit=None):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(limit) if limit else resp.read()
    return data.decode("utf-8", "ignore")


def months_ago(d, months):
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    return datetime.date(y, m, 1)


def norm_url(u):
    """매칭용 URL 정규화: 쿼리 제거, 끝 슬래시 제거, 모바일 /m 제거."""
    u = (u or "").split("?")[0].split("#")[0].strip().rstrip("/")
    u = u.replace("/m/entry/", "/entry/").replace("/m/", "/")
    return u


def rss_pubdate(s):
    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        nums = re.findall(r"\d+", s or "")
        return None


# ----------------------------- 글 목록 수집 -----------------------------

def is_post_url(base, url):
    """글(포스팅) URL 인지 판별. 카테고리/태그/방명록/홈 제외."""
    if norm_url(url) == norm_url(base):
        return False
    low = url.lower()
    if any(x in low for x in ["/category", "/tag", "/guestbook", "/notice",
                              "/m/", "/author/", "/author"]):
        return False
    if "tistory.com" in base:
        return "/entry/" in url      # 티스토리는 /entry/ 만 글
    return True                      # 인블로그는 슬러그 글


def collect_posts(base):
    """RSS + sitemap 으로 글 목록 수집. 반환: {url: {"title":.., "date":..}}"""
    posts = {}
    # 1) RSS: link/title/pubDate 한 번에
    try:
        xml = http_get(base + "/rss")
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
            lm = re.search(r"<link>\s*(.*?)\s*</link>", item, re.S)
            if not lm:
                continue
            url = lm.group(1).strip()
            if not is_post_url(base, url):
                continue
            tm = re.search(r"<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", item, re.S)
            pm = re.search(r"<pubDate>\s*(.*?)\s*</pubDate>", item, re.S)
            d = rss_pubdate(pm.group(1)) if pm else None
            posts[norm_url(url)] = {
                "url": url,
                "title": html.unescape(html.unescape(tm.group(1).strip())) if tm else "",
                "date": d.isoformat() if d else "",
            }
    except Exception as e:
        print("    [경고] RSS 수집 실패(%s): %s" % (base, str(e)[:100]))
    # 2) sitemap: RSS 에 없는 글 보완
    try:
        xml = http_get(base + "/sitemap.xml")
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml):
            loc = loc.strip()
            if not is_post_url(base, loc):
                continue
            key = norm_url(loc)
            if key not in posts:
                posts[key] = {"url": loc, "title": "", "date": ""}
    except Exception as e:
        print("    [경고] sitemap 수집 실패(%s): %s" % (base, str(e)[:100]))
    return posts


def fetch_meta(url):
    """글 페이지에서 제목/발행일 보완(article:published_time, <title>)."""
    title, date = "", ""
    try:
        data = http_get(url, timeout=15, limit=300000)
        m = re.search(r"<title[^>]*>(.*?)</title>", data, re.I | re.S)
        if m:
            title = html.unescape(m.group(1)).strip()
            for sep in [" :: ", " :", " | ", " - "]:
                if sep in title and len(title.split(sep)[0]) >= 4:
                    title = title.split(sep)[0].strip()
                    break
        for pat in [r'article:published_time"[^>]*content="([^"]+)"',
                    r'"datePublished"\s*:\s*"([^"]+)"',
                    r'property="og:regDate"[^>]*content="([^"]+)"']:
            dm = re.search(pat, data, re.I)
            if dm:
                nums = re.findall(r"\d+", dm.group(1))
                if len(nums) >= 3:
                    date = "%04d-%02d-%02d" % (int(nums[0]), int(nums[1]), int(nums[2]))
                break
    except Exception:
        pass
    return title, date


# ----------------------------- Search Console -----------------------------

def get_service():
    raw = os.environ.get("GOOGLE_SA_JSON")
    if raw:
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        keys = glob.glob(os.path.join(BASE_DIR, "blog-search-stats-*.json"))
        if not keys:
            print("[오류] 서비스 계정 키를 찾을 수 없습니다. GOOGLE_SA_JSON 또는 키 파일 필요.")
            sys.exit(1)
        creds = service_account.Credentials.from_service_account_file(keys[0], scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def is_noise_query(q):
    q = (q or "").strip().lower()
    return q.startswith("site:") or q.startswith("inurl:") or q.startswith("cache:")


def fetch_sc(svc, site, start, end):
    """글 URL → {impr, clicks, top_kw}. (최근 기간, 모든 검색어 합산)"""
    by_page = {}
    start_row = 0
    while True:
        body = {"startDate": start, "endDate": end,
                "dimensions": ["page", "query"], "rowLimit": 25000, "startRow": start_row}
        try:
            resp = svc.searchanalytics().query(siteUrl=site, body=body).execute()
        except Exception as e:
            print("    [오류] SC 조회 실패(%s): %s" % (site, str(e)[:140]))
            break
        rows = resp.get("rows", [])
        for r in rows:
            page, query = r["keys"]
            if is_noise_query(query):
                continue
            key = norm_url(page)
            agg = by_page.setdefault(key, {"impr": 0, "clicks": 0, "top": None})
            agg["impr"] += r.get("impressions", 0)
            agg["clicks"] += r.get("clicks", 0)
            cand = {"query": query, "impr": r.get("impressions", 0)}
            if agg["top"] is None or cand["impr"] > agg["top"]["impr"]:
                agg["top"] = cand
        if len(rows) < 25000:
            break
        start_row += len(rows)
        time.sleep(0.2)
    return by_page


# ----------------------------- 메인 -----------------------------

def run(args):
    svc = get_service()
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=args.days)).isoformat()
    end = today.isoformat()
    today_str = today.isoformat()
    age_cutoff = months_ago(today, args.max_age_months)

    # 기존 데이터(이력) 로드
    raw = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
    old_posts = raw.get("posts", {}) if isinstance(raw, dict) else {}
    snapshots = raw.get("snapshots", []) if isinstance(raw, dict) else []

    age_desc = ("발행 최근 %d개월" % args.max_age_months) if args.max_age_months > 0 else "등록된 전체"
    print("■ 구글 검색노출 점검 (지표 최근 %d일 / %s 글)" % (args.days, age_desc))

    posts_db = {}
    for site, label, base in PROPERTIES:
        listed = collect_posts(base)
        sc = fetch_sc(svc, site, start, end)
        kept = 0
        for key, info in listed.items():
            title, date = info["title"], info["date"]
            # 발행일 없으면 페이지에서 보완
            if not date or not title:
                ft, fd = fetch_meta(info["url"])
                title = title or ft
                date = date or fd
                time.sleep(0.1)
            d = None
            if date:
                nums = re.findall(r"\d+", date)
                if len(nums) >= 3:
                    try:
                        d = datetime.date(int(nums[0]), int(nums[1]), int(nums[2]))
                    except ValueError:
                        d = None
            # 발행일 필터 (max_age_months>0 이고 발행일 알 때만 적용; 0이면 전체)
            if args.max_age_months > 0 and d is not None and d < age_cutoff:
                continue
            scd = sc.get(key, {})
            impr = scd.get("impr", 0)
            clicks = scd.get("clicks", 0)
            top_kw = scd.get("top", {}).get("query", "") if scd.get("top") else ""
            full_key = label + "|" + key
            rec = old_posts.get(full_key, {})
            rec.update({
                "blog": label, "title": title or info["url"],
                "url": info["url"], "date": date,
                "keyword": top_kw, "impressions": impr, "clicks": clicks,
            })
            hist = [h for h in rec.get("history", []) if h.get("checked") != today_str]
            hist.append({"checked": today_str, "impressions": impr, "clicks": clicks})
            rec["history"] = hist[-30:]
            posts_db[full_key] = rec
            kept += 1
        exposed = sum(1 for k, info in listed.items() if sc.get(k, {}).get("impr", 0) > 0)
        print("  [%s] 글 %d개 수집 / 포함 %d개 / 노출 발생 %d개"
              % (label, len(listed), kept, exposed))

    # 회차 스냅샷 (카드 추이용: 총글/노출글/총노출/총클릭)
    rows = [build_row(v) for v in posts_db.values()]

    def _metrics(rs):
        return {"total": len(rs),
                "exposed": sum(1 for r in rs if r["impressions"] > 0),
                "impressions": sum(r["impressions"] for r in rs),
                "clicks": sum(r["clicks"] for r in rs)}

    snap = {"checked": today_str, "overall": _metrics(rows), "blogs": {}}
    for _, label, _b in PROPERTIES:
        rb = [r for r in rows if r["blog"] == label]
        if rb:
            snap["blogs"][label] = _metrics(rb)
    snapshots = [s for s in snapshots if s.get("checked") != today_str]
    snapshots.append(snap)
    snapshots = snapshots[-90:]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"posts": posts_db, "snapshots": snapshots}, f, ensure_ascii=False, indent=2)

    generate_html(posts_db, snapshots, days=args.days)
    print("\n완료! report-google.html 갱신됨.")


# ----------------------------- HTML 리포트 -----------------------------

def parse_pub_date(s):
    nums = re.findall(r"\d+", s or "")
    if len(nums) >= 3:
        try:
            return datetime.date(int(nums[0]), int(nums[1]), int(nums[2]))
        except ValueError:
            return None
    return None


def build_row(rec):
    impr = rec.get("impressions", 0)
    clicks = rec.get("clicks", 0)
    kw = rec.get("keyword", "")
    ctr = round(clicks / impr * 100, 1) if impr else 0
    search_url = ""
    if kw:
        search_url = ("https://www.google.com/search?hl=ko&gl=kr&q="
                      + urllib.parse.quote(kw))
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
        "impressions": impr,
        "clicks": clicks,
        "ctr": ctr,
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
span.kw-off{color:#c0c4cc;font-size:13px}
td.num{text-align:right;color:#374151;font-size:14px;white-space:nowrap}
td.num b{font-weight:700}
.muted0{color:#c0c4cc}
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
  <div class="card"><div class="n" id="c_total">__TOTAL__</div><div class="l">전체 글</div><div class="spark-box" id="sp_total"></div></div>
  <div class="card"><div class="n" id="c_exposed">__EXPOSED__</div><div class="l">노출된 글</div><div class="spark-box" id="sp_exposed"></div></div>
  <div class="card"><div class="n" id="c_impr">__IMPR__</div><div class="l">총 노출수(30일)</div><div class="spark-box" id="sp_impr"></div></div>
  <div class="card"><div class="n" id="c_clicks">__CLICKS__</div><div class="l">총 클릭수(30일)</div><div class="spark-box" id="sp_clicks"></div></div>
</div>
<div class="bar">
  <input id="q" type="text" placeholder="제목·검색어 검색">
  <select id="blog">__BLOG_OPTIONS__</select>
  <label class="chk"><input type="checkbox" id="expo"> 노출된 글만</label>
  <div class="cnt" id="cnt"></div>
  <select id="per"><option value="25">25개씩</option><option value="50">50개씩</option><option value="100">100개씩</option></select>
</div>
<table>
<thead><tr>
  <th data-key="dateNum" data-type="num">작성일<span class="ar"></span></th>
  <th data-key="title" data-type="str">글 제목<span class="ar"></span></th>
  <th data-key="blog" data-type="str">블로그<span class="ar"></span></th>
  <th data-key="keyword" data-type="str">대표 검색어<span class="ar"></span></th>
  <th data-key="impressions" data-type="num">노출수<span class="ar"></span></th>
  <th data-key="clicks" data-type="num">클릭수<span class="ar"></span></th>
  <th data-key="ctr" data-type="num">클릭률<span class="ar"></span></th>
</tr></thead>
<tbody id="tb"></tbody></table>
<div class="pg" id="pg"></div>
<div class="note">
※ 본 데이터는 구글 <b>Search Console</b> 기준이며, 실제 사람들이 검색해 내 글이 노출/클릭된 실측값입니다. (보통 2~3일 지연)<br>
※ <b>노출수</b> = 구글 검색결과에서 유저가 실제로 본 화면에 내 글이 뜬 횟수. <b>클릭수</b> = 그중 실제 방문 수. <b>클릭률</b> = 클릭÷노출.<br>
※ <b>등록된 전체 글</b>을 표시하며(노출 0 포함), 노출수·클릭수는 <b>최근 __DAYS__일</b> 합계입니다. '대표 검색어'는 노출이 가장 많은 검색어입니다.
</div>
</div>
<script>
const DATA = __DATA__;
const SNAPS = __SNAPS__;
let sortKey="impressions", sortDir=-1, page=1, perPage=25, query="", expoOnly=false, blogSel="";
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
  if(expoOnly){d=d.filter(r=>r.impressions>0);}
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
  document.getElementById("c_total").textContent=base.length;
  document.getElementById("c_exposed").textContent=base.filter(function(r){return r.impressions>0;}).length;
  document.getElementById("c_impr").textContent=base.reduce(function(a,r){return a+r.impressions;},0);
  document.getElementById("c_clicks").textContent=base.reduce(function(a,r){return a+r.clicks;},0);
  document.getElementById("sp_total").innerHTML=sparkSvg(seriesFor(blogSel,"total"),"#2563eb");
  document.getElementById("sp_exposed").innerHTML=sparkSvg(seriesFor(blogSel,"exposed"),"#15803d");
  document.getElementById("sp_impr").innerHTML=sparkSvg(seriesFor(blogSel,"impressions"),"#b45309");
  document.getElementById("sp_clicks").innerHTML=sparkSvg(seriesFor(blogSel,"clicks"),"#7c3aed");
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
    const kw=r.keyword?('<a class="kw" href="'+esc(r.searchUrl)+'" target="_blank">'+esc(r.keyword)+'</a>'):('<span class="kw-off">-</span>');
    const imp=r.impressions>0?('<b>'+r.impressions+'</b>'):('<span class="muted0">0</span>');
    const clk=r.clicks>0?('<b>'+r.clicks+'</b>'):('<span class="muted0">0</span>');
    const ctr=r.impressions>0?(r.ctr+'%'):('<span class="muted0">-</span>');
    return '<tr><td class="date" data-label="작성일">'+esc(r.date)+'</td><td class="title" data-label="글 제목">'+title+'</td><td class="blog" data-label="블로그">'+esc(r.blog)+'</td><td data-label="대표 검색어">'+kw+'</td><td class="num" data-label="노출수">'+imp+'</td><td class="num" data-label="클릭수">'+clk+'</td><td class="num" data-label="클릭률">'+ctr+'</td></tr>';
  }).join("");
  tb.innerHTML = rowsHtml || ('<tr><td colspan="7" class="empty">'+(expoOnly?'구글 검색에 노출된 글이 아직 없습니다.':'표시할 글이 없습니다.')+'</td></tr>');
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
document.getElementById("expo").addEventListener("change",function(e){expoOnly=e.target.checked;page=1;render();});
render();
</script>
</body></html>"""


def generate_html(data, snapshots=None, days=30):
    snapshots = snapshots or []
    rows = [build_row(r) for r in data.values()]
    total = len(rows)
    exposed = sum(1 for r in rows if r["impressions"] > 0)
    impr = sum(r["impressions"] for r in rows)
    clicks = sum(r["clicks"] for r in rows)
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    present = [lbl for _, lbl, _b in PROPERTIES if any(r["blog"] == lbl for r in rows)]
    blog_opts = '<option value="">전체 블로그</option>' + "".join(
        '<option value="%s">%s</option>' % (html.escape(b), html.escape(b)) for b in present)

    page = (HTML_TEMPLATE
            .replace("__NOW__", now)
            .replace("__DAYS__", str(days))
            .replace("__BLOG_OPTIONS__", blog_opts)
            .replace("__TOTAL__", str(total))
            .replace("__EXPOSED__", str(exposed))
            .replace("__IMPR__", str(impr))
            .replace("__CLICKS__", str(clicks))
            .replace("__SNAPS__", json.dumps(snapshots, ensure_ascii=False))
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False)))

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    ap = argparse.ArgumentParser(description="구글 Search Console 검색노출 점검")
    ap.add_argument("--days", type=int, default=30,
                    help="노출/클릭 집계 기간, 최근 N일 (기본 30)")
    ap.add_argument("--max-age-months", type=int, default=6,
                    help="발행일 기준 최근 N개월 이내 글만 포함 (기본 6)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
