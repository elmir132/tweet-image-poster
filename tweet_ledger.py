"""A private record of what was posted, so an identical post is not sent twice by accident.

Only hashes, tweet ids and times are stored: never the text or the image paths.
"""
import hashlib
import json
import os
import time
import unicodedata

DEFAULT_LOG = os.path.join(os.path.expanduser("~"), ".tweet-image", "posted.jsonl")
WINDOW_SECONDS = 24 * 3600


def fingerprint(parts, image_paths):
    digest = hashlib.sha256()
    for part in parts:
        digest.update(unicodedata.normalize("NFC", part).strip().encode("utf-8"))
        digest.update(b"\x00")
    for path in image_paths:
        with open(path, "rb") as handle:
            digest.update(hashlib.sha256(handle.read()).digest())
    return digest.hexdigest()


def _entries(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a damaged line is skipped, not fatal
        if isinstance(entry, dict) and "fingerprint" in entry and "ts" in entry:
            entries.append(entry)
    return entries


def recent_duplicate(path, fp, now=None, window=WINDOW_SECONDS):
    """The log entry of an identical post sent within the window, or None."""
    now = time.time() if now is None else now
    for entry in reversed(_entries(path)):
        if entry["fingerprint"] == fp and now - entry["ts"] < window:
            return entry
    return None


def record(path, fp, tweet_ids, now=None):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    line = json.dumps({"ts": int(time.time() if now is None else now), "fingerprint": fp, "tweet_ids": [str(i) for i in tweet_ids]})
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
