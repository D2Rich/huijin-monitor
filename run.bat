@echo off
cd /d "%~dp0"
echo ==== %date% %time% >> run.log
rem local job: refresh SZSE ETF share history and push szse_snapshots.csv to GitHub
rem (SZSE blocks datacenter IPs; the daily report itself is built by GitHub Actions)
rem git via local proxy sometimes fails TLS handshake (exit 128), so retry each step
set n=0
:pull
git pull -q --rebase origin master >> run.log 2>&1 && goto pulled
set /a n+=1
if %n% lss 5 (%SystemRoot%\System32	imeout.exe /t 30 /nobreak >nul & goto pull)
:pulled
python huijin_monitor.py --szse-only >> run.log 2>&1
git add szse_snapshots.csv >> run.log 2>&1
git commit -q -m "szse snapshot %date:~0,10%" >> run.log 2>&1
set n=0
:push
git push -q origin master >> run.log 2>&1 && goto pushed
set /a n+=1
if %n% lss 5 (%SystemRoot%\System32	imeout.exe /t 30 /nobreak >nul & git pull -q --rebase origin master >> run.log 2>&1 & goto push)
:pushed
git pull -q --rebase origin master >> run.log 2>&1
echo done >> run.log
