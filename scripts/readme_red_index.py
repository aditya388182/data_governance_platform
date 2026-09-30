#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
START, END = "<!-- RED-PR-INDEX:START -->", "<!-- RED-PR-INDEX:END -->"
RED = re.compile(r"\bRED\b|\bred\s*(→|->)", re.I)


def rows(prs):
    reds = sorted((p for p in prs if RED.search(p["title"])), key=lambda p: p["number"])
    out = ["| PR | What it proves | Outcome |", "|---|---|---|"]
    for p in reds:
        outcome = "merged after the fix (red is in its checks history)" if p.get("mergedAt") else "closed, red preserved"
        title = p["title"].replace("|", "\\|")
        out.append(f"| [#{p['number']}]({p['url']}) | {title} | {outcome} |")
    return out, len(reds)


def splice(text, block):
    if START not in text or END not in text:
        raise SystemExit(f"README.md lacks the {START} / {END} markers")
    a, b = text.index(START) + len(START), text.index(END)
    return text[:a] + "\n" + "\n".join(block) + "\n" + text[b:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    prs = json.loads(subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", "300", "--json",
                                     "number,title,url,state,mergedAt"], capture_output=True, text=True, check=True).stdout)
    block, n = rows(prs)
    if n == 0:
        print("no red PRs found by title — check `gh pr list --state all`", file=sys.stderr)
        return 1
    if a.dry_run:
        print("\n".join(block))
    else:
        README.write_text(splice(README.read_text(), block))
        print(f"README.md: red-PR index rewritten ({n} PRs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
