"""
Publish the signage from the Nextcloud Play folder (runs on GitHub Actions, see .github/workflows).

The Play folder (Marketing/SIGNAGE/Signage_Slides_Play) is shared read-only by a public link,
stored as the repository secret NEXTCLOUD_SHARE. Everything in it plays; the file names are the
playlist ("005_name.png": three-digit position first), settings.txt holds "seconds per slide: 10".

Every run:
1. list the folder (WebDAV through the share link)
2. if the listing differs from the one seen last time: remember it and stop. The folder is still
   changing (an export, an archive move, renames syncing up); publish only a state that has been
   stable for one whole interval.
3. if it is stable and not yet published: download what is new (by ETag), write slides/ and videos/
   (content-hash names; images as PNG or JPEG q92, whichever is smaller), slides.json, prune.

Keep order_names() and encode() in step with indesign-mcp/publish_signage_web.py (local fallback).
Exit code 0 always when nothing broke; the workflow commits whatever changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "tools", "state.json")
IMAGE_EXT = (".png", ".jpg", ".jpeg")
VIDEO_EXT = (".mp4",)
DEFAULT_DURATION = 30
GITHUB_LIMIT = 100 * 1024 * 1024
JPEG = [cv2.IMWRITE_JPEG_QUALITY, 92, cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
POSITION = re.compile(r"^(\d{3})_")
DAV = "{DAV:}"


def kind(name: str):
    n = name.lower()
    return "slide" if n.endswith(IMAGE_EXT) else "video" if n.endswith(VIDEO_EXT) else None


def position(name: str):
    m = POSITION.search(name)
    return int(m.group(1)) if m else None


def order_names(names):
    """Same rules as publish_signage_web.order_names: numbered files by number (a video first on a
    tie), then unnumbered slides, then unnumbered videos; an unnumbered fresh export keeps its own
    order with numbered videos slotted in at their positions."""
    names = [n for n in names if kind(n)]
    numbered = [n for n in names if position(n) is not None]
    loose = sorted((n for n in names if position(n) is None), key=lambda n: (kind(n) == "video", n))
    if any(kind(n) == "slide" for n in numbered) or not numbered:
        return sorted(numbered, key=lambda n: (position(n), kind(n) != "video", n)) + loose
    order = [n for n in loose if kind(n) == "slide"]
    for n in sorted(numbered, key=lambda n: (position(n), n)):
        order.insert(min(position(n) - 1, len(order)), n)
    return order + [n for n in loose if kind(n) == "video"]


def encode(raw: bytes, ext: str):
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    ok, jpg = cv2.imencode(".jpg", img, JPEG)
    ext = ext.lower().lstrip(".").replace("jpeg", "jpg")
    if ok and len(jpg) < len(raw):
        return jpg.tobytes(), "jpg"
    return raw, ext


class Share:
    def __init__(self, link: str):
        link = link.strip().rstrip("/")
        self.token = link.rsplit("/", 1)[-1]
        self.base = link.split("/s/")[0] + f"/public.php/dav/files/{self.token}/"
        self.s = requests.Session()

    def listing(self):
        r = self.s.request("PROPFIND", self.base, headers={"Depth": "1"}, timeout=60)
        r.raise_for_status()
        out = {}
        for resp in ET.fromstring(r.content).iter(f"{DAV}response"):
            href = urllib.parse.unquote(resp.findtext(f"{DAV}href") or "")
            name = href.rstrip("/").rsplit("/", 1)[-1]
            if href.endswith("/") or not name:
                continue
            prop = resp.find(f".//{DAV}prop")
            out[name] = {"etag": (prop.findtext(f"{DAV}getetag") or "").strip('"'),
                         "size": int(prop.findtext(f"{DAV}getcontentlength") or 0)}
        return out

    def get(self, name: str) -> bytes:
        r = self.s.get(self.base + urllib.parse.quote(name), timeout=600)
        r.raise_for_status()
        return r.content


def video_seconds(path: str):
    c = cv2.VideoCapture(path)
    fps, frames = c.get(cv2.CAP_PROP_FPS), c.get(cv2.CAP_PROP_FRAME_COUNT)
    c.release()
    return round(frames / fps, 1) if fps and frames else None


def player_version() -> int:
    m = re.search(r"const PLAYER = (\d+);", open(os.path.join(ROOT, "index.html"), encoding="utf-8").read())
    return int(m.group(1))


def main() -> None:
    link = os.environ.get("NEXTCLOUD_SHARE", "")
    if not link:
        sys.exit("NEXTCLOUD_SHARE is not set (repository secret)")
    share = Share(link)
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    listing = share.listing()
    fingerprint = hashlib.sha1(json.dumps(sorted((n, v["etag"]) for n, v in listing.items())).encode()).hexdigest()

    if fingerprint == state.get("published"):
        print("nothing new in the Play folder")
        return
    if fingerprint != state.get("seen"):
        state["seen"] = fingerprint
        state["seen_at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1)
        print("the Play folder changed: waiting one interval until it is stable before publishing")
        return

    files = state.setdefault("files", {})   # etag -> {"src", "duration"} of what was published
    slides = []
    for name in order_names(listing):
        meta = listing[name]
        known = files.get(meta["etag"])
        if known and os.path.exists(os.path.join(ROOT, known["src"])):
            entry = dict(known)
        else:
            raw = share.get(name)
            if kind(name) == "video":
                if len(raw) > GITHUB_LIMIT:
                    print(f"SKIPPED {name}: {len(raw) / 1e6:.0f} MB is over GitHub's 100 MB file limit")
                    continue
                src = f"videos/{hashlib.sha1(raw).hexdigest()[:16]}.mp4"
                path = os.path.join(ROOT, src)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").write(raw)
                entry = {"type": "video", "src": src, "duration": video_seconds(path), "size": len(raw)}
            else:
                data, ext = encode(raw, os.path.splitext(name)[1])
                src = f"slides/{hashlib.sha1(data).hexdigest()[:16]}.{ext}"
                path = os.path.join(ROOT, src)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if not os.path.exists(path):
                    open(path, "wb").write(data)
                entry = {"src": src}
            files[meta["etag"]] = entry
        slides.append({**entry, "name": name})

    duration = DEFAULT_DURATION
    if "settings.txt" in listing:
        m = re.search(r"seconds per slide\s*[:=]\s*([\d.]+)", share.get("settings.txt").decode("utf-8", "replace"), re.I)
        if m and float(m.group(1)) > 0:
            duration = float(m.group(1))

    if not slides:
        print("the Play folder has nothing to play: keeping the current set")
        return
    keep = {s["src"] for s in slides}
    for sub in ("slides", "videos"):
        folder = os.path.join(ROOT, sub)
        for n in os.listdir(folder) if os.path.isdir(folder) else []:
            if f"{sub}/{n}" not in keep:
                os.remove(os.path.join(folder, n))
    state["files"] = {e: v for e, v in files.items() if v["src"] in keep}

    set_id = hashlib.sha1("|".join(s["src"] for s in slides).encode()).hexdigest()[:12]
    manifest = {"id": set_id, "published": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "player": player_version(), "duration": duration, "slides": slides}
    json.dump(manifest, open(os.path.join(ROOT, "slides.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    state["published"] = fingerprint
    json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1)
    videos = sum(1 for s in slides if s.get("type") == "video")
    print(f"published set {set_id}: {len(slides) - videos} slides + {videos} videos, {duration:g} s per slide")


if __name__ == "__main__":
    main()
