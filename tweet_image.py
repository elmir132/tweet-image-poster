"""Post a tweet with up to four images from the command line.

Dry run by default: nothing is sent unless --send is passed.
Credentials come from environment variables, never from the source file.
"""
import argparse
import os
import sys

MAX_TEXT = 280
MAX_IMAGES = 4
MAX_ALT = 1000
SIZE_LIMITS = {".jpg": 5, ".jpeg": 5, ".png": 5, ".webp": 5, ".gif": 15}  # MB
ENV_KEYS = ("TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET")


class ValidationError(ValueError):
    pass


def validate(text, images, alts=None):
    """Return a list of problems; an empty list means the post is valid."""
    problems = []
    if not text.strip() and not images:
        problems.append("nothing to post: give text or at least one image")
    if len(text) > MAX_TEXT:
        problems.append(f"text is {len(text)} characters, the limit is {MAX_TEXT}")
    if len(images) > MAX_IMAGES:
        problems.append(f"{len(images)} images given, the limit is {MAX_IMAGES}")
    kinds = {os.path.splitext(p)[1].lower() == ".gif" for p in images}
    if len(kinds) > 1:
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
    return problems


def credentials(env=None):
    env = os.environ if env is None else env
    missing = [k for k in ENV_KEYS if not env.get(k)]
    if missing:
        raise ValidationError("missing environment variables: " + ", ".join(missing))
    return tuple(env[k] for k in ENV_KEYS)


def post(text, images, alts, upload, create):
    """Upload each image, attach alt text, then create the tweet.

    `upload(path)` returns a media id, `create(text, media_ids)` returns the tweet id.
    Both are injected so the logic can be tested without the network.
    """
    media_ids = [upload(path) for path in images]
    return create(text, media_ids or None)


def _tweepy_clients(env=None):
    import tweepy  # imported lazily so --dry-run and the tests need no dependency

    key, secret, token, token_secret = credentials(env)
    api = tweepy.API(tweepy.OAuth1UserHandler(key, secret, token, token_secret))
    client = tweepy.Client(consumer_key=key, consumer_secret=secret,
                           access_token=token, access_token_secret=token_secret)

    def upload(path):
        return api.media_upload(filename=path).media_id_string

    def create(text, media_ids):
        return client.create_tweet(text=text, media_ids=media_ids).data["id"]

    return upload, create, api


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("text", nargs="?", default="", help="tweet text")
    ap.add_argument("-i", "--image", action="append", default=[], help="image path (repeat, max 4)")
    ap.add_argument("--alt", action="append", default=[], help="alt text, one per image in order")
    ap.add_argument("--send", action="store_true", help="actually post (default is a dry run)")
    args = ap.parse_args(argv)

    problems = validate(args.text, args.image, args.alt)
    if problems:
        print("\n".join("error: " + p for p in problems), file=sys.stderr)
        return 2
    if not args.send:
        print(f"dry run: would post {len(args.text)} characters with {len(args.image)} image(s). Use --send to post.")
        return 0
    try:
        upload, create, api = _tweepy_clients()
    except ValidationError as err:
        print("error: " + str(err), file=sys.stderr)
        return 2
    except ImportError:
        print("error: install the dependency first: pip install -r requirements.txt", file=sys.stderr)
        return 2

    def upload_with_alt(path):
        media_id = upload(path)
        index = args.image.index(path)
        if index < len(args.alt) and args.alt[index]:
            api.create_media_metadata(media_id, args.alt[index])
        return media_id

    tweet_id = post(args.text, args.image, args.alt, upload_with_alt, create)
    print(f"posted: https://x.com/i/status/{tweet_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
