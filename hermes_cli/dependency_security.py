"""Offline, stdlib-only dependency regression probe; execute with Python -I -S.

Reads only allowlisted distribution METADATA, never imports installed packages or
Hermes configuration. This known-regression check is NOT a current advisory audit.
Exit codes: 0 clear in declared scope, 1 findings, 2 unknown/incomplete.
"""
from __future__ import annotations

import argparse
from email.parser import Parser
import json
from pathlib import Path
import re
import sys

REQUIRED = ("mcp", "httpx2", "httpcore2")
FLOORS = {
    "httpx2": ("2.12.0", "GHSA-8xx6-hgc6-gc2m"),
    "httpcore2": ("2.10.0", "GHSA-7mj9-2mp8-4m2p"),
    "pynacl": ("1.6.2", "GHSA-mrfv-m5wm-5w6w"),
}
ALLOWLIST = frozenset((*REQUIRED, "pynacl", "mistralai"))


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def stable_version(value):
    # Non-stable/unfamiliar versions remain UNKNOWN rather than being cleared.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", value):
        raise ValueError("unsupported-version")
    parts = tuple(int(p) for p in value.split("."))
    return parts + (0,) * (4 - len(parts))


def inspect_site(site: Path):
    versions = {}
    issues = []
    findings = []
    if not site.is_dir() or site.is_symlink():
        issues.append("site-unavailable")
    else:
        try:
            for directory in sorted(site.glob("*.dist-info")):
                expected = normalize(directory.name[:-10].rsplit("-", 1)[0])
                if expected not in ALLOWLIST:
                    continue
                metadata = directory / "METADATA"
                try:
                    if directory.is_symlink() or metadata.is_symlink() or metadata.stat().st_size > 262144:
                        raise ValueError("unsafe-metadata")
                    msg = Parser().parsestr(metadata.read_text(encoding="utf-8"), headersonly=True)
                    names, values = msg.get_all("Name", []), msg.get_all("Version", [])
                    if len(names) != 1 or len(values) != 1 or normalize(names[0]) != expected:
                        raise ValueError("invalid-metadata")
                    stable_version(values[0])
                    if expected in versions:
                        raise ValueError("duplicate-metadata")
                    versions[expected] = values[0]
                except (OSError, ValueError, UnicodeError):
                    issues.append(expected + ":metadata-unknown")
        except OSError:
            issues.append("site-unreadable")
    for required in REQUIRED:
        if required not in versions:
            issues.append(required + ":missing")
    for name, (fixed, advisory) in FLOORS.items():
        if name in versions and stable_version(versions[name]) < stable_version(fixed):
            findings.append({"package": name, "version": versions[name], "advisory": advisory})
    if versions.get("mistralai") == "2.4.6":
        findings.append({"package": "mistralai", "version": versions["mistralai"], "advisory": "shai-hulud-2026-05"})
    return {
        "schema": 1,
        "scope": "offline-known-dependency-regressions",
        "status": "unknown" if issues else "findings" if findings else "clear",
        "versions": versions,
        "findings": findings,
        "issues": sorted(set(issues)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", required=True, type=Path)
    args = parser.parse_args()
    site = (args.venv / "Lib" / "site-packages" if sys.platform == "win32" else
            args.venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
    try:
        report = inspect_site(site)
    except Exception:
        report = {"schema": 1, "scope": "offline-known-dependency-regressions", "status": "unknown",
                  "versions": {}, "findings": [], "issues": ["probe-error"]}
    print(json.dumps(report, sort_keys=True))
    return {"clear": 0, "findings": 1, "unknown": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
