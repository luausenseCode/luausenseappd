#!/usr/bin/env bash
# luauwhitelist: бот и веб-дашборд в одном контейнере.
# Если умирает один из процессов - контейнер завершается, fly.io его перезапустит.
set -u

python /app/bot.py &
bot_pid=$!

python -m uvicorn web:app --host 0.0.0.0 --port "${PORT:-8080}" &
web_pid=$!

wait -n "$bot_pid" "$web_pid"
exit $?
