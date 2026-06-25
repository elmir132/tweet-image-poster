import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tweet_envfile as envfile  # noqa: E402
import tweet_ledger as ledger  # noqa: E402
import tweet_threads as threads  # noqa: E402
from tweet_text import MAX_WEIGHT, weighted_length  # noqa: E402


def strip_counter(part):
    head, _, tail = part.rpartition(" (")
    return head if tail.endswith(")") and "/" in tail else part


class ThreadTests(unittest.TestCase):
    TEXT = ("The first idea is simple and short. The second idea needs a few more words to explain it properly. "
            "Here is a third sentence that keeps going for a while. ") * 8

    def test_short_text_is_one_post_without_a_counter(self):
        self.assertEqual(threads.split_thread("hello there"), ["hello there"])
        self.assertEqual(threads.split_thread("   "), [])

    def test_a_long_text_becomes_numbered_posts_that_all_fit(self):
        parts = threads.split_thread(self.TEXT)
        self.assertGreater(len(parts), 2)
        for number, part in enumerate(parts, 1):
            self.assertLessEqual(weighted_length(part), MAX_WEIGHT)
            self.assertTrue(part.endswith(f" ({number}/{len(parts)})"), part[-12:])

    def test_nothing_is_lost_or_reordered(self):
        parts = threads.split_thread(self.TEXT)
        rebuilt = " ".join(strip_counter(p) for p in parts)
        self.assertEqual(" ".join(rebuilt.split()), " ".join(self.TEXT.split()))

    def test_posts_end_at_sentence_boundaries_when_possible(self):
        for part in threads.split_thread(self.TEXT)[:-1]:
            self.assertRegex(strip_counter(part), r"[.!?]$")

    def test_paragraph_breaks_are_kept_inside_a_post(self):
        text = "Para one is here. " * 5 + "\n\n" + "Para two is here. " * 5
        parts = threads.split_thread(text * 3)
        self.assertTrue(any("\n\n" in p for p in parts))

    def test_numbers_can_be_switched_off_and_then_the_full_width_is_used(self):
        numbered = threads.split_thread(self.TEXT)
        plain = threads.split_thread(self.TEXT, numbered=False)
        self.assertFalse(any("(1/" in p for p in plain))
        self.assertLessEqual(len(plain), len(numbered))
        self.assertTrue(all(weighted_length(p) <= MAX_WEIGHT for p in plain))

    def test_manual_separators_are_used_as_written(self):
        parts = threads.split_thread("First post\n---\nSecond post\n  ---  \nThird post")
        self.assertEqual(parts, ["First post (1/3)", "Second post (2/3)", "Third post (3/3)"])
        self.assertEqual(threads.split_thread("A\n---\nB", numbered=False), ["A", "B"])

    def test_a_link_is_never_cut_in_half(self):
        url = "https://example.com/" + "x" * 150
        text = ("word " * 50) + url + (" word" * 60)
        parts = threads.split_thread(text)
        self.assertEqual(sum(p.count(url) for p in parts), 1)
        self.assertTrue(all(weighted_length(p) <= MAX_WEIGHT for p in parts))

    def test_one_enormous_word_is_cut_as_a_last_resort(self):
        parts = threads.split_thread("a" * 900)
        self.assertGreater(len(parts), 3)
        self.assertTrue(all(weighted_length(p) <= MAX_WEIGHT for p in parts))
        self.assertEqual("".join(strip_counter(p) for p in parts), "a" * 900)

    def test_emoji_count_double(self):
        parts = threads.split_thread("\U0001F600 " * 200)
        self.assertTrue(all(weighted_length(p) <= MAX_WEIGHT for p in parts))
        self.assertEqual(sum(strip_counter(p).count("\U0001F600") for p in parts), 200)

    def test_too_many_posts_is_an_error(self):
        with self.assertRaises(threads.ThreadError):
            threads.split_thread("word " * 30000)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = os.path.join(self.dir.name, "sub", "posted.jsonl")
        self.image = os.path.join(self.dir.name, "a.png")
        with open(self.image, "wb") as handle:
            handle.write(b"picture one")

    def test_fingerprint_depends_on_text_and_image_content_but_not_whitespace_at_the_ends(self):
        a = ledger.fingerprint(["hello"], [self.image])
        self.assertEqual(a, ledger.fingerprint(["  hello \n"], [self.image]))
        self.assertNotEqual(a, ledger.fingerprint(["hello!"], [self.image]))
        self.assertNotEqual(a, ledger.fingerprint(["hello"], []))
        with open(self.image, "wb") as handle:
            handle.write(b"picture two")
        self.assertNotEqual(a, ledger.fingerprint(["hello"], [self.image]))

    def test_a_recent_identical_post_is_found_and_an_old_one_is_not(self):
        fp = ledger.fingerprint(["x"], [])
        self.assertIsNone(ledger.recent_duplicate(self.log, fp))
        ledger.record(self.log, fp, [11, 12], now=1000)
        found = ledger.recent_duplicate(self.log, fp, now=1000 + 3600)
        self.assertEqual(found["tweet_ids"], ["11", "12"])
        self.assertIsNone(ledger.recent_duplicate(self.log, fp, now=1000 + 25 * 3600))
        self.assertIsNone(ledger.recent_duplicate(self.log, ledger.fingerprint(["other"], []), now=1001))

    def test_damaged_lines_are_skipped(self):
        fp = ledger.fingerprint(["x"], [])
        ledger.record(self.log, fp, [1], now=1000)
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("not json\n[]\n{\"ts\": 1}\n")
        self.assertIsNotNone(ledger.recent_duplicate(self.log, fp, now=1001))

    def test_the_log_holds_no_text_or_paths_and_is_private(self):
        ledger.record(self.log, ledger.fingerprint(["my secret draft"], [self.image]), [5], now=1000)
        with open(self.log, encoding="utf-8") as handle:
            content = handle.read()
        self.assertNotIn("secret", content)
        self.assertNotIn("a.png", content)
        self.assertEqual(set(json.loads(content)), {"ts", "fingerprint", "tweet_ids"})
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.log).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.log)).st_mode), 0o700)


class EnvFileTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, ".env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            return envfile.load_env_file(path)

    def test_common_forms(self):
        values = self.load("# comment\n\nA=1\nexport B = two \nC=\"quoted value\"\nD='single'\nE=a=b\n")
        self.assertEqual(values, {"A": "1", "B": "two", "C": "quoted value", "D": "single", "E": "a=b"})

    def test_bad_lines_are_reported_with_their_number(self):
        for bad in ("just words\n", "=novalue\n", "BAD KEY=1\n"):
            with self.assertRaises(envfile.EnvFileError) as ctx:
                self.load("A=1\n" + bad)
            self.assertIn("line 2", str(ctx.exception))

    def test_a_missing_file_is_a_clear_error(self):
        with self.assertRaises(envfile.EnvFileError):
            envfile.load_env_file("/no/such/file.env")


if __name__ == "__main__":
    unittest.main()
