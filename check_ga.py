#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인블로그 글별 방문자 통계 자동 점검 스크립트 (Google Analytics 4)
-------------------------------------------------------------
구글 애널리틱스(GA4) Data API 로 인블로그의 '글별 방문자수·조회수'를
모든 유입 경로(다음·네이버·구글·직접 등) 합산으로 가져옵니다.

- 글 목록·제목·발행일은 check_google.collect_posts() 재사용
- 결과: report-visitors.html / data_ga.json

인증: 환경변수 GOOGLE_SA_JSON 또는 로컬 blog-search-stats-*.json
필요: GA4 속성에 서비스계정 '뷰어' 권한 + Google Analytics Data API 사용
"""

import os
import re
import sys
import json
import glob
import argparse
import datetime
import urllib.parse

from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric)

import check_google  # collect_posts / fetch_meta 재사용

GA_PROPERTY = "543530488"            # GA4 속성 ID(숫자)
INBLOG_BASE = "https://blog.designpunch.co.kr"
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data_ga.json")
REPORT_FILE = os.path.join(BASE_DIR, "report-visitors.html")


def get_client():
    raw = os.environ.get("GOOGLE_SA_JSON")
    if raw:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=SCOPES)
    else:
        keys = glob.glob(os.path.join(BASE_DIR, "blog-search-stats-*.json"))
        if not keys:
            print("[오류] 서비스 계정 키를 찾을 수 없습니다.")
            sys.exit(1)
        creds = service_account.Credentials.from_service_account_file(keys[0], scopes=SCOPES)
    return BetaAnalyticsDataClient(credentials=creds)


def path_of(url):
    p = urllib.parse.urlparse(url).path
    p = p.split("?")[0].rstrip("/")
    return p or "/"


def fetch_ga(client, days):
    """GA4: 페이지경로 → {views, users}. (모든 유입 합산)"""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    req = RunReportRequest(
        property="properties/" + GA_PROPERTY,
        date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
        dimensions=[Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews"), Metric(name="totalUsers")],
        limit=100000,
    )
    out = {}
    try:
        resp = client.run_report(req)
        for r in resp.rows:
            path = r.dimension_values[0].value.split("?")[0].rstrip("/") or "/"
            views = int(r.metric_values[0].value or 0)
            users = int(r.metric_values[1].value or 0)
            cur = out.setdefault(path, {"views": 0, "users": 0})
            cur["views"] += views
            cur["users"] += users
    except Exception as e:
        print("    [경고] GA4 조회 실패: %s" % str(e)[:160])
    return out


def run(args):
    client = get_client()
    today_str = datetime.date.today().isoformat()

    raw = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
    old_posts = raw.get("posts", {}) if isinstance(raw, dict) else {}
    snapshots = raw.get("snapshots", []) if isinstance(raw, dict) else []

    print("■ 인블로그 방문자 점검 (GA4, 최근 %d일)" % args.days)
    posts = check_google.collect_posts(INBLOG_BASE)
    ga = fetch_ga(client, args.days)
    print("  글 %d개 수집 / GA4 방문 발생 페이지 %d개" % (len(posts), len(ga)))

    posts_db = {}
    for key, info in posts.items():
        url = info["url"]
        title, date = info.get("title", ""), info.get("date", "")
        if not title or not date:
            ft, fd = check_google.fetch_meta(url)
            title = title or ft
            date = date or fd
        g = ga.get(path_of(url), {})
        views = g.get("views", 0)
        users = g.get("users", 0)
        rec = old_posts.get(url, {})
        rec.update({"title": title or url, "url": url, "date": date,
                    "views": views, "users": users})
        hist = [h for h in rec.get("history", []) if h.get("checked") != today_str]
        hist.append({"checked": today_str, "views": views, "users": users})
        rec["history"] = hist[-90:]
        posts_db[url] = rec

    rows = [build_row(v) for v in posts_db.values()]
    snap = {"checked": today_str,
            "total": len(rows),
            "visited": sum(1 for r in rows if r["users"] > 0),
            "users": sum(r["users"] for r in rows),
            "views": sum(r["views"] for r in rows)}
    snapshots = [s for s in snapshots if s.get("checked") != today_str]
    snapshots.append(snap)
    snapshots = snapshots[-90:]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"posts": posts_db, "snapshots": snapshots}, f, ensure_ascii=False, indent=2)

    generate_html(posts_db, snapshots, days=args.days)
    print("\n완료! report-visitors.html 갱신됨.")


def parse_pub_date(s):
    nums = re.findall(r"\d+", s or "")
    if len(nums) >= 3:
        try:
            return datetime.date(int(nums[0]), int(nums[1]), int(nums[2]))
        except ValueError:
            return None
    return None


def build_row(rec):
    d = parse_pub_date(rec.get("date", ""))
    date_num = (d.year * 10000 + d.month * 100 + d.day) if d else 0
    return {
        "date": rec.get("date", ""),
        "dateNum": date_num,
        "title": rec.get("title", ""),
        "url": rec.get("url", ""),
        "users": rec.get("users", 0),
        "views": rec.get("views", 0),
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>디자인펀치 인블로그 방문자 리포트</title>
<style>
:root{--bd:#e5e7eb;--mut:#6b7280;}
*{box-sizing:border-box}
body{font-family:-apple-system,'Apple SD Gothic Neo',Pretendard,sans-serif;margin:0;background:#f8fafc;color:#111827}
.wrap{max-width:1000px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:24px;margin:0 0 6px}
.sub{color:var(--mut);font-size:14px;margin-bottom:6px}
.nav{margin-bottom:20px;font-size:13px}
.nav a{color:#2563eb;text-decoration:none}.nav a:hover{text-decoration:underline}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.card{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:16px}
.card .n{font-size:26px;font-weight:700}
.card .l{color:var(--mut);font-size:13px;margin-top:4px}
.card .spark-box{margin-top:8px;min-height:34px}
.banner{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;border-radius:10px;padding:12px 14px;font-size:13px;margin-bottom:16px;line-height:1.5}
.bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:10px;flex-wrap:wrap}
.bar input{padding:8px 12px;border:1px solid var(--bd);border-radius:8px;font-size:14px;width:240px}
.bar select{padding:8px;border:1px solid var(--bd);border-radius:8px;font-size:13px}
.bar .chk{font-size:13px;color:#374151;display:flex;align-items:center;gap:5px;cursor:pointer;white-space:nowrap}
.bar .chk input{width:15px;height:15px;margin:0;cursor:pointer}
td.empty{text-align:center;color:#9ca3af;padding:34px 12px;font-size:14px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--bd);border-radius:12px;overflow:hidden}
th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--bd);font-size:14px;vertical-align:middle}
th{background:#f1f5f9;font-size:13px;color:#374151;cursor:pointer;user-select:none;white-space:nowrap}
th .ar{color:#9ca3af;font-size:11px;margin-left:3px}
.tip{display:inline-block;margin-left:4px;width:14px;height:14px;line-height:14px;text-align:center;border-radius:50%;background:#cbd5e1;color:#fff;font-size:10px;cursor:help;font-weight:400;vertical-align:middle}
td.date{white-space:nowrap;color:var(--mut);font-size:13px}
td.title a{color:#111827;text-decoration:none}
td.title a:hover{text-decoration:underline}
td.num{text-align:right;font-size:14px;white-space:nowrap}
td.num b{font-weight:700}
.muted0{color:#c0c4cc}
.pg{display:flex;gap:6px;justify-content:center;align-items:center;margin-top:18px;flex-wrap:wrap}
.pg button{min-width:34px;padding:6px 10px;border:1px solid var(--bd);background:#fff;border-radius:8px;cursor:pointer;font-size:13px}
.pg button.cur{background:#111827;color:#fff;border-color:#111827}
.pg button:disabled{opacity:.4;cursor:default}
.cnt{color:var(--mut);font-size:13px}
.note{margin-top:18px;color:var(--mut);font-size:12px;line-height:1.6}
@media(max-width:680px){
  .wrap{padding:20px 12px 60px}
  h1{font-size:20px}
  .cards{grid-template-columns:repeat(2,1fr);gap:8px}
  .card{padding:13px}.card .n{font-size:22px}
  .bar input{width:100%}
  thead{display:none}
  table,tbody,tr,td{display:block;width:100%}
  table{border:none;background:transparent}
  tr{border:1px solid var(--bd);border-radius:10px;margin-bottom:10px;padding:8px 12px;background:#fff}
  td{border:none;padding:7px 0;display:flex;justify-content:space-between;align-items:center;gap:14px;text-align:right}
  td::before{content:attr(data-label);color:var(--mut);font-weight:600;font-size:12px;text-align:left}
  td.title{display:block;text-align:left}td.title::before{display:block;margin-bottom:4px}
  td.title a{font-weight:600}
  td.empty{display:block;text-align:center}td.empty::before{display:none}
}
</style></head>
<body><div class="wrap">
<h1>디자인펀치 인블로그 방문자 리포트</h1>
<div class="sub">인블로그 (구글 애널리틱스 GA4 기준 · 전체 유입 합산) · 점검일시 __NOW__</div>
<div class="nav">📊 <a href="report.html">네이버 검색순위</a> · <a href="report-google.html">구글 검색노출</a></div>
__BANNER__
<div class="cards">
  <div class="card"><div class="n" id="c_total">__TOTAL__</div><div class="l">전체 글</div><div class="spark-box" id="sp_total"></div></div>
  <div class="card"><div class="n" id="c_visited">__VISITED__</div><div class="l">방문 있는 글</div><div class="spark-box" id="sp_visited"></div></div>
  <div class="card"><div class="n" id="c_users">__USERS__</div><div class="l">총 방문자수(__DAYS__일)</div><div class="spark-box" id="sp_users"></div></div>
  <div class="card"><div class="n" id="c_views">__VIEWS__</div><div class="l">총 조회수(__DAYS__일)</div><div class="spark-box" id="sp_views"></div></div>
</div>
<div class="bar">
  <input id="q" type="text" placeholder="제목 검색">
  <label class="chk"><input type="checkbox" id="vis"> 방문 있는 글만</label>
  <div class="cnt" id="cnt"></div>
  <select id="per"><option value="25">25개씩</option><option value="50">50개씩</option><option value="100">100개씩</option></select>
</div>
<table>
<thead><tr>
  <th data-key="dateNum" data-type="num">작성일<span class="ar"></span></th>
  <th data-key="title" data-type="str">글 제목<span class="ar"></span></th>
  <th data-key="users" data-type="num">방문자수<span class="tip" title="글에 들어온 실제 사람 수입니다. 한 사람이 여러 번 봐도 1명으로 셉니다. (모든 유입 합산)">i</span><span class="ar"></span></th>
  <th data-key="views" data-type="num">조회수<span class="tip" title="글이 열린 총 횟수입니다. 한 사람이 여러 번 보면 각각 셉니다.">i</span><span class="ar"></span></th>
</tr></thead>
<tbody id="tb"></tbody></table>
<div class="pg" id="pg"></div>
<div class="note">
※ <b>방문자수</b> = 글에 들어온 실제 사람 수(중복 제외), <b>조회수</b> = 글이 열린 총 횟수. 모든 유입(다음·네이버·구글·직접 등)을 합산합니다.<br>
※ GA4 설치 시점부터 집계되며, 최근 __DAYS__일 데이터입니다. (설치 직후엔 비어 있을 수 있고, 보통 24~48시간 후부터 쌓입니다)
</div>
</div>
<script>
const DATA = __DATA__;
const SNAPS = __SNAPS__;
let sortKey="users", sortDir=-1, page=1, perPage=25, query="", visOnly=false;
const tb=document.getElementById("tb"), pg=document.getElementById("pg"), cnt=document.getElementById("cnt");
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\\"/g,"&quot;");}
function scoped(){
  let d=DATA.slice();
  if(query){const q=query.toLowerCase();d=d.filter(r=>(r.title||"").toLowerCase().includes(q));}
  return d;
}
function filtered(){
  let d=scoped();
  if(visOnly){d=d.filter(r=>r.users>0);}
  d.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
    if(typeof x==="string"){return x.localeCompare(y,"ko")*sortDir;}
    return ((x||0)-(y||0))*sortDir;});
  return d;
}
function sparkSvg(vals,color){
  vals=vals.filter(function(v){return v!=null;});
  if(!vals.length)return "";
  var w=150,h=34,pad=4,n=vals.length,lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),rng=(hi-lo)||1;
  if(n===1){return '<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'"><circle cx="'+(w/2)+'" cy="'+(h/2)+'" r="3" fill="'+color+'"/></svg>';}
  var pts=vals.map(function(v,i){return [(pad+(w-2*pad)*i/(n-1)).toFixed(1),(pad+(h-2*pad)*(1-(v-lo)/rng)).toFixed(1)];});
  var path="M"+pts.map(function(p){return p[0]+" "+p[1];}).join(" L");
  var lst=pts[n-1];
  return '<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'"><path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="2" stroke-linejoin="round"/><circle cx="'+lst[0]+'" cy="'+lst[1]+'" r="2.5" fill="'+color+'"/></svg>';
}
function seriesFor(metric){return SNAPS.map(function(s){return s?s[metric]:null;});}
function updateCards(){
  var base=scoped();
  document.getElementById("c_total").textContent=base.length;
  document.getElementById("c_visited").textContent=base.filter(function(r){return r.users>0;}).length;
  document.getElementById("c_users").textContent=base.reduce(function(a,r){return a+r.users;},0);
  document.getElementById("c_views").textContent=base.reduce(function(a,r){return a+r.views;},0);
  document.getElementById("sp_total").innerHTML=sparkSvg(seriesFor("total"),"#2563eb");
  document.getElementById("sp_visited").innerHTML=sparkSvg(seriesFor("visited"),"#15803d");
  document.getElementById("sp_users").innerHTML=sparkSvg(seriesFor("users"),"#b45309");
  document.getElementById("sp_views").innerHTML=sparkSvg(seriesFor("views"),"#7c3aed");
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
    const u=r.users>0?('<b>'+r.users+'</b>'):('<span class="muted0">0</span>');
    const v=r.views>0?('<b>'+r.views+'</b>'):('<span class="muted0">0</span>');
    return '<tr><td class="date" data-label="작성일">'+esc(r.date)+'</td><td class="title" data-label="글 제목">'+title+'</td><td class="num" data-label="방문자수">'+u+'</td><td class="num" data-label="조회수">'+v+'</td></tr>';
  }).join("");
  tb.innerHTML = rowsHtml || ('<tr><td colspan="4" class="empty">'+(visOnly?'방문이 발생한 글이 아직 없습니다.':'표시할 글이 없습니다.')+'</td></tr>');
  cnt.textContent="총 "+d.length+"개 중 "+(d.length?(start+1):0)+"-"+Math.min(start+perPage,d.length)+" 표시";
  let hh='<button '+(page<=1?'disabled':'')+' data-p="'+(page-1)+'">이전</button>';
  const win=2;
  for(let i=1;i<=pages;i++){
    if(i===1||i===pages||(i>=page-win&&i<=page+win)){hh+='<button class="'+(i===page?'cur':'')+'" data-p="'+i+'">'+i+'</button>';}
    else if(i===page-win-1||i===page+win+1){hh+='<span class="cnt">…</span>';}
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
    if(sortKey===k){sortDir=-sortDir;}else{sortKey=k;sortDir=1;}page=1;render();};
});
document.getElementById("q").addEventListener("input",function(e){query=e.target.value;page=1;render();});
document.getElementById("per").addEventListener("change",function(e){perPage=parseInt(e.target.value);page=1;render();});
document.getElementById("vis").addEventListener("change",function(e){visOnly=e.target.checked;page=1;render();});
render();
</script>
</body></html>"""


def generate_html(data, snapshots=None, days=28):
    snapshots = snapshots or []
    rows = [build_row(r) for r in data.values()]
    total = len(rows)
    visited = sum(1 for r in rows if r["users"] > 0)
    users = sum(r["users"] for r in rows)
    views = sum(r["views"] for r in rows)
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    banner = ""
    if users == 0:
        banner = ('<div class="banner">⏳ 아직 GA4 방문자 데이터가 없습니다. '
                  '방금 설치하셨다면 보통 <b>24~48시간 후</b>부터 집계되며, '
                  '이후 매일 자동으로 채워집니다. (글 목록은 미리 표시됩니다)</div>')

    page = (HTML_TEMPLATE
            .replace("__NOW__", now)
            .replace("__DAYS__", str(days))
            .replace("__BANNER__", banner)
            .replace("__TOTAL__", str(total))
            .replace("__VISITED__", str(visited))
            .replace("__USERS__", str(users))
            .replace("__VIEWS__", str(views))
            .replace("__SNAPS__", json.dumps(snapshots, ensure_ascii=False))
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False)))
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    ap = argparse.ArgumentParser(description="인블로그 GA4 방문자 점검")
    ap.add_argument("--days", type=int, default=28, help="최근 N일 집계 (기본 28)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
