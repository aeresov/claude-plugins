# SPDX-License-Identifier: AGPL-3.0-only
"""ovpn2_helper: the profile allowlist, the route check and the up/down flows. systemd, ip and openvpn are faked; nothing
here needs root or touches the host."""

from __future__ import annotations

import ast
import io
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openvpn3_mcp import ovpn2_helper as helper
from openvpn3_mcp.ovpn2_helper import Refused, sanitize_profile

PEM = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUexample\n-----END CERTIFICATE-----"

# What `aws ec2 export-client-vpn-client-configuration` hands out for a mutual-TLS endpoint, plus the lines a
# provisioning command may append.
AWS_PROFILE = f"""\
client
dev tun
proto udp
remote cvpn-endpoint-0123456789abcdef0.prod.clientvpn.us-east-1.amazonaws.com 443
remote-random-hostname
resolv-retry infinite
nobind
remote-cert-tls server
cipher AES-256-GCM
verb 3
<ca>
{PEM}
</ca>


reneg-sec 0

verify-x509-name server name
dhcp-option DOMAIN amazonaws.com
dhcp-option DOMAIN compute.internal

<cert>
{PEM}
</cert>

<key>
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCexample
-----END PRIVATE KEY-----
</key>
"""

MINIMAL = "client\nremote vpn.example.com 1194\n"


# sanitize_profile ------------------------------------------------------------


def test_aws_profile_is_accepted_and_rewritten():
    out, dropped = sanitize_profile(AWS_PROFILE)
    lines = out.splitlines()
    assert lines[:7] == [
        "client",
        "proto udp",
        "remote cvpn-endpoint-0123456789abcdef0.prod.clientvpn.us-east-1.amazonaws.com 443",
        "remote-random-hostname",
        "nobind",
        "remote-cert-tls server",
        "cipher AES-256-GCM",
    ]
    assert "reneg-sec 0" in lines and "verify-x509-name server name" in lines
    # The backend sets these itself, and DNS options stay out of what OpenVPN reads.
    assert dropped == ["dev", "resolv-retry", "verb", "dhcp-option"]
    assert not any(line.startswith(("dev", "verb", "resolv-retry", "dhcp-option")) for line in lines)
    assert sanitize_profile(MINIMAL + "mute 1\n") == (MINIMAL, ["mute"])  # would hide the PUSH_REPLY lines the helper reads


def test_inline_blocks_survive_verbatim():
    out, _ = sanitize_profile(AWS_PROFILE)
    for tag in ("ca", "cert", "key"):
        assert out.count(f"<{tag}>\n") == 1 and out.count(f"</{tag}>\n") == 1
    assert f"<ca>\n{PEM}\n</ca>\n" in out
    assert "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCexample\n-----END PRIVATE KEY-----" in out


@pytest.mark.parametrize(
    "line",
    [
        "up /tmp/x.sh",
        "down /tmp/x.sh",
        "route-up /tmp/x.sh",
        "ipchange /tmp/x.sh",
        "tls-verify /tmp/x.sh",
        "script-security 2",
        "plugin /tmp/x.so",
        "iproute /tmp/ip",
        "engine /tmp/x.so",
        "providers legacy",
        "setenv PATH /tmp",
        "setenv opt up /tmp/x.sh",
        "setenv-safe X 1",
        "log /etc/cron.d/x",
        "log-append /etc/ld.so.preload",
        "status /etc/resolv.conf",
        "writepid /etc/x",
        "management /run/x.sock unix",
        "management-client-user nobody",
        "config /etc/shadow",
        "auth-user-pass /etc/shadow",
        "askpass /etc/shadow",
        "ca /etc/ssl/ca.pem",
        "cert /etc/ssl/c.pem",
        "key /etc/ssl/k.pem",
        "cd /tmp",
        "chroot /tmp",
        "user root",
        "group root",
        "daemon",
        "route 0.0.0.0 0.0.0.0",
        "route 10.0.0.0 255.0.0.0",
        "route-nopull",
        "redirect-gateway def1",
        "redirect-private",
        "ifconfig 10.0.0.5 255.255.255.0",
        "pull-filter accept redirect-gateway",
        "allow-pull-fqdn",
        "ignore-unknown-option up",
        "compress lz4",
        "comp-lzo",
        "allow-compression yes",
        "remap-usr1 SIGHUP",
        "inactive 0",
        "http-proxy proxy.example.com 3128",
        "socks-proxy 127.0.0.1 1080",
        "mktun",
        "dns server 1 address 1.1.1.1",
    ],
)
def test_everything_off_the_allowlist_refuses_the_profile(line):
    with pytest.raises(Refused, match="not allowed by the OpenVPN 2 backend"):
        sanitize_profile(MINIMAL + line + "\n")


@pytest.mark.parametrize(
    "line",
    [
        '"up" /tmp/x.sh',  # quotes don't change what OpenVPN parses the directive as
        "'up' /tmp/x.sh",
        "  \tup /tmp/x.sh",
        "--up /tmp/x.sh",  # a leading -- is stripped by OpenVPN
        'u"p" /tmp/x.sh'.replace('u"p"', '"u"p'),  # adjacent quoted and bare text
    ],
)
def test_disguised_directives_are_still_refused(line):
    with pytest.raises(Refused):
        sanitize_profile(MINIMAL + line + "\n")


def test_bom_does_not_hide_the_first_directive():
    with pytest.raises(Refused, match="'up'"):
        sanitize_profile("﻿up /tmp/x.sh\n" + MINIMAL)
    assert sanitize_profile("﻿" + MINIMAL)[0].startswith("client\n")


def test_leading_dashes_get_a_hint():
    with pytest.raises(Refused, match="drop the leading dashes"):
        sanitize_profile(MINIMAL + "--nobind\n")


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("nobind \\x", "backslash"),  # OpenVPN only warns here and drops the line; a line it reads differently is refused
        ('verify-x509-name "unterminated', "unterminated quote"),
        ("verify-x509-name 'unterminated", "unterminated quote"),
        ("nobind \x01", "control character"),
        ("verify-x509-name " + "x" * 300, "longer than"),
    ],
)
def test_lines_openvpn_would_read_differently_are_refused(line, why):
    with pytest.raises(Refused, match=why):
        sanitize_profile(MINIMAL + line + "\n")


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        ("nobind # trailing comment", "nobind"),
        ("nobind ; trailing comment", "nobind"),
        ("verify-x509-name 'vpn server' name", 'verify-x509-name "vpn server" name'),
        ('verify-x509-name "C=US, CN=vpn" subject', 'verify-x509-name "C=US, CN=vpn" subject'),
        ("verify-x509-name vpn\\ server name", 'verify-x509-name "vpn server" name'),
        ("proto\tudp", "proto udp"),
        ("remote 10.1.2.3 443 udp", "remote 10.1.2.3 443 udp"),
        ("remote fd00::1 1194", "remote fd00::1 1194"),
        ("tls-version-min 1.2 or-highest", "tls-version-min 1.2 or-highest"),
        ("keepalive 10 60", "keepalive 10 60"),
    ],
)
def test_accepted_lines_are_rewritten_from_their_tokens(line, kept):
    out, _ = sanitize_profile(MINIMAL + line + "\n")
    assert kept in out.splitlines()


def test_comment_only_and_blank_lines_vanish():
    out, dropped = sanitize_profile("# a comment\n; another\n\n   \n" + MINIMAL)
    assert out == MINIMAL and dropped == []


def test_crlf_profile_is_read_like_lf():
    assert sanitize_profile(MINIMAL.replace("\n", "\r\n"))[0] == MINIMAL


@pytest.mark.parametrize(
    "line",
    [
        "remote",
        "remote bad_host! 443",
        "remote vpn.example.com 0",
        "remote vpn.example.com 70000",
        "remote vpn.example.com 443 sctp",
        "remote vpn.example.com 443 udp extra",
        "proto icmp",
        "remote-cert-tls client",
        "client now",
        "dev tap",
        "dev tun-x",
        "cipher AES-256-GCM;up",
        "reneg-sec forever",
        "tls-version-min 0.9",
        "verify-x509-name a b",
        "tun-mtu 1",
        "reneg-sec \u00b2",  # a superscript two: str.isdigit() says yes, int() says ValueError
        "remote vpn.example.com \u00b2",
    ],
)
def test_allowed_directives_with_bad_parameters_are_refused(line):
    with pytest.raises(Refused):
        sanitize_profile("client\n" + line + "\n" + ("" if line.startswith("remote ") else "remote vpn.example.com\n"))


def test_profile_without_remote_is_refused():
    with pytest.raises(Refused, match="no 'remote' line"):
        sanitize_profile("client\nnobind\n")


def test_unknown_inline_block_is_refused():
    with pytest.raises(Refused, match="<connection> is not allowed"):
        sanitize_profile(MINIMAL + "<connection>\nremote evil.example.com 443\n</connection>\n")


def test_unclosed_inline_block_is_refused():
    with pytest.raises(Refused, match="never closed"):
        sanitize_profile(MINIMAL + "<ca>\n" + PEM + "\n")


def test_directive_lookalikes_inside_a_block_stay_data():
    # OpenVPN reads a block verbatim up to its closing tag, so this is certificate text to it, never a directive.
    out, _ = sanitize_profile(MINIMAL + "<ca>\nup /tmp/x.sh\n</ca>\n")
    assert out.endswith("<ca>\nup /tmp/x.sh\n</ca>\n")


def test_block_ends_where_openvpn_ends_it():
    # OpenVPN closes a block at the first line that *starts with* the closing tag; what follows on that line is dropped,
    # and the next line is a directive again.
    with pytest.raises(Refused, match="'up'"):
        sanitize_profile(MINIMAL + "<ca>\nx\n  </ca> trailing\nup /tmp/x.sh\n")
    out, _ = sanitize_profile(MINIMAL + "<ca>\nx\n  </ca> trailing\nnobind\n")
    assert out.endswith("<ca>\nx\n</ca>\nnobind\n")


def test_long_line_inside_a_block_is_refused():
    with pytest.raises(Refused, match="longer than"):
        sanitize_profile(MINIMAL + "<ca>\n" + "A" * 300 + "\n</ca>\n")


def test_line_limit_is_in_bytes_and_below_openvpns_chunk_size():
    # OpenVPN reads an inline block in 255-byte chunks and takes a chunk that starts with the closing tag for the end of
    # the block. A body line of 255+ bytes ending in "</ca>" would close the block for OpenVPN while this code still
    # copied the following lines out as certificate text: directives nobody vetted, read by a root-run OpenVPN.
    assert helper._MAX_LINE_BYTES < 255
    at_limit = "A" * helper._MAX_LINE_BYTES
    assert at_limit in sanitize_profile(MINIMAL + f"<ca>\n{at_limit}\n</ca>\n")[0]
    with pytest.raises(Refused, match="longer than"):
        sanitize_profile(MINIMAL + f"<ca>\n{at_limit}A\n</ca>\n")
    smuggle = "\u00e9" * 127 + "x</ca>"  # 133 characters, 260 bytes: short if counted in characters
    assert len(smuggle) < helper._MAX_LINE_BYTES < len(smuggle.encode())
    with pytest.raises(Refused, match="longer than"):
        sanitize_profile(MINIMAL + f"<ca>\n{smuggle}\nplugin /tmp/x.so\n</ca>\n")
    with pytest.raises(Refused, match="longer than"):
        sanitize_profile(MINIMAL + "# " + "\u00e9" * 130 + "\n")


@pytest.mark.parametrize("header", ["-----BEGIN ENCRYPTED PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED"])
def test_passphrase_protected_key_is_refused(header):
    # OpenVPN would ask for the passphrase (systemd-ask-password, as root) and wait for an answer nobody gives.
    with pytest.raises(Refused, match="passphrase-protected"):
        sanitize_profile(MINIMAL + f"<key>\n{header}\nMIIFHDBOBgkqhkiG9w0BBQ0wQTApBgkqhkiG9w0BBQww\n-----END ENCRYPTED PRIVATE KEY-----\n</key>\n")
    assert "passphrase" not in sanitize_profile(MINIMAL + "<ca>\nENCRYPTED is just a word in a CA comment\n</ca>\n")[0]


def test_refusal_never_quotes_more_than_the_directive_name():
    secret = "hunter2-very-secret"
    with pytest.raises(Refused) as excinfo:
        sanitize_profile(MINIMAL + f"auth-user-pass {secret}\n")
    assert secret not in str(excinfo.value)


