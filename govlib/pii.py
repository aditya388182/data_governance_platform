from __future__ import annotations

import re


def compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def matches(field_name: str, compiled: list[re.Pattern]) -> list[str]:
    return [p.pattern for p in compiled if p.search(field_name)]
