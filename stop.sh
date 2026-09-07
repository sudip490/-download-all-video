#!/bin/sh
# Stops the Video Downloader started by start.sh.
cd "$(dirname "$0")" || exit 1
if [ -f logs/server.pid ] && kill "$(cat logs/server.pid)" 2>/dev/null; then
  rm -f logs/server.pid
  echo "Video Downloader stopped."
else
  echo "Video Downloader was not running."
fi
