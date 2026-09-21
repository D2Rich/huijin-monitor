# 助力汇金 每日监控

每天 23:45 运行 `run.bat`（或 `python huijin_monitor.py`），生成 `latest.md` 和 `reports/日期.md`，内容可直接粘贴给 ChatGPT。

## 云端与本机的分工
GitHub Actions 每个交易日北京 00:30 与 06:00 各跑一次，电脑关机也能出日报：上交所、中金所、腾讯指数在 GitHub 服务器上可直接抓；
深交所拒绝数据中心 IP，云端用仓库里的 `szse_snapshots.csv`（本机任务每晚 23:45 用深交所历史接口刷新并推送）+ 腾讯行情总市值估算（≈，精度约 ±0.1 亿份）兜底。

## 为什么不让 ChatGPT 自己抓
上交所份额接口 `query.sse.com.cn/commonQuery.do` 必须带 `Referer: http://www.sse.com.cn/`，否则返回 `System Error`；
上交所网页本身是 JS 渲染的。ChatGPT 的浏览工具不能自定义请求头，所以永远读不到当天数据（中金所的 XML 不需要请求头，它能读）。
本脚本在本机抓取（带头），ChatGPT 只负责按规则解读。

## 定时任务（Windows）
以管理员身份在 cmd 里执行一次：
schtasks /Create /SC DAILY /TN "HuijinMonitor" /TR "\"E:\Claude\huijin_monitor\run.bat\"" /ST 23:45 /F

## 口径（全部来自 UP 主视频/日更）
- 一倍量 = 该 ETF 2025 年末峰值总份额 × 0.1%；十倍量 = 1%。买入 = ≥3 只十倍量流入或整体 ≥70 倍；卖出对称。
- 汇金仓位区间：B = 当前份额 ÷ 峰值；区间 [B − (1 − A), B]，A = 2025 年报汇金占比。
- 交叉验证：沪深300/500/1000 各用嘉实/华夏的深交所孪生标的，取最坏情况。
- 期货：中信席位全部合约，净空Δ = 空单变化 − 多单变化；300 手 = 一倍量；套保逻辑加空 = 看涨。空单 ≥5000 手看涨 / 多单 ≥5000 手看跌。
- 机构 = 前 20 席位：求和法（varvolume 之和）与余额差法（总空 − 总多 的今日余额减昨日余额）。
- 行情：上证收盘 > MA20 为上涨行情（看期货为主），否则下跌行情（看 ETF 为主）。到期周（每月第三个周五前一周）期货数据失真。
- 执行：信号日 T，在 T+1 或 T+2 尾盘操作；仓位上限 = 沪深300ETF 汇金区间上限。

## 数据更新时间（实测）
- 上交所 ETF 份额：当日 22:50 前后可查当日；深交所 ETF规模 历史接口（`scsj_fund_jjgm`，可查 6 个月）约 23:15–23:20 发布当日。
- 中金所持仓排名：收盘后 17:00 前后。

## 日报地址
https://raw.githubusercontent.com/D2Rich/huijin-monitor/master/latest.md

## 腾讯云函数（深交所，国内 IP，电脑可以关）
`scf/index.py`：定时用深交所历史接口刷新 `szse_snapshots.csv` 并通过 GitHub API 写回仓库。
1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate：Repository access 只选 huijin-monitor，Permissions → Contents = Read and write，生成后复制（只显示一次）。
2. 腾讯云 → 云函数 SCF → 函数服务 → 新建 → 从头开始：地域 广州，运行环境 Python 3.10，内存 128MB，执行超时 120 秒。
3. 在线编辑：把 `scf/index.py` 的内容粘到 index.py；执行方法保持 `index.main_handler`。
4. 函数配置 → 环境变量：`GITHUB_TOKEN` = 第 1 步的 token。
5. 触发管理 → 创建触发器 → 定时触发 → 自定义：`0 35 23 ? * MON-FRI *`；再建一个 `0 10 0 ? * TUE-SAT *`。
6. 点"测试"跑一次，返回 `{'ok': True, ...}` 即成功；仓库会出现 "szse snapshot … via scf" 的提交。