@pytest.mark.parametrize("stray", ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7", "-----BEGIN PRIVATE KEY-----", "Sup3rSecretPassw0rd"])
def test_refusal_does_not_quote_text_that_is_not_a_directive(stray):
    # A profile whose <key> tag went missing: its lines are key material, not directive names, and must not be echoed.
    with pytest.raises(Refused, match="doesn't start with a directive") as excinfo:
        sanitize_profile(MINIMAL + stray + "\n")
    assert stray not in str(excinfo.value) and stray[:8] not in str(excinfo.value)


# naming ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../etc",
        "a/b",
        "-leading-dash",
        "a b",
        "x" * 65,
        "ünï",
        "a;b",
        "a\nb",
        ".hidden",
        "demo\n",
        "demo\t",
        "demo\r",
        "demo%i",
        "demo\x00",
        "a*",
        "a?",
        "a[bc]",
        "a$b",
        "a@b",
        "a:b",
        "a,b",
        "a=b",
        "a~b",
        'a"b',
        "a'b",
        "a\\x2fb",
    ],
)
def test_bad_names_are_refused(name):
    with pytest.raises(Refused, match="profile name"):
        helper.check_name(name)


def test_names_map_to_unit_device_and_profile_path():
    name = "ovpn3-od-0b7e9c1e-5d3f-4c53-9d0a-1f2e3d4c5b6a"
    assert helper.unit_name(name) == f"openvpn3-on-demand-{name}.service"
    assert helper.profile_path(name) == helper.RUN_DIR / f"{name}.conf"
    device = helper.device_name(name)
    assert device.startswith("ovod") and len(device) <= 15  # IFNAMSIZ
    assert helper.device_name("other") != device


# unit state ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("props", "state"),
    [
        ({"LoadState": "not-found", "ActiveState": "inactive"}, "absent"),
        ({}, "absent"),
        ({"LoadState": "loaded", "ActiveState": "activating"}, "connecting"),
        # systemd says "active" as soon as OpenVPN has parsed its options — before it has sent a single packet.
        ({"LoadState": "loaded", "ActiveState": "active", "StatusText": "Pre-connection initialization successful"}, "connecting"),
        ({"LoadState": "loaded", "ActiveState": "active", "StatusText": ""}, "connecting"),
        ({"LoadState": "loaded", "ActiveState": "active", "StatusText": helper.CONNECTED_STATUS}, "connected"),
        ({"LoadState": "loaded", "ActiveState": "deactivating", "StatusText": helper.CONNECTED_STATUS}, "stopping"),
        ({"LoadState": "loaded", "ActiveState": "inactive", "Result": "success"}, "absent"),
        ({"LoadState": "loaded", "ActiveState": "failed", "Result": "exit-code"}, "failed"),
        ({"LoadState": "loaded", "ActiveState": "inactive", "Result": "timeout"}, "failed"),
    ],
)
def test_unit_state(props, state):
    assert helper.unit_state(props) == state


def test_describe_names_the_failure():
    info = helper.describe("demo", {"LoadState": "loaded", "ActiveState": "failed", "Result": "exit-code", "ExecMainStatus": "1"})
    assert info == {"name": "demo", "unit": "openvpn3-on-demand-demo.service", "state": "failed", "detail": "exit-code, exit status 1"}


# command lines ---------------------------------------------------------------


def test_pull_filters_come_before_the_profile_and_overrides_after():
    argv = helper.openvpn_argv("demo", Path("/run/x/demo.conf"), drop_privileges=True)
    config_at = argv.index("--config")
    filters = [(argv[i + 1], argv[i + 2]) for i, arg in enumerate(argv) if arg == "--pull-filter"]
    # First match wins among pull filters, and for single-valued options the last one does.
    assert max(i for i, arg in enumerate(argv) if arg == "--pull-filter") < config_at
    assert filters == [("accept", prefix) for prefix in helper.PULL_ACCEPT] + [("ignore", "")]
    for option in (
        "--dev",
        "--route-noexec",
        "--script-security",
        "--verb",
        "--mute",
        "--resolv-retry",
        "--connect-timeout",
        "--hand-window",
        "--remap-usr1",
        "--inactive",
        "--user",
    ):
        assert argv.index(option) > config_at
    assert argv[argv.index("--script-security") + 1] == "1"
    # The helper reads the server's PUSH_REPLY from OpenVPN's log: a profile must not be able to quieten it.
    assert argv[argv.index("--verb") + 1] == "3" and argv[argv.index("--mute") + 1] == "0"
    assert argv[argv.index("--remap-usr1") + 1] == "SIGTERM"
    assert argv[argv.index("--dev") + 1] == helper.device_name("demo")
    for forbidden in ("--up", "--down", "--route-up", "--plugin", "--management", "--daemon", "--log", "--route", "--redirect-gateway"):
        assert forbidden not in argv


def test_pushed_options_are_default_deny():
    # A deny-list doesn't hold: OpenVPN matches filters against the raw pushed text, so '"redirect-gateway" def1' walks
    # past `ignore redirect-gateway`. Hence: accept the few options a split tunnel needs, drop everything else.
    argv = helper.openvpn_argv("demo", Path("/run/x/demo.conf"), drop_privileges=False)
    last_filter = max(i for i, arg in enumerate(argv) if arg == "--pull-filter")
    assert argv[last_filter : last_filter + 3] == ["--pull-filter", "ignore", ""]
    for dangerous in ("redirect-gateway", "redirect-private", "dhcp-option", "dns", "route-ipv6", "ifconfig-ipv6", "route-metric", "inactive", "block-outside-dns", "setenv"):
        assert not any(dangerous.startswith(prefix) or prefix.startswith(dangerous) for prefix in helper.PULL_ACCEPT), dangerous
    # "route" must end in a blank or it would also accept route-ipv6 / route-metric; same for ping vs ping-exit.
    assert "route " in helper.PULL_ACCEPT and "route" not in helper.PULL_ACCEPT
    assert "ping " in helper.PULL_ACCEPT and "ping" not in helper.PULL_ACCEPT
    # A reply too long for one message arrives in parts; dropping the marker would bring the device up before the part
    # that carries `ifconfig` has arrived — a dead tunnel.
    assert "push-continuation " in helper.PULL_ACCEPT


def test_openvpn_never_installs_routes_itself():
    argv = helper.openvpn_argv("demo", Path("/run/x/demo.conf"), drop_privileges=False)
    assert "--route-noexec" in argv and "--route-metric" not in argv


def test_idle_limit_has_a_byte_floor():
    # Without it the kernel's own IPv6 router solicitations on the device count as traffic and the limit never fires.
    argv = helper.openvpn_argv("demo", Path("/run/x/demo.conf"), drop_privileges=False)
    at = argv.index("--inactive")
    assert argv[at + 1 : at + 3] == [str(helper.INACTIVE_SECS), str(helper.INACTIVE_BYTES)] and helper.INACTIVE_BYTES >= 1024


def test_privilege_drop_only_when_the_account_exists():
    assert "--user" not in helper.openvpn_argv("demo", Path("/run/x/demo.conf"), drop_privileges=False)


def test_transient_unit_is_named_limited_and_cleans_up_its_profile():
    argv = helper.systemd_run_argv("demo", Path("/run/x/demo.conf"), drop_privileges=True)
    assert argv[0] == "systemd-run" and "--collect" in argv and "--unit=openvpn3-on-demand-demo.service" in argv
    props = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-p"]
    assert "Type=notify" in props and "Restart=no" in props and "NoNewPrivileges=true" in props and "TimeoutStartSec=30" in props
    assert f"RuntimeMaxSec={helper.MAX_LIFETIME_SECS}" in props
    # Key material and the "checked" marker go with the tunnel, however it ends.
    assert f"ExecStopPost=/usr/bin/rm -f /run/x/demo.conf {helper.ok_path('demo')}" in props
    assert argv[argv.index(helper.OPENVPN) :] == helper.openvpn_argv("demo", Path("/run/x/demo.conf"), drop_privileges=True)


# routes ----------------------------------------------------------------------

AWS_PUSH = (
    "PUSH: Received control message: 'PUSH_REPLY,dhcp-option DNS 10.0.0.2,dhcp-option DNS 169.254.169.253,route 10.0.0.0 255.255.0.0,"
    "route-gateway 10.1.0.33,topology subnet,ping 1,ping-restart 20,echo,echo,echo,ifconfig 10.1.0.34 255.255.255.224,peer-id 0,cipher AES-256-GCM,"
    "protocol-flags cc-exit tls-ekm dyn-tls-crypt,tun-mtu 1500'"
)


def push(*options: str) -> str:
    return "PUSH: Received control message: 'PUSH_REPLY," + ",".join(options) + "'"


def test_pushed_routes_from_a_real_aws_reply():
    assert helper.pushed_routes(["VERIFY OK: depth=0, CN=server", AWS_PUSH, "Initialization Sequence Completed"]) == (["10.0.0.0/16"], [], True)


def test_pushed_routes_keep_only_private_prefixes_meant_for_the_tunnel():
    wanted, skipped, complete = helper.pushed_routes(
        [
            push(
                "route 10.201.50.0 255.255.255.0",
                "route 8.8.8.0 255.255.255.0",
                "route 0.0.0.0 0.0.0.0",
                "route 172.16.5.9",  # no netmask: a host route
                "route 10.201.50.0 255.255.255.0 vpn_gateway 0",  # duplicate, with a metric that must not matter
                "route 10.60.0.0 255.255.0.0 10.1.0.33 7",  # through the pushed route-gateway: the tunnel
                "route 10.55.0.0 255.255.0.0 net_gateway",  # the server wants this network kept OUT of the tunnel
                "route 169.254.169.254 255.255.255.255 192.168.250.2 1",
                "route remote_host 255.255.255.255 net_gateway",
                "route 10.0.0.5 255.255.0.0",  # host bits set
                "route",
                "route-gateway 10.1.0.33",
                "push-continuation 2",
            ),
            push("route 192.168.7.0 255.255.255.0", "push-continuation 1"),
        ]
    )
    assert complete and wanted == ["10.201.50.0/24", "172.16.5.9/32", "10.60.0.0/16", "192.168.7.0/24"]
    assert skipped == [
        "8.8.8.0/24: outside the private ranges",
        "0.0.0.0/0: outside the private ranges",
        "10.55.0.0/16: the VPN asked for it to go through net_gateway, not through the tunnel",
        "169.254.169.254/32: the VPN asked for it to go through 192.168.250.2, not through the tunnel",
        "remote_host 255.255.255.255: not a network and netmask",
        "10.0.0.5 255.255.0.0: not a network and netmask",
        "(nothing): not a network and netmask",
    ]


def test_pushed_routes_know_when_a_reply_in_parts_is_not_all_there_yet():
    first = push("route 10.201.50.0 255.255.255.0", "push-continuation 2")
    assert helper.pushed_routes([first])[2] is False  # more is coming: installing now would silently drop the rest
    assert helper.pushed_routes([first, push("ifconfig 10.1.0.34 255.255.255.224", "push-continuation 1")])[2] is True
    assert helper.pushed_routes([push("ifconfig 10.1.0.34 255.255.255.224")])[2] is True


def test_pushed_routes_need_the_whole_line_to_be_the_push():
    # Text a server controls (a certificate name, an echo) can't pass for a PUSH_REPLY by containing one.
    assert helper.pushed_routes(["VERIFY OK: depth=0, CN=PUSH: Received control message: 'PUSH_REPLY,route 10.0.0.0 255.0.0.0'"]) == ([], [], False)
    assert helper.pushed_routes([]) == ([], [], False)


