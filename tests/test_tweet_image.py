import os
import sys
import tempfile
import types
import unittest
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tweet_image as t


HEADERS = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff",
           ".gif": b"GIF89a", ".webp": b"RIFF\x00\x00\x00\x00WEBP"}


def make_file(directory, name, size=10, valid=True):
    path = os.path.join(directory, name)
    ext = os.path.splitext(name)[1].lower()
    head = HEADERS.get(ext, b"") if valid else b""
    with open(path, "wb") as fh:
        fh.write(head + b"x" * max(0, size - len(head)))
    return path


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_text_only_is_valid(self):
        self.assertEqual(t.validate("hello", []), [])

    def test_empty_post_rejected(self):
        self.assertTrue(t.validate("  ", []))

    def test_text_length_boundary(self):
        self.assertEqual(t.validate("a" * 280, []), [])
        self.assertTrue(t.validate("a" * 281, []))

    def test_url_counts_23(self):
        url = "https://example.com/" + "x" * 200
        self.assertEqual(t.weighted_length(url), 23)
        self.assertEqual(t.validate("a" * 256 + " " + url, []), [])  # 256 + 1 + 23 = 280
        self.assertTrue(t.validate("a" * 257 + " " + url, []))  # 281

    def test_emoji_and_cjk_count_2(self):
        self.assertEqual(t.weighted_length("\U0001F600"), 2)
        self.assertEqual(t.weighted_length("\u4f60\u597d"), 4)
        self.assertEqual(t.weighted_length("\u00e9"), 1)
        self.assertTrue(t.validate("\U0001F600" * 141, []))
        self.assertEqual(t.validate("\U0001F600" * 140, []), [])

    def test_reply_to_must_be_numeric(self):
        self.assertTrue(t.validate("x", [], reply_to="abc"))
        self.assertEqual(t.validate("x", [], reply_to="123"), [])

    def test_too_many_images(self):
        files = [make_file(self.dir.name, f"{i}.png") for i in range(5)]
        self.assertTrue(any("limit is 4" in p for p in t.validate("x", files)))

    def test_four_images_ok(self):
        files = [make_file(self.dir.name, f"{i}.png") for i in range(4)]
        self.assertEqual(t.validate("x", files), [])

    def test_missing_file_and_bad_type(self):
        problems = t.validate("x", ["nope.png", make_file(self.dir.name, "a.txt")])
        self.assertTrue(any("not found" in p for p in problems))
        self.assertTrue(any("unsupported" in p for p in problems))

    def test_size_limit(self):
        big = make_file(self.dir.name, "big.png", 5 * 1024 * 1024 + 1)
        self.assertTrue(any("larger than 5 MB" in p for p in t.validate("x", [big])))

    def test_renamed_text_file_is_rejected(self):
        fake = make_file(self.dir.name, "fake.png", valid=False)
        self.assertTrue(any("not a real .png" in p for p in t.validate("x", [fake])))

    def test_real_headers_accepted_for_every_type(self):
        files = [make_file(self.dir.name, n) for n in ("a.png", "b.jpg", "c.jpeg", "d.webp")]
        self.assertEqual(t.validate("x", files[:4]), [])
        self.assertEqual(t.validate("x", [make_file(self.dir.name, "g.gif")]), [])

    def test_gif_cannot_mix(self):
        files = [make_file(self.dir.name, "a.gif"), make_file(self.dir.name, "b.png")]
        self.assertTrue(any("GIF" in p for p in t.validate("x", files)))

    def test_alt_checks(self):
        png = make_file(self.dir.name, "a.png")
        self.assertTrue(t.validate("x", [png], ["a" * 1001]))
        self.assertTrue(t.validate("x", [png], ["one", "two"]))


class CredentialTests(unittest.TestCase):
    def test_missing_names_are_reported(self):
        with self.assertRaises(t.ValidationError) as ctx:
            t.credentials({"TWITTER_API_KEY": "k"})
        self.assertIn("TWITTER_API_SECRET", str(ctx.exception))
        self.assertNotIn("TWITTER_API_KEY,", str(ctx.exception))

    def test_all_present(self):
        env = {k: "v" for k in t.ENV_KEYS}
        self.assertEqual(t.credentials(env), ("v",) * 4)


