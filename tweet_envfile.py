"""Read a KEY=VALUE file (like .env) without any dependency. The values are returned, never exported or printed."""


class EnvFileError(ValueError):
    pass


def load_env_file(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as err:
        raise EnvFileError(f"cannot read {path}: {err.strerror}")
    values = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not key.replace("_", "").isalnum():
            raise EnvFileError(f"{path} line {number}: expected KEY=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values
