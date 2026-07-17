# tweet-image-poster

A command-line tool that posts a tweet, or a numbered thread, with up to four images. It started as a 20-line script with credentials pasted into the source. This version keeps credentials out of the source, checks the post before sending, and is a **dry run unless you pass `--send`**.

## Usage

```bash
pip install -r requirements.txt          # only needed for --send
export TWITTER_API_KEY=...  TWITTER_API_SECRET=...
export TWITTER_ACCESS_TOKEN=...  TWITTER_ACCESS_SECRET=...      # or use --env-file keys.env

python tweet_image.py "Hello from the terminal" -i photo.png --alt "A screenshot of the terminal"
python tweet_image.py "Hello from the terminal" -i photo.png --alt "..." --send
python tweet_image.py -f post.txt -i a.png -i b.png --alt "first" --alt "second"   # text from a file ("-" = stdin)
python tweet_image.py "Replying" --reply-to 1234567890 --send
python tweet_image.py -f long.txt --thread --send                                   # a numbered thread
```

Give text either as the positional argument or with `--file`, not both. After `pip install .` the command is also available as `tweet-image`. Without `--send` it only checks the post and prints what it would do.

## What it does

### Checks before sending

- text within X's 280 weight limit, counted as X counts it: each link (with `https://` or a bare domain on a common TLD such as `example.com`) is 23, emoji and CJK characters are 2; and at least text or one image
- at most 4 images; `.jpg`, `.jpeg`, `.png`, `.webp` up to 5 MB, `.gif` up to 15 MB; a GIF is not mixed with other images
- images are checked by their first bytes, so a renamed text file is rejected
- alt text of at most 1000 characters, matched to images by position; `--reply-to` is a numeric tweet id
- all four credential variables are set (the error names the missing ones, never their values)

### Threads

`--thread` splits text that is longer than one post into a numbered thread. It cuts at paragraph ends, then sentence ends, then spaces, and inside a single word only as a last resort; a link is never cut in half. Each post ends with `(1/3)`-style counters (`--no-numbers` turns them off). Put a line containing only `---` in the text to choose the splits yourself. The images and alt text go on the first post and every later post replies to the one before. If posting stops part way, the error lists the ids that did go out and the `--reply-to` to continue from.

```text
$ tweet-image -f long.txt --thread
dry run: valid thread of 2 posts. Use --send to post.
  1/2 weight 241/280: Today I shipped a small tool that posts a thread. It splits…
  2/2 weight 102/280: Everything is a dry run unless I pass --send, so I can chec…
```

### Photo privacy

Phone photos often carry GPS coordinates, the camera model and a timestamp. The tool reads the file's own bytes (no imaging library) and warns when a photo contains GPS data. `--strip-metadata` uploads a copy without EXIF/GPS, XMP, IPTC, comments and PNG text chunks; your original file is never modified, the temporary copy is deleted afterwards, and the copy must report the same pixel size as the original or it is refused. A photo's rotation tag is kept so it does not end up sideways. This works for JPEG, PNG and WebP; GIFs are left as they are, and the pixels are never re-encoded.

```text
$ tweet-image --info -i photo.jpg -i diagram.png
photo.jpg: jpeg, 4032x3024 px, 192 bytes, metadata: GPS location, EXIF, comment
diagram.png: png, 1200x800 px, 5,019 bytes, metadata: text chunks

$ tweet-image "Launch day" -i photo.jpg --strip-metadata
stripped from photo.jpg: GPS location, EXIF, comment
dry run: valid post, weight 10/280, 1 image(s). Use --send to post.
```

(These examples use small synthetic sample files.) Whether X removes location data itself when you upload is something to check in X's own documentation; this tool lets you not depend on it.

### Accessibility

A missing alt text is a warning. `--require-alt` makes it an error.

### Safety net against double posts

After a successful send the tool records a hash of the post (text and image contents), the tweet ids and the time in `~/.tweet-image/posted.jsonl` (private to your user; no text or file names are stored). An identical post within 24 hours is refused unless you pass `--force`. `--log FILE` or `TWEET_IMAGE_LOG` picks another location, an empty `TWEET_IMAGE_LOG` or `--no-log` turns it off. Dry runs never touch it.

