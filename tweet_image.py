"""Post a tweet, or a numbered thread, with up to four images from the command line.

Dry run by default: nothing is sent unless --send is passed.
Credentials come from environment variables (or an --env-file), never from the source file.
"""
import argparse
import contextlib
import json
import os
import sys
import tempfile
import time

import tweet_envfile as envfile
import tweet_ledger as ledger
import tweet_media as media
import tweet_threads as threads
from tweet_text import (MAX_WEIGHT, TLDS, URL_RE, URL_WEIGHT, _char_weight,  # noqa: F401 (re-exported)
                        weighted_length)

__version__ = "0.3.0"

MAX_IMAGES = 4
MAX_ALT = 1000
MAX_DIMENSION = 8192
SIZE_LIMITS = {".jpg": 5, ".jpeg": 5, ".png": 5, ".webp": 5, ".gif": 15}  # MB
ENV_KEYS = ("TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET")
SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
}
SPEC_KEYS = {"text": str, "images": list, "alts": list, "reply_to": str, "thread": bool,
             "require_alt": bool, "strip_metadata": bool}


class ValidationError(ValueError):
    pass


class ThreadPostError(Exception):
    """Posting stopped part way. `posted` holds the ids that did go out; `cause` is the error."""

    def __init__(self, posted, cause):
        super().__init__(str(cause))
        self.posted = posted
        self.cause = cause


def looks_like(path, ext):
    """True if the file starts with the magic bytes of its extension (not a renamed text file)."""
    with open(path, "rb") as fh:
        head = fh.read(12)
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(sig) for sig in SIGNATURES[ext])


def validate(text, images, alts=None, reply_to=None, require_alt=False):
    """Return a list of problems; an empty list means the post is valid."""
    problems = []
    if not text.strip() and not images:
        problems.append("nothing to post: give text or at least one image")
    weight = weighted_length(text)
    if weight > MAX_WEIGHT:
        problems.append(f"text weighs {weight}, the limit is {MAX_WEIGHT} (URLs count {URL_WEIGHT}, emoji and CJK count 2)")
    if len(images) > MAX_IMAGES:
        problems.append(f"{len(images)} images given, the limit is {MAX_IMAGES}")
    if len({os.path.splitext(p)[1].lower() == ".gif" for p in images}) > 1:
        problems.append("a GIF cannot be mixed with other images in one post")
    for path in images:
        ext = os.path.splitext(path)[1].lower()
        if ext not in SIZE_LIMITS:
            problems.append(f"{path}: unsupported type {ext or '(none)'}")
        elif not os.path.isfile(path):
            problems.append(f"{path}: file not found")
        elif os.path.getsize(path) > SIZE_LIMITS[ext] * 1024 * 1024:
            problems.append(f"{path}: larger than {SIZE_LIMITS[ext]} MB")
        elif not looks_like(path, ext):
            problems.append(f"{path}: content is not a real {ext} image")
    for alt in alts or []:
        if len(alt) > MAX_ALT:
            problems.append(f"alt text is {len(alt)} characters, the limit is {MAX_ALT}")
    if alts and len(alts) > len(images):
        problems.append("more --alt values than images")
    if require_alt:
        for index, path in enumerate(images):
            if index >= len(alts or []) or not alts[index].strip():
                problems.append(f"image {index + 1} ({os.path.basename(path)}) has no alt text and --require-alt is set")
    if reply_to is not None and not reply_to.isdigit():
        problems.append("--reply-to must be a numeric tweet id")
    return problems


def inspect_images(paths):
    """media.image_info for each path; an unreadable file gives an empty dict."""
    infos = []
    for path in paths:
        try:
            infos.append(media.image_info(path))
        except OSError:
            infos.append({})
    return infos


def warnings_for(paths, alts, infos):
    """Things worth knowing that do not stop a post."""
    warnings = []
    for index, (path, info) in enumerate(zip(paths, infos)):
        name = os.path.basename(path)
        if info.get("gps"):
            warnings.append(f"{name} contains GPS location data; use --strip-metadata to remove it before upload")
        if max(info.get("width") or 0, info.get("height") or 0) > MAX_DIMENSION:
            warnings.append(f"{name} is {info.get('width')}x{info.get('height')} px; X's documented maximum, as I know it, is {MAX_DIMENSION}")
        if index >= len(alts) or not alts[index].strip():
            warnings.append(f"image {index + 1} ({name}) has no alt text, so people using screen readers get no description")
    return warnings


def credentials(env=None):
    env = os.environ if env is None else env
    missing = [k for k in ENV_KEYS if not env.get(k)]
    if missing:
        raise ValidationError("missing environment variables: " + ", ".join(missing))
    return tuple(env[k] for k in ENV_KEYS)


