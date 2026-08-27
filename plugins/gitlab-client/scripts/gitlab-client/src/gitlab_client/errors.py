# SPDX-License-Identifier: AGPL-3.0-only
"""Exception hierarchy. Each class carries the process exit code `cli.main` returns."""


class GlError(Exception):
    """Base: message goes to stderr, process exits 1."""

    exit_code = 1


class ConfigError(GlError):
    """Settings, token, project-resolution, or usage problems — exit 2."""

    exit_code = 2


class PolicyError(GlError):
    """Refused by the write policy before any network call — exit 3."""

    exit_code = 3


class HttpError(GlError):
    """A non-2xx response (after the single 429 retry)."""

    def __init__(self, status: int, method: str, url: str, detail: str):
        self.status, self.method, self.url, self.detail = status, method, url, detail
        super().__init__(f"HTTP {status} {method} {url}: {detail}")
