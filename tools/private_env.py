"""Read operator-owned dotenv files without shell evaluation or secret output."""
import os
import re
import stat


def load_private_env(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Use a private regular env file (chmod 600).")
        raw = stream.read(65_537)
    if len(raw) > 65_536 or "\0" in raw:
        raise ValueError("Invalid env file.")
    values = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError("Use unique KEY=value lines; shell commands are not supported.")
        value = value.strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Unclosed dotenv quote.")
            value = value[1:-1]
        if "${" in value or "$(" in value or "`" in value:
            raise ValueError("Use literal values, without shell expansion.")
        values[key] = value
    return values
