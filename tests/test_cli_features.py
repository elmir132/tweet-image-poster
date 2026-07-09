import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import helpers as h  # noqa: E402
import tweet_ledger as ledger  # noqa: E402
import tweet_media as media  # noqa: E402
import tweet_threads as threads  # noqa: E402
import tweet_image as t  # noqa: E402

LONG = ("The first idea is simple and short. The second idea needs a few more words to explain it properly. "
        "Here is a third sentence that keeps going for a while. ") * 8


N = len(threads.split_thread(LONG))   # how many posts the sample text makes


def slurp(path, mode="rb"):
    with open(path, mode) as handle:
        return handle.read()


class HttpError(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"HTTP {status}")
        self.response = types.SimpleNamespace(status_code=status, headers=headers or {})


class Backend:
    """Stands in for X: records every call and can be told to fail."""

    def __init__(self):
        self.calls = []
        self.next_id = 100
        self.upload_failures = []   # exceptions to raise on the first upload attempts
        self.create_failures = []   # exceptions to raise on the first create attempts
        self.fail_on_create_number = None

    def upload(self, path):
        if self.upload_failures:
            raise self.upload_failures.pop(0)
        self.calls.append(("upload", path))
        return "media-" + os.path.basename(path)

    def set_alt(self, media_id, alt):
        self.calls.append(("alt", media_id, alt))

    def create(self, text, media_ids, reply_to):
        if self.create_failures:
            raise self.create_failures.pop(0)
        creates = sum(1 for c in self.calls if c[0] == "create")
        if self.fail_on_create_number is not None and creates + 1 == self.fail_on_create_number:
            raise RuntimeError("API said no")
        self.next_id += 1
        self.calls.append(("create", text, media_ids, reply_to, str(self.next_id)))
        return str(self.next_id)

    def whoami(self):
        return "someone"

    @property
    def functions(self):
        return (self.upload, self.set_alt, self.create, self.whoami)

    def creates(self):
        return [c for c in self.calls if c[0] == "create"]


class CliCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = os.path.join(self.dir.name, "posted.jsonl")
        self.backend = Backend()
        self.sleeps = []

    def path(self, name):
        return os.path.join(self.dir.name, name)

    def image(self, name="a.png", data=None):
        path = self.path(name)
        with open(path, "wb") as handle:
            handle.write(data if data is not None else h.png())
        return path

    def run_cli(self, *argv, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        kwargs.setdefault("functions", self.backend.functions)
        kwargs.setdefault("env", {})
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = t.main(list(argv), sleep=self.sleeps.append, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def send(self, *argv, **kwargs):
        extra = kwargs.pop("extra", ())
        return self.run_cli(*argv, "--send", "--log", self.log, *extra, **kwargs)


class ThreadCliTests(CliCase):
    def test_long_text_without_thread_is_still_an_error(self):
        code, _, err = self.run_cli(LONG)
        self.assertEqual(code, 2)
        self.assertIn("limit is 280", err)

    def test_dry_run_of_a_thread_previews_every_post(self):
        code, out, _ = self.run_cli(LONG, "--thread")
        self.assertEqual(code, 0)
        self.assertIn("valid thread of", out)
        self.assertIn(f"1/{N} weight", out)
        self.assertEqual(out.count(" weight "), N)
        self.assertEqual(self.backend.calls, [])

    def test_a_thread_is_posted_as_a_chain_with_images_on_the_first_post_only(self):
        img = self.image()
        code, out, _ = self.send(LONG, "--thread", "-i", img, "--alt", "a diagram")
        self.assertEqual(code, 0)
        creates = self.backend.creates()
        self.assertEqual(len(creates), N)
        self.assertEqual([c[3] for c in creates], [None] + [str(100 + i) for i in range(1, N)])
        self.assertEqual(creates[0][2], ["media-a.png"])
        self.assertTrue(all(c[2] is None for c in creates[1:]))
        self.assertEqual(out.count("posted: https://x.com/i/status/"), N)
        self.assertEqual(self.backend.calls.count(("upload", img)), 1)

    def test_the_first_post_can_reply_to_an_existing_tweet(self):
        code, _, _ = self.send(LONG, "--thread", "--reply-to", "55")
        self.assertEqual(code, 0)
        self.assertEqual(self.backend.creates()[0][3], "55")

    def test_a_failure_part_way_names_what_was_already_posted(self):
        self.backend.fail_on_create_number = 3
        code, _, err = self.send(LONG, "--thread")
        self.assertEqual(code, 1)
        self.assertIn(f"stopped after 2 of {N} posts", err)
        self.assertIn("101, 102", err)
        self.assertIn("--reply-to 102", err)

    def test_manual_separators_and_the_numbering_switch(self):
        code, _, _ = self.send("one\n---\ntwo", "--thread", "--no-numbers")
        self.assertEqual(code, 0)
        self.assertEqual([c[1] for c in self.backend.creates()], ["one", "two"])

    def test_a_manual_part_that_is_too_long_is_reported_with_its_number(self):
        code, _, err = self.run_cli("short\n---\n" + "x" * 300, "--thread")
        self.assertEqual(code, 2)
        self.assertIn("post 2/2:", err)

    def test_an_absurd_thread_is_refused(self):
        code, _, err = self.run_cli("word " * 30000, "--thread")
        self.assertEqual(code, 2)
        self.assertIn("most this tool will make", err)


class DuplicateGuardTests(CliCase):
    def test_the_same_post_twice_is_refused_until_forced(self):
        self.assertEqual(self.send("hello world")[0], 0)
        code, _, err = self.send("hello world")
        self.assertEqual(code, 2)
        self.assertIn("identical post was sent", err)
        self.assertIn("101", err)
        self.assertEqual(len(self.backend.creates()), 1)
        self.assertEqual(self.send("hello world", extra=["--force"])[0], 0)
        self.assertEqual(len(self.backend.creates()), 2)

    def test_a_different_text_or_image_is_not_a_duplicate(self):
        self.assertEqual(self.send("hello world")[0], 0)
        self.assertEqual(self.send("hello again")[0], 0)
        first = self.image("one.png", h.png(8, 4))
        second = self.image("two.png", h.png(9, 4))
        self.assertEqual(self.send("hello world", "-i", first)[0], 0)
        self.assertEqual(self.send("hello world", "-i", second)[0], 0)

    def test_a_failed_post_is_not_recorded_so_it_can_be_retried(self):
        self.backend.create_failures = [RuntimeError("down")]
        self.assertEqual(self.send("try me")[0], 1)
        self.assertEqual(self.send("try me")[0], 0)

    def test_no_log_skips_the_check_and_writes_nothing(self):
        for _ in range(2):
            code, _, _ = self.run_cli("same", "--send", "--no-log")
            self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.log))

    def test_the_log_location_can_come_from_the_environment_or_be_switched_off_there(self):
        env_log = self.path("from-env.jsonl")
        self.assertEqual(self.run_cli("env post", "--send", env={"TWEET_IMAGE_LOG": env_log})[0], 0)
        self.assertTrue(os.path.exists(env_log))
        for _ in range(2):
            self.assertEqual(self.run_cli("off post", "--send", env={"TWEET_IMAGE_LOG": ""})[0], 0)

    def test_dry_runs_never_touch_the_log(self):
        self.run_cli("hello", "--log", self.log)
        self.assertFalse(os.path.exists(self.log))