class FakeNet:
    """A tiny model of `ip`: what is on the tunnel device, and how each address is reached."""

    def __init__(self, monkeypatch) -> None:
        self.addrs: list[dict] = [{"family": "inet", "local": "10.1.0.34", "prefixlen": 27}, {"family": "inet6", "local": "fe80::1", "prefixlen": 64}]
        self.tun_routes: list[str] = ["10.1.0.32/27"]
        self.tun_routes6: list[str] = ["fe80::/64"]
        self.reach: dict[str, tuple[str, str, str] | None] = {}  # address -> (type, gateway, dev); default: via the gateway on ens5
        self.captures: dict[str, list[str]] = {}  # prefix -> addresses that move onto the tunnel when it is installed
        self.displaced: dict[str, tuple[str, str, str] | None] = {}
        self.add_errors: dict[str, str] = {}
        self.added: list[str] = []
        self.deleted: list[str] = []
        self.default: list[dict] = [{"dst": "default", "gateway": "10.0.0.1", "dev": "ens5"}]
        # The host's own interfaces, as `ip -j -4 addr show` lists them: the box this backend was written for.
        self.links: list[dict] = [
            {"ifname": "lo", "flags": ["LOOPBACK", "UP"], "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
            {"ifname": "ens5", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "10.0.0.66", "prefixlen": 24}]},
        ]
        # The route the kernel makes for the tunnel's own address, and the metric it carries: 0 on a fresh tunnel, until
        # `ip addr change ... metric N` re-keys it. This fixture starts with that already done.
        self.connected: set[str] = {"10.1.0.32/27"}
        self.connected_metric = helper.ROUTE_METRIC
        self.addr_change_rc = 0
        self.addr_changes: list[list[str]] = []
        self.older: dict[str, str] = {}  # prefix -> a device that already had a route for it
        self.device = "ovodx"
        self.on_check = lambda: None  # called when the tunnel's address is looked at, i.e. while the checks run
        self.fallback = helper._run
        monkeypatch.setattr(helper, "_json_cmd", self.json_cmd)
        monkeypatch.setattr(helper, "_run", self.run)

    def json_cmd(self, argv: list[str]) -> Any:
        if argv == ["ip", "-j", "-4", "route", "show", "default"]:
            return self.default
        if argv == ["ip", "-j", "-4", "addr", "show"]:
            return self.links
        if argv[:6] == ["ip", "-j", "-4", "addr", "show", "dev"]:
            self.device = argv[6]
            return [{"ifname": argv[6], "addr_info": [a for a in self.addrs if a["family"] == "inet"]}]
        if argv[:5] == ["ip", "-j", "addr", "show", "dev"]:
            self.on_check()
            self.device = argv[5]
            return [{"ifname": argv[5], "addr_info": self.addrs}]
        if argv[:6] == ["ip", "-j", "-4", "route", "show", "dev"]:
            # Like `ip`: a metric of 0 is simply not printed.
            return [{"dst": dst, **({"metric": metric} if (metric := self.metric(dst)) else {})} for dst in self.tun_routes]
        if argv[:6] == ["ip", "-j", "-4", "route", "show", "exact"]:
            mine = [{"dst": argv[6], "dev": self.device}] if argv[6] in self.tun_routes else []
            return ([{"dst": argv[6], "dev": self.older[argv[6]]}] if argv[6] in self.older else []) + mine
        if argv[:6] == ["ip", "-j", "-6", "route", "show", "dev"]:
            return [{"dst": dst} for dst in self.tun_routes6]
        raise AssertionError(argv)

    def metric(self, dst: str) -> int:
        return self.connected_metric if dst in self.connected else helper.ROUTE_METRIC

    def run(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["ip", "addr", "change"]:
            self.addr_changes.append(argv)
            assert argv[-4:-2] == ["dev", self.device] or argv[-4] == "dev", argv
            if self.addr_change_rc == 0:
                self.connected_metric = int(argv[argv.index("metric") + 1])
            return subprocess.CompletedProcess(argv, self.addr_change_rc, "", "" if self.addr_change_rc == 0 else 'Error: either "local" is duplicate, or "metric" is a garbage.')
        if argv[:4] == ["ip", "-j", "route", "get"]:
            route = self.reach.get(argv[4], ("unicast", "10.0.0.1", "ens5"))
            if route is None:
                return subprocess.CompletedProcess(argv, 2, "", "RTNETLINK answers: Network is unreachable")
            entry = {"dst": argv[4], "dev": route[2], **({"gateway": route[1]} if route[1] else {}), **({"type": "local"} if route[0] == "local" else {})}
            return subprocess.CompletedProcess(argv, 0, json.dumps([entry]), "")
        if argv[:3] == ["ip", "route", "append"]:
            prefix, device = argv[3], argv[5]
            assert argv[4] == "dev" and argv[6:] == ["metric", str(helper.ROUTE_METRIC)], argv  # bound to the device, at our metric
            if prefix in self.add_errors:
                return subprocess.CompletedProcess(argv, 2, "", self.add_errors[prefix])
            self.added.append(prefix)
            self.tun_routes.append(prefix)
            for addr in self.captures.get(prefix, []):
                self.displaced[addr] = self.reach.get(addr, ("unicast", "10.0.0.1", "ens5"))
                self.reach[addr] = ("unicast", "", device)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["ip", "route", "del"]:
            prefix = argv[3]
            self.deleted.append(prefix)
            self.tun_routes.remove(prefix)
            for addr in self.captures.get(prefix, []):
                self.reach[addr] = self.displaced.pop(addr)  # the route that was there before wins again
            return subprocess.CompletedProcess(argv, 0, "", "")
        return self.fallback(argv, **kwargs)


BASELINE: dict[str, tuple[str, str, str] | None] = {
    "10.0.0.1": ("unicast", "", "ens5"),
    "10.0.0.2": ("unicast", "", "ens5"),
    "169.254.169.254": ("unicast", "10.0.0.1", "ens5"),
}


@pytest.fixture
def net(monkeypatch) -> FakeNet:
    fake = FakeNet(monkeypatch)
    fake.reach.update({addr: route for addr, route in BASELINE.items() if route})
    return fake


def test_route_to_reads_type_gateway_and_device(net):
    net.reach["192.0.2.53"] = None
    net.reach["169.254.169.254"] = ("local", "", "lo")
    assert helper.route_to("10.0.0.2") == ("unicast", "", "ens5")
    assert helper.route_to("8.8.8.8") == ("unicast", "10.0.0.1", "ens5")
    assert helper.route_to("169.254.169.254") == ("local", "", "lo")
    assert helper.route_to("192.0.2.53") is None  # no route at all is an answer, not an error


def test_install_routes_binds_each_prefix_to_the_tunnel_device(net):
    # The real case this backend was written for: the VPN pushes 10.0.0.0/16 to a host that itself lives in 10.0.0.0/24.
    # Its gateway and DNS server are inside the prefix but keep their own, more specific routes.
    assert helper.install_routes("ovodx", ["10.0.0.0/16", "172.16.5.0/24"], BASELINE) == []
    assert net.added == ["10.0.0.0/16", "172.16.5.0/24"] and net.deleted == []


def test_install_routes_takes_out_a_prefix_that_captures_a_protected_address(net):
    net.captures["10.0.0.2/32"] = ["10.0.0.2"]
    skipped = helper.install_routes("ovodx", ["10.0.0.2/32", "10.201.0.0/16"], BASELINE)
    assert skipped == ["10.0.0.2/32: it would take over 10.0.0.2 (this host's gateway, DNS server or metadata address)"]
    assert net.deleted == ["10.0.0.2/32"] and net.tun_routes[-1] == "10.201.0.0/16"
    assert helper.route_to("10.0.0.2") == BASELINE["10.0.0.2"]


def test_install_routes_reports_every_route_it_could_not_add(net):
    # A route that isn't there must never go unmentioned: the tunnel would be reported up and the traffic go elsewhere.
    net.add_errors = {"10.9.0.0/16": "RTNETLINK answers: Operation not permitted"}
    assert helper.install_routes("ovodx", ["10.8.0.0/16", "10.9.0.0/16"], BASELINE) == ["10.9.0.0/16: RTNETLINK answers: Operation not permitted"]
    assert net.added == ["10.8.0.0/16"]


def test_install_routes_appends_so_two_tunnels_can_carry_the_same_prefix(monkeypatch):
    # `ip route add` answers "File exists" for a prefix another tunnel already has at this metric; the second tunnel would
    # then have no route of its own once the first one closes.
    seen: list[list[str]] = []
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    assert helper.install_routes("ovodx", ["10.0.0.0/16"], {}) == []
    assert seen == [["ip", "route", "append", "10.0.0.0/16", "dev", "ovodx", "metric", str(helper.ROUTE_METRIC)]]


LAN = [ipaddress.IPv4Network("10.0.0.0/24")]


def test_host_networks_are_the_subnets_of_the_hosts_own_interfaces(net):
    net.links += [
        {"ifname": "docker0", "flags": ["NO-CARRIER", "UP"], "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]},
        {"ifname": "tailscale0", "flags": ["POINTOPOINT", "UP"], "addr_info": [{"family": "inet", "local": "100.101.102.103", "prefixlen": 32}]},
        {"ifname": "ovod0123456789", "flags": ["POINTOPOINT", "UP"], "addr_info": [{"family": "inet", "local": "10.1.0.34", "prefixlen": 27}]},
        {"ifname": "odd", "flags": [], "addr_info": [{"family": "inet", "local": "not-an-address", "prefixlen": 24}, {"family": "inet", "prefixlen": 24}]},
    ]
    # Loopback, another on-demand tunnel's device and a lone /32 are not networks the host has neighbours in.
    assert [str(n) for n in helper.host_networks()] == ["10.0.0.0/24", "172.17.0.0/16"]


@pytest.mark.parametrize("prefix", ["10.0.0.128/25", "10.0.0.0/24", "10.0.0.77/32"])
def test_install_routes_leaves_the_hosts_own_network_alone(net, prefix):
    # A VPN does not get to carve a piece out of the network this host lives in, whatever the metric: a more specific
    # route wins over the host's own, and its neighbours' traffic would go down the tunnel.
    skipped = helper.install_routes("ovodx", [prefix, "10.0.0.0/16"], BASELINE, LAN)
    assert skipped == [f"{prefix}: inside 10.0.0.0/24, a network this host is directly attached to; those addresses stay local"]
    assert net.added == ["10.0.0.0/16"]  # the wider prefix the real VPN pushes is still carried


def test_the_tunnels_own_route_is_moved_below_the_hosts_routes(net):
    # The kernel makes the route for the tunnel's address at metric 0. Where the VPN's pool is the host's own network,
    # that beats the host's route to its neighbours (Amazon Linux: 512) until it is re-keyed.
    net.connected_metric = 0
    problems, _ = helper.tunnel_violations("ovodx", BASELINE)
    assert problems == [f"route 10.1.0.32/27 on the tunnel has metric 0, below {helper.ROUTE_METRIC}, so it could outrank this host's own route to that network"]
    helper.demote_connected_routes("ovodx")
    assert net.addr_changes == [["ip", "addr", "change", "10.1.0.34/27", "dev", "ovodx", "metric", str(helper.ROUTE_METRIC)]]
    assert helper.tunnel_violations("ovodx", BASELINE) == ([], ["10.1.0.32/27"])


def test_a_point_to_point_tunnel_address_is_re_keyed_with_its_peer(net):
    net.addrs = [{"family": "inet", "local": "10.8.0.6", "address": "10.8.0.5", "prefixlen": 32}]  # topology net30 / p2p
    helper.demote_connected_routes("ovodx")
    assert net.addr_changes == [["ip", "addr", "change", "10.8.0.6", "peer", "10.8.0.5/32", "dev", "ovodx", "metric", str(helper.ROUTE_METRIC)]]


def test_a_tunnel_whose_route_could_not_be_re_keyed_is_a_violation(net):
    net.connected_metric, net.addr_change_rc = 0, 2  # an `ip` too old to know `metric` on an address
    helper.demote_connected_routes("ovodx")
    assert any("has metric 0" in problem for problem in helper.tunnel_violations("ovodx", BASELINE)[0])


def test_a_vpn_network_carved_out_of_the_hosts_own_is_a_violation(net):
    net.addrs[0] = {"family": "inet", "local": "10.0.0.130", "prefixlen": 25}
    net.tun_routes, net.connected = ["10.0.0.128/25"], {"10.0.0.128/25"}
    problems, _ = helper.tunnel_violations("ovodx", BASELINE, LAN)
    assert problems == ["the VPN's own network 10.0.0.128/25 lies inside 10.0.0.0/24, a network this host is directly attached to, and would take those addresses over"]
    # The very same network is fine once its route sits below the host's: the host's route wins, neighbours stay local.
    net.addrs[0] = {"family": "inet", "local": "10.0.0.130", "prefixlen": 24}
    net.tun_routes, net.connected = ["10.0.0.0/24"], {"10.0.0.0/24"}
    assert helper.tunnel_violations("ovodx", BASELINE, LAN)[0] == []


def test_shadowed_routes_name_the_device_that_keeps_the_traffic(net):
    net.tun_routes += ["10.0.0.0/16", "10.77.0.0/16"]
    net.older = {"10.77.0.0/16": "ovodother", "10.1.0.32/27": "ens5"}
    assert helper.shadowed_routes("ovodx", ["10.1.0.32/27", "10.0.0.0/16", "10.77.0.0/16"]) == [
        "10.1.0.32/27: ens5 already has a route for it and keeps that traffic until its route is gone",
        "10.77.0.0/16: ovodother already has a route for it and keeps that traffic until its route is gone",
    ]


def test_close_ipv6_switches_the_device_off_and_reads_it_back(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "IPV6_CONF", tmp_path / "absent")
    assert helper.close_ipv6("ovodx") == ""  # a host booted without IPv6: nothing to close
    conf = tmp_path / "conf"
    (conf / "ovodx").mkdir(parents=True)
    knob = conf / "ovodx" / "disable_ipv6"
    knob.write_text("0\n")
    monkeypatch.setattr(helper, "IPV6_CONF", conf)
    assert helper.close_ipv6("ovodx") == "" and knob.read_text() == "1\n"
    assert helper.close_ipv6("ovodx") == ""  # already off (a container's default): nothing is written
    # No switch for the device, or one that can't be written: said, so that the tunnel does not stay up half open.
    assert "could not be switched off on ovody" in helper.close_ipv6("ovody")
    knob.write_text("0\n")
    knob.chmod(0o444)
    if os.geteuid() != 0:
        assert "could not be switched off on ovodx (Permission denied)" in helper.close_ipv6("ovodx")


def test_a_healthy_tunnel_has_no_violations(net):
    net.tun_routes.append("10.0.0.0/16")
    assert helper.tunnel_violations("ovodx", BASELINE) == ([], ["10.1.0.32/27", "10.0.0.0/16"])


@pytest.mark.parametrize("dst", ["default", "0.0.0.0/1", "128.0.0.0/1", "8.8.8.0/24", "169.254.0.0/16", "10.0.0.0/7", "172.0.0.0/8"])
def test_routes_outside_the_private_ranges_are_violations(net, dst):
    net.tun_routes = [dst]
    problems, _ = helper.tunnel_violations("ovodx", BASELINE)
    assert len(problems) == 1 and "not inside a private range" in problems[0]


@pytest.mark.parametrize(
    ("address", "phrase"),
    [
        ("8.8.8.8", "which is not a private address"),
        ("169.254.169.254", "which is not a private address"),
        ("10.0.0.2", "which is this host's gateway, DNS server or metadata address"),
    ],
)
def test_the_address_the_vpn_hands_out_is_checked(net, address, phrase):
    # ifconfig is the one pushed option that cannot be filtered: a tunnel given the DNS server's address would make
    # every DNS query land on this host itself.
    net.addrs[0] = {"family": "inet", "local": address, "prefixlen": 32}
    problems, _ = helper.tunnel_violations("ovodx", BASELINE)
    assert any(phrase in problem for problem in problems)


def test_a_tunnel_device_without_an_address_is_a_violation(net):
    # OpenVPN said "Initialization Sequence Completed", but the device carries nothing (a reply that lost its ifconfig).
    net.addrs = [{"family": "inet6", "local": "fe80::1", "prefixlen": 64}]
    net.tun_routes = []
    problems, routes = helper.tunnel_violations("ovodx", BASELINE)
    assert problems == ["the tunnel device ovodx has no IPv4 address (the VPN's reply did not carry a usable one)"] and routes == []


def test_protected_address_reached_differently_is_a_violation(net):
    net.reach["169.254.169.254"] = ("local", "", "lo")
    net.reach["10.0.0.2"] = ("unicast", "", "ovodx")
    problems, _ = helper.tunnel_violations("ovodx", BASELINE)
    assert problems == [
        "10.0.0.2 (this host's gateway, DNS server or metadata address) is reached differently with the tunnel up: directly on ens5 before, directly on ovodx now",
        "169.254.169.254 (this host's gateway, DNS server or metadata address) is reached differently with the tunnel up: via 10.0.0.1 on ens5 before, "
        "delivered to this host itself now",
    ]


def test_protected_address_with_no_route_before_or_after_is_fine(net):
    # A nameserver in resolv.conf the host never could reach: nothing was taken over, and the check must not blow up.
    net.reach["192.0.2.53"] = None
    assert helper.tunnel_violations("ovodx", {**BASELINE, "192.0.2.53": None})[0] == []


def test_ipv6_on_the_tunnel_other_than_link_local_is_a_violation(net):
    net.addrs.append({"family": "inet6", "local": "2001:db8::5", "prefixlen": 64})
    net.tun_routes6 = ["fe80::/64", "ff00::/8", "default", "2000::/3"]
    problems, _ = helper.tunnel_violations("ovodx", BASELINE)
    assert [p.split(" (")[0] for p in problems] == ["IPv6 address 2001:db8::5 on the tunnel", "IPv6 route ::/0", "IPv6 route 2000::/3"]


def test_protected_addresses_are_gateways_nameservers_and_metadata(net, monkeypatch, tmp_path):
    etc, run = tmp_path / "resolv.conf", tmp_path / "uplink.conf"
    etc.write_text("# stub\nnameserver 127.0.0.53\nnameserver 10.0.0.2 # vpc\noptions edns0\n")
    run.write_text("nameserver 10.0.0.2\nnameserver 192.168.1.1\nnameserver fd00::53\n")
    monkeypatch.setattr(helper, "RESOLV_CONFS", (etc, run, tmp_path / "missing"))
    net.default = [{"dst": "default", "gateway": "10.0.0.1", "dev": "ens5"}, {"dst": "default", "dev": "ppp0"}]
    # Loopback (a local stub) and IPv6 resolvers are left out; duplicates collapse.
    assert helper.protected_addresses() == ["169.254.169.254", "10.0.0.1", "10.0.0.2", "192.168.1.1"]


# diagnosis -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("log", "phrase"),
    [
        (["RESOLVE: Cannot resolve host address: abc.cvpn-endpoint-1.prod.clientvpn.us-east-1.amazonaws.com:443"], "didn't resolve"),
        (["VERIFY ERROR: depth=1, error=self-signed certificate in certificate chain", "TLS Error: TLS handshake failed"], "certificate was not accepted"),
        (["VERIFY OK: depth=0, CN=server", "TLS Error: TLS key negotiation failed to occur within 30 seconds"], "client certificate was rejected or expired"),
        (["TLS Error: TLS key negotiation failed to occur within 30 seconds"], "stopped answering"),
        (["Server poll timeout, restarting"], "didn't answer"),
        (["read UDPv4 [ECONNREFUSED]: Connection refused (fd=3,code=111)"], "didn't answer"),
        (["AUTH: Received control message: AUTH_FAILED"], "refused the credentials"),
        (["Options error: Unrecognized option or missing or extra parameter(s)"], "rejected an option"),
        (["ERROR: Cannot ioctl TUNSETIFF tun: Operation not permitted (errno=1)"], "tunnel device"),
        (["something else entirely"], ""),
    ],
)
def test_diagnose(log, phrase):
    hint = helper.diagnose(log)
    assert phrase in hint if phrase else hint == ""


# up / down -------------------------------------------------------------------


class FakeHost:
    """Stands in for systemctl / systemd-run / journalctl / openvpn, and records what was run; `net` models `ip`."""

    def __init__(self, monkeypatch, tmp_path: Path) -> None:
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.states: list[dict[str, str]] = []  # one per `systemctl show`; the last repeats
        self.systemd_run_rc = 0
        self.systemd_run_stderr = ""
        self.rollback_rc = 0
        self.rollback_rcs: list[int] = []  # one per arming attempt, before rollback_rc applies
        self.timer_armed = False
        self.stop_rc = 0
        self.marker_seen_at_disarm: bool | None = None
        self.stop_clears = True
        self.timer_stays_armed = False
        self.version = "OpenVPN 2.6.12 aarch64-amazon-linux-gnu [SSL (OpenSSL)] [LZO] [LZ4] [EPOLL] [MH/PKTINFO] [AEAD]"
        self.journal = f"VERIFY OK: depth=0, CN=server\n{AWS_PUSH}\n{helper.CONNECTED_STATUS}\n"
        self.old_journal = push("route 172.31.0.0 255.255.0.0", "ifconfig 10.9.9.9 255.255.255.0") + "\nVERIFY ERROR: an earlier run's failure\n"
        self.on_start = lambda: None  # what the tunnel coming up does to the host (tests change routes here)
        self.run_dir = tmp_path / "run"
        self.byo_dir = tmp_path / "etc"
        self.byo_dir.mkdir()
        self.clock = 0.0
        monkeypatch.setattr(helper, "RUN_DIR", self.run_dir)
        monkeypatch.setattr(helper, "BYO_DIR", self.byo_dir)
        monkeypatch.setattr(helper, "RESOLV_CONFS", ())
        # A tun device appears with IPv6 on, like on the real host; its switch shows up when the unit starts.
        self.ipv6_conf = tmp_path / "ipv6conf"
        self.ipv6_conf.mkdir()
        monkeypatch.setattr(helper, "IPV6_CONF", self.ipv6_conf)
        monkeypatch.setattr(helper, "PROC_STATUS", tmp_path / "proc-status")  # absent: no sandbox flag to read
        monkeypatch.setattr(helper, "_pending_signal", 0)
        monkeypatch.setattr(helper, "_interrupted", False)
        monkeypatch.setattr(helper, "_is_root", lambda: True)
        monkeypatch.setattr(helper, "_user_exists", lambda user: True)
        monkeypatch.setattr(helper, "_run", self._run)
        monkeypatch.setattr(helper, "_sleep", self._sleep)
        monkeypatch.setattr(helper, "_now", lambda: self.clock)
        monkeypatch.setattr(helper.shutil, "which", lambda name: None if name == "restorecon" else f"/usr/bin/{name}")
        self.net = FakeNet(monkeypatch)  # takes the `ip` calls and hands everything else to _run above
        self.net.reach["10.0.0.1"] = ("unicast", "", "ens5")
        self.net.connected_metric = 0  # a fresh tunnel: the kernel's route for its address has not been re-keyed yet
        self.net.on_check = self._note_commit_state
        self.committed_before_checks: list[str] = []  # what a commit-before-check helper would have done by then
        self.show_fails = False

    def _note_commit_state(self) -> None:
        if ["systemctl", "stop", "openvpn3-od-rollback-demo.timer"] in self.calls:
            self.committed_before_checks.append("rollback timer cancelled")
        if (self.run_dir / "demo.ok").exists():
            self.committed_before_checks.append("marker written")

    def _sleep(self, seconds: float) -> None:
        self.clock += seconds

    def _run(self, argv: list[str], *, input_text: str | None = None, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        self.inputs.append(input_text)
        out, err, rc = "", "", 0
        if argv[:2] == [helper.OPENVPN, "--version"]:
            out = self.version + "\nlibrary versions: OpenSSL 3\n"
        elif argv[:2] == ["systemctl", "show"] and self.show_fails:
            rc, err = 1, "Failed to connect to bus: No such file or directory"
        elif argv[:2] == ["systemctl", "show"]:
            props = self.states.pop(0) if len(self.states) > 1 else (self.states[0] if self.states else {"LoadState": "not-found", "ActiveState": "inactive"})
            asked = argv[argv.index("-p") + 1].split(",")  # like systemctl: only the properties that were asked for
            out = "".join(f"{k}={v}\n" for k, v in props.items() if k in asked)
        elif argv[0] == "systemd-run" and any(arg.startswith("--on-active=") for arg in argv):
            rc = self.rollback_rcs.pop(0) if self.rollback_rcs else self.rollback_rc
            err = "Failed to start transient timer unit: Unit openvpn3-od-rollback-demo.timer was already loaded or has a fragment file." if rc else ""
            self.timer_armed = self.timer_armed or rc == 0
        elif argv[0] == "systemd-run":
            rc, err = self.systemd_run_rc, self.systemd_run_stderr
            if rc == 0:
                knob = self.ipv6_conf / argv[argv.index("--dev") + 1] / "disable_ipv6"
                knob.parent.mkdir(exist_ok=True)
                knob.write_text("0\n")
                self.on_start()
        elif argv[:2] == ["systemctl", "stop"]:
            rc = self.stop_rc
            if argv[2].startswith(helper.ROLLBACK_PREFIX) and argv[2].endswith(".timer"):
                if self.marker_seen_at_disarm is None:
                    self.marker_seen_at_disarm = (self.run_dir / "demo.ok").exists()
                self.timer_armed = self.timer_stays_armed
            if rc == 0 and argv[2].startswith(helper.UNIT_PREFIX) and self.stop_clears:
                self.states = [ABSENT]  # a stopped transient unit is gone
        elif argv[:2] == ["systemctl", "is-active"]:
            rc = 0 if self.timer_armed else 3
        elif argv[0] == "journalctl":
            assert argv[argv.index("-o") + 1] == "cat", argv  # bare messages: the PUSH_REPLY line is matched as a whole line
            # Only this run of the unit: asked by unit name alone, the journal also holds what earlier runs under the name
            # logged; bounded by --since (no InvocationID known: the unit never got going) it holds nothing of theirs.
            out = self.journal if f"_SYSTEMD_INVOCATION_ID={INVOCATION}" in argv else ("" if "--since" in argv else self.old_journal)
        return subprocess.CompletedProcess(argv, rc, out, err)

    def ran(self, *prefix: str) -> list[list[str]]:
        return [call for call in self.calls if call[: len(prefix)] == list(prefix)]

    def stopped(self, name: str = "demo") -> bool:
        return bool(self.ran("systemctl", "stop", f"openvpn3-on-demand-{name}.service"))

    def left_behind(self) -> list[str]:
        return sorted(p.name for p in self.run_dir.iterdir() if p.name != ".lock") if self.run_dir.exists() else []

    def ipv6_switch(self, name: str = "demo") -> str:
        return (self.ipv6_conf / helper.device_name(name) / "disable_ipv6").read_text().strip()

    def unit_starts(self) -> list[list[str]]:
        return [call for call in self.ran("systemd-run") if not any(arg.startswith("--on-active=") for arg in call)]


INVOCATION = "5c0ffee0d15ea5e4b1d0c0de5a11ad00"
ABSENT = {"LoadState": "not-found", "ActiveState": "inactive"}
CONNECTING = {"LoadState": "loaded", "ActiveState": "active", "StatusText": "Pre-connection initialization successful", "InvocationID": INVOCATION}
CONNECTED = {"LoadState": "loaded", "ActiveState": "active", "StatusText": helper.CONNECTED_STATUS, "InvocationID": INVOCATION}


@pytest.fixture
def host(monkeypatch, tmp_path) -> FakeHost:
    return FakeHost(monkeypatch, tmp_path)


def test_up_starts_a_unit_waits_for_openvpn_adds_the_pushed_route_and_marks_the_tunnel_checked(host):
    host.states = [ABSENT, CONNECTING, CONNECTING, CONNECTED]
    result = helper.cmd_up("demo", byo=False, profile_text=AWS_PROFILE)
    assert result["status"] == "connected"
    assert result["unit"] == "openvpn3-on-demand-demo.service"
    # OpenVPN installed nothing; the helper added the one private prefix the server pushed, on the tunnel device.
    assert host.net.added == ["10.0.0.0/16"]
    assert result["routes"] == ["10.1.0.32/27", "10.0.0.0/16"] and result["skipped_routes"] == [] and result["shadowed_routes"] == []
    assert result["protected"] == ["169.254.169.254", "10.0.0.1"]
    # The two things the server still had a say in are closed before the checks: the route of the tunnel's own address
    # is re-keyed below the host's routes, and the device no longer listens for IPv6 router advertisements.
    device = helper.device_name("demo")
    assert host.net.addr_changes == [["ip", "addr", "change", "10.1.0.34/27", "dev", device, "metric", str(helper.ROUTE_METRIC)]]
    assert host.ipv6_switch() == "1"
    assert result["dropped_directives"] == ["dev", "resolv-retry", "verb", "dhcp-option"]
    armed, started = host.ran("systemd-run")
    assert "--unit=openvpn3-on-demand-demo.service" in started
    # Commit-confirm: the rollback timer exists before OpenVPN does, and is cancelled only after the checks.
    assert "--unit=openvpn3-od-rollback-demo.service" in armed and f"--on-active={helper.ROLLBACK_SECS}" in armed
    assert armed[-3:] == ["/usr/bin/systemctl", "stop", "openvpn3-on-demand-demo.service"]
    assert host.calls.index(armed) < host.calls.index(started) < host.calls.index(["systemctl", "stop", "openvpn3-od-rollback-demo.timer"])
    assert host.marker_seen_at_disarm is True  # the "checked" marker is written before the timer is cancelled, not after
    # ...and neither happens before the checks have run: a helper killed mid-check must leave a tunnel that is unmarked
    # and still has its timer.
    assert host.committed_before_checks == []
    assert helper.ROLLBACK_SECS > helper.CONNECT_TIMEOUT_SECS
    profile = host.run_dir / "demo.conf"
    # The vetted rewrite is what OpenVPN gets — never the caller's text — and only root can read it.
    assert profile.read_text() == sanitize_profile(AWS_PROFILE)[0]
    assert profile.stat().st_mode & 0o777 == 0o600
    marker = host.run_dir / "demo.ok"
    assert json.loads(marker.read_text()) == {"routes": ["10.1.0.32/27", "10.0.0.0/16"], "skipped_routes": [], "shadowed_routes": []}
    assert marker.stat().st_mode & 0o777 == 0o644  # status reads it without root
    assert host.run_dir.stat().st_mode & 0o777 == 0o755  # ...and only root writes where the vetted profile and the marker live
    assert not host.stopped()


def test_up_does_not_start_a_tunnel_it_could_not_arm_a_rollback_for(host):
    host.rollback_rc = 1
    with pytest.raises(Refused, match="rollback timer"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert host.unit_starts() == [] and host.left_behind() == []


def test_up_clears_a_rollback_timer_left_by_a_connect_that_was_killed(host):
    # A helper killed outright between arming the timer and starting the unit leaves the timer; systemd then refuses a
    # second unit of that name. The lock is held and there is no tunnel, so nothing depends on it: clear it, arm again.
    host.states = [ABSENT, CONNECTED]
    host.rollback_rcs = [1]
    assert helper.cmd_up("demo", byo=False, profile_text=MINIMAL)["status"] == "connected"
    stop_timer = ["systemctl", "stop", "openvpn3-od-rollback-demo.timer"]
    steps = ["arm" if "--on-active=120" in c else "start" if c[0] == "systemd-run" else "clear" for c in host.calls if c[0] == "systemd-run" or c == stop_timer]
    assert steps == ["arm", "clear", "arm", "start", "clear"]  # the last one is the normal cancel after the checks


def test_rollback_units_are_not_listed_as_tunnels():
    assert not helper.rollback_unit("demo").startswith(helper.UNIT_PREFIX)


@pytest.mark.parametrize("name", ["x.timer", "corp.service", "a.socket"])
def test_rollback_unit_names_spell_out_their_suffix(name):
    # systemd would read a profile name's own last label as the unit type: `--unit=…-x.timer` makes a timer named exactly
    # that, and stopping `…-x.timer.timer` afterwards cancels nothing — the tunnel is stopped under the caller two minutes on.
    assert helper.rollback_unit(name) == f"openvpn3-od-rollback-{name}.service"
    assert helper.rollback_unit(name, "timer") == f"openvpn3-od-rollback-{name}.timer"
    assert helper.unit_name(name) == f"openvpn3-on-demand-{name}.service"


def test_up_takes_the_tunnel_down_when_the_rollback_timer_cannot_be_cancelled(host):
    host.states = [ABSENT, CONNECTED]
    host.timer_stays_armed = True
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "rollback timer could not be cancelled" in result["message"]
    assert host.stopped() and host.left_behind() == []


def test_failed_cleanup_leaves_the_rollback_timer_armed(host):
    # The unit won't confirm it is gone: the timer is the last thing standing between the host and an unchecked tunnel.
    host.states = [ABSENT, CONNECTING]
    host.stop_clears = False
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "could not be confirmed stopped; a timer stops it within 2 minutes" in result["message"]
    assert ["systemctl", "stop", "openvpn3-od-rollback-demo.timer"] not in host.calls and host.timer_armed


STOPPING = {"LoadState": "loaded", "ActiveState": "deactivating", "InvocationID": "5c0ffee0d15ea5e4b1d0c0de5a11ad00"}


def test_a_timer_is_promised_only_when_there_is_one(host):
    # The checks passed and the timer was cancelled; then the tunnel is found going away and won't confirm it is gone.
    # Saying "a timer stops it" would be untrue by now, so a new one is set first.
    host.states = [ABSENT, CONNECTED, STOPPING]
    host.stop_clears = False
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "went away while it was being checked" in result["message"]
    assert "a timer stops it within 2 minutes" in result["message"] and host.timer_armed
    assert len(host.ran("systemd-run")) == 3  # the first timer, the unit, the timer set again


def test_without_a_timer_the_user_is_told_to_stop_the_unit(host):
    host.states = [ABSENT, CONNECTED, STOPPING]
    host.stop_clears = False
    host.rollback_rcs = [0, 1, 1]  # arming works for the connect and fails afterwards (also on the one retry)
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert "no timer could be set to stop it: run `sudo systemctl stop openvpn3-on-demand-demo.service`" in result["message"]
    assert "a timer stops it" not in result["message"] and not host.timer_armed


def test_up_removes_the_profile_when_it_stops_before_anything_was_started(host, monkeypatch):
    def boom(name):
        raise OSError(2, "No such file or directory: 'systemd-run'")

    monkeypatch.setattr(helper, "_arm_rollback", boom)
    with pytest.raises(OSError):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert host.left_behind() == []  # no unit will ever run the clean-up for it, so the key material goes at once


def test_one_up_or_down_at_a_time(host, monkeypatch):
    import fcntl

    host.run_dir.mkdir()
    holder = (host.run_dir / ".lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX)  # another helper is mid-connect
    try:
        with pytest.raises(Refused, match="another connect or disconnect"):
            helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
        assert not host.ran("systemd-run") and host.left_behind() == []
        with pytest.raises(Refused, match="another connect or disconnect"):
            helper.cmd_down("demo")
    finally:
        holder.close()
    host.states = [ABSENT, CONNECTED]
    assert helper.cmd_up("demo", byo=False, profile_text=MINIMAL)["status"] == "connected"


def test_up_needs_root(host, monkeypatch):
    monkeypatch.setattr(helper, "_is_root", lambda: False)
    with pytest.raises(Refused, match="root"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert not host.ran("systemd-run")


def test_up_refuses_a_bad_profile_before_touching_anything(host):
    with pytest.raises(Refused, match="'up'"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL + "up /tmp/x.sh\n")
    assert not host.ran("systemd-run") and not host.run_dir.exists()


@pytest.mark.parametrize("version", ["OpenVPN 2.5.9 x86_64-pc-linux-gnu", "OpenVPN 2.7.0 x86_64-pc-linux-gnu", "OpenVPN 3.0 something"])
def test_up_refuses_openvpn_versions_it_was_not_written_for(host, version):
    host.version = version
    with pytest.raises(Refused, match="not a version this backend supports"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert not host.ran("systemd-run")


def test_up_leaves_an_existing_unit_alone(host):
    host.states = [CONNECTED]
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    # Not an error and not a refusal: somebody's tunnel holds the name, and the caller decides what that means.
    # (no "checked" marker in this fixture, so what status would call it is "connecting")
    assert result["status"] == "exists" and result["state"] == "connecting" and "sudo systemctl stop openvpn3-on-demand-demo.service" in result["message"]
    assert not host.ran("systemd-run") and not host.ran("systemctl", "stop")  # not even the rollback timer is touched
    assert host.left_behind() == []


def test_up_reports_a_unit_that_does_not_start(host):
    host.systemd_run_rc, host.systemd_run_stderr = 1, "Failed to start transient service unit: Access denied"
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "Access denied" in result["message"]
    # No run of this unit to read a log from - and what an earlier tunnel under the same name logged is not quoted.
    assert "earlier run" not in result["message"] and all("--since" in call for call in host.ran("journalctl"))
    assert host.left_behind() == []


def test_up_tears_down_and_explains_when_openvpn_gives_up(host):
    host.states = [ABSENT, CONNECTING, ABSENT]  # the invocation seen while it was connecting is the one whose log is read
    host.journal = "Started openvpn3-on-demand-demo.service\nVERIFY ERROR: depth=1, error=self-signed certificate\nTLS Error: TLS handshake failed\n"
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error"
    assert "exited before the tunnel came up" in result["message"]
    assert "certificate was not accepted" in result["message"]
    assert "VERIFY ERROR" in result["message"] and "Started openvpn3-on-demand" not in result["message"]
    assert host.stopped() and host.left_behind() == [] and host.net.added == []


def test_up_times_out_and_stops_what_it_started(host):
    host.states = [ABSENT, CONNECTING]
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and f"within {helper.CONNECT_TIMEOUT_SECS:g}s" in result["message"]
    assert host.stopped() and host.left_behind() == []
    assert ["systemctl", "stop", "openvpn3-od-rollback-demo.timer"] in host.calls  # gone for sure, so the timer goes too


def test_up_reads_only_this_runs_log_lines(host):
    # A BYO profile, or an ephemeral one reconnecting within a task, reuses its unit name. What the VPN pushed last time
    # (host.old_journal: 172.31.0.0/16) is not what it pushed now.
    host.states = [ABSENT, CONNECTED]
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "connected" and host.net.added == ["10.0.0.0/16"]
    assert all(f"_SYSTEMD_INVOCATION_ID={INVOCATION}" in call for call in host.ran("journalctl"))
    # ...and all of them: a reply with hundreds of filtered options logs a line for each, and a window of the last few
    # hundred lines would have lost the reply's first parts, routes included, while its last part still said "complete".
    assert host.ran("journalctl") and all("-n" not in call for call in host.ran("journalctl"))


def test_journal_lines_are_capped_only_when_asked(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, "a\n\nb\n", ""))
    assert helper.journal_lines("u.service") == ["a", "b"] and helper.journal_lines("u.service", 0, invocation="abc") == ["a", "b"]
    assert seen == [
        ["journalctl", "-u", "u.service", "-n", "40", "-o", "cat", "--no-pager", "-q"],
        ["journalctl", "_SYSTEMD_INVOCATION_ID=abc", "-o", "cat", "--no-pager", "-q"],
    ]


def test_up_does_not_guess_when_systemd_names_no_invocation(host):
    host.states = [ABSENT, {k: v for k, v in CONNECTED.items() if k != "InvocationID"}]
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "InvocationID" in result["message"]
    assert host.net.added == [] and host.stopped() and host.left_behind() == []


def test_up_cleans_up_when_the_start_itself_hangs(host, monkeypatch):
    # A unit stuck in "activating" (OpenVPN waiting for something) makes systemd-run block until our own timeout.
    real = host._run

    def hanging(argv, **kwargs):
        if argv[0] == "systemd-run" and not any(arg.startswith("--on-active=") for arg in argv):
            host.calls.append(argv)
            raise subprocess.TimeoutExpired(argv, 60)
        return real(argv, **kwargs)

    monkeypatch.setattr(host.net, "fallback", hanging)
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "could not be checked" in result["message"]
    assert host.stopped() and host.left_behind() == []


def test_up_fails_closed_when_only_part_of_the_push_is_in_the_journal(host):
    host.states = [ABSENT, CONNECTED]
    host.journal = push("route 10.201.0.0 255.255.0.0", "push-continuation 2") + "\n"
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "could not be read in full" in result["message"]
    assert host.net.added == [] and host.stopped()


def test_up_fails_closed_when_the_journal_does_not_show_what_was_pushed(host):
    # No PUSH_REPLY to read means no routes could be vetted: nothing is installed and the tunnel does not stay.
    host.states = [ABSENT, CONNECTED]
    host.journal = f"{helper.CONNECTED_STATUS}\n"
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "could not be read in full from the journal" in result["message"]
    assert host.net.added == [] and host.stopped() and host.left_behind() == []


def test_up_leaves_out_pushed_routes_that_are_not_private_and_says_so(host):
    host.states = [ABSENT, CONNECTED]
    host.journal = push("route 10.201.0.0 255.255.0.0", "route 8.8.8.0 255.255.255.0", "route 0.0.0.0 0.0.0.0 10.0.0.9 1", "ifconfig 10.1.0.34 255.255.255.224") + "\n"
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "connected"
    assert host.net.added == ["10.201.0.0/16"]
    assert result["skipped_routes"] == ["8.8.8.0/24: outside the private ranges", "0.0.0.0/0: the VPN asked for it to go through 10.0.0.9, not through the tunnel"]
    assert not host.stopped()


def test_up_leaves_out_a_pushed_route_that_would_take_over_a_dns_server(host, monkeypatch, tmp_path):
    # A resolver outside the host's own network is reached through the gateway; a pushed route for it would move every
    # DNS query into the tunnel.
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 10.50.0.2\n")
    monkeypatch.setattr(helper, "RESOLV_CONFS", (resolv,))
    host.states = [ABSENT, CONNECTED]
    host.journal = push("route 10.50.0.2 255.255.255.255", "route 10.0.0.0 255.255.0.0") + "\n"
    host.net.captures["10.50.0.2/32"] = ["10.50.0.2"]
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "connected" and host.net.deleted == ["10.50.0.2/32"]
    assert result["routes"] == ["10.1.0.32/27", "10.0.0.0/16"]
    assert result["skipped_routes"] == ["10.50.0.2/32: it would take over 10.50.0.2 (this host's gateway, DNS server or metadata address)"]


def test_up_leaves_out_pushed_routes_inside_the_hosts_own_network(host):
    # The host lives in 10.0.0.0/24 (FakeNet.links). The /16 around it is what the VPN is for; pieces of the /24 are not.
    host.states = [ABSENT, CONNECTED]
    host.journal = push("route 10.0.0.1 255.255.255.255", "route 10.0.0.128 255.255.255.128", "route 10.0.0.0 255.255.0.0") + "\n"
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "connected" and host.net.added == ["10.0.0.0/16"] and host.net.deleted == []
    assert result["skipped_routes"] == [
        "10.0.0.1/32: inside 10.0.0.0/24, a network this host is directly attached to; those addresses stay local",
        "10.0.0.128/25: inside 10.0.0.0/24, a network this host is directly attached to; those addresses stay local",
    ]


def test_up_takes_the_tunnel_down_when_the_vpn_pool_is_the_hosts_network_and_its_route_cannot_be_moved(host):
    # ifconfig 10.0.0.130/24 on a host living in 10.0.0.0/24: at the kernel's metric 0 the tunnel would take the host's
    # neighbours. Normally the route is re-keyed and the tunnel is fine; where that fails, it does not stay.
    host.states = [ABSENT, CONNECTED]
    host.net.addrs[0] = {"family": "inet", "local": "10.0.0.130", "prefixlen": 24}
    host.net.tun_routes, host.net.connected = ["10.0.0.0/24"], {"10.0.0.0/24"}
    host.net.older = {"10.0.0.0/24": "ens5"}
    host.journal = push("ifconfig 10.0.0.130 255.255.255.0") + "\n"
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "connected" and result["routes"] == ["10.0.0.0/24"]
    assert result["shadowed_routes"] == ["10.0.0.0/24: ens5 already has a route for it and keeps that traffic until its route is gone"]
    assert helper.cmd_down("demo")["status"] == "disconnected"
    host.states = [ABSENT, CONNECTED]
    host.net.connected_metric, host.net.addr_change_rc = 0, 2
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "has metric 0, below 5000" in result["message"] and host.left_behind() == []


def test_up_takes_the_tunnel_down_when_ipv6_cannot_be_switched_off(host, monkeypatch):
    host.states = [ABSENT, CONNECTED]
    monkeypatch.setattr(helper, "close_ipv6", lambda device: f"IPv6 could not be switched off on {device} (Read-only file system)")
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "Read-only file system" in result["message"] and "router advertisements" in result["message"]
    assert host.stopped() and host.left_behind() == [] and host.net.added == []


def test_up_switches_ipv6_off_while_openvpn_is_still_connecting(host, monkeypatch):
    # The device is there before OpenVPN reports the tunnel up, and takes router advertisements from that moment.
    seen: list[str] = []
    host.states = [ABSENT, CONNECTING, CONNECTING, CONNECTED]
    real_sleep = host._sleep

    def sleep(seconds):
        seen.append(host.ipv6_switch())
        real_sleep(seconds)

    monkeypatch.setattr(helper, "_sleep", sleep)
    assert helper.cmd_up("demo", byo=False, profile_text=MINIMAL)["status"] == "connected"
    assert seen and set(seen) == {"1"}  # by the first pause after the unit started, it was already off


def test_up_takes_the_tunnel_down_again_when_the_vpn_hands_out_a_public_address(host):
    host.states = [ABSENT, CONNECTED]
    host.net.addrs[0] = {"family": "inet", "local": "8.8.8.8", "prefixlen": 1}
    host.net.tun_routes = ["0.0.0.0/1"]
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error"
    assert "taken down again" in result["message"] and "8.8.8.8, which is not a private address" in result["message"]
    assert "0.0.0.0/1 is not inside a private range" in result["message"]
    assert "Last log lines" not in result["message"]  # the log has nothing to say about a route check
    assert host.stopped() and host.left_behind() == []


def test_up_takes_the_tunnel_down_again_when_it_changed_how_the_metadata_address_is_reached(host):
    host.states = [ABSENT, CONNECTED]
    host.on_start = lambda: host.net.reach.update({"169.254.169.254": ("local", "", "lo")})  # what a pushed ifconfig of that address does
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "169.254.169.254" in result["message"] and "delivered to this host itself now" in result["message"]
    assert host.stopped() and host.left_behind() == []
    assert host.committed_before_checks == [] and not (host.run_dir / "demo.ok").exists()


def test_up_takes_the_tunnel_down_when_the_checks_themselves_fail(host, monkeypatch):
    host.states = [ABSENT, CONNECTED]
    real = helper._json_cmd

    def broken(argv):
        if argv[:4] == ["ip", "-j", "addr", "show"]:
            raise Refused("ip -j addr show failed: Cannot open netlink socket")
        return real(argv)

    monkeypatch.setattr(helper, "_json_cmd", broken)
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    # An unchecked tunnel must not stay up waiting for the rollback timer.
    assert result["status"] == "error" and "could not be checked" in result["message"] and "netlink" in result["message"]
    assert host.stopped() and host.left_behind() == []


def signal_during(host, monkeypatch, when, signum: int = 15, times: int = 1):
    """Deliver `signum` (through the helper's own handler, as the kernel would) the first time a command matching `when` runs."""
    real = host.net.fallback
    fired: list[list[str]] = []

    def run(argv, **kwargs):
        if when(argv) and not fired:
            fired.append(argv)
            for _ in range(times):
                helper._interrupt(signum, None)
        return real(argv, **kwargs)

    monkeypatch.setattr(host.net, "fallback", run)
    return fired


STOP_UNIT = ["systemctl", "stop", "openvpn3-on-demand-demo.service"]
STOP_TIMER = ["systemctl", "stop", "openvpn3-od-rollback-demo.timer"]


def is_unit_start(argv: list[str]) -> bool:
    return argv[0] == "systemd-run" and not any(arg.startswith("--on-active=") for arg in argv)


def is_timer_start(argv: list[str]) -> bool:
    return argv[0] == "systemd-run" and any(arg.startswith("--on-active=") for arg in argv)


def test_the_signal_handler_only_takes_a_note(host):
    # An exception thrown from the handler lands wherever the process happens to be: inside the clean-up for an earlier
    # signal (which it then aborts), or inside subprocess with a lock held. So it never raises; `up` asks between steps.
    assert helper._interrupt(15, None) is None and helper._interrupt(1, None) is None
    with pytest.raises(helper.Interrupted, match="signal 15"):  # the first one is the one reported
        helper._checkpoint()
    helper._interrupt(2, None)
    helper._checkpoint()  # acted on once; later signals change nothing


def test_up_cleans_up_when_it_is_interrupted_mid_connect(host, monkeypatch):
    host.states = [ABSENT, CONNECTING]
    signal_during(host, monkeypatch, lambda argv: argv[:2] == ["systemctl", "show"] and host.unit_starts())
    with pytest.raises(helper.Interrupted, match="taken down again") as excinfo:
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert "signal 15" in str(excinfo.value)  # says what happened to the tunnel, not just that a signal came
    assert host.stopped() and host.left_behind() == []
    assert STOP_TIMER in host.calls and not host.timer_armed


@pytest.mark.parametrize("second", [STOP_UNIT, ["systemctl", "reset-failed", "openvpn3-on-demand-demo.service"], STOP_TIMER])
def test_the_clean_up_itself_cannot_be_interrupted(host, monkeypatch, second):
    # A caller killed together with its sudo delivers the signal twice, and systemd follows SIGTERM with SIGHUP: whenever
    # the second one lands — before the clean-up has begun or in the middle of it — the teardown runs to its end.
    host.states = [ABSENT, CONNECTING]
    real = host.net.fallback

    def run(argv, **kwargs):
        if argv[:2] == ["systemctl", "show"] and host.unit_starts() and not helper._pending_signal:
            helper._interrupt(15, None)
            helper._interrupt(1, None)  # right behind the first, before any clean-up has started
        if argv == second:
            helper._interrupt(1, None)
        return real(argv, **kwargs)

    monkeypatch.setattr(host.net, "fallback", run)
    with pytest.raises(helper.Interrupted, match="signal 15.*taken down again"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert host.stopped() and host.left_behind() == [] and STOP_TIMER in host.calls and not host.timer_armed


def test_a_signal_while_the_timer_is_being_armed_leaves_no_timer_behind(host, monkeypatch):
    # Left armed with nothing started, the timer would make systemd refuse the next connect under this name for two minutes.
    signal_during(host, monkeypatch, is_timer_start)
    with pytest.raises(helper.Interrupted, match="signal 15; no tunnel was started"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert host.unit_starts() == [] and host.left_behind() == [] and STOP_TIMER in host.calls and not host.timer_armed


def test_a_signal_while_the_unit_is_being_started_is_acted_on_once_it_has(host, monkeypatch):
    # Acting on it at once would run the clean-up before the unit exists, and the unit would then start with nothing left
    # to check or stop it.
    host.states = [ABSENT, CONNECTED]
    signal_during(host, monkeypatch, is_unit_start, times=2)
    with pytest.raises(helper.Interrupted, match="taken down again"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    start, stop = host.calls.index(host.unit_starts()[0]), host.calls.index(STOP_UNIT)
    assert start < stop and host.left_behind() == [] and host.net.added == [] and not host.timer_armed


def test_a_signal_after_the_tunnel_passed_its_checks_leaves_a_checked_tunnel_and_says_so(host, monkeypatch):
    # Once the marker is written the tunnel is what `status` and the next connect will find; answering "interrupted" for
    # it, or tearing down only sometimes, would make the answer depend on a race.
    host.states = [ABSENT, CONNECTED]
    signal_during(host, monkeypatch, lambda argv: argv == STOP_TIMER, times=2)
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "connected" and not host.stopped() and host.left_behind() == ["demo.conf", "demo.ok"]


def test_a_signal_while_waiting_for_the_lock_just_leaves(host, monkeypatch):
    import fcntl

    host.run_dir.mkdir()
    holder = (host.run_dir / ".lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX)
    monkeypatch.setattr(helper, "_sleep", lambda seconds: helper._interrupt(1, None))
    try:
        with pytest.raises(helper.Interrupted, match="signal 1; no tunnel was started"):
            helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    finally:
        holder.close()
    assert not host.ran("systemd-run") and host.left_behind() == []


def test_up_notices_a_tunnel_that_went_away_while_it_was_checked(host):
    host.states = [ABSENT, CONNECTED, ABSENT]  # the last read, after the rollback timer is cancelled, finds it gone
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "went away while it was being checked" in result["message"]
    assert host.left_behind() == []


def test_up_byo_reads_the_profile_from_etc(host):
    host.states = [ABSENT, CONNECTED]
    (host.byo_dir / "corp.conf").write_text(AWS_PROFILE)
    assert helper.cmd_up("corp", byo=True, profile_text=None)["status"] == "connected"
    assert (host.run_dir / "corp.conf").read_text() == sanitize_profile(AWS_PROFILE)[0]


def test_up_byo_without_a_profile_says_so(host):
    result = helper.cmd_up("corp", byo=True, profile_text=None)
    assert result == {"status": "no_config", "name": "corp", "message": f"No OpenVPN 2 profile at {host.byo_dir}/corp.conf."}
    assert not host.ran("systemd-run")


def test_up_byo_profile_is_vetted_like_any_other(host):
    (host.byo_dir / "corp.conf").write_text(MINIMAL + "plugin /tmp/x.so\n")
    with pytest.raises(Refused, match="'plugin'"):
        helper.cmd_up("corp", byo=True, profile_text=None)


def test_down_stops_the_unit_and_removes_the_profile_and_the_marker(host):
    host.states = [CONNECTED, ABSENT]
    host.run_dir.mkdir()
    (host.run_dir / "demo.conf").write_text("x")
    (host.run_dir / "demo.ok").write_text("{}")
    assert helper.cmd_down("demo") == {"status": "disconnected", "name": "demo", "cleared": True}
    assert host.stopped() and host.left_behind() == []


def test_down_without_a_unit_is_not_connected(host):
    host.run_dir.mkdir()
    (host.run_dir / "demo.conf").write_text("x")
    host.timer_armed = True  # what a connect that was killed outright can leave
    assert helper.cmd_down("demo") == {"status": "not_connected", "name": "demo"}
    # Still a full reset for the name: the leftover timer and profile go, the (absent) unit is not touched.
    assert host.ran("systemctl", "stop") == [["systemctl", "stop", "openvpn3-od-rollback-demo.timer"]]
    assert not host.timer_armed and host.left_behind() == []


def test_down_reports_a_unit_that_would_not_stop(host):
    host.states = [CONNECTED, CONNECTED]
    host.stop_rc = 1
    result = helper.cmd_down("demo")
    assert result["status"] == "error" and "systemctl stop" in result["message"]


def test_down_says_when_the_unit_is_still_going(host):
    host.states = [CONNECTED, {"LoadState": "loaded", "ActiveState": "deactivating"}]
    host.stop_clears = False
    assert helper.cmd_down("demo") == {"status": "disconnected", "name": "demo", "cleared": False}


def test_status_lists_only_live_units(host, monkeypatch):
    monkeypatch.setattr(helper, "list_units", lambda: ["a", "b"])
    host.states = [CONNECTED, ABSENT]
    host.run_dir.mkdir()
    (host.run_dir / "a.ok").write_text("{}")
    assert helper.cmd_status(None) == {
        "status": "ok",
        "sessions": [{"name": "a", "unit": "openvpn3-on-demand-a.service", "state": "connected", "detail": helper.CONNECTED_STATUS}],
    }


def test_status_ignores_units_under_the_prefix_that_are_not_ours(host, monkeypatch):
    monkeypatch.setattr(helper, "list_units", lambda: ["a", "not a name we make", "b\\x0a"])
    host.states = [CONNECTING]
    assert [s["name"] for s in helper.cmd_status(None)["sessions"]] == ["a"]


def test_a_tunnel_is_only_reported_connected_once_it_passed_its_checks(host):
    # OpenVPN says "connected" before `up` has vetted routes and addresses — and if `up` died there, the rollback timer
    # is about to remove the tunnel. Neither is something to call usable or to adopt as already connected.
    host.run_dir.mkdir()
    pending = helper.describe("demo", CONNECTED)
    assert pending["state"] == "connecting" and pending["detail"] == "tunnel established, checks not finished"
    (host.run_dir / "demo.ok").write_text("{}")
    assert helper.describe("demo", CONNECTED)["state"] == "connected"


def test_a_failing_systemctl_is_an_error_never_no_tunnel(host):
    # "Couldn't find out" read as "nothing there" would answer not_connected for a live tunnel and start a second one.
    host.show_fails = True
    with pytest.raises(Refused, match="Failed to connect to bus"):
        helper.show_unit("openvpn3-on-demand-demo.service")
    with pytest.raises(Refused, match="Failed to connect to bus"):
        helper.cmd_status("demo")
    with pytest.raises(Refused, match="Failed to connect to bus"):
        helper.cmd_down("demo")
    with pytest.raises(Refused, match="Failed to connect to bus"):
        helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert not host.ran("systemd-run") and not host.ran("systemctl", "stop")


def test_a_failing_list_units_is_an_error_not_an_empty_list(monkeypatch):
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus"))
    with pytest.raises(Refused, match="list-units failed"):
        helper.list_units()
    with pytest.raises(Refused):
        helper.cmd_status(None)


def test_json_cmd_fails_closed(monkeypatch):
    # Every route and address check goes through it: an `ip` that fails or prints something else must stop the check.
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 2, "", "Cannot open netlink socket"))
    with pytest.raises(Refused, match="Cannot open netlink socket"):
        helper._json_cmd(["ip", "-j", "-4", "route", "show", "default"])
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "not json", ""))
    with pytest.raises(ValueError):
        helper._json_cmd(["ip", "-j", "addr", "show", "dev", "x"])
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, '[{"dst": "default"}]', ""))
    assert helper._json_cmd(["ip", "-j", "-4", "route", "show", "default"]) == [{"dst": "default"}]


def test_list_units_asks_for_plain_output(monkeypatch):
    seen: list[list[str]] = []

    def run(argv, **kw):
        seen.append(argv)
        # Without --plain a failed unit's row starts with a bullet and the name is no longer the first column.
        prefix = "" if "--plain" in argv else "\u25cf "
        return subprocess.CompletedProcess(argv, 0, f"{prefix}openvpn3-on-demand-corp.service loaded failed failed openvpn3-on-demand tunnel corp\n", "")

    monkeypatch.setattr(helper, "_run", run)
    assert helper.list_units() == ["corp"]
    assert seen == [["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager", "--type=service", "openvpn3-on-demand-*"]]


def test_list_units_parses_systemctl_output(monkeypatch):
    listing = (
        "openvpn3-on-demand-ovpn3-od-abc.service loaded active running openvpn3-on-demand tunnel ovpn3-od-abc\n"
        "openvpn3-on-demand-corp.service         loaded failed failed  openvpn3-on-demand tunnel corp\n"
        "unrelated.service                        loaded active running x\n"
    )
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, listing, ""))
    assert helper.list_units() == ["ovpn3-od-abc", "corp"]


# CLI -------------------------------------------------------------------------


def run_main(monkeypatch, capsys, argv: list[str], stdin: bytes = b"") -> tuple[int, dict]:
    monkeypatch.setattr(helper.sys, "stdin", type("Stdin", (), {"buffer": io.BytesIO(stdin)})())
    code = helper.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_main_up_passes_stdin_and_prints_one_json_object(host, monkeypatch, capsys):
    host.states = [ABSENT, CONNECTED]
    code, result = run_main(monkeypatch, capsys, ["up", "demo"], AWS_PROFILE.encode())
    assert code == 0 and result["status"] == "connected" and result["name"] == "demo"


def test_main_reports_an_interrupted_up_and_restores_signal_handlers(host, monkeypatch, capsys):
    import signal

    host.states = [ABSENT, CONNECTING]
    before = signal.getsignal(signal.SIGTERM)

    def hang_up(seconds):
        assert signal.getsignal(signal.SIGTERM) is helper._interrupt  # a caller hanging up mid-connect reaches the clean-up
        os.kill(os.getpid(), signal.SIGTERM)  # a real signal: delivered to the handler before the next line runs
        os.kill(os.getpid(), signal.SIGHUP)

    monkeypatch.setattr(helper, "_sleep", hang_up)
    code, result = run_main(monkeypatch, capsys, ["up", "demo"], MINIMAL.encode())
    assert code == 1 and result["message"].startswith("interrupted: The tunnel was started but could not be checked (Interrupted: signal 15)")
    assert host.stopped() and host.left_behind() == []
    assert signal.getsignal(signal.SIGTERM) is before


def test_main_turns_a_refusal_into_json_and_exit_1(host, monkeypatch, capsys):
    code, result = run_main(monkeypatch, capsys, ["up", "demo"], (MINIMAL + "up /x\n").encode())
    assert code == 1 and result["status"] == "error" and "'up'" in result["message"]


def test_main_measures_the_size_limit_in_bytes(host, monkeypatch, capsys):
    # Multi-byte characters must not let a profile past the limit with its tail silently cut off and never vetted.
    oversized = ("# " + "é" * 120 + "\n") * 5000 + MINIMAL + "up /tmp/x.sh\n"
    assert len(oversized) < 1024 * 1024 < len(oversized.encode())
    code, result = run_main(monkeypatch, capsys, ["up", "demo"], oversized.encode())
    assert code == 1 and "larger than 1 MiB" in result["message"]


def test_main_rejects_non_utf8_profiles(host, monkeypatch, capsys):
    code, result = run_main(monkeypatch, capsys, ["up", "demo"], b"client\nremote \xff\xfe\n")
    assert code == 1 and "not valid UTF-8" in result["message"]


def test_main_rejects_bad_names_before_reading_anything(host, monkeypatch, capsys):
    code, result = run_main(monkeypatch, capsys, ["down", "../../etc"])
    assert code == 1 and "profile name" in result["message"] and not host.calls


def test_main_usage_error_is_exit_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        helper.main(["frobnicate"])
    assert excinfo.value.code == 2
    assert json.loads(capsys.readouterr().err)["status"] == "error"


def test_helper_state_tells_a_stale_installed_copy_from_a_current_one(monkeypatch, tmp_path):
    installed = tmp_path / "ovpn2-helper"
    monkeypatch.setattr(helper, "INSTALLED_PATH", installed)
    assert helper.helper_state() == "bundled"
    installed.write_bytes(Path(helper.__file__).read_bytes())
    assert helper.helper_state() == "installed" and helper.root_argv() == ["sudo", "-n", str(installed)]
    # Left over from an earlier plugin version: it would keep vetting profiles and routes by that version's rules.
    installed.write_bytes(b"#!/usr/bin/python3 -I\n# last month's helper\n")
    assert helper.helper_state() == "stale"
    assert helper.reinstall_hint() == f"sudo install -D -o root -g root -m 0755 {Path(helper.__file__).resolve()} {installed}"


def test_probe_reports_protocol_version_and_whether_sudo_would_run_the_helper(host, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(helper, "INSTALLED_PATH", tmp_path / "not-installed")
    code, result = run_main(monkeypatch, capsys, ["probe"])
    assert code == 0 and result["protocol"] == helper.PROTOCOL and result["supported"] is True and result["openvpn"].startswith("OpenVPN 2.6.12")
    assert result["sudo"] is True and result["helper"] == "bundled"
    # Asked, never run: `sudo -l` looks the command up in the policy and executes nothing.
    (asked,) = host.ran("sudo")
    assert asked[:4] == ["sudo", "-n", "-l", "--"] and asked[4:] == ["/usr/bin/python3", "-I", str(Path(helper.__file__).resolve())]


def test_probe_says_when_sudo_would_refuse(host, monkeypatch, capsys):
    real_run = host._run
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "sudo: a password is required") if argv[0] == "sudo" else real_run(argv, **kw))
    assert run_main(monkeypatch, capsys, ["probe"])[1]["sudo"] is False


