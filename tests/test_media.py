import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import helpers as h  # noqa: E402
import tweet_media as media  # noqa: E402

try:
    from PIL import Image
except ImportError:  # the tests below that need a real decoder are skipped
    Image = None


class MediaTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    @staticmethod
    def read(path):
        with open(path, "rb") as handle:
            return handle.read()

    def write(self, name, data):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path


class ReadInfoTests(MediaTestCase):
    def test_jpeg_size_and_clean_file(self):
        info = media.image_info(self.write("a.jpg", h.jpeg(40, 30)))
        self.assertEqual((info["format"], info["width"], info["height"]), ("jpeg", 40, 30))
        self.assertEqual(info["labels"], [])
        self.assertFalse(info["gps"])

    def test_gps_is_found_in_both_byte_orders(self):
        for endian in ("<", ">"):
            info = media.image_info(self.write("g.jpg", h.jpeg(exif=h.tiff(orientation=6, gps=True, endian=endian))))
            self.assertTrue(info["gps"], endian)
            self.assertEqual(info["orientation"], 6, endian)
            self.assertEqual(info["labels"][:2], ["GPS location", "EXIF"])

    def test_exif_without_gps_is_not_reported_as_gps(self):
        info = media.image_info(self.write("e.jpg", h.jpeg(exif=h.tiff(orientation=1))))
        self.assertFalse(info["gps"])
        self.assertEqual(info["labels"], ["EXIF"])

    def test_other_jpeg_metadata_is_listed(self):
        info = media.image_info(self.write("m.jpg", h.jpeg(xmp=True, iptc=True, comment=True)))
        self.assertEqual(info["labels"], ["XMP", "IPTC", "comment"])

    def test_png_size_labels_and_animation(self):
        info = media.image_info(self.write("a.png", h.png(12, 7, text=True, time=True, animated=True)))
        self.assertEqual((info["format"], info["width"], info["height"]), ("png", 12, 7))
        self.assertEqual(info["labels"], ["text chunks", "timestamp"])
        self.assertTrue(info["animated"])

    def test_png_exif_gps(self):
        info = media.image_info(self.write("g.png", h.png(exif=h.tiff(gps=True))))
        self.assertTrue(info["gps"])
        self.assertIn("GPS location", info["labels"])

    def test_webp_size_and_metadata(self):
        info = media.image_info(self.write("a.webp", h.webp(100, 50, exif=h.tiff(gps=True), xmp=True)))
        self.assertEqual((info["format"], info["width"], info["height"]), ("webp", 100, 50))
        self.assertTrue(info["gps"])
        self.assertIn("XMP", info["labels"])

    def test_gif_size(self):
        info = media.image_info(self.write("a.gif", h.gif(9, 4)))
        self.assertEqual((info["format"], info["width"], info["height"]), ("gif", 9, 4))

    def test_unknown_and_damaged_files_never_raise(self):
        self.assertIsNone(media.image_info(self.write("t.png", b"just text"))["format"])
        whole = h.jpeg(exif=h.tiff(orientation=6, gps=True))
        for cut in range(0, len(whole), 7):
            media.image_info(self.write("cut.jpg", whole[:cut]))
        png = h.png(text=True, exif=h.tiff(gps=True))
        for cut in range(0, len(png), 9):
            media.image_info(self.write("cut.png", png[:cut]))
        wp = h.webp(exif=h.tiff(gps=True), xmp=True)
        for cut in range(0, len(wp), 5):
            media.image_info(self.write("cut.webp", wp[:cut]))

    def test_garbage_tiff_block_is_ignored(self):
        info = media.image_info(self.write("x.jpg", h.jpeg(exif=b"II*\x00\xff\xff\xff\x7f")))
        self.assertFalse(info["gps"])