class RetryTests(CliCase):
    def test_a_rate_limited_upload_is_retried_with_the_server_delay(self):
        img = self.image()
        self.backend.upload_failures = [HttpError(429, {"Retry-After": "7"})]
        code, _, _ = self.send("hi", "-i", img)
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [7.0])

    def test_a_server_error_on_upload_is_retried_with_growing_delays(self):
        img = self.image()
        self.backend.upload_failures = [HttpError(503), HttpError(502)]
        self.assertEqual(self.send("hi", "-i", img)[0], 0)
        self.assertEqual(self.sleeps, [1, 2])

    def test_retries_stop_at_the_limit(self):
        img = self.image()
        self.backend.upload_failures = [HttpError(503)] * 5
        code, _, err = self.send("hi", "-i", img, extra=["--retries", "1"])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.sleeps), 1)
        self.assertIn("Nothing was posted", err)

    def test_create_is_retried_after_a_rate_limit_only(self):
        self.backend.create_failures = [HttpError(429, {"Retry-After": "3"})]
        self.assertEqual(self.send("limited")[0], 0)
        self.assertEqual(self.sleeps, [3.0])
        self.sleeps.clear()
        self.backend.create_failures = [HttpError(503)]
        code, _, _ = self.send("server error")
        self.assertEqual(code, 1)           # not retried: the post may already have gone out
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(self.backend.creates()), 1)

    def test_other_errors_are_not_retried(self):
        self.backend.create_failures = [RuntimeError("boom")]
        self.assertEqual(self.send("x1")[0], 1)
        self.assertEqual(self.sleeps, [])

    def test_the_retry_count_is_bounded(self):
        self.assertEqual(self.run_cli("x", "--retries", "9")[0], 2)
        self.assertEqual(self.run_cli("x", "--retries", "-1")[0], 2)

    def test_a_bad_retry_after_header_falls_back_to_backoff(self):
        calls = []

        def flaky(*args):
            calls.append(1)
            if len(calls) == 1:
                raise HttpError(429, {"Retry-After": "soon"})
            return "ok"
        self.assertEqual(t.with_retries(flaky, 2, True, self.sleeps.append)(), "ok")
        self.assertEqual(self.sleeps, [1])


class PrivacyAndAccessibilityTests(CliCase):
    def gps_photo(self):
        return self.image("photo.jpg", h.jpeg(20, 10, exif=h.tiff(orientation=6, gps=True), comment=True))

    def test_a_geotagged_photo_triggers_a_warning(self):
        code, _, err = self.run_cli("hi", "-i", self.gps_photo(), "--alt", "a photo")
        self.assertEqual(code, 0)
        self.assertIn("contains GPS location data", err)
        self.assertIn("--strip-metadata", err)

    def test_stripping_uploads_a_clean_copy_and_leaves_the_original_alone(self):
        photo = self.gps_photo()
        before = slurp(photo)
        code, out, err = self.send("hi", "-i", photo, "--alt", "a photo", "--strip-metadata")
        self.assertEqual(code, 0)
        self.assertIn("stripped from photo.jpg: GPS location", out)
        self.assertNotIn("GPS", err)
        uploaded = [c[1] for c in self.backend.calls if c[0] == "upload"][0]
        self.assertNotEqual(uploaded, photo)
        self.assertEqual(slurp(photo), before)
        self.assertFalse(os.path.exists(uploaded), "the temporary copy is removed afterwards")

    def test_the_uploaded_copy_really_has_no_gps(self):
        seen = []

        def spy(path):
            seen.append(media.image_info(path))
            return "m"
        photo = self.gps_photo()
        fns = (spy, None, self.backend.create)
        self.assertEqual(self.send("hi", "-i", photo, "--strip-metadata", functions=fns)[0], 0)
        self.assertFalse(seen[0]["gps"])
        self.assertEqual(seen[0]["orientation"], 6)

    def test_a_dry_run_reports_what_would_be_stripped(self):
        code, out, _ = self.run_cli("hi", "-i", self.gps_photo(), "--strip-metadata")
        self.assertEqual(code, 0)
        self.assertIn("stripped from photo.jpg", out)
        self.assertEqual(self.backend.calls, [])

    def test_a_missing_alt_text_is_a_warning_and_with_require_alt_an_error(self):
        img = self.image()
        code, _, err = self.run_cli("hi", "-i", img)
        self.assertEqual(code, 0)
        self.assertIn("has no alt text", err)
        code, _, err = self.run_cli("hi", "-i", img, "--require-alt")
        self.assertEqual(code, 2)
        self.assertIn("--require-alt", err)
        self.assertEqual(self.run_cli("hi", "-i", img, "--alt", "described", "--require-alt")[0], 0)

    def test_info_prints_what_is_known_about_each_image(self):
        photo = self.gps_photo()
        code, out, _ = self.run_cli("--info", "-i", photo)
        self.assertEqual(code, 0)
        self.assertIn("jpeg, 20x10 px", out)
        self.assertIn("GPS location, EXIF, comment", out)
        self.assertEqual(self.run_cli("--info")[0], 2)
        code, out, _ = self.run_cli("--info", "--json", "-i", photo)
        self.assertTrue(json.loads(out)[0]["gps"])

    def test_a_huge_image_gets_a_size_warning(self):
        huge = self.image("huge.png", h.png(9000, 1))
        _, _, err = self.run_cli("hi", "-i", huge, "--alt", "x")
        self.assertIn("9000x1 px", err)


