@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 本机只做一件事: 抓深交所当日份额快照并推到 GitHub (深交所拒绝 GitHub 服务器访问); 日报由 GitHub Actions 生成
git pull -q --rebase origin master
python huijin_monitor.py --snapshot-only >> run.log 2>&1
git add szse_snapshots.csv
git commit -q -m "szse snapshot %date:~0,10%" 2>nul
git push -q origin master
