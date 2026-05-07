import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tweet_image as t


def make_file(directory, name, size=10):
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
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
                     create=lambda text, ids: calls.append((text, ids)) or "99")
        self.assertEqual(tid, "99")
        self.assertEqual(calls, ["a.png", "b.png", ("hi", ["a.png-id", "b.png-id"])])

    def test_text_only_passes_no_media(self):
        seen = []
        t.post("hi", [], [], upload=None, create=lambda text, ids: seen.append(ids) or "1")
        self.assertEqual(seen, [None])


class CliTests(unittest.TestCase):
    def test_dry_run_sends_nothing(self):
        self.assertEqual(t.main(["hello"]), 0)

    def test_invalid_returns_2(self):
        self.assertEqual(t.main(["a" * 300]), 2)

    def test_send_without_credentials_fails_cleanly(self):
        old = {k: os.environ.pop(k, None) for k in t.ENV_KEYS}
        try:
            self.assertEqual(t.main(["hello", "--send"]), 2)
        finally:
            os.environ.update({k: v for k, v in old.items() if v})


if __name__ == "__main__":
    unittest.main()
