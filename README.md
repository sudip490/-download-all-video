# Video Downloader

A small local website: paste a link, choose what you want, and the file downloads to your computer.
Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) and ffmpeg.

## Run it

```sh
./start.sh
```

Then open http://127.0.0.1:5000 in your browser.

## What it can do

- **Single videos** from about 1,400 sites (see http://127.0.0.1:5000/sites) plus direct links to video files and streams.
- **Playlists and channels** – paste a playlist or channel link, tick the videos you want, get them all in one ZIP.
- **Several links at once** – paste multiple links separated by spaces or new lines.
- **YouTube search** – type words instead of a link and pick from the results.
- **Quality** – best, a specific resolution, audio only, or pick the exact format from a table with codecs and sizes.
- **Containers** – MP4, MKV, WebM or leave the original.
- **Audio formats** – MP3, M4A, Opus, FLAC, WAV or original.
- **Subtitles** – any available language (including auto-generated), embedded in the video or as a separate .srt file.
- **Clip** – download only a part of a video, e.g. from 1:30 to 2:45.
- **Thumbnail and metadata** embedded into the file (title, uploader, chapters, cover art).
- **Remove sponsor segments** on YouTube using SponsorBlock.
- **Split into chapters** – one file per chapter.
- **Login cookies** – from your browser or a cookies.txt file, for private or age-restricted videos.
- **Proxy** and **browser impersonation** for sites that block downloaders or are geo-restricted.
- **Save a copy** into a folder of your choice, so files stay after the one-hour cleanup.
- **Recent downloads** list with links to re-download for one hour.
- **One-click update** of yt-dlp from the page.

## Things that will not work

- Streaming services with copy protection (Netflix, Prime Video, Hotstar premium, SonyLIV premium and similar).
- Sites that re-upload content they do not own. yt-dlp has no support for these and none will be added here.

## Videos that need you to be logged in

Private, age-restricted or followers-only videos need your login. Two ways:

1. **From your browser** – open Settings on the page and choose the browser you are logged into (Firefox works best on Linux).
2. **cookies.txt file** – install a browser extension such as "Get cookies.txt LOCALLY", export cookies for the site,
   save the file as `cookies.txt` next to `app.py`, then choose "cookies.txt file" in Settings.

## Keep yt-dlp fresh

Sites change often. When a link that used to work stops working, click **Update** in Settings on the page.
The app installs the newest yt-dlp and restarts itself. Or from a terminal:

```sh
.venv/bin/pip install -U "yt-dlp[default,curl-cffi,secretstorage]"
```

## First-time setup

```sh
python3 -m venv .venv
.venv/bin/pip install flask "yt-dlp[default,curl-cffi,secretstorage]"
```

ffmpeg must be installed on the system (used to merge video + audio, convert audio, embed subtitles and thumbnails).

## Notes

- Downloaded files are kept in `downloads/` for one hour and then deleted automatically. Use "Also save a copy" in Settings to keep them.
- Playlists are limited to the first 300 entries.
- The server only listens on your own computer (127.0.0.1). Change the host in `app.py` to `0.0.0.0` to reach it from other devices on your Wi-Fi.
- Only download videos you own or have permission to save.
