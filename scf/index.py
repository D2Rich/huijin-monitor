# -*- coding: utf-8 -*-
"""
腾讯云函数 (SCF) 版: 只做一件事 —— 用国内 IP 抓深交所 ETF 份额历史, 更新 GitHub 仓库里的 szse_snapshots.csv。
其余 (上交所/中金所/日报生成) 仍由 GitHub Actions 完成。
部署: 云函数 → 新建 → 从头开始 → Python 3.10 → 在线编辑, 把本文件内容粘进 index.py
      环境变量 GITHUB_TOKEN = 你在 GitHub 生成的 fine-grained token (仅 huijin-monitor 仓库, Contents: Read and write)
      触发器: 定时触发, 自定义 cron:  0 35 23 ? * MON-FRI *   (北京 23:35, 深交所约 23:17 发布当日)
      再加一个 0 10 0 ? * TUE-SAT * 作为补跑 (00:10)。首次运行后看日志里打印的北京时间, 确认时区对得上
      内存 128MB, 超时 120 秒, 地域选国内 (广州/上海), 保证出口是国内 IP
"""
import os, json, csv, io, base64, datetime, time, urllib.request, urllib.error

REPO = os.environ.get("GH_REPO", "D2Rich/huijin-monitor")
PATH = "szse_snapshots.csv"
BRANCH = os.environ.get("GH_BRANCH", "master")
CODES = ["159915", "159919", "159922", "159845"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

def http(url, headers=None, data=None, method=None, timeout=30, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (404, 409, 422): return e.code, e.read()
        except Exception as e:
            last = e
        time.sleep(5 * (i + 1))
    raise last

def szse_hist(code, start, end):
    url = ("https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=scsj_fund_jjgm&jjlb=ETF"
           f"&txtDm={code}&txtStart={start}&txtEnd={end}&PAGENO=1&random=0.{int(time.time()*1000)%100000}")
    st, b = http(url, headers={"Referer": "https://www.szse.cn/"}, timeout=20)
    j = json.loads(b)
    return {r["size_date"]: float(str(r["current_size"]).replace(",", "")) for r in (j[0].get("data") or [])}

def gh_get(token):
    st, b = http(f"https://api.github.com/repos/{REPO}/contents/{PATH}?ref={BRANCH}",
                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    if st == 404: return None, ""
    j = json.loads(b)
    return j["sha"], base64.b64decode(j["content"]).decode("utf-8")

def gh_put(token, sha, text, msg):
    body = {"message": msg, "content": base64.b64encode(text.encode("utf-8")).decode(), "branch": BRANCH}
    if sha: body["sha"] = sha
    st, b = http(f"https://api.github.com/repos/{REPO}/contents/{PATH}", method="PUT", data=json.dumps(body).encode(),
                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    return st, b[:200]

def main_handler(event=None, context=None):
    token = os.environ["GITHUB_TOKEN"]
    now_bj = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    print("北京时间", now_bj.strftime("%Y-%m-%d %H:%M"))
    today = now_bj.date()
    start = (today - datetime.timedelta(days=20)).isoformat(); end = today.isoformat()
    fresh = {}
    for code in CODES:
        try:
            fresh[code] = szse_hist(code, start, end)
        except Exception as e:
            print("深交所失败", code, repr(e)[:100])
    if not any(fresh.values()):
        return {"ok": False, "msg": "深交所全部失败"}
    for attempt in range(3):   # GitHub 侧可能被 Actions 同时改动 -> 409, 重新读 sha 再写
        sha, text = gh_get(token)
        rows = {}
        if text.strip():
            for r in csv.DictReader(io.StringIO(text)):
                rows[(r["date"], r["code"])] = (float(r["shares_wan"]), r.get("source") or "szse")
        n_new = 0
        for code, hist in fresh.items():
            for d, v in hist.items():
                old = rows.get((d, code))
                if old is None or old[1] != "szse" or abs(old[0] - v) > 0.01:
                    rows[(d, code)] = (v, "szse"); n_new += 1
        if n_new == 0:
            return {"ok": True, "msg": "无新数据", "latest": max(d for d, c in rows)}
        out = io.StringIO(); w = csv.writer(out); w.writerow(["date", "code", "shares_wan", "source"])
        for (d, code), (v, s) in sorted(rows.items()): w.writerow([d, code, v, s])
        st, msg = gh_put(token, sha, out.getvalue(), f"szse snapshot {end} via scf")
        if st in (200, 201):
            return {"ok": True, "updated": n_new, "latest": max(d for d, c in rows)}
        print("PUT 失败", st, msg); time.sleep(3)
    return {"ok": False, "msg": "GitHub 写入失败"}

if __name__ == "__main__":
    print(main_handler())
