"""Extract observed Docker identities; execution validation remains stricter."""

import re


def docker_identifier(value):
    match = re.search(r"([a-f0-9]{64})(?:\.scope)?$", value)
    return match[1] if match else None
