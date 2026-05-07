# tweet-image-poster

A small command-line tool that posts a tweet with up to four images. It started as a 20-line script with credentials pasted into the source; this version keeps credentials in environment variables, validates the post before sending, and is a dry run unless you pass `--send`.

## Usage

```bash
pip install -r requirements.txt          # only needed for --send
export TWITTER_API_KEY=...  TWITTER_API_SECRET=...
export TWITTER_ACCESS_TOKEN=...  TWITTER_ACCESS_SECRET=...

python tweet_image.py "Hello from the terminal" -i photo.png --alt "A screenshot of the terminal"
python tweet_image.py "Hello from the terminal" -i photo.png --alt "..." --send
```

Without `--send` it only checks the post and prints what it would do.

## What it checks before sending

- text of at most 280 characters, and at least text or one image
- at most 4 images; `.jpg`, `.jpeg`, `.png`, `.webp` up to 5 MB, `.gif` up to 15 MB
- a GIF is not mixed with other images
- alt text of at most 1000 characters, no more alt texts than images
- all four credential variables are set (the error names the missing ones, never their values)

## Tests

```bash
python -m unittest discover -s tests
```

The tests cover validation, credential handling, the upload-then-post order and the CLI exit codes. The network calls are injected, so the tests use fakes.

## Limits you should know about

- **Not verified against the live X API.** The posting path uses tweepy (v1.1 media upload, then `create_tweet`). X changes its endpoints, access tiers and rate limits often, so a real `--send` may need adjusting. Run it once with a throwaway post before relying on it.
- The size and count limits above are the ones I know of and may be out of date.
- One post per run, no threads, no scheduling.

## Credentials

Never put keys in the source. The original script had keys hardcoded and was not published for that reason. If you used keys that were ever stored in a file like that, revoke them in the developer portal and create new ones.

## Notes

Refactored and tested with AI assistance (Claude Code); the original script and the idea are mine, and I reviewed the logic and tests.

## License

MIT
