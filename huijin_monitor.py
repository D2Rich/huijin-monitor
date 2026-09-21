# -*- coding: utf-8 -*-
"""
助力汇金 每日监控 (本地拉取, 生成可直接粘贴给 ChatGPT 的日报)
用法:  python huijin_monitor.py            # 默认: 今天 (若今天数据未出则自动回退到最近一个有数据的交易日)
       python huijin_monitor.py 2026-09-17  # 指定日期
数据源 (均为官方公开接口):
  上交所 ETF 份额  query.sse.com.cn  (需要 Referer 头, 这是 ChatGPT 直接抓取失败的原因)
  深交所 ETF 份额  szse.cn 历史接口 scsj_fund_jjgm (可查 6 个月; 拒绝数据中心 IP, GitHub 上用本地推送的缓存 + 腾讯估算兜底)
  中金所 持仓排名  cffex.com.cn  (IF/IH/IC/IM 四个 XML)
  指数日线         腾讯 ifzq.gtimg.cn (上证 MA20 判行情, MA360)
规则口径: 全部来自 UP 主视频/日更 (见 README), 倍量基准 = 2025 年末峰值总份额 × 0.1%, 期货 300 手 = 一倍量
"""
import os, sys, json, csv, datetime, urllib.request, urllib.parse, urllib.error, xml.etree.ElementTree as ET
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache"); os.makedirs(CACHE, exist_ok=True)
REPORTS = os.path.join(HERE, "reports"); os.makedirs(REPORTS, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
for k in ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"]:
    os.environ.pop(k, None)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ---------------- 基准表 (UP 主口径; 峰值 = 2025 年 12 月上交所份额峰值; 深交所三只由其视频反推, 约数) ----------------
# code: (名称, 交易所, 2025年末峰值份额(万份), 汇金2025年报占比A)
ETF = {
    "510300": ("沪深300ETF华泰柏瑞", "SSE", 9025000, 0.8276),
    "510050": ("上证50ETF华夏",     "SSE", 5765000, 0.8605),
    "510500": ("中证500ETF南方",    "SSE", 1917000, 0.7458),
    "512100": ("中证1000ETF南方",   "SSE", 2591000, 0.8480),   # A 由 UP 主表格区间反推
    "588080": ("科创50ETF易方达",   "SSE", 5183000, 0.4195),
    "510230": ("金融ETF国泰",       "SSE",  337000, 0.7759),
    "159915": ("创业板ETF易方达",   "SZSE", 3157000, 0.5413),  # 峰值由 UP 主表格反推
    "159919": ("沪深300ETF嘉实",    "SZSE", 4500000, None),    # 交叉验证用, 峰值由 9/17 视频反推(约)
    "159922": ("中证500ETF嘉实",    "SZSE",  505000, None),
    "159845": ("中证1000ETF华夏",   "SZSE", 1560000, None),
}
CORE4 = ["510300", "510050", "510500", "512100"]          # 四大股指
TWIN = {"510300": "159919", "510500": "159922", "512100": "159845"}  # 交叉验证: 取最坏情况
FUT_ONE = 300          # 期货一倍量 (手)
FUT_BIG = 5000         # 2.0 原文: 空单/多单 ≥5000 手
PRODUCTS = ["IH", "IF", "IC", "IM"]
TYPES = {"0": "vol", "1": "long", "2": "short"}

def http_get(url, referer=None, timeout=30, retries=3):
    """带重试: 上交所偶尔对某些出口 IP 返回 403, 等一会再试通常就好."""
    import time, random
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9", **({"Referer": referer} if referer else {})})
            return OPENER.open(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404: raise          # 非交易日/未发布, 不必重试
            if i < retries - 1: time.sleep(15 + random.random() * 10)
        except Exception as e:
            last = e
            if i < retries - 1: time.sleep(15 + random.random() * 10)
    raise last

# ---------------- 上交所 ----------------
def sse_shares(day):
    """返回 {code: 万份} ; 无数据返回 None. 带本地缓存."""
    f = os.path.join(CACHE, f"sse_{day}.json")
    if os.path.exists(f):
        rows = json.load(open(f, encoding="utf-8"))
        return rows or None
    url = ("http://query.sse.com.cn/commonQuery.do?isPagination=true&pageHelp.pageSize=1000&pageHelp.pageNo=1"
           f"&pageHelp.beginPage=1&pageHelp.endPage=1&sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE={day}")
    try:
        j = json.loads(http_get(url, referer="http://www.sse.com.cn/"))
        data = j.get("pageHelp", {}).get("data") or []
    except Exception as e:
        print("  上交所接口失败:", e); return None
    rows = {r["SEC_CODE"]: float(r["TOT_VOL"]) for r in data}
    if rows:
        json.dump(rows, open(f, "w", encoding="utf-8"))
    return rows or None

# ---------------- 深交所 (历史份额接口, 最多查 6 个月; 数据中心 IP 会被拒 -> 用本地缓存 + 腾讯估算兜底) ----------------
SNAP = os.path.join(HERE, "szse_snapshots.csv")   # 缓存: date, code, shares_wan, source(szse|tencent)
SRC = {}
def szse_hist(code, start, end):
    """深交所 ETF规模 历史接口 (市场数据→基金数据→基金规模→ETF规模), 返回 {日期: 万份}."""
    url = ("https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=scsj_fund_jjgm&jjlb=ETF"
           f"&txtDm={code}&txtStart={start}&txtEnd={end}&PAGENO=1&random=0.{datetime.datetime.now().microsecond}")
    j = json.loads(http_get(url, referer="https://www.szse.cn/", timeout=15, retries=2))
    return {r["size_date"]: float(str(r["current_size"]).replace(",", "")) for r in (j[0].get("data") or [])}
def tencent_estimate(code):
    """腾讯行情: 总市值(亿元, 两位小数)/价格 = 份额(亿份), 精度约 ±0.1 亿份; 返回 (万份, 行情日期)."""
    b = http_get(f"https://qt.gtimg.cn/q=sz{code}", timeout=15, retries=2).decode("gbk", "ignore")
    f = b.split("~"); price = float(f[3]); cap = float(f[44]); ts = f[30]
    return round(cap / price * 1e4, 2), f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", ts[8:14]
def szse_snapshot_load(with_source=False):
    snaps = {}; src = {}
    if os.path.exists(SNAP):
        for r in csv.DictReader(open(SNAP, encoding="utf-8")):
            snaps.setdefault(r["date"], {})[r["code"]] = float(r["shares_wan"])
            src.setdefault(r["date"], {})[r["code"]] = r.get("source") or "szse"
    return (snaps, src) if with_source else snaps
def szse_snapshot_save(snaps, src):
    with open(SNAP, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["date", "code", "shares_wan", "source"])
        for d in sorted(snaps):
            for code, v in sorted(snaps[d].items()): w.writerow([d, code, v, src.get(d, {}).get(code, "szse")])
def szse_snapshot_take(day, days_back=20):
    """用深交所历史接口刷新最近 days_back 天的精确值 (覆盖之前的估算值); 被拒时对 day 用腾讯估算兜底."""
    snaps, src = szse_snapshot_load(True)
    start = (datetime.date.fromisoformat(day) - datetime.timedelta(days=days_back)).isoformat()
    ok = False
    for code, (name, ex, base, A) in ETF.items():
        if ex != "SZSE": continue
        try:
            for d, v in szse_hist(code, start, day).items():
                snaps.setdefault(d, {})[code] = v; src.setdefault(d, {})[code] = "szse"; ok = True
        except Exception as e:
            print("  深交所历史接口失败:", code, str(e)[:60])
    if not ok:
        for code, (name, ex, base, A) in ETF.items():
            if ex != "SZSE" or src.get(day, {}).get(code) == "szse": continue
            try:
                v, asof, tm = tencent_estimate(code)
                prev_days = sorted(d for d in snaps if d < day and code in snaps[d])
                pv = snaps[prev_days[-1]][code] if prev_days else None
                # 腾讯的份额是"最近一次发布值": 行情日=day 时对应 day; 若已到下一交易日且未收盘(15:00 前), 仍对应 day
                usable = asof == day or (asof > day and tm < "150000")
                if usable and not (pv and abs(v - pv) / pv < 1e-5):   # 与前一日完全相同 = 深交所还没发布, 腾讯只是镜像旧值
                    snaps.setdefault(day, {})[code] = v; src.setdefault(day, {})[code] = "tencent"
                    print(f"  {code} 用腾讯估算 {v:.0f} 万份 (≈)")
            except Exception as e:
                print("  腾讯行情也失败:", code, str(e)[:60])
    szse_snapshot_save(snaps, src)
    return snaps

# ---------------- 中金所 ----------------
def cffex(day, prod):
    ymd = day.replace("-", "")
    f = os.path.join(CACHE, f"cffex_{ymd}_{prod}.xml")
    if not os.path.exists(f):
        url = f"http://www.cffex.com.cn/sj/ccpm/{ymd[:6]}/{ymd[6:]}/{prod}.xml"
        try:
            b = http_get(url, referer="http://www.cffex.com.cn/ccpm/")
            if b"<data" not in b: return None
            open(f, "wb").write(b)
        except Exception:
            return None
    root = ET.parse(f).getroot()
    seats = defaultdict(lambda: [0, 0, 0, 0])   # seat -> [L, dL, S, dS] 全部合约合计
    insts = set()
    for d in root.findall("data"):
        t = TYPES.get(d.findtext("datatypeid"), ""); n = d.findtext("shortname"); v = int(d.findtext("volume")); c = int(d.findtext("varvolume"))
        insts.add(d.findtext("instrumentid"))
        if t == "long": seats[n][0] += v; seats[n][1] += c
        elif t == "short": seats[n][2] += v; seats[n][3] += c
    return dict(seats), sorted(insts)
def third_friday(y, m):
    d = datetime.date(y, m, 15)
    while d.weekday() != 4: d += datetime.timedelta(days=1)
    return d

# ---------------- 指数 (腾讯) ----------------
def index_daily(code="sh000001", n=420):
    end = datetime.date.today(); start = end - datetime.timedelta(days=int(n * 1.6))
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,{start},{end},{n},qfq"
    j = json.loads(http_get(url)); x = j["data"][code]; rows = x.get("qfqday") or x.get("day")
    return [(r[0], float(r[2])) for r in rows]

# ---------------- 主流程 ----------------
def prev_trading_days(day, n):
    """返回 day 之前 n 个有中金所数据的交易日 (从近到远)."""
    out = []; d = datetime.date.fromisoformat(day)
    while len(out) < n and (datetime.date.fromisoformat(day) - d).days < 30:
        d -= datetime.timedelta(days=1)
        if d.weekday() >= 5: continue
        if cffex(d.isoformat(), "IF"): out.append(d.isoformat())
    return out

def main(day=None):
    today = datetime.date.today().isoformat()
    day = day or today
    # 若指定/默认日期无上交所数据, 回退
    tried = 0
    while sse_shares(day) is None and tried < 7:
        d = datetime.date.fromisoformat(day) - datetime.timedelta(days=1); day = d.isoformat(); tried += 1
    sh = sse_shares(day)
    if sh is None:
        print("近 7 天都没有上交所份额数据, 退出"); return
    prevs = prev_trading_days(day, 4)
    prev = prevs[0]
    sh_prev = sse_shares(prev)
    lines = [f"# 助力汇金日报  数据日 {day}", ""]

    # ---- 行情判定 ----
    idx = index_daily()
    closes = [c for d, c in idx if d <= day]
    ma20 = sum(closes[-20:]) / 20; ma360 = sum(closes[-360:]) / min(360, len(closes))
    regime = "上涨行情" if closes[-1] > ma20 else "下跌行情"
    lines += [f"## 行情判定", f"上证 {idx[[d for d, c in idx].index(max(d for d, c in idx if d <= day))][1]:.0f}，MA20 {ma20:.0f} → **{regime}**（3.0：下跌看 ETF 为主，上涨看期货为主）；MA360 {ma360:.0f}，{'在上方' if closes[-1] > ma360 else '已跌破'} {abs(closes[-1]/ma360-1)*100:.1f}%", ""]

    # ---- ETF ----
    global SRC
    snaps = szse_snapshot_take(day)
    SRC = szse_snapshot_load(True)[1]
    lines += ["## ETF 一级市场份额（一倍量 = 2025 年末峰值 × 0.1%；≈ 表示深交所值来自腾讯总市值估算，精度约 ±0.1 亿份）", "", "| 代码 | 标的 | 份额(亿份) | 变化(万份) | 倍量 | 方向 | 十倍量 | 汇金仓位区间 |", "|---|---|---|---|---|---|---|---|"]
    mult = {}; n_ten_in = n_ten_out = 0; total = 0.0
    for code, (name, ex, base, A) in ETF.items():
        approx = ""
        if ex == "SSE":
            cur = sh.get(code); pv = (sh_prev or {}).get(code)
        else:
            cur = snaps.get(day, {}).get(code); pv = snaps.get(prev, {}).get(code)
            approx = "≈" if any(SRC.get(x, {}).get(code) == "tencent" for x in (day, prev)) else ""
        if cur is None:
            lines.append(f"| {code} | {name} | 无数据 | | | | | |"); continue
        if pv is None:
            lines.append(f"| {code} | {name} | {cur/1e4:.1f} | 无前值 | | | | |"); continue
        chg = cur - pv; m = chg / (base * 0.001); mult[code] = m
        B = cur / base
        rng = f"{max(B-(1-A),0)*100:.1f}%–{B*100:.1f}%" if A else "—"
        ten = "✅" if abs(m) >= 10 else ""
        if code in ETF and A is not None:   # 只把 UP 主篮子里的 7 只计入整体倍量; 嘉实/华夏三只只做交叉验证
            total += m
            if m >= 10: n_ten_in += 1
            if m <= -10: n_ten_out += 1
        lines.append(f"| {code} | {name} | {cur/1e4:.1f} | {approx}{chg:+,.0f} | {approx}{m:+.1f}x | {'申购' if chg>0 else '赎回' if chg<0 else '—'} | {ten} | {rng} |")
    # 交叉验证 (取最坏情况)
    cross = []
    for a, b in TWIN.items():
        if a in mult and b in mult:
            worst = min(mult[a], mult[b], key=lambda v: v)   # 最坏 = 更小的那个 (流入取小, 流出取更负)
            cross.append(f"{ETF[a][0][:6]} {mult[a]:+.1f}x / 嘉实·华夏 {mult[b]:+.1f}x → 取 {worst:+.1f}x")
    lines += ["", f"整体倍量（7 只）：**{total:+.0f} 倍**；十倍量标的：{n_ten_in} 只流入 / {n_ten_out} 只流出"]
    if cross: lines.append("交叉验证（取最坏）：" + "；".join(cross))
    # 三日累计 (口径统一用上交所 6 只, 因深交所历史只能靠快照)
    sse6 = [c for c in ETF if ETF[c][1] == "SSE" and ETF[c][3] is not None]
    cum = sum(mult[c] for c in sse6 if c in mult); hist = [f"{day} {cum:+.0f}倍"]
    for pd_ in prevs[:2]:
        s1 = sse_shares(pd_); s0 = sse_shares(prev_trading_days(pd_, 1)[0]) if prev_trading_days(pd_, 1) else None
        if s1 and s0:
            t = sum((s1[c] - s0[c]) / (ETF[c][2] * 0.001) for c in ETF if ETF[c][1] == "SSE" and c in s1 and c in s0 and ETF[c][3] is not None)
            hist.append(f"{pd_} {t:+.0f}倍"); cum += t
    lines.append(f"三日累计整体倍量（上交所 6 只）：{cum:+.0f} 倍（{'，'.join(hist)}）")
    etf_sig = "买入" if (n_ten_in >= 3 or total >= 70) and not (n_ten_out >= 3 or total <= -70) else "卖出" if (n_ten_out >= 3 or total <= -70) else "无"
    lines.append(f"**ETF 信号（2.0 原文：≥3 只十倍量或整体 ≥70 倍）：{etf_sig}**")
    lines.append("")

    # ---- 期货 ----
    lines += ["## 股指期货（中金所前 20 席位，全部合约）", ""]
    fut = {}; fut_prev = {}
    for p in PRODUCTS:
        fut[p] = cffex(day, p); fut_prev[p] = cffex(prev, p)
    if all(fut.values()):
        # 到期周
        d0 = datetime.date.fromisoformat(day); exp = third_friday(d0.year, d0.month)
        p0 = datetime.date.fromisoformat(prev); exp_prev = third_friday(p0.year, p0.month)
        roll = 0 <= (exp - d0).days <= 7 or (p0 <= exp_prev < d0)   # 到期周, 或到期后第一个交易日 (余额差法含摘牌合约)
        lines.append("| 品种 | 中信净空Δ(S−L) | 倍量(300手) | 中信ΔS | 中信ΔL | 中信净空持仓 | 前20 求和法净空Δ | 前20 余额差法 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        zx_tot = 0; t20_sum = 0; t20_bal = 0; dS_tot = dL_tot = 0
        for p in PRODUCTS:
            seats, insts = fut[p]; pseats = fut_prev[p][0] if fut_prev[p] else {}
            zx = next((v for n, v in seats.items() if n.startswith("中信期货")), [0, 0, 0, 0])
            net = zx[3] - zx[1]; zx_tot += net; dS_tot += zx[3]; dL_tot += zx[1]
            s20 = sum(v[3] - v[1] for v in seats.values()); t20_sum += s20
            bal = sum(v[2] - v[0] for v in seats.values()) - sum(v[2] - v[0] for v in pseats.values()); t20_bal += bal
            lines.append(f"| {p} | {net:+d} | {net/FUT_ONE:+.1f}x | {zx[3]:+d} | {zx[1]:+d} | {zx[2]-zx[0]:+d} | {s20:+d} | {bal:+d} |")
        lines.append("")
        # 三日累计 前20 余额差 & 中信
        cum20 = t20_bal; cumzx = zx_tot
        for i in range(2):
            a = prevs[i]; b = prevs[i + 1]
            fa = {p: cffex(a, p) for p in PRODUCTS}; fb = {p: cffex(b, p) for p in PRODUCTS}
            if all(fa.values()) and all(fb.values()):
                cum20 += sum(sum(v[2] - v[0] for v in fa[p][0].values()) - sum(v[2] - v[0] for v in fb[p][0].values()) for p in PRODUCTS)
                cumzx += sum(next((v[3] - v[1] for n, v in fa[p][0].items() if n.startswith("中信期货")), 0) for p in PRODUCTS)
        lines.append(f"中信四品种净空Δ合计 **{zx_tot:+d} 手**（ΔS {dS_tot:+d} / ΔL {dL_tot:+d}）；三日累计 {cumzx:+d} 手")
        lines.append(f"前 20 席位（机构）：求和法 {t20_sum:+d} 手，余额差法 **{t20_bal:+d} 手**；三日累计余额差 {cum20:+d} 手")
        fut_sig = "看涨" if dS_tot >= FUT_BIG and dL_tot < FUT_BIG else "看跌" if dL_tot >= FUT_BIG and dS_tot < FUT_BIG else "无"
        lines.append(f"**期货信号（2.0 原文：空单 ≥5000 手看涨 / 多单 ≥5000 手看跌）：{fut_sig}**" + ("　⚠️ 到期周或到期次日（%s 到期），移仓/摘牌影响，数据失真" % (exp if (exp - d0).days >= 0 else exp_prev) if roll else ""))
    else:
        fut_sig = "无数据"; roll = False
        lines.append("中金所当日数据未出（收盘后 17:00 前后发布）")
    lines.append("")

    # ---- 综合 ----
    main_sig = etf_sig if regime == "下跌行情" else fut_sig
    lines += ["## 综合（3.0：延迟执行，T+1 或 T+2 尾盘）", f"主信号来源：{'ETF 份额' if regime=='下跌行情' else '期货'} → **{main_sig}**；次信号：{fut_sig if regime=='下跌行情' else etf_sig}",
              f"仓位上限（沪深300ETF 汇金区间上限）：**{sh['510300']/ETF['510300'][2]*100:.0f}%**", ""]
    text = "\n".join(lines)
    open(os.path.join(REPORTS, f"{day}.md"), "w", encoding="utf-8").write(text)
    open(os.path.join(HERE, "latest.md"), "w", encoding="utf-8").write(text)
    sys.stdout.reconfigure(encoding="utf-8"); print(text)

if __name__ == "__main__":
    if "--snapshot-only" in sys.argv or "--szse-only" in sys.argv:
        snaps = szse_snapshot_take(datetime.date.today().isoformat())
        print("深交所最近三天:", {d: v for d, v in snaps.items() if d >= (datetime.date.today() - datetime.timedelta(days=4)).isoformat()})
    else:
        main(next((a for a in sys.argv[1:] if not a.startswith("--")), None))
