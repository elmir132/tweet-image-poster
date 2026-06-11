"""Read image headers and remove metadata, in plain Python with no imaging library.

What it does: report format and size, find EXIF/GPS and other metadata, and write a copy of
a JPEG, PNG or WebP without it. What it does not do: re-encode pixels, resize, or touch GIFs.
Every parser is bounds-checked; a damaged file gives partial information, never a crash.
"""
import os
import struct

MAX_READ = 20 * 1024 * 1024
JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
PNG_META = {b"eXIf": "EXIF", b"tEXt": "text chunks", b"iTXt": "text chunks", b"zTXt": "text chunks", b"tIME": "timestamp"}
XMP_JPEG = b"http://ns.adobe.com/xap/1.0/\x00"


class MediaError(ValueError):
    pass


def _tiff_facts(tiff):
    """(orientation or None, has_gps) from the TIFF block inside EXIF. (None, False) if unreadable."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None, False
    end = "<" if tiff[:2] == b"II" else ">"
    try:
        if struct.unpack_from(end + "H", tiff, 2)[0] != 42:
            return None, False
        offset = struct.unpack_from(end + "I", tiff, 4)[0]
        count = struct.unpack_from(end + "H", tiff, offset)[0]
        orientation, gps = None, False
        for i in range(min(count, 200)):
            base = offset + 2 + 12 * i
            tag, kind = struct.unpack_from(end + "HH", tiff, base)
            if tag == 0x0112 and kind == 3:
                orientation = struct.unpack_from(end + "H", tiff, base + 8)[0]
            elif tag == 0x8825:
                gps = True
        return orientation, gps
    except struct.error:
        return None, False


def _jpeg_segments(data):
    """Yield (marker, start, end) for each segment before the scan data; end is exclusive.
    A final ('scan', pos, len(data)) is yielded where the compressed image begins."""
    pos = 2
    while pos + 2 <= len(data):
        if data[pos] != 0xFF:
            return
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xD9:
            return
        if pos + 4 > len(data):
            return
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if length < 2:
            return
        end = pos + 2 + length
        if marker == 0xDA:
            yield "scan", pos, len(data)
            return
        yield marker, pos, min(end, len(data))
        pos = end


def _jpeg_info(data):
    info = {"format": "jpeg", "labels": [], "orientation": None, "gps": False}
    for marker, start, end in _jpeg_segments(data):
        if marker == "scan":
            break
        body = data[start + 4:end]
        if marker in JPEG_SOF and len(body) >= 5:
            info["height"], info["width"] = struct.unpack(">HH", body[1:5])
        elif marker == 0xE1 and body.startswith(b"Exif\x00\x00"):
            orientation, gps = _tiff_facts(body[6:])
            info["orientation"] = orientation
            info["gps"] = info["gps"] or gps
            info["labels"].append("EXIF")
        elif marker == 0xE1 and body.startswith(XMP_JPEG):
            info["labels"].append("XMP")
        elif marker == 0xED:
            info["labels"].append("IPTC")
        elif marker == 0xFE:
            info["labels"].append("comment")
    return info


def _png_chunks(data):
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        yield ctype, pos, min(end, len(data))
        pos = end
        if ctype == b"IEND":
            return


def _png_info(data):
    info = {"format": "png", "labels": [], "orientation": None, "gps": False}
    if len(data) >= 24:
        info["width"], info["height"] = struct.unpack(">II", data[16:24])
    for ctype, start, end in _png_chunks(data):
        if ctype in PNG_META:
            info["labels"].append(PNG_META[ctype])
        if ctype == b"eXIf":
            orientation, gps = _tiff_facts(data[start + 8:end - 4])
            info["orientation"], info["gps"] = orientation, info["gps"] or gps
        if ctype == b"acTL":
            info["animated"] = True
    return info


def _webp_chunks(data):
    pos = 12
    while pos + 8 <= len(data):
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = pos + 8 + size + (size & 1)
        yield data[pos:pos + 4], pos, min(end, len(data))
        pos = end


def _webp_info(data):
    info = {"format": "webp", "labels": [], "orientation": None, "gps": False}
    for fourcc, start, end in _webp_chunks(data):
        body = data[start + 8:end]
        if fourcc == b"VP8X" and len(body) >= 10:
            info["width"] = 1 + int.from_bytes(body[4:7], "little")
            info["height"] = 1 + int.from_bytes(body[7:10], "little")
        elif fourcc == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
            info.setdefault("width", struct.unpack_from("<H", body, 6)[0] & 0x3FFF)
            info.setdefault("height", struct.unpack_from("<H", body, 8)[0] & 0x3FFF)
        elif fourcc == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
            bits = struct.unpack_from("<I", body, 1)[0]
            info.setdefault("width", (bits & 0x3FFF) + 1)
            info.setdefault("height", ((bits >> 14) & 0x3FFF) + 1)
        elif fourcc == b"EXIF":
            tiff = body[6:] if body.startswith(b"Exif\x00\x00") else body
            orientation, gps = _tiff_facts(tiff)
            info["orientation"], info["gps"] = orientation, info["gps"] or gps
            info["labels"].append("EXIF")
        elif fourcc == b"XMP ":
            info["labels"].append("XMP")
        elif fourcc == b"ANIM":
            info["animated"] = True
    return info


def _read(path):
    with open(path, "rb") as handle:
        return handle.read(MAX_READ + 1)


def image_info(path):
    """What can be read from the file's own bytes. Keys: format, width, height, bytes, labels
    (metadata found), gps (bool), orientation, and animated when known. Never raises for a
    damaged image; unknown values are simply missing."""
    data = _read(path)
    size = os.path.getsize(path)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        info = _png_info(data)
    elif data.startswith(b"\xff\xd8\xff"):
        info = _jpeg_info(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        info = _webp_info(data)
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        info = {"format": "gif", "labels": [], "orientation": None, "gps": False, "width": width, "height": height}
    else:
        info = {"format": None, "labels": [], "orientation": None, "gps": False}
    info["bytes"] = size
    if info["gps"]:
        info["labels"] = ["GPS location"] + info["labels"]
    info["labels"] = list(dict.fromkeys(info["labels"]))
    return info


def _minimal_orientation_exif(orientation):
    """An EXIF block holding only the Orientation tag, so a stripped photo still displays upright."""
    tiff = b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">H", 1) + struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", orientation, 0) + b"\x00\x00\x00\x00"
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _strip_jpeg(data):
    info = _jpeg_info(data)
    out = bytearray(b"\xff\xd8")
    removed = []
    keep_orientation = info["orientation"] not in (None, 1)
    if keep_orientation:
        out += _minimal_orientation_exif(info["orientation"])
    scan_at = len(data)
    for marker, start, end in _jpeg_segments(data):
        if marker == "scan":
            scan_at = start
            break
        body = data[start + 4:end]
        if marker == 0xE1 and body.startswith(b"Exif\x00\x00"):
            removed.append("GPS location" if info["gps"] else "EXIF")
            if info["gps"]:
                removed.append("EXIF")
        elif marker == 0xE1 and body.startswith(XMP_JPEG):
            removed.append("XMP")
        elif marker == 0xED:
            removed.append("IPTC")
        elif marker == 0xFE:
            removed.append("comment")
        else:
            out += data[start:end]
    out += data[scan_at:]
    return bytes(out), list(dict.fromkeys(removed))


def _strip_png(data):
    info = _png_info(data)
    out = bytearray(data[:8])
    removed = []
    for ctype, start, end in _png_chunks(data):
        if ctype in PNG_META:
            removed.append(PNG_META[ctype])
        else:
            out += data[start:end]
    if info["gps"]:
        removed.insert(0, "GPS location")
    return bytes(out), list(dict.fromkeys(removed))


def _strip_webp(data):
    info = _webp_info(data)
    out = bytearray()
    removed = []
    for fourcc, start, end in _webp_chunks(data):
        chunk = bytearray(data[start:end])
        if fourcc in (b"EXIF", b"XMP "):
            removed.append("EXIF" if fourcc == b"EXIF" else "XMP")
            continue
        if fourcc == b"VP8X" and len(chunk) > 8:
            chunk[8] &= ~0x0C  # clear the EXIF (0x08) and XMP (0x04) flags
        out += chunk
    if info["gps"]:
        removed.insert(0, "GPS location")
    body = b"WEBP" + bytes(out)
    return b"RIFF" + struct.pack("<I", len(body)) + body, list(dict.fromkeys(removed))


def strip_metadata(src, dest):
    """Write a metadata-free copy of src to dest. Returns the list of what was removed; when
    that list is empty nothing is written. The copy must report the same size as the
    original or MediaError is raised and dest is not left behind."""
    data = _read(src)
    if len(data) > MAX_READ:
        raise MediaError(f"{src}: too large to process")
    if data.startswith(b"\xff\xd8\xff"):
        cleaned, removed = _strip_jpeg(data)
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        cleaned, removed = _strip_png(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        cleaned, removed = _strip_webp(data)
    else:
        return []  # GIFs and unknown files are left alone
    if not removed:
        return []
    before, after = image_info(src), None
    with open(dest, "wb") as handle:
        handle.write(cleaned)
    after = image_info(dest)
    if (before.get("width"), before.get("height")) != (after.get("width"), after.get("height")):
        os.remove(dest)
        raise MediaError(f"{src}: removing metadata changed the image size; the file was not used")
    return removed
