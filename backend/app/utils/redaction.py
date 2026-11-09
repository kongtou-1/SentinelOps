"""Redaction at every model and persistence boundary; no command interpretation."""

import re

SENSITIVE = re.compile(
    r"password|passphrase|secret|api.?key|authorization|token|private.?key", re.I
)


def redact(value):
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if SENSITIVE.search(str(k)) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    value = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        value,
        flags=re.S,
    )
    value = re.sub(r"(?i)(bearer\s+)[\w.\-/+=]+", r"\1[REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
    return re.sub(
        r"(?i)((?:password|api[_-]?key|secret|token)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+",
        r"\1[REDACTED]",
        value,
    )


class RedactedStream:
    """Keep unfinished lines/PEM blocks private, including split credential values."""

    def __init__(self):
        self.pending = ""

    def feed(self, text, final=False):
        self.pending += text
        if final:
            result, self.pending = redact(self.pending), ""
            if "-----BEGIN" in result and "PRIVATE KEY-----" in result:
                result = result[: result.index("-----BEGIN")] + "[REDACTED PRIVATE KEY]"
            return result
        boundary = self.pending.rfind("\n") + 1
        unfinished = re.search(
            r"(?i)(bearer\s*|(?:password|api[_-]?key|secret|token)[\"']?\s*[:=]\s*[\"']?)$",
            self.pending[:boundary],
        )
        if unfinished:
            boundary = min(boundary, unfinished.start())
        begin = self.pending.find("-----BEGIN")
        if begin >= 0 and "-----END" not in self.pending[begin:]:
            boundary = min(boundary, begin)
        result = redact(self.pending[:boundary])
        self.pending = self.pending[boundary:]
        return result