class PostTests(unittest.TestCase):
    def test_uploads_in_order_then_creates(self):
        calls = []
        tid = t.post("hi", ["a.png", "b.png"], [],
                     upload=lambda p: calls.append(p) or p + "-id",
                     create=lambda text, ids, reply: calls.append((text, ids)) or "99")
        self.assertEqual(tid, "99")
        self.assertEqual(calls, ["a.png", "b.png", ("hi", ["a.png-id", "b.png-id"])])

    def test_text_only_passes_no_media(self):
        seen = []
        t.post("hi", [], [], upload=None, create=lambda text, ids, reply: seen.append(ids) or "1")
        self.assertEqual(seen, [None])

    def test_alt_goes_to_the_right_image_even_with_duplicate_paths(self):
        alts = []
        t.post("hi", ["a.png", "a.png"], ["first", "second"],
               upload=lambda p: f"id{len(alts)}",
               create=lambda *a: "1",
               set_alt=lambda mid, alt: alts.append((mid, alt)))
        self.assertEqual(alts, [("id0", "first"), ("id1", "second")])

    def test_images_without_alt_get_no_metadata_call(self):
        alts = []
        t.post("hi", ["a.png", "b.png"], ["only first"], upload=lambda p: p,
               create=lambda *a: "1", set_alt=lambda mid, alt: alts.append(mid))
        self.assertEqual(alts, ["a.png"])

    def test_reply_to_is_passed_through(self):
        seen = []
        t.post("hi", [], [], upload=None, create=lambda text, ids, reply: seen.append(reply) or "1", reply_to="42")
        self.assertEqual(seen, ["42"])


class CliTests(unittest.TestCase):
    def test_dry_run_sends_nothing(self):
        self.assertEqual(t.main(["hello"]), 0)

    def test_invalid_returns_2(self):
        self.assertEqual(t.main(["a" * 300]), 2)

    def test_send_without_credentials_fails_cleanly(self):
        saved = {k: os.environ.pop(k) for k in t.ENV_KEYS if k in os.environ}
        self.addCleanup(os.environ.update, saved)
        fake_tweepy = types.ModuleType("tweepy")  # so the credential check runs, not the import
        sys.modules["tweepy"] = fake_tweepy
        self.addCleanup(sys.modules.pop, "tweepy", None)
        self.assertEqual(t.main(["hello", "--send"]), 2)

    def test_send_posts_through_injected_functions(self):
        sent = []
        fns = (lambda p: "m1", lambda m, a: sent.append(("alt", m, a)),
               lambda text, ids, reply: sent.append(("tweet", text, ids)) or "7")
        with tempfile.TemporaryDirectory() as d:
            img = make_file(d, "a.png")
            code = t.main(["hi", "-i", img, "--alt", "desc", "--send"], functions=fns)
        self.assertEqual(code, 0)
        self.assertEqual(sent, [("alt", "m1", "desc"), ("tweet", "hi", ["m1"])])

    def test_api_failure_returns_1_and_says_nothing_posted(self):
        def boom(text, ids, reply):
            raise RuntimeError("429 too many requests")
        self.assertEqual(t.main(["hi", "--send"], functions=(None, None, boom)), 1)

    def test_text_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("from file\n")
            sent = []
            fns = (None, None, lambda text, ids, reply: sent.append(text) or "1")
            self.assertEqual(t.main(["-f", path, "--send"], functions=fns), 0)
        self.assertEqual(sent, ["from file"])
        self.assertEqual(t.main(["-f", "/no/such/file"]), 2)

    def test_text_from_stdin_does_not_close_stdin(self):
        original = t.sys.stdin
        t.sys.stdin = StringIO("from stdin\n")
        try:
            self.assertEqual(t.main(["-f", "-"]), 0)
            self.assertFalse(t.sys.stdin.closed)
        finally:
            t.sys.stdin = original

    def test_positional_text_and_file_are_rejected_together(self):
        self.assertEqual(t.main(["inline", "--file", "-"]), 2)


if __name__ == "__main__":
    unittest.main()
