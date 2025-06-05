import fnmatch
import os
from typing import Optional

import yaml

from bugster.constants import IGNORE_PATTERNS, TESTS_DIR


def get_specs_paths(
    relatives_to: Optional[str] = None, folder_name: Optional[str] = None
) -> list[str]:
    """Get all spec files paths in the tests directory."""
    file_paths = []
    dir = os.path.join(TESTS_DIR, folder_name) if folder_name else TESTS_DIR

    for root, _, files in os.walk(dir):
        if "example" in root:
            continue

        for file in files:
            full_path = os.path.join(root, file)

            if relatives_to:
                full_path = os.path.relpath(full_path, start=relatives_to)

            file_paths.append(full_path)

    return file_paths


def get_specs_pages():
    """Get the specs pages."""
    specs_paths = get_specs_paths()
    specs_pages = {}

    for spec_path in specs_paths:
        with open(spec_path, encoding="utf-8") as file:
            data = yaml.safe_load(file)

            if isinstance(data, list):
                if not data:
                    raise ValueError(f"Empty list in spec file: {spec_path}")

                data = data[0]
            elif not isinstance(data, dict):
                raise ValueError(f"Invalid spec file: {spec_path}")

            if "page_path" not in data:
                raise ValueError(f"Missing 'page_path' in spec file: {spec_path}")

            page_path = data["page_path"]
            relative_path = os.path.relpath(spec_path, TESTS_DIR)
            specs_pages[page_path] = {
                "data": data,
                "path": relative_path,
            }

    return specs_pages


def filter_path(
    path: str, allowed_extensions: Optional[list[str]] = None
) -> Optional[str]:
    """Filter a single path based on ignore patterns and `.gitignore` rules."""
    from bugster.libs.utils.git import get_gitignore

    gitignore = get_gitignore()
    GITIGNORE_PATH = ".gitignore"

    if not allowed_extensions:
        allowed_extensions = [".ts", ".tsx", ".js", ".jsx"]

    if not any(path.endswith(ext) for ext in allowed_extensions):
        return None

    if os.path.isdir(path):
        return None

    if any(fnmatch.fnmatch(path, pattern) for pattern in IGNORE_PATTERNS):
        return None

    if gitignore and gitignore.match_file(path):
        return None

    if path == GITIGNORE_PATH:
        return None

    return path.replace(os.sep, "/")
