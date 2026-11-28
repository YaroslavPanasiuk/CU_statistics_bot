"""Put a Google service-account key into .env, safely.

    PYTHONPATH=$PWD venv/bin/python deploy/set_google_creds.py ~/new-key.json

The value is a whole JSON document on one line, containing double quotes and a
PEM block whose newlines must survive as the two characters backslash-n. Doing
that by hand with sed is how a key ends up subtly corrupt, and the symptom is
not a parse error but "invalid_grant" hours later.

So the value is written single-quoted. python-dotenv -- which is what reads
this file; the systemd unit has no EnvironmentFile -- performs no escape
processing inside single quotes, so the document goes in verbatim and comes
back out identical. That is then asserted before the file is kept: the new
.env is parsed back and compared with the key file, and a mismatch restores
the backup rather than leaving a broken deployment.

Nothing here prints the key.
"""
import json
import shutil
import sys
from pathlib import Path

VAR = "GOOGLE_CREDS_JSON"


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2

    key_path = Path(argv[0]).expanduser()
    env_path = Path(argv[1]).expanduser() if len(argv) > 1 else Path(".env")

    if not key_path.is_file():
        print(f"FAIL: no such key file: {key_path}")
        return 1
    if not env_path.is_file():
        print(f"FAIL: no such .env: {env_path}")
        return 1

    try:
        creds = json.loads(key_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: {key_path} is not readable JSON: {exc}")
        return 1

    for field in ("type", "project_id", "client_email", "private_key"):
        if not creds.get(field):
            print(f"FAIL: the key file has no {field!r}; is this a service-account key?")
            return 1
    if creds["type"] != "service_account":
        print(f"FAIL: expected a service_account key, got type={creds['type']!r}")
        return 1

    compact = json.dumps(creds, ensure_ascii=False, separators=(",", ":"))
    if "'" in compact:
        # Single quotes are what make the value safe to embed verbatim; a value
        # containing one would end the quoting early and corrupt the rest.
        print("FAIL: the key contains a single quote, which this writer cannot quote safely.")
        return 1

    backup = env_path.with_suffix(env_path.suffix + f".bak-{key_path.stem[:12]}")
    shutil.copy2(env_path, backup)

    lines = env_path.read_text(encoding="utf-8").splitlines()
    line = f"{VAR}='{compact}'"
    for i, existing in enumerate(lines):
        if existing.startswith(f"{VAR}="):
            lines[i] = line
            break
    else:
        lines.append(line)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The point of this script: prove the round trip before anyone restarts.
    try:
        from dotenv import dotenv_values

        parsed = dotenv_values(env_path)
        if json.loads(parsed[VAR] or "{}") != creds:
            raise ValueError("the parsed value does not match the key file")
    except Exception as exc:
        shutil.copy2(backup, env_path)
        print(f"FAIL: {exc}")
        print(f"      {env_path} was restored from {backup.name}; nothing changed.")
        return 1

    print(f"{env_path}: {VAR} now holds {creds['client_email']}")
    print(f"  project: {creds['project_id']}")
    print(f"  backup:  {backup}")
    print("  round trip verified: the bot will parse this to exactly the key file.")
    print("\nRestart the bot to pick it up:")
    print("  sudo systemctl restart CU_statistics_bot.service")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
