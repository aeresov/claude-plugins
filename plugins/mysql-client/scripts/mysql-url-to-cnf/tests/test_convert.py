# SPDX-License-Identifier: AGPL-3.0-only
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


def test_example_rds_proxy_url():
    url = ("mysql://su_ws_user:cQQo*Wvtw6DfxG^F@"
           "feat-staged-fw-hatch-userpass.endpoint.proxy-chmww1z0xozm."
           "us-east-1.rds.amazonaws.com:3306/altecws?sslmode=require")
    cnf = parse(url_to_cnf(url))
    assert cnf["host"].endswith(".rds.amazonaws.com")
    assert cnf["user"] == '"su_ws_user"'
    assert cnf["password"] == '"cQQo*Wvtw6DfxG^F"'
    assert cnf["database"] == '"altecws"'
    assert cnf["ssl-mode"] == "REQUIRED"


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


@pytest.mark.parametrize("mode,expected", [
    ("disable", "DISABLED"),
    ("prefer", "PREFERRED"),
    ("require", "REQUIRED"),
    ("verify-ca", "VERIFY_CA"),
    ("verify-full", "VERIFY_IDENTITY"),
    ("verify-identity", "VERIFY_IDENTITY"),
])
def test_sslmode_mapping(mode, expected):
    assert parse(url_to_cnf("mysql://u:pw@h/db?sslmode=" + mode))["ssl-mode"] == expected


def test_sslmode_already_mysql_form_passes_through():
    cnf = parse(url_to_cnf("mysql://u:pw@h/db?ssl-mode=VERIFY_IDENTITY"))
    assert cnf["ssl-mode"] == "VERIFY_IDENTITY"


def test_surrounding_whitespace_stripped():
    assert parse(url_to_cnf("  mysql://u:pw@h/db\n"))["host"] == "h"


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "make[1]: Entering directory",
    "https://example.com/",
    "mysql://h/db",
    "mysql://u:pw@/db",
    "mysql://:pw@h/db",
])
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


def test_main_cli_success():
    result = subprocess.run(
        [sys.executable, str(MODULE)],
        input="mysql://u:pw@h/db?sslmode=require",
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("[client]\n")
    assert 'password="pw"' in result.stdout


def test_main_cli_rejects_garbage_without_echo():
    result = subprocess.run(
        [sys.executable, str(MODULE)],
        input="not a url",
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "mysql-url-to-cnf:" in result.stderr
    assert "not a url" not in result.stderr
