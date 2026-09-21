@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== %date% %time% >> run.log
rem 本机只做一件事: 抓深交所当日份额快照并推到 GitHub (深交所拒绝 GitHub 服务器访问); 日报由 GitHub Actions 生成
rem github 走本机代理时 TLS 偶尔握手失败 (exit 128), 所以每一步都重试
set n=0
:pull
git pull -q --rebase origin master >> run.log 2>&1 && goto pulled
set /a n+=1
if %n% lss 5 (timeout /t 30 /nobreak >nul & goto pull)
:pulled
python huijin_monitor.py --snapshot-only >> run.log 2>&1
git add szse_snapshots.csv >> run.log 2>&1
git commit -q -m "szse snapshot %date:~0,10%" >> run.log 2>&1
set n=0
:push
git push -q origin master >> run.log 2>&1 && goto pushed
set /a n+=1
if %n% lss 5 (timeout /t 30 /nobreak >nul & git pull -q --rebase origin master >> run.log 2>&1 & goto push)
:pushed
git pull -q --rebase origin master >> run.log 2>&1
echo done >> run.log
