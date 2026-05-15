# tweet-image-poster

A small command-line tool that posts a tweet with up to four images. It started as a 20-line script with credentials pasted into the source; this version keeps credentials in environment variables, validates the post before sending, and is a dry run unless you pass `--send`.

## Usage

```bash
pip install -r requirements.txt          # only needed for --send
export TWITTER_API_KEY=...  TWITTER_API_SECRET=...
export TWITTER_ACCESS_TOKEN=...  TWITTER_ACCESS_SECRET=...

python tweet_image.py "Hello from the terminal" -i photo.png --alt "A screenshot of the terminal"
python tweet_image.py "Hello from the terminal" -i photo.png --alt "..." --send
python tweet_image.py -f post.txt -i a.png -i b.png --alt "first" --alt "second"   # text from a file ("-" = stdin)
python tweet_image.py "Replying" --reply-to 1234567890 --send
```

Give text either as the positional argument or with `--file`, not both. Use
`--file -` to read the post from standard input; the tool does not close the
caller's stdin stream.

After `pip install .` the command is also available as `tweet-image`.

Without `--send` it only checks the post and prints what it would do.

## What it checks before sending

- text within X's 280 weight limit, counted as X counts it: each URL is 23, emoji and CJK characters are 2; and at least text or one image
- at most 4 images; `.jpg`, `.jpeg`, `.png`, `.webp` up to 5 MB, `.gif` up to 15 MB
- a GIF is not mixed with other images
- alt text of at most 1000 characters, no more alt texts than images, matched to images by position
- `--reply-to` is a numeric tweet id
- all four credential variables are set (the error names the missing ones, never their values)

## Tests

```bash
python -m unittest discover -s tests
```

The tests cover validation (including the weighted length), credential handling, the upload, alt text, post order, error handling and the CLI exit codes. The network calls are injected, so the tests use fakes.

## Limits you should know about

- **Not verified against the live X API.** The posting path uses tweepy (v1.1 media upload, then `create_tweet`). X changes its endpoints, access tiers and rate limits often, so a real `--send` may need adjusting. Run it once with a throwaway post before relying on it.
- The size and count limits above are the ones I know of and may be out of date.
- One post per run, no threads, no scheduling.
- If posting fails, the tool prints the error and exits with code 1. Images uploaded before the failure stay unattached on X and expire on their own.

## Credentials

Never put keys in the source. The original script had keys hardcoded and was not published for that reason. If you used keys that were ever stored in a file like that, revoke them in the developer portal and create new ones.

## Notes

Refactored and tested with AI assistance (Claude Code); the original script and the idea are mine, and I reviewed the logic and tests.

## License

MIT
