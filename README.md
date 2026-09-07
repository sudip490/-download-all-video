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
3. **Paste cookies.txt text** – the same export, but open the file, copy everything and paste it into the box that
   appears in Settings. This is the way to log in on a hosted copy of the app. The text is remembered only in your
   own browser and is sent to the server with each request.

## Keep yt-dlp fresh

Sites change often. When a link that used to work stops working, click **Update** in Settings on the page.
The app installs the newest yt-dlp and restarts itself. Or from a terminal:

```sh
.venv/bin/pip install -U "yt-dlp[default,curl-cffi,secretstorage]"
```

## Host it online (Render, free plan)

The app needs a real server that runs all the time, with ffmpeg and disk space. Vercel, Netlify and similar
"serverless" hosts cannot run it. Render's free plan can, straight from this GitHub repo.

1. Go to https://render.com and sign up with your GitHub account.
2. Click **New** → **Web Service**, pick this repository. Render detects the `Dockerfile`.
3. Leave the instance type on **Free**.
4. Under **Environment variables** add:
   - `APP_PASSWORD` = a password of your choice (required, everyone must type it to use the site)
   - `PUBLIC_MODE` = `1`
5. Click **Deploy**. The first build takes a few minutes. Your site will be at `https://<name>.onrender.com`.

Every `git push` to the repo redeploys automatically, which is also how yt-dlp gets updated.

**Public mode** turns on safe defaults for a server on the internet:

| Setting            | Default in public mode | Change with            |
|--------------------|------------------------|------------------------|
| Password           | required               | `APP_PASSWORD`         |
| Max file size      | 2048 MB                | `MAX_FILESIZE_MB`      |
| Downloads at once  | 2                      | `MAX_JOBS`             |
| Playlist size      | 50 videos              | `MAX_PLAYLIST`         |
| Files kept for     | 30 minutes             | `JOB_TTL_MINUTES`      |
| Browser cookies, save-to-folder, self-update | off | (only in local mode; cookies.txt paste still works) |

Things to know about hosting:

- **Free plan sleeps** after 15 minutes without visitors; the first visit afterwards takes about a minute to wake.
- **YouTube blocks most cloud servers** ("Sign in to confirm you're not a bot"). Other sites usually work.
  A proxy from the Settings panel can help. Running the app at home avoids this completely.
- **Free disk is temporary.** Downloaded files disappear when the service restarts, which is fine since they are only kept briefly anyway.

### Any other server (VPS with Docker)

```sh
git clone https://github.com/sudip490/-download-all-video.git
cd -download-all-video
docker build -t downloader .
docker run -d --name downloader --restart unless-stopped -p 80:5000 -e APP_PASSWORD=choose-a-password downloader
```

Then open `http://<server-ip>/`.

## First-time setup (on your own computer)

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