### Rate limits and failures

An upload or alt-text request is retried after a rate limit (429, waiting the time X asks for), a server error or a dropped connection, up to `--retries` times (default 2, at most 5). Creating the tweet is retried **only** after a 429: after a timeout or a 5xx error the post may already have gone out, and retrying could post it twice.

### Scripting

`--json` prints the result as JSON (the planned posts and warnings for a dry run, or the posted ids and URLs). `--from-json post.json` reads the post from a file, which makes a post reviewable and versionable:

```json
{
  "text": "Release notes are up.",
  "images": ["notes.png"],
  "alts": ["The release notes page"],
  "thread": false,
  "require_alt": true,
  "strip_metadata": true
}
```

Image paths are relative to the JSON file; options given on the command line win over the file. Unknown keys and wrong types are errors.

### Credentials

Credentials come from the four `TWITTER_*` environment variables or from a `KEY=VALUE` file passed with `--env-file` (read by the tool itself, never exported or printed). `--whoami` asks X which account the credentials belong to, so you can check them without posting.

## All options

| Option | What it does |
|---|---|
| `-f`, `--file FILE` | read the text from a file, or `-` for standard input |
| `-i`, `--image PATH` | an image (repeat, at most 4) |
| `--alt TEXT` | alt text, one per image in order |
| `--reply-to ID` | reply to this tweet |
| `--send` | actually post (default is a dry run) |
| `--thread`, `--no-numbers` | make a thread / leave out the counters |
| `--require-alt` | refuse an image without alt text |
| `--strip-metadata` | upload metadata-free copies of JPEG, PNG and WebP images |
| `--info` | show format, size and metadata of the images, then exit |
| `--from-json FILE` | read the post from a JSON file |
| `--env-file FILE` | read the credentials from a `KEY=VALUE` file |
| `--whoami` | check the credentials, then exit |
| `--json` | print the result as JSON |
| `--retries N` | retries after a rate limit or failed upload (0 to 5, default 2) |
| `--force`, `--log FILE`, `--no-log` | control the duplicate-post record |
| `--version` | print the version |

Exit status: 0 success, 1 posting failed, 2 invalid post or usage.

## Tests

```bash
python -m unittest discover -s tests
```

109 tests, all offline: the X library is replaced by fakes (including a fake `tweepy` module, so the real wiring runs without a network), and test images are built byte by byte so no imaging library is needed. They cover validation and weighted length, threads and their splitting, image header reading and metadata removal (with a real decoder as an extra check when Pillow is installed), the duplicate record, retries, JSON files, `.env` reading, credentials never being printed, and every exit code. CI runs them on Python 3.9 and 3.12 together with a lint step.

## Limits you should know about

- **Never run against the live X API.** Every test uses fakes. The posting path uses tweepy (v1.1 media upload, then `create_tweet`); `--whoami` uses `get_me`. X changes its endpoints, access tiers and rate limits often, so a real `--send` may need adjusting. Run it once with a throwaway post before relying on it.
- The size, count and dimension limits (including the 8192 px warning) are the ones I know of and may be out of date.
- The thread counters reserve room for up to 99 posts, and a thread is capped at 99 posts.
- Metadata removal is done by editing the file's structure, not by re-encoding. It is checked against hand-built files and, when available, a real decoder, but not against the huge variety of files cameras and apps produce. Check a stripped file with `--info` if it matters.
- The duplicate check only knows about posts sent from this machine with this tool.
- One post or thread per run, no scheduling. If posting fails, uploaded images that were not attached expire on their own.

## Credentials

Never put keys in the source. The original script had keys hardcoded and was not published for that reason. If you used keys that were ever stored in a file like that, revoke them in the developer portal and create new ones.

## Notes

Refactored and extended with AI assistance (Claude Code); the original script and the idea are mine, and I reviewed the logic and tests.

## License

MIT
