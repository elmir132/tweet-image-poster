"""How X counts the length of a post. Shared by the validator and the thread splitter."""
import re
import unicodedata

MAX_WEIGHT = 280
URL_WEIGHT = 23
TLDS = ("com|org|net|io|co|ai|app|dev|edu|gov|me|ly|xyz|info|us|uk|de|fr|nl|eu|ca|au|tv|gg|so|sh|to|be|ua|az")
# A link with a scheme, or a bare domain on a known TLD (with an optional path).
# The TLD list is deliberate: it keeps "file.py" or "node.js" from counting as links.
URL_RE = re.compile(r"https?://\S+|\b(?:[a-z0-9-]+\.)+(?:" + TLDS + r")\b(?:/\S*)?", re.IGNORECASE)


def _char_weight(ch):
    cp = ord(ch)
    light = cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223 or 8242 <= cp <= 8247
    return 1 if light else 2


def weighted_length(text):
    """Length as X counts it: every URL is 23, CJK characters and emoji count 2.

    This follows the published twitter-text rules as I know them; X may change them.
    """
    text = unicodedata.normalize("NFC", text)
    urls = URL_RE.findall(text)
    rest = URL_RE.sub("", text)
    return URL_WEIGHT * len(urls) + sum(_char_weight(c) for c in rest)
