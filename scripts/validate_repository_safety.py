#!/usr/bin/env python3
"""Fail closed when tracked files cross the repository's data-safety boundary."""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

ALLOWED_XLSX = {
    "outputs/phase1/Business_Asset_Discovery_Template.xlsx",
    "outputs/phase1_5/Business_Asset_Initialization_v2.xlsx",
    "outputs/phase1_5/Business_Asset_Initialization_v2_1.xlsx",
}

ALLOWED_DISCOVERY = {"phase1_5/discovery/README.md"}

PROHIBITED_PREFIXES = (
    ".tmp/",
    "attachments/",
    "data/raw/",
    "generated_reports/",
    "local_private/",
    "private_data/",
    "weekly_reports/",
)

PROHIBITED_EXTENSIONS = {
    ".csv",
    ".eml",
    ".key",
    ".msg",
    ".ost",
    ".pem",
    ".pst",
}

TEXT_EXTENSIONS = {
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".txt",
    ".py",
    ".ps1",
}

EMAIL_PATTERN = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:\\")
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^\s*(password|passwd|token|access_token|refresh_token|cookie|"
    r"client_secret|api_key|login_account)\s*:\s*([^\r\n#]+)"
)
AUTO_SEND_TRUE_PATTERN = re.compile(r"(?im)^\s*auto_send\s*:\s*true\s*$")
PLACEHOLDER_PATTERN = re.compile(r"^\$\{[A-Z0-9_]+_LOCAL_ONLY\}$")


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    ]


def is_safe_sensitive_value(raw_value: str) -> bool:
    value = raw_value.strip().strip("'\"")
    return (
        not value
        or value.upper() == "TBD"
        or value.lower() in {"false", "null", "none"}
        or bool(PLACEHOLDER_PATTERN.fullmatch(value))
    )


def validate_text(path: str, errors: list[str]) -> None:
    absolute_path = REPOSITORY_ROOT / path
    try:
        text = absolute_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"{path}: tracked text file is not valid UTF-8")
        return

    if EMAIL_PATTERN.search(text):
        errors.append(f"{path}: contains an email address")
    if WINDOWS_PATH_PATTERN.search(text):
        errors.append(f"{path}: contains an absolute Windows path")
    if PRIVATE_KEY_PATTERN.search(text):
        errors.append(f"{path}: contains private-key material")
    if AUTO_SEND_TRUE_PATTERN.search(text):
        errors.append(f"{path}: sets auto_send=true")

    for match in SENSITIVE_ASSIGNMENT_PATTERN.finditer(text):
        if not is_safe_sensitive_value(match.group(2)):
            errors.append(
                f"{path}:{text.count(chr(10), 0, match.start()) + 1}: "
                f"contains a non-placeholder value for {match.group(1)}"
            )


def validate_xlsx(path: str, errors: list[str]) -> None:
    if path not in ALLOWED_XLSX:
        errors.append(f"{path}: spreadsheet is not on the reviewed template allowlist")
        return

    absolute_path = REPOSITORY_ROOT / path
    try:
        with zipfile.ZipFile(absolute_path) as workbook:
            bad_member = workbook.testzip()
            if bad_member:
                errors.append(f"{path}: corrupt XLSX member {bad_member}")
    except zipfile.BadZipFile:
        errors.append(f"{path}: invalid XLSX container")


def main() -> int:
    errors: list[str] = []
    files = tracked_files()

    for path in files:
        suffix = Path(path).suffix.lower()

        if path.startswith("phase1_5/discovery/") and path not in ALLOWED_DISCOVERY:
            errors.append(f"{path}: local-only discovery content is tracked")

        if path.startswith(PROHIBITED_PREFIXES):
            errors.append(f"{path}: prohibited operational-data path is tracked")

        if suffix in PROHIBITED_EXTENSIONS:
            errors.append(f"{path}: prohibited data or credential file type")

        if suffix == ".xlsx":
            validate_xlsx(path, errors)
        elif suffix in TEXT_EXTENSIONS:
            validate_text(path, errors)

    if errors:
        print("Repository safety validation FAILED:")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    print(
        "Repository safety validation passed: "
        f"{len(files)} tracked files checked; "
        f"{len(ALLOWED_XLSX)} reviewed spreadsheet templates allowed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
