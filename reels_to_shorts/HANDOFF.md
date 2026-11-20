# Handoff: Instagram Reels -> GTA 6 YouTube Shorts

**Goal:** Owner's friend records/edits GTA 6 clips and posts them to the owner's Instagram pages (10 pages, see `sources.txt`). Owner wants all videos downloaded in bulk, duplicates removed (keep one copy), and posted to their YouTube channel as Shorts with hook/search-style titles and descriptions.

**Status:** Script written (`pipeline.py`), untested. Not yet run: this cloud session cannot log into Instagram or open a browser for YouTube OAuth. Google OAuth JSON creds live in the owner's other session.

**Steps (run locally or where the creds are):**
1. `pip install yt-dlp google-api-python-client google-auth-oauthlib pillow`; install ffmpeg.
2. Export Instagram cookies (logged-in browser, "Get cookies.txt" extension) to `cookies.txt`.
3. `python pipeline.py download --cookies cookies.txt`
4. `python pipeline.py dedupe`  (sha256 exact match + frame-hash near match; keeps largest file)
5. Review `unique.json`, then `python pipeline.py upload --client-secret client_secret.json` (uploads PRIVATE by default; add `--public` after checking).

**Open items:**
- Captions are template hooks (see `HOOKS`/`TAGS`); upgrade to per-video titles via an LLM or speech transcript if wanted.
- Shorts must be <=3 min and vertical; check lengths.
- YouTube API: unverified projects force private uploads, daily quota ~6 uploads on the default quota.
- One entry in the owner's list was a YouTube link (`youtu.be/ymCHX_Dcc-U`), not an IG page; ask what it is for.
- Owner states they own all pages; some usernames look like third-party clip accounts, so confirm rights before going public.