class StripTests(MediaTestCase):
    def strip(self, name, data):
        src = self.write(name, data)
        dest = os.path.join(self.dir.name, "out_" + name)
        return src, dest, media.strip_metadata(src, dest)

    def test_jpeg_loses_everything_private_and_keeps_the_picture(self):
        original = h.jpeg(33, 22, exif=h.tiff(gps=True), xmp=True, iptc=True, comment=True, scan=b"PIXELS")
        src, dest, removed = self.strip("a.jpg", original)
        self.assertEqual(removed[:2], ["GPS location", "EXIF"])
        for label in ("XMP", "IPTC", "comment"):
            self.assertIn(label, removed)
        info = media.image_info(dest)
        self.assertEqual((info["width"], info["height"], info["labels"], info["gps"]), (33, 22, [], False))
        data = self.read(dest)
        self.assertTrue(data.endswith(b"PIXELS\xff\xd9"))   # compressed image data untouched
        self.assertIn(b"JFIF", data)                           # the format header is kept
        self.assertNotIn(b"made on my phone", data)
        self.assertEqual(self.read(src), original)     # the original file is never modified

    def test_orientation_survives_so_the_photo_is_not_rotated(self):
        _, dest, removed = self.strip("o.jpg", h.jpeg(exif=h.tiff(orientation=6, gps=True)))
        self.assertIn("GPS location", removed)
        info = media.image_info(dest)
        self.assertEqual(info["orientation"], 6)
        self.assertFalse(info["gps"])

    def test_nothing_to_remove_writes_nothing(self):
        src, dest, removed = self.strip("c.jpg", h.jpeg())
        self.assertEqual(removed, [])
        self.assertFalse(os.path.exists(dest))

    def test_png_text_exif_and_time_are_dropped(self):
        original = h.png(10, 6, text=True, exif=h.tiff(gps=True), time=True)
        _, dest, removed = self.strip("a.png", original)
        self.assertEqual(removed[0], "GPS location")
        self.assertEqual(set(removed), {"GPS location", "text chunks", "EXIF", "timestamp"})
        info = media.image_info(dest)
        self.assertEqual((info["width"], info["height"], info["labels"]), (10, 6, []))
        data = self.read(dest)
        self.assertTrue(data.startswith(b"\x89PNG") and data.endswith(h.chunk(b"IEND", b"")))
        self.assertNotIn(b"taken at home", data)

    def test_webp_chunks_and_flags_are_removed_and_size_stays_consistent(self):
        _, dest, removed = self.strip("a.webp", h.webp(16, 9, exif=h.tiff(gps=True), xmp=True))
        self.assertEqual(set(removed), {"GPS location", "EXIF", "XMP"})
        data = self.read(dest)
        self.assertEqual(int.from_bytes(data[4:8], "little"), len(data) - 8)
        self.assertEqual(data[20] & 0x0C, 0)   # the EXIF and XMP flags in the VP8X header are cleared
        info = media.image_info(dest)
        self.assertEqual((info["width"], info["height"], info["labels"]), (16, 9, []))

    def test_gif_and_unknown_files_are_left_alone(self):
        for name, data in (("a.gif", h.gif()), ("t.png", b"not an image")):
            src, dest, removed = self.strip(name, data)
            self.assertEqual(removed, [])
            self.assertFalse(os.path.exists(dest))

    def test_a_result_with_a_different_size_is_refused_and_removed(self):
        src = self.write("a.jpg", h.jpeg(20, 10, comment=True))
        dest = os.path.join(self.dir.name, "bad.jpg")
        original = media._strip_jpeg
        media._strip_jpeg = lambda data: (h.jpeg(99, 99), ["comment"])
        self.addCleanup(setattr, media, "_strip_jpeg", original)
        with self.assertRaises(media.MediaError):
            media.strip_metadata(src, dest)
        self.assertFalse(os.path.exists(dest))

    @unittest.skipIf(Image is None, "Pillow is not installed")
    def test_real_decoders_still_open_the_stripped_files(self):
        for name, data in (("a.png", h.png(8, 4, text=True, exif=h.tiff(gps=True))),
                           ("a.jpg", h.jpeg(20, 10, exif=h.tiff(orientation=3, gps=True), comment=True))):
            _, dest, removed = self.strip(name, data)
            self.assertTrue(removed)
            with Image.open(dest) as picture:
                picture.load()


if __name__ == "__main__":
    unittest.main()
