#!/bin/sh
# Adds "Video Downloader" to the application menu and starts it automatically at login (Linux desktops).
# Run it once: ./install-launcher.sh        Remove with: ./install-launcher.sh --remove
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="$HOME/.local/share/applications"
AUTO="$HOME/.config/autostart"
if [ "$1" = "--remove" ]; then
  rm -f "$APPS/video-downloader.desktop" "$AUTO/video-downloader.desktop"
  echo "Launcher and autostart removed."
  exit 0
fi
mkdir -p "$APPS" "$AUTO"
cat > "$APPS/video-downloader.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Video Downloader
Comment=Download videos from YouTube and 1400 other sites
Exec="$DIR/start.sh"
Icon=folder-download
Terminal=false
Categories=Network;AudioVideo;
DESKTOP
cat > "$AUTO/video-downloader.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Video Downloader (background)
Comment=Keeps the Video Downloader ready at http://127.0.0.1:5000
Exec="$DIR/start.sh" --no-browser
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
chmod +x "$DIR/start.sh" "$DIR/stop.sh"
echo "Installed: 'Video Downloader' is now in your application menu and will start at login."
echo "Open it any time at http://127.0.0.1:5000"
