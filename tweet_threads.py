"""Split a long text into a numbered thread that fits X's length rules."""
import re

from tweet_text import MAX_WEIGHT, weighted_length

COUNTER_RESERVE = len(" (99/99)")  # room kept free for the " (3/12)" suffix
MAX_PARTS = 99
SEPARATOR = re.compile(r"(?m)^[ \t]*---[ \t]*$")


class ThreadError(ValueError):
    pass


def _units(text):
    """Paragraphs and sentences, each as (separator before it, text)."""
    units = []
    for p_index, paragraph in enumerate(re.split(r"\n\s*\n", text.strip())):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        for s_index, sentence in enumerate(re.split(r"(?<=[.!?])\s+", paragraph)):
            sep = "" if not units else ("\n\n" if s_index == 0 and p_index > 0 else " ")
            units.append((sep, sentence))
    return units


def _hard_split(word, budget):
    """A single word too heavy for one post: cut it into pieces that fit."""
    pieces, current = [], ""
    for ch in word:
        if weighted_length(current + ch) > budget:
            pieces.append(current)
            current = ch
        else:
            current += ch
    return pieces + ([current] if current else [])


def _auto_split(text, budget):
    chunks, current = [], ""

    def push(piece):
        nonlocal current
        if current:
            chunks.append(current)
        current = piece

    for sep, sentence in _units(text):
        candidate = current + sep + sentence if current else sentence
        if weighted_length(candidate) <= budget:
            current = candidate
            continue
        if weighted_length(sentence) <= budget:
            push(sentence)
            continue
        for word in sentence.split():  # a sentence longer than a whole post: split at spaces
            candidate = (current + " " + word) if current else word
            if weighted_length(candidate) <= budget:
                current = candidate
            elif weighted_length(word) <= budget:
                push(word)
            else:
                pieces = _hard_split(word, budget)
                for piece in pieces[:-1]:
                    push(piece)
                push(pieces[-1])
    push("")
    return chunks


def split_thread(text, limit=MAX_WEIGHT, numbered=True):
    """Return the posts of a thread.

    Text that already fits is one post. A line containing only `---` splits the text by
    hand (the parts are used as written). Otherwise the text is cut at paragraph ends, then
    sentence ends, then spaces, and only inside a single word as a last resort. When there is
    more than one post and `numbered` is true, each gets a " (i/n)" suffix.
    """
    text = text.strip()
    if not text:
        return []
    manual = [part.strip() for part in SEPARATOR.split(text) if part.strip()]
    if len(manual) > 1:
        parts = manual
    elif weighted_length(text) <= limit:
        return [text]
    else:
        parts = _auto_split(text, limit - (COUNTER_RESERVE if numbered else 0))
    if len(parts) > MAX_PARTS:
        raise ThreadError(f"the text needs {len(parts)} posts; the most this tool will make is {MAX_PARTS}")
    if numbered and len(parts) > 1:
        parts = [f"{part} ({i}/{len(parts)})" for i, part in enumerate(parts, 1)]
    return parts