def post(text, images, alts, upload, create, set_alt=None, reply_to=None):
    """Upload each image, attach its alt text, then create the tweet.

    `upload(path)` returns a media id, `set_alt(media_id, alt)` attaches alt text and
    `create(text, media_ids, reply_to)` returns the tweet id. All are injected so the
    logic can be tested without the network.
    """
    media_ids = []
    for index, path in enumerate(images):
        media_id = upload(path)
        alt = alts[index] if index < len(alts) else ""
        if alt and set_alt:
            set_alt(media_id, alt)
        media_ids.append(media_id)
    return create(text or None, media_ids or None, reply_to)


def post_thread(parts, images, alts, upload, create, set_alt=None, reply_to=None):
    """Post the first part with the images, then each later part as a reply to the one before.
    Returns the list of tweet ids. If something fails part way, ThreadPostError says which
    ids were already posted so the thread can be continued by hand with --reply-to."""
    ids = []
    try:
        ids.append(post(parts[0], images, alts, upload, create, set_alt, reply_to))
        for text in parts[1:]:
            ids.append(create(text, None, ids[-1]))
    except Exception as err:  # network and API errors differ by tweepy version
        raise ThreadPostError(ids, err)
    return ids


def _status(err):
    return getattr(getattr(err, "response", None), "status_code", None)


def _retryable(err, creating):
    """A rate limit (429) can always be retried: the post was not accepted. A server error or
    a dropped connection is only retried for uploads and alt text, because a create request
    that timed out may already have posted, and retrying it could post twice."""
    if _status(err) == 429:
        return True
    if creating:
        return False
    status = _status(err)
    return (status is not None and status >= 500) or isinstance(err, (ConnectionError, TimeoutError)) \
        or type(err).__name__ in ("ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout")


def with_retries(function, retries, creating=False, sleep=time.sleep):
    if function is None:
        return None

    def wrapped(*args):
        for attempt in range(retries + 1):
            try:
                return function(*args)
            except Exception as err:
                if attempt >= retries or not _retryable(err, creating):
                    raise
                headers = getattr(getattr(err, "response", None), "headers", None) or {}
                try:
                    delay = min(60, max(0, float(headers.get("Retry-After", 2 ** attempt))))
                except (TypeError, ValueError):
                    delay = 2 ** attempt
                sleep(delay)
    return wrapped


def _tweepy_functions(env=None):
    import tweepy  # imported lazily so a dry run and the tests need no dependency

    key, secret, token, token_secret = credentials(env)
    api = tweepy.API(tweepy.OAuth1UserHandler(key, secret, token, token_secret))
    client = tweepy.Client(consumer_key=key, consumer_secret=secret,
                           access_token=token, access_token_secret=token_secret)

    def upload(path):
        return api.media_upload(filename=path).media_id_string

    def set_alt(media_id, alt):
        api.create_media_metadata(media_id, alt)

    def create(text, media_ids, reply_to):
        kwargs = {"in_reply_to_tweet_id": reply_to} if reply_to else {}
        return client.create_tweet(text=text, media_ids=media_ids, **kwargs).data["id"]

    def whoami():
        return client.get_me().data.username

    return upload, set_alt, create, whoami


def read_text(arg_text, file_path):
    if file_path == "-":
        return sys.stdin.read().strip()
    if file_path:
        with open(file_path, encoding="utf-8") as source:
            return source.read().strip()
    return arg_text


