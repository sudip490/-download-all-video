#!/bin/sh
# Starts the Video Downloader on this computer (if it is not already running) and opens it in the browser.
# Usage: ./start.sh            start and open the browser
#        ./start.sh --no-browser   start only (used by autostart at login)
cd "$(dirname "$0")" || exit 1
URL=http://127.0.0.1:5000
PY=.venv/bin/python
is_up() { "$PY" -c "import urllib.request; urllib.request.urlopen('$URL/healthz', timeout=2)" >/dev/null 2>&1; }

if ! is_up; then
  mkdir -p logs
  nohup "$PY" app.py >> logs/server.log 2>&1 &
  echo $! > logs/server.pid
  i=0
  while [ $i -lt 40 ] && ! is_up; do sleep 0.5; i=$((i+1)); done
fi

if is_up; then
  [ -t 1 ] && echo "Video Downloader is running at $URL  (stop it with ./stop.sh)"
  [ "$1" = "--no-browser" ] || xdg-open "$URL" >/dev/null 2>&1 || true
else
  echo "Could not start the Video Downloader. See logs/server.log" >&2
  exit 1
fi