def test_probe_says_when_sudo_cannot_be_asked_from_a_sandboxed_shell(host, monkeypatch, capsys, tmp_path):
    # Under no-new-privileges sudo fails whatever its rules say; reporting that as "refused" sends the user to sudoers.
    (tmp_path / "proc-status").write_text("Name:\tpython3\nNoNewPrivs:\t1\nSeccomp:\t2\n")
    assert run_main(monkeypatch, capsys, ["probe"])[1]["sudo"] is None and not host.ran("sudo")
    (tmp_path / "proc-status").write_text("Name:\tpython3\nNoNewPrivs:\t0\n")
    assert run_main(monkeypatch, capsys, ["probe"])[1]["sudo"] is True


def test_tools_are_shielded_from_the_callers_signals_while_up_runs(monkeypatch):
    # The tools share the helper's process group: a signal meant for `up` would kill the tool a check waits for, and its
    # second copy the clean-up's `systemctl stop`. They inherit a blocked mask instead; outside `up` nothing changes.
    import signal

    script = "import signal; print(int(signal.SIGTERM in signal.pthread_sigmask(signal.SIG_BLOCK, [])))"
    assert helper._run([sys.executable, "-c", script]).stdout.strip() == "0"
    before = signal.signal(signal.SIGTERM, helper._interrupt)
    monkeypatch.setattr(helper, "_pending_signal", 0)
    try:
        assert helper._run([sys.executable, "-c", script]).stdout.strip() == "1"
        os.kill(os.getpid(), signal.SIGTERM)
        assert helper._pending_signal == signal.SIGTERM  # ...and the helper itself still takes its note
        assert signal.SIGTERM not in signal.pthread_sigmask(signal.SIG_BLOCK, [])
    finally:
        signal.signal(signal.SIGTERM, before)


