"""Validate evidence returned by the verifier against independently fetched text."""

import html
import unicodedata


def normalize(text: str) -> str:
    decoded = html.unescape(text)
    normalized = unicodedata.normalize("NFKC", decoded)
    return " ".join(normalized.split()).lower()


def checked_evidence(excerpt: str, page_text: str):
    """Return the evidence only when it satisfies the assignment's excerpt rule."""
    if not isinstance(excerpt, str) or len(excerpt) > 300:
        return None
    normalized = normalize(excerpt)
    if normalized and normalized in normalize(page_text):
        return excerpt
    return None
