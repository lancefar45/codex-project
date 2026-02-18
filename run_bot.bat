@echo off
cd /d C:\tradingbot
call .venv\Scripts\activate
python paper_trader.py >> bot_run_log.txt 2>&1