@pytest.mark.parametrize(("rc", "cancelled"), [(3, True), (4, True), (0, False), (-15, False), (1, False)])
def test_the_timer_counts_as_cancelled_only_on_systemds_own_word(monkeypatch, rc, cancelled):
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, rc if argv[1] == "is-active" else 0, "", ""))
    assert helper._disarm_rollback("demo") is cancelled


def test_the_clean_up_survives_a_stop_that_hangs(host, monkeypatch):
    host.states = [ABSENT, CONNECTING, ABSENT]
    real = host.net.fallback

    def run(argv, **kwargs):
        if argv[:2] == ["systemctl", "stop"] and argv[2].startswith(helper.UNIT_PREFIX):
            raise subprocess.TimeoutExpired(argv, 60)
        return real(argv, **kwargs)

    monkeypatch.setattr(host.net, "fallback", run)
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["status"] == "error" and "exited before the tunnel came up" in result["message"] and host.left_behind() == []


def test_the_root_helper_does_not_take_its_tools_from_the_callers_path(host, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "/tmp/evil:/usr/bin")
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    run_main(monkeypatch, capsys, ["status"])
    assert os.environ["PATH"] == helper.ROOT_PATH == "/usr/sbin:/usr/bin:/sbin:/bin"


def test_up_says_which_account_openvpn_runs_as(host, monkeypatch):
    host.states = [ABSENT, CONNECTED]
    assert helper.cmd_up("demo", byo=False, profile_text=MINIMAL)["runs_as"] == "openvpn"
    assert helper.cmd_down("demo")["status"] == "disconnected"
    host.states = [ABSENT, CONNECTED]
    monkeypatch.setattr(helper, "_user_exists", lambda user: False)  # Debian/Ubuntu: the package creates no such account
    result = helper.cmd_up("demo", byo=False, profile_text=MINIMAL)
    assert result["runs_as"] == "root" and "--user" not in host.unit_starts()[-1]


