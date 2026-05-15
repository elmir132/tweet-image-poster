"""Post a tweet with up to four images from the command line.

Dry run by default: nothing is sent unless --send is passed.
Credentials come from environment variables, never from the source file.
"""
import argparse
import os
import re
import sys
import unicodedata

MAX_WEIGHT = 280
URL_WEIGHT = 23
MAX_IMAGES = 4
MAX_ALT = 1000
SIZE_LIMITS = {".jpg": 5, ".jpeg": 5, ".png": 5, ".webp": 5, ".gif": 15}  # MB
ENV_KEYS = ("TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET")
URL_RE = re.compile(r"https?://\S+")


class ValidationError(ValueError):
    pass


def _char_weight(ch):
    cp = ord(ch)
    light = cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223 or 8242 <= cp <= 8247
    return 1 if light else 2


def weighted_length(text):
    """Length as X counts it: every URL is 23, CJK characters and emoji count 2.

    This follows the published twitter-text rules as I know them; X may change them.
    """
    text = unicodedata.normalize("NFC", text)
    urls = URL_RE.findall(text)
    rest = URL_RE.sub("", text)
    return URL_WEIGHT * len(urls) + sum(_char_weight(c) for c in rest)


def validate(text, images, alts=None, reply_to=None):
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
    for alt in alts or []:
        if len(alt) > MAX_ALT:
            problems.append(f"alt text is {len(alt)} characters, the limit is {MAX_ALT}")
    if alts and len(alts) > len(images):
        problems.append("more --alt values than images")
    if reply_to is not None and not reply_to.isdigit():
        problems.append("--reply-to must be a numeric tweet id")
    return problems


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
    return create(text, media_ids or None, reply_to)


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

    return upload, set_alt, create


def read_text(arg_text, file_path):
    if file_path == "-":
        return sys.stdin.read().strip()
    if file_path:
        with open(file_path, encoding="utf-8") as source:
            return source.read().strip()
    return arg_text


def main(argv=None, functions=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("text", nargs="?", default="", help="tweet text")
    ap.add_argument("-f", "--file", help="read the text from a file, or - for stdin")
    ap.add_argument("-i", "--image", action="append", default=[], help="image path (repeat, max 4)")
    ap.add_argument("--alt", action="append", default=[], help="alt text, one per image in order")
    ap.add_argument("--reply-to", help="id of a tweet to reply to")
    ap.add_argument("--send", action="store_true", help="actually post (default is a dry run)")
    args = ap.parse_args(argv)

    if args.file and args.text:
        print("error: give text either as an argument or with --file, not both", file=sys.stderr)
        return 2
    try:
        text = read_text(args.text, args.file)
    except OSError as err:
        print(f"error: cannot read {args.file}: {err.strerror}", file=sys.stderr)
        return 2
    problems = validate(text, args.image, args.alt, args.reply_to)
    if problems:
        print("\n".join("error: " + p for p in problems), file=sys.stderr)
        return 2
    if not args.send:
        print(f"dry run: valid post, weight {weighted_length(text)}/{MAX_WEIGHT}, "
              f"{len(args.image)} image(s). Use --send to post.")
        return 0
    try:
        upload, set_alt, create = functions or _tweepy_functions()
    except ValidationError as err:
        print("error: " + str(err), file=sys.stderr)
        return 2
    except ImportError:
        print("error: install the dependency first: pip install -r requirements.txt", file=sys.stderr)
        return 2
    try:
        tweet_id = post(text, args.image, args.alt, upload, create, set_alt, args.reply_to)
    except Exception as err:  # network and API errors differ by tweepy version
        print(f"error: posting failed ({type(err).__name__}): {err}. "
              "Nothing was posted; uploaded images are not attached to any tweet.", file=sys.stderr)
        return 1
    print(f"posted: https://x.com/i/status/{tweet_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
