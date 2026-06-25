"""Builders for tiny but structurally real image files, so the tests need no imaging library."""
import struct
import zlib


def segment(marker, body):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(body) + 2) + body


def tiff(orientation=None, gps=False, endian="<"):
    entries = []
    if orientation is not None:
        entries.append((0x0112, 3, 1, orientation))
    if gps:
        entries.append((0x8825, 4, 1, 0))  # the value is patched to point at the GPS block below
    header = (b"II*\x00" if endian == "<" else b"MM\x00*") + struct.pack(endian + "I", 8)
    ifd_size = 2 + 12 * len(entries) + 4
    gps_offset = 8 + ifd_size
    body = struct.pack(endian + "H", len(entries))
    for tag, kind, count, value in entries:
        if tag == 0x8825:
            value = gps_offset
        if kind == 3:
            body += struct.pack(endian + "HHIHH", tag, kind, count, value, 0)
        else:
            body += struct.pack(endian + "HHII", tag, kind, count, value)
    body += struct.pack(endian + "I", 0)
    if gps:
        body += struct.pack(endian + "HI", 0, 0)
    return header + body


def jpeg(width=20, height=10, exif=None, xmp=False, iptc=False, comment=False, scan=b"\x12\x34\x56\x78"):
    out = b"\xff\xd8" + segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if exif is not None:
        out += segment(0xE1, b"Exif\x00\x00" + exif)
    if xmp:
        out += segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>")
    if iptc:
        out += segment(0xED, b"Photoshop 3.0\x00")
    if comment:
        out += segment(0xFE, b"made on my phone")
    out += segment(0xDB, b"\x00" + bytes(range(64)))
    out += segment(0xC0, b"\x08" + struct.pack(">HH", height, width) + b"\x01\x01\x11\x00")
    out += segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + scan + b"\xff\xd9"
    return out


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(width=8, height=4, text=False, exif=None, time=False, animated=False):
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    if text:
        out += chunk(b"tEXt", b"Comment\x00taken at home")
    if exif is not None:
        out += chunk(b"eXIf", exif)
    if time:
        out += chunk(b"tIME", struct.pack(">HBBBBB", 2026, 10, 12, 9, 30, 0))
    if animated:
        out += chunk(b"acTL", struct.pack(">II", 2, 0))
    rows = b"".join(b"\x00" + b"\x7f\x20\x40" * width for _ in range(height))
    return out + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def riff_chunk(fourcc, body):
    return fourcc + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def webp(width=16, height=9, exif=None, xmp=False):
    flags = (0x08 if exif is not None else 0) | (0x04 if xmp else 0)
    vp8x = struct.pack("<B3x", flags) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + riff_chunk(b"VP8X", vp8x)
    if exif is not None:
        body += riff_chunk(b"EXIF", b"Exif\x00\x00" + exif)
    if xmp:
        body += riff_chunk(b"XMP ", b"<x:xmpmeta/>")
    body += riff_chunk(b"VP8L", b"\x2f" + b"\x00" * 9)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif(width=5, height=3):
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00;"
