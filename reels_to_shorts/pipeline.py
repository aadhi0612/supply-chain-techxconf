#!/usr/bin/env python3
"""Download Instagram Reels from your pages -> dedupe -> make GTA 6 Shorts titles -> upload to YouTube.
Usage:
  python pipeline.py download --cookies cookies.txt
  python pipeline.py dedupe
  python pipeline.py upload --client-secret client_secret.json [--public]
Needs: pip install yt-dlp google-api-python-client google-auth-oauthlib pillow ; ffmpeg on PATH.
"""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).parent
DL = ROOT / "downloads"
KEEP = ROOT / "unique.json"
DONE = ROOT / "uploaded.json"
TAGS = ["GTA 6", "GTA VI", "GTA 6 gameplay", "GTA 6 leaks", "GTA 6 shorts", "Grand Theft Auto 6", "Vice City", "Rockstar Games"]
HOOKS = ["You WON'T believe this in GTA 6", "This GTA 6 detail is insane", "GTA 6 gameplay moment nobody expected",
         "Rockstar hid this in GTA 6", "GTA 6 is on another level"]

def sources():
    return [l.strip() for l in (ROOT / "sources.txt").read_text().splitlines() if l.strip().startswith("https://www.instagram.com/")]

def download(a):
    DL.mkdir(exist_ok=True)
    for url in sources():
        user = url.rstrip("/").split("/")[-1]
        cmd = ["yt-dlp", "--cookies", a.cookies, "-o", str(DL / f"{user}__%(id)s.%(ext)s"),
               "--write-info-json", "--download-archive", str(ROOT / "archive.txt"), url + "/reels/"]
        subprocess.run(cmd)

def frame_hash(video):
    from PIL import Image
    import tempfile, glob, os
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", "fps=1,scale=8:8,format=gray",
                        "-frames:v", "8", f"{t}/f%02d.png"])
        bits = ""
        for f in sorted(glob.glob(f"{t}/f*.png")):
            px = list(Image.open(f).convert("L").getdata())
            m = sum(px) / len(px)
            bits += "".join("1" if p > m else "0" for p in px)
        return bits

def dedupe(a):
    seen_sha, seen_ph, keep = {}, [], []
    vids = sorted(DL.glob("*.mp4"), key=lambda p: -p.stat().st_size)  # keep best quality
    for v in vids:
        sha = hashlib.sha256(v.read_bytes()).hexdigest()
        if sha in seen_sha:
            print("exact dup:", v.name, "==", seen_sha[sha]); continue
        seen_sha[sha] = v.name
        ph = frame_hash(v)
        dup = next((n for h, n in seen_ph if ph and len(h) == len(ph) and
                    sum(x != y for x, y in zip(h, ph)) / len(ph) < 0.12), None)
        if dup:
            print("near dup:", v.name, "~", dup); continue
        seen_ph.append((ph, v.name)); keep.append(v.name)
    KEEP.write_text(json.dumps(keep, indent=1))
    print(f"{len(keep)} unique of {len(vids)}")

def caption(video):
    info = DL / (Path(video).stem + ".info.json")
    orig = ""
    if info.exists():
        orig = (json.loads(info.read_text()).get("description") or "").split("\n")[0][:60]
    n = int(hashlib.md5(video.encode()).hexdigest(), 16)
    title = f"{HOOKS[n % len(HOOKS)]} #Shorts"[:100]
    desc = (f"{orig}\n\n" if orig else "") + "GTA 6 gameplay clips, leaks, details and moments.\n\n" + \
           " ".join("#" + t.replace(" ", "") for t in TAGS[:5]) + " #Shorts"
    return title, desc

def upload(a):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = InstalledAppFlow.from_client_secrets_file(a.client_secret,
            ["https://www.googleapis.com/auth/youtube.upload"]).run_local_server(port=0)
    yt = build("youtube", "v3", credentials=creds)
    done = json.loads(DONE.read_text()) if DONE.exists() else {}
    for name in json.loads(KEEP.read_text()):
        if name in done: continue
        title, desc = caption(name)
        body = {"snippet": {"title": title, "description": desc, "tags": TAGS, "categoryId": "20"},
                "status": {"privacyStatus": "public" if a.public else "private", "selfDeclaredMadeForKids": False}}
        r = yt.videos().insert(part="snippet,status", body=body,
                media_body=MediaFileUpload(str(DL / name), resumable=True)).execute()
        done[name] = r["id"]; DONE.write_text(json.dumps(done, indent=1))
        print("uploaded", name, "->", r["id"], title)

if __name__ == "__main__":
    p = argparse.ArgumentParser(); s = p.add_subparsers(dest="cmd", required=True)
    d = s.add_parser("download"); d.add_argument("--cookies", required=True)
    s.add_parser("dedupe")
    u = s.add_parser("upload"); u.add_argument("--client-secret", required=True); u.add_argument("--public", action="store_true")
    a = p.parse_args(); {"download": download, "dedupe": dedupe, "upload": upload}[a.cmd](a)