def load_spec(path):
    """A post described in JSON: text, images, alts, reply_to, thread, require_alt, strip_metadata.
    Image paths are relative to the JSON file. Unknown keys and wrong types are errors."""
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle)
    if not isinstance(spec, dict):
        raise ValueError("the JSON file must contain one object")
    unknown = sorted(set(spec) - set(SPEC_KEYS))
    if unknown:
        raise ValueError("unknown key(s): " + ", ".join(unknown))
    for key, kind in SPEC_KEYS.items():
        if key in spec and not isinstance(spec[key], kind):
            raise ValueError(f"'{key}' must be {'a list' if kind is list else 'text' if kind is str else 'true or false'}")
    for key in ("images", "alts"):
        if key in spec and not all(isinstance(item, str) for item in spec[key]):
            raise ValueError(f"'{key}' must be a list of text values")
    base = os.path.dirname(os.path.abspath(path))
    if "images" in spec:
        spec["images"] = [p if os.path.isabs(p) else os.path.join(base, p) for p in spec["images"]]
    return spec


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("text", nargs="?", default="", help="tweet text")
    ap.add_argument("-f", "--file", help="read the text from a file, or - for stdin")
    ap.add_argument("-i", "--image", action="append", default=[], help="image path (repeat, max 4)")
    ap.add_argument("--alt", action="append", default=[], help="alt text, one per image in order")
    ap.add_argument("--reply-to", help="id of a tweet to reply to")
    ap.add_argument("--send", action="store_true", help="actually post (default is a dry run)")
    ap.add_argument("--thread", action="store_true", help="split text longer than one post into a numbered thread (or split at lines containing only ---)")
    ap.add_argument("--no-numbers", action="store_true", help="do not add (1/3) counters to a thread")
    ap.add_argument("--require-alt", action="store_true", help="refuse to post an image without alt text")
    ap.add_argument("--strip-metadata", action="store_true", help="upload copies of JPEG, PNG and WebP images without EXIF/GPS and other metadata")
    ap.add_argument("--info", action="store_true", help="print format, size, metadata and GPS findings for the images, then exit")
    ap.add_argument("--from-json", metavar="FILE", help="read the post (text, images, alts, ...) from a JSON file; command-line options win")
    ap.add_argument("--env-file", metavar="FILE", help="read the four TWITTER_* values from a KEY=VALUE file")
    ap.add_argument("--whoami", action="store_true", help="check the credentials by asking X which account they belong to, then exit")
    ap.add_argument("--json", action="store_true", help="print the result as JSON on standard output")
    ap.add_argument("--retries", type=int, default=2, help="retries after a rate limit or a failed upload (default 2)")
    ap.add_argument("--force", action="store_true", help="send even if an identical post was sent in the last 24 hours")
    ap.add_argument("--log", metavar="FILE", help="where to record posts for the duplicate check (default ~/.tweet-image/posted.jsonl)")
    ap.add_argument("--no-log", action="store_true", help="do not record posts or check for duplicates")
    ap.add_argument("--version", action="version", version=f"tweet-image {__version__}")
    return ap


def _fail(message, code=2):
    print("error: " + message, file=sys.stderr)
    return code


def _log_path(args, env):
    if args.no_log:
        return None
    if args.log:
        return args.log
    configured = env.get("TWEET_IMAGE_LOG")
    if configured is not None:
        return configured or None  # an empty value switches the log off
    return ledger.DEFAULT_LOG


def _preview(text, width=60):
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= width else one_line[:width - 1] + "…"


def _print_info(images, infos):
    for path, info in zip(images, infos):
        size = f"{info['width']}x{info['height']} px" if info.get("width") else "size unknown"
        found = ", ".join(info.get("labels") or []) or "none"
        print(f"{path}: {info.get('format') or 'unknown format'}, {size}, {info.get('bytes', 0):,} bytes, metadata: {found}")