# how it has to run -------------------------------------------------------------


def test_standard_library_only():
    # It runs as root under the system python3, outside the venv.
    tree = ast.parse(Path(helper.__file__).read_text())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
    assert not any(isinstance(node, ast.ImportFrom) and node.level for node in ast.walk(tree)), "relative imports break running by path"
    assert imported - {"__future__"} <= set(sys.stdlib_module_names)


def python_3_9() -> str | None:
    for candidate in ("/usr/bin/python3", shutil.which("python3.9") or ""):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"], capture_output=True, text=True, check=False)
            if probe.stdout.strip() == "True":
                return candidate
    return None


def test_runs_under_a_real_python_3_9():
    # The helper runs as root under the system python3, 3.9 on Amazon Linux 2023. Every other test imports it through
    # the package on Python >= 3.10, and a syntax check does not notice a 3.10-only call or a type union built at
    # runtime; this runs the real thing where a 3.9 interpreter exists (it does on the host this backend was built for).
    python = python_3_9()
    if python is None:
        pytest.skip("no Python 3.9 on this machine")
    script = (
        "import importlib.util, json, sys\n"
        "from pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('h', sys.argv[1]); h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)\n"
        "text, dropped = h.sanitize_profile(sys.stdin.read())\n"
        "routes = h.pushed_routes([sys.argv[2]])\n"
        "argv = h.openvpn_argv('demo', Path('/run/x/demo.conf'), drop_privileges=True)\n"
        "state = h.describe('demo', {'LoadState': 'loaded', 'ActiveState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': '1'})\n"
        "print(json.dumps({'lines': len(text.splitlines()), 'dropped': dropped, 'routes': routes, 'argv': len(argv), 'state': state['state'],"
        " 'unit': h.unit_name('demo'), 'rollback': h.rollback_unit('demo', 'timer'), 'how': h._how(('local', '', 'lo')), 'diag': h.diagnose(['Server poll timeout'])}))\n"
    )
    proc = subprocess.run([python, "-I", "-c", script, helper.__file__, AWS_PUSH], input=AWS_PROFILE, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["dropped"] == ["dev", "resolv-retry", "verb", "dhcp-option"] and out["routes"] == [["10.0.0.0/16"], [], True]
    assert out["state"] == "failed" and out["unit"] == "openvpn3-on-demand-demo.service" and out["argv"] > 40
    for args in (["--help"], ["status", "bad name"]):
        proc = subprocess.run([python, "-I", helper.__file__, *args], capture_output=True, text=True, check=False)
        assert proc.returncode in (0, 1) and "Traceback" not in proc.stderr, proc.stderr


def test_python_3_9_syntax():
    # Amazon Linux 2023's /usr/bin/python3 is 3.9: no match statements, no parenthesized with-items.
    source = Path(helper.__file__).read_text()
    ast.parse(source, feature_version=(3, 9))
    assert source.startswith("#!/usr/bin/python3 -I\n")
    assert "from __future__ import annotations" in source


def test_runs_by_path_in_isolated_mode():
    # --help touches nothing on the machine running the tests (probe would ask the real sudo and run the real openvpn).
    proc = subprocess.run([sys.executable, "-I", helper.__file__, "--help"], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "probe" in proc.stdout and "up" in proc.stdout


# what the helper is allowed to do, spelled out --------------------------------
# These repeat the module's own tables on purpose: widening what a root-run OpenVPN is handed, or what a VPN server may
# push, has to be done in two places — the code and here — so it cannot happen by accident.


def test_the_profile_allowlist_is_exactly_this():
    kept = {
        "auth",
        "auth-nocache",
        "cipher",
        "client",
        "connect-retry",
        "data-ciphers",
        "data-ciphers-fallback",
        "explicit-exit-notify",
        "float",
        "keepalive",
        "key-direction",
        "mssfix",
        "mute-replay-warnings",
        "nobind",
        "persist-key",
        "persist-tun",
        "ping",
        "ping-restart",
        "proto",
        "pull",
        "rcvbuf",
        "remote",
        "remote-cert-tls",
        "remote-random",
        "remote-random-hostname",
        "reneg-sec",
        "sndbuf",
        "tls-cipher",
        "tls-ciphersuites",
        "tls-client",
        "tls-groups",
        "tls-version-max",
        "tls-version-min",
        "tun-mtu",
        "verify-x509-name",
    }
    replaced_by_the_backend = {
        "connect-retry-max",
        "connect-timeout",
        "dev",
        "dev-type",
        "dhcp-option",
        "hand-window",
        "mute",
        "resolv-retry",
        "server-poll-timeout",
        "verb",
    }
    assert {name for name, (_, action) in helper._DIRECTIVES.items() if action == helper.KEEP} == kept
    assert {name for name, (_, action) in helper._DIRECTIVES.items() if action == helper.DROP} == replaced_by_the_backend
    assert helper.INLINE_TAGS == {"auth-user-pass", "ca", "cert", "extra-certs", "key", "tls-auth", "tls-crypt", "tls-crypt-v2"}


def test_the_options_a_server_may_push_are_exactly_these():
    assert helper.PULL_ACCEPT == (
        "route ",
        "route-gateway ",
        "ifconfig ",
        "topology ",
        "ping ",
        "ping-restart ",
        "peer-id ",
        "cipher ",
        "protocol-flags ",
        "key-derivation ",
        "tun-mtu ",
        "explicit-exit-notify",
        "auth-token",
        "push-continuation ",
    )


def test_safety_constants_and_unit_hardening():
    # The host's own route must win on an equal prefix: above anything a distribution assigns (Amazon Linux 512,
    # NetworkManager up to 600 and a few hundred more per extra interface).
    assert helper.ROUTE_METRIC > 1024
    # What the skill and the README promise.
    assert helper.INACTIVE_SECS == 3600 and helper.MAX_LIFETIME_SECS == 12 * 3600 and helper.ROLLBACK_SECS == 120
    assert [str(net) for net in helper.PRIVATE_NETWORKS] == ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"]
    assert helper.SUPPORTED_VERSIONS == ((2, 6),)
    argv = helper.systemd_run_argv("demo", Path("/run/x/demo.conf"), drop_privileges=True)
    assert argv[argv.index("--user") + 1] == "openvpn" and argv[argv.index("--group") + 1] == "openvpn"
    assert argv.index("--suppress-timestamps") > argv.index("--config")  # the PUSH_REPLY line is matched as a whole line
    props = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-p"]
    assert "CapabilityBoundingSet=CAP_IPC_LOCK CAP_NET_ADMIN CAP_NET_RAW CAP_SETGID CAP_SETUID CAP_SETPCAP CAP_SYS_CHROOT CAP_DAC_OVERRIDE" in props
    assert "PrivateTmp=true" in props and "ProtectSystem=true" in props and "ProtectHome=true" in props
    assert "DeviceAllow=/dev/net/tun rw" in props
