# SPDX-License-Identifier: AGPL-3.0-only
"""Convert a mysql:// URL into a MySQL [client] option-file block.

`url_to_cnf()` parses a mysql:// or mariadb:// URL and returns the option-file
body `mysql --defaults-file` consumes; it raises ValueError on anything that
isn't a usable URL. `main()` wraps it as a stdin->stdout filter for the
mysql-client plugin's connection-discovery flow. Pure standard library — no
runtime dependencies, runnable with plain python3.
"""
import sys
from urllib.parse import unquote

SSL_MODES = {
    "disable": "DISABLED", "disabled": "DISABLED",
    "prefer": "PREFERRED", "preferred": "PREFERRED",
    "require": "REQUIRED", "required": "REQUIRED",
    "verify-ca": "VERIFY_CA", "verify-full": "VERIFY_IDENTITY",
    "verify-identity": "VERIFY_IDENTITY",
}


def ini_value(value):
    # MySQL option files: '#' starts a comment, surrounding space is trimmed,
    # '\' escapes. Double-quote + backslash-escape keeps any value intact.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def url_to_cnf(raw):
    """Parse a mysql:// URL; return a [client] INI block.

    Raises ValueError on anything that isn't a usable mysql:// / mariadb:// URL.
    The userinfo is split on the last '@' and first ':', so a raw '@', ':' or
    '/' in the password is tolerated; user/password/database are percent-decoded.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("empty input — connection_cmd produced nothing")
    scheme, sep, after = raw.partition("://")
    if not sep or scheme.lower() not in ("mysql", "mariadb"):
        raise ValueError("input is not a mysql:// or mariadb:// URL")

    userinfo, at, hostpart = after.rpartition("@")
    if not at:
        raise ValueError("URL has no '<user>[:<password>]@' part")
    user, _, password = userinfo.partition(":")
    if not user:
        raise ValueError("URL has no user")

    hostport = hostpart.split("/", 1)[0].split("?", 1)[0]
    host, _, port = hostport.partition(":")
    if not host:
        raise ValueError("URL has no host")

    tail = hostpart[len(hostport):]
    database = tail.split("?", 1)[0].lstrip("/")
    query = tail.split("?", 1)[1] if "?" in tail else ""

    ssl_mode = ""
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        if key.lower() in ("sslmode", "ssl-mode") and value:
            ssl_mode = SSL_MODES.get(value.lower(), value.upper())

    rows = [
        "[client]",
        "host=" + host,
        "port=" + (port or "3306"),
        "user=" + ini_value(unquote(user)),
        "password=" + ini_value(unquote(password)),
    ]
    if database:
        rows.append("database=" + ini_value(unquote(database)))
    if ssl_mode:
        rows.append("ssl-mode=" + ssl_mode)
    return "\n".join(rows) + "\n"


def main():
    try:
        sys.stdout.write(url_to_cnf(sys.stdin.read()))
    except ValueError as exc:
        sys.exit("mysql-url-to-cnf: " + str(exc))


if __name__ == "__main__":
    main()
