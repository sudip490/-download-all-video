# Video Downloader

A small local website: paste a video link, choose a quality, and the file downloads to your computer.
Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) and ffmpeg.

## Run it

```sh
./start.sh
```

Then open http://127.0.0.1:5000 in your browser.

## Which sites work?

Open http://127.0.0.1:5000/sites for the full, searchable list (about 1,400 names).
Popular ones: YouTube, Facebook, Instagram, TikTok, Twitter/X, Vimeo, Dailymotion, Reddit, Twitch,
SoundCloud, Pinterest, LinkedIn, Telegram, VK. Direct links to a video file (ending in .mp4, .m3u8 and so on) also work.

Things that will not work:

- Streaming services with copy protection (Netflix, Prime Video, Hotstar premium, SonyLIV premium and similar).
- Sites that re-upload content they do not own. yt-dlp has no support for these and none will be added here.

## Videos that need you to be logged in

Private, age-restricted or followers-only videos need your login. Two ways:

1. **From your browser** – in the "Login cookies" dropdown choose the browser you are logged into (Firefox works best on Linux).
2. **cookies.txt file** – install a browser extension such as "Get cookies.txt LOCALLY", export cookies for the site,
   save the file as `cookies.txt` next to `app.py`, then choose "cookies.txt file" in the dropdown.

## Keep yt-dlp fresh

Sites change often. When a link that used to work stops working, click **Update** next to the yt-dlp version on the page.
The app installs the newest yt-dlp and restarts itself. Or from a terminal:

```sh
.venv/bin/pip install -U "yt-dlp[default,curl-cffi,secretstorage]"
```

## First-time setup

```sh
python3 -m venv .venv
.venv/bin/pip install flask "yt-dlp[default,curl-cffi,secretstorage]"
```

ffmpeg must be installed on the system (used to merge video + audio and to make MP3s).

## Notes

- Downloaded files are kept in `downloads/` for one hour and then deleted automatically.
- The server only listens on your own computer (127.0.0.1). Change `host` in `app.py` to `0.0.0.0` to reach it from other devices on your Wi-Fi.
- Only download videos you own or have permission to save.
