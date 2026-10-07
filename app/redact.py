"""Detect and hide secrets so they never end up in memory or logs."""

import re

TOKEN_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z0-9 ]*PRIVATE KEY-----|$)"), "a private key"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), "an API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "a Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "a Google API key"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"), "a JWT token"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{12,}"), "a bearer token"),
]

SECRET_WORDS = re.compile(
    r"(?i)\b(passwords?|passwd|passcodes?|pwd|pin codes?|api[ _\-]?keys?|secret keys?|"
    r"access tokens?|auth(?:entication)? tokens?|session tokens?|session ids?|cookies?|"
    r"private keys?|ssh keys?|seed phrases?|recovery phrases?|security questions?|"
    r"cvv|otp|one[ -]time codes?)\b"
)

# Long random-looking strings (mix of letters and digits) are probably tokens
LONG_TOKEN = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b")

CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _find_cards(text):
    found = []
    for match in CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append(match)
    return found


def find_secret(text):
    """Return a short description of the first secret-looking thing in text, or None."""
    if not text:
        return None
    for pattern, label in TOKEN_PATTERNS:
        if pattern.search(text):
            return label
    if _find_cards(text):
        return "a credit card number"
    if SECRET_WORDS.search(text):
        return "a password or other credential"
    if LONG_TOKEN.search(text):
        return "a token"
    return None


def redact(text):
    """Mask secret values in text. Used before writing anything to logs."""
    if not text:
        return text
    text = str(text)
    for pattern, _label in TOKEN_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    for match in reversed(_find_cards(text)):
        text = text[: match.start()] + "[REDACTED]" + text[match.end():]
    # "password: hunter2" style pairs
    text = re.sub(
        r"(?i)\b(password|passwd|pwd|token|api[_\-]?key|secret|authorization|cookie)(\s*[:=]\s*)(\S+)",
        r"\1\2[REDACTED]",
        text,
    )
    text = LONG_TOKEN.sub("[REDACTED]", text)
    return text