def main(argv=None, functions=None, env=None, sleep=time.sleep):
    args = build_parser().parse_args(argv)
    env_map = dict(os.environ) if env is None else dict(env)
    if args.env_file:
        try:
            env_map.update(envfile.load_env_file(args.env_file))
        except envfile.EnvFileError as err:
            return _fail(str(err))
    if args.retries < 0 or args.retries > 5:
        return _fail("--retries must be between 0 and 5")

    if args.whoami:
        return _whoami(functions, env_map)

    spec = {}
    if args.from_json:
        try:
            spec = load_spec(args.from_json)
        except (OSError, ValueError) as err:
            return _fail(f"cannot use {args.from_json}: {err}")

    if args.file and args.text:
        return _fail("give text either as an argument or with --file, not both")
    try:
        text = read_text(args.text or spec.get("text", ""), args.file)
    except OSError as err:
        return _fail(f"cannot read {args.file}: {err.strerror}")
    images = args.image or spec.get("images", [])
    alts = args.alt or spec.get("alts", [])
    reply_to = args.reply_to or spec.get("reply_to")
    want_thread = args.thread or spec.get("thread", False)
    require_alt = args.require_alt or spec.get("require_alt", False)
    strip = args.strip_metadata or spec.get("strip_metadata", False)

    if args.info:
        if not images:
            return _fail("--info needs at least one --image")
        infos = inspect_images(images)
        if args.json:
            print(json.dumps([{"path": p, **i} for p, i in zip(images, infos)], indent=2))
        else:
            _print_info(images, infos)
        return 0

    try:
        parts = threads.split_thread(text, numbered=not args.no_numbers) if want_thread else [text]
    except threads.ThreadError as err:
        return _fail(str(err))
    if not parts:
        parts = [""]

    problems = []
    for number, part in enumerate(parts, 1):
        first = number == 1
        found = validate(part, images if first else [], alts if first else [], reply_to if first else None, require_alt if first else False)
        prefix = f"post {number}/{len(parts)}: " if len(parts) > 1 else ""
        problems += [prefix + p for p in found]
    if problems:
        print("\n".join("error: " + p for p in problems), file=sys.stderr)
        return 2

    infos = inspect_images(images)
    warnings = warnings_for(images, alts, infos)
    if strip:
        warnings = [w for w in warnings if "GPS" not in w]
    for warning in warnings:
        print("warning: " + warning, file=sys.stderr)

    with contextlib.ExitStack() as stack:
        upload_paths, stripped = list(images), {}
        if strip and images:
            folder = stack.enter_context(tempfile.TemporaryDirectory())
            for index, path in enumerate(images):
                dest = os.path.join(folder, f"{index}{os.path.splitext(path)[1].lower()}")
                try:
                    removed = media.strip_metadata(path, dest)
                except (media.MediaError, OSError) as err:
                    return _fail(str(err))
                if removed:
                    upload_paths[index] = dest
                    stripped[path] = removed
                    print(f"stripped from {os.path.basename(path)}: {', '.join(removed)}")
        weights = [weighted_length(p) for p in parts]

        if not args.send:
            if args.json:
                print(json.dumps({"dry_run": True, "posts": [{"text": p, "weight": w} for p, w in zip(parts, weights)],
                                  "images": images, "stripped": stripped, "warnings": warnings}, indent=2))
            elif len(parts) == 1:
                print(f"dry run: valid post, weight {weights[0]}/{MAX_WEIGHT}, "
                      f"{len(images)} image(s). Use --send to post.")
            else:
                on_first = f", {len(images)} image(s) on the first post" if images else ""
                print(f"dry run: valid thread of {len(parts)} posts{on_first}. Use --send to post.")
                for number, (part, weight) in enumerate(zip(parts, weights), 1):
                    print(f"  {number}/{len(parts)} weight {weight}/{MAX_WEIGHT}: {_preview(part)}")
            return 0

        try:
            fns = functions or _tweepy_functions(env_map)
        except ValidationError as err:
            return _fail(str(err))
        except ImportError:
            return _fail("install the dependency first: pip install -r requirements.txt")
        upload = with_retries(fns[0], args.retries, False, sleep)
        set_alt = with_retries(fns[1], args.retries, False, sleep)
        create = with_retries(fns[2], args.retries, True, sleep)

        log = _log_path(args, env_map)
        fingerprint = None
        if log:
            fingerprint = ledger.fingerprint(parts, images)
            earlier = ledger.recent_duplicate(log, fingerprint)
            if earlier and not args.force:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(earlier["ts"]))
                return _fail(f"an identical post was sent at {when} (tweet {', '.join(earlier['tweet_ids'])}); "
                             "use --force to send it again")

        try:
            ids = post_thread(parts, upload_paths, alts, upload, create, set_alt, reply_to)
        except ThreadPostError as err:
            if err.posted:
                print(f"error: posting stopped after {len(err.posted)} of {len(parts)} posts "
                      f"({type(err.cause).__name__}): {err.cause}. Already posted: {', '.join(map(str, err.posted))}. "
                      f"To continue, post the rest with --reply-to {err.posted[-1]}.", file=sys.stderr)
            else:
                print(f"error: posting failed ({type(err.cause).__name__}): {err.cause}. "
                      "Nothing was posted; uploaded images are not attached to any tweet.", file=sys.stderr)
            return 1
        if log:
            try:
                ledger.record(log, fingerprint, ids)
            except OSError as err:
                print(f"warning: could not write the post log ({err.strerror})", file=sys.stderr)
        urls = [f"https://x.com/i/status/{i}" for i in ids]
        if args.json:
            print(json.dumps({"dry_run": False, "posted": [{"id": str(i), "url": u} for i, u in zip(ids, urls)], "warnings": warnings}, indent=2))
        else:
            for url in urls:
                print(f"posted: {url}")
        return 0


def _whoami(functions, env_map):
    try:
        fns = functions or _tweepy_functions(env_map)
    except ValidationError as err:
        return _fail(str(err))
    except ImportError:
        return _fail("install the dependency first: pip install -r requirements.txt")
    if len(fns) < 4 or fns[3] is None:
        return _fail("this backend cannot look up the account")
    try:
        name = fns[3]()
    except Exception as err:  # network and API errors differ by tweepy version
        print(f"error: could not check the credentials ({type(err).__name__}): {err}", file=sys.stderr)
        return 1
    print(f"credentials work: authenticated as @{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
