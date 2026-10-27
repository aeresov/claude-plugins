# SPDX-License-Identifier: AGPL-3.0-only
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mysql_url_to_cnf import ini_value, url_to_cnf

MODULE = Path(__file__).resolve().parent.parent / "src" / "mysql_url_to_cnf" / "__init__.py"


def parse(ini):
    """Turn a [client] INI block into a dict of its key=value lines."""
    assert ini.startswith("[client]\n")
    assert ini.endswith("\n")
    return dict(line.partition("=")[::2] for line in ini.splitlines()[1:])


def test_basic_url():
    cnf = parse(url_to_cnf("mysql://reader:pw@db.local:3306/app?sslmode=require"))
    assert cnf == {
        "host": "db.local",
        "port": "3306",
        "user": '"reader"',
        "password": '"pw"',
        "database": '"app"',
        "ssl-mode": "REQUIRED",
    }


def test_percent_encoded_password():
    cnf = parse(url_to_cnf("mysql://u:p%40ss%2Fword@h/db"))
    assert cnf["password"] == '"p@ss/word"'


def test_raw_slash_in_password():
    cnf = parse(url_to_cnf("mysql://u:pa/ss@h/db"))
    assert cnf["password"] == '"pa/ss"'
    assert cnf["host"] == "h"


def test_raw_at_in_password():
    cnf = parse(url_to_cnf("mysql://u:p@ss@h/db"))
    assert cnf["password"] == '"p@ss"'
    assert cnf["host"] == "h"


def test_ipv6_host_with_port():
    cnf = parse(url_to_cnf("mysql://u:pw@[::1]:3307/db"))
    assert cnf["host"] == "::1"
    assert cnf["port"] == "3307"


def test_ipv6_host_default_port():
    cnf = parse(url_to_cnf("mysql://u:pw@[2001:db8::5]/db"))
    assert cnf["host"] == "2001:db8::5"
    assert cnf["port"] == "3306"


def test_mariadb_scheme():
    assert parse(url_to_cnf("mariadb://u:pw@10.0.0.5/db"))["host"] == "10.0.0.5"


def test_default_port():
    assert parse(url_to_cnf("mysql://u:pw@h/db"))["port"] == "3306"


def test_no_database_line():
    assert "database" not in parse(url_to_cnf("mysql://u:pw@h:3306"))


def test_empty_password():
    assert parse(url_to_cnf("mysql://u@h/db"))["password"] == '""'


def test_no_sslmode_line():
    assert "ssl-mode" not in parse(url_to_cnf("mysql://u:pw@h/db"))


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("disable", "DISABLED"),
        ("prefer", "PREFERRED"),
        ("require", "REQUIRED"),
        ("verify-ca", "VERIFY_CA"),
        ("verify-full", "VERIFY_IDENTITY"),
        ("verify-identity", "VERIFY_IDENTITY"),
    ],
)
def test_sslmode_mapping(mode, expected):
    assert parse(url_to_cnf("mysql://u:pw@h/db?sslmode=" + mode))["ssl-mode"] == expected


def test_sslmode_already_mysql_form_passes_through():
    cnf = parse(url_to_cnf("mysql://u:pw@h/db?ssl-mode=VERIFY_IDENTITY"))
    assert cnf["ssl-mode"] == "VERIFY_IDENTITY"


def test_surrounding_whitespace_stripped():
    assert parse(url_to_cnf("  mysql://u:pw@h/db\n"))["host"] == "h"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "make[1]: Entering directory",
        # trailing output after a valid URL — the `make` recipe case local-settings.md warns about
        "mysql://u:pw@h/db\nmake[1]: Leaving directory",
        "mysql://u:pw@h/db extra",
        "https://example.com/",
        "mysql://h/db",
        "mysql://u:pw@/db",
        "mysql://:pw@h/db",
        "mysql://u:pw@[::1/db",
        "mysql://u:pw@[::1]3306/db",
        "mysql://u:pw@[]/db",
    ],
)
def test_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        url_to_cnf(bad)


def test_ini_value_quotes_hash():
    assert ini_value("pa#ss") == '"pa#ss"'


def test_ini_value_escapes_backslash():
    assert ini_value("pa\\ss") == '"pa\\\\ss"'


def test_ini_value_escapes_double_quote():
    assert ini_value('pa"ss') == '"pa\\"ss"'


def test_hash_password_is_quoted():
    assert parse(url_to_cnf("mysql://u:pa%23ss@h/db"))["password"] == '"pa#ss"'


@pytest.mark.parametrize("control", [*range(32), 127])
@pytest.mark.parametrize("component", ["user", "password", "database"])
def test_rejects_percent_encoded_control_characters(control, component):
    values = {"user": "reader", "password": "secret", "database": "app"}
    values[component] += f"%{control:02X}"
    with pytest.raises(ValueError, match="unsupported control characters"):
        url_to_cnf("mysql://{user}:{password}@h/{database}".format(**values))


def test_client_option_parser_preserves_escaped_values(tmp_path):
    mysql = shutil.which("mysql")
    if mysql is None:
        pytest.skip("mysql client not installed")
    isolated_parser = subprocess.run(
        [mysql, "--no-defaults", "--no-login-paths", "--version"],
        capture_output=True,
        text=True,
    )
    if isolated_parser.returncode != 0:
        # MariaDB's mysql alias lacks --no-login-paths; keep login-file isolation.
        pytest.skip("client does not support isolated --no-login-paths parsing")
    config = tmp_path / "client.cnf"
    config.write_text(url_to_cnf("mysql://reader:secret@h/db%23name%5Cpath%22tail"))
    result = subprocess.run(
        [mysql, f"--defaults-file={config}", "--no-login-paths", "--print-defaults"],
        capture_output=True,
        text=True,
        check=True,
    )
    # --print-defaults only parses the synthetic config; it never connects.
    assert '--database=db#name\\path"tail' in result.stdout
    assert "--init-command=" not in result.stdout


def test_main_cli_success():
    result = subprocess.run(
        [sys.executable, str(MODULE)],
        input="mysql://u:pw@h/db?sslmode=require",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("[client]\n")
    assert 'password="pw"' in result.stdout


def test_main_cli_rejects_garbage_without_echo():
    result = subprocess.run(
        [sys.executable, str(MODULE)],
        input="not a url",
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "mysql-url-to-cnf:" in result.stderr
    assert "not a url" not in result.stderr


def test_main_cli_rejects_option_injection_without_output():
    url = "mysql://reader:secret@h/db%0Ainit-command%3DSELECT%201%0A%23"
    result = subprocess.run([sys.executable, str(MODULE)], input=url, capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "unsupported control characters" in result.stderr
    assert "secret" not in result.stderr
    assert "init-command" not in result.stderr