class JsonAndSpecTests(CliCase):
    def spec(self, data):
        path = self.path("post.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_json_output_for_a_dry_run_and_for_a_send(self):
        code, out, _ = self.run_cli("hello", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["posts"], [{"text": "hello", "weight": 5}])
        code, out, _ = self.send("hello json", "--json")
        data = json.loads(out)
        self.assertFalse(data["dry_run"])
        self.assertEqual(data["posted"][0]["url"], "https://x.com/i/status/101")

    def test_a_post_can_be_described_in_a_json_file(self):
        self.image("pic.png")
        spec = self.spec({"text": "from the spec", "images": ["pic.png"], "alts": ["a picture"], "reply_to": "9"})
        code, _, _ = self.send("--from-json", spec)
        self.assertEqual(code, 0)
        create = self.backend.creates()[0]
        self.assertEqual((create[1], create[2], create[3]), ("from the spec", ["media-pic.png"], "9"))
        self.assertIn(("alt", "media-pic.png", "a picture"), self.backend.calls)

    def test_command_line_options_win_over_the_file(self):
        spec = self.spec({"text": "from the spec", "reply_to": "9"})
        self.assertEqual(self.send("typed text", "--from-json", spec, "--reply-to", "10")[0], 0)
        create = self.backend.creates()[0]
        self.assertEqual((create[1], create[3]), ("typed text", "10"))

    def test_a_spec_can_turn_on_thread_and_require_alt(self):
        spec = self.spec({"text": LONG, "thread": True})
        self.assertEqual(self.send("--from-json", spec)[0], 0)
        self.assertEqual(len(self.backend.creates()), N)
        picture = self.image()
        spec = self.spec({"text": "x", "images": [picture], "require_alt": True})
        self.assertEqual(self.run_cli("--from-json", spec)[0], 2)

    def test_a_bad_spec_is_rejected_clearly(self):
        cases = [("[]", "one object"), ('{"txt": "x"}', "unknown key"), ('{"text": 5}', "'text' must be text"),
                 ('{"images": "a.png"}', "'images' must be a list"), ('{"thread": "yes"}', "true or false"),
                 ('{"images": [1]}', "list of text"), ("{oops", "")]
        for raw, message in cases:
            path = self.path("bad.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(raw)
            code, _, err = self.run_cli("--from-json", path)
            self.assertEqual(code, 2, raw)
            self.assertIn(message, err)
        self.assertEqual(self.run_cli("--from-json", self.path("missing.json"))[0], 2)


class FakeTweepy:
    """A stand-in for the tweepy package, so the real wiring in _tweepy_functions runs offline."""

    def __init__(self):
        self.keys = None
        self.created = []
        self.alts = []
        module = types.ModuleType("tweepy")
        outer = self

        class OAuth1UserHandler:
            def __init__(self, *keys):
                outer.keys = keys

        class API:
            def __init__(self, auth):
                pass

            def media_upload(self, filename):
                return types.SimpleNamespace(media_id_string="M-" + os.path.basename(filename))

            def create_media_metadata(self, media_id, alt):
                outer.alts.append((media_id, alt))

        class Client:
            def __init__(self, **kwargs):
                outer.client_keys = kwargs

            def create_tweet(self, text=None, media_ids=None, **kwargs):
                outer.created.append((text, media_ids, kwargs))
                return types.SimpleNamespace(data={"id": "777"})

            def get_me(self):
                return types.SimpleNamespace(data=types.SimpleNamespace(username="elmir_test"))

        module.OAuth1UserHandler, module.API, module.Client = OAuth1UserHandler, API, Client
        self.module = module


class TweepyWiringTests(CliCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeTweepy()
        sys.modules["tweepy"] = self.fake.module
        self.addCleanup(sys.modules.pop, "tweepy", None)
        self.env_file = self.path("keys.env")
        with open(self.env_file, "w", encoding="utf-8") as handle:
            handle.write("TWITTER_API_KEY=k1\nTWITTER_API_SECRET=k2\nexport TWITTER_ACCESS_TOKEN='k3'\nTWITTER_ACCESS_SECRET=k4\n")

    def real(self, *argv):
        return self.run_cli(*argv, functions=None)

    def test_credentials_come_from_the_env_file_and_the_post_goes_through_the_library(self):
        img = self.image()
        code, out, _ = self.real("hi", "-i", img, "--alt", "desc", "--send", "--log", self.log, "--env-file", self.env_file)
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.keys, ("k1", "k2", "k3", "k4"))
        self.assertEqual(self.fake.created, [("hi", ["M-a.png"], {})])
        self.assertEqual(self.fake.alts, [("M-a.png", "desc")])
        self.assertIn("posted: https://x.com/i/status/777", out)

    def test_a_reply_passes_the_tweet_id_to_the_library(self):
        self.real("hi", "--send", "--log", self.log, "--env-file", self.env_file, "--reply-to", "55")
        self.assertEqual(self.fake.created[0][2], {"in_reply_to_tweet_id": "55"})

    def test_whoami_checks_the_credentials(self):
        code, out, _ = self.real("--whoami", "--env-file", self.env_file)
        self.assertEqual(code, 0)
        self.assertIn("authenticated as @elmir_test", out)

    def test_whoami_without_credentials_names_what_is_missing(self):
        code, _, err = self.real("--whoami")
        self.assertEqual(code, 2)
        self.assertIn("TWITTER_API_KEY", err)

    def test_a_missing_env_file_is_a_clear_error(self):
        self.assertEqual(self.real("hi", "--env-file", self.path("nope.env"))[0], 2)

    def test_whoami_reports_a_failing_lookup(self):
        def broken():
            raise RuntimeError("401")
        code, _, err = self.run_cli("--whoami", functions=(None, None, None, broken))
        self.assertEqual(code, 1)
        self.assertIn("could not check the credentials", err)
        self.assertEqual(self.run_cli("--whoami", functions=(None, None, None))[0], 2)

    def test_credential_values_never_appear_in_output(self):
        code, out, err = self.real("hi", "--send", "--log", self.log, "--env-file", self.env_file)
        self.assertEqual(code, 0)
        for secret in ("k1", "k2", "k3", "k4"):
            self.assertNotIn(secret + "\n", out + err)


class LedgerIntegrationTests(CliCase):
    def test_a_successful_send_is_recorded_with_its_ids(self):
        self.send(LONG, "--thread")
        entry = json.loads(slurp(self.log, "r").splitlines()[0])
        self.assertEqual(entry["tweet_ids"], [str(101 + i) for i in range(N)])
        self.assertEqual(entry["fingerprint"], ledger.fingerprint(threads.split_thread(LONG), []))


if __name__ == "__main__":
    unittest.main()
