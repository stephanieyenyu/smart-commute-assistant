#!/usr/bin/env python3
"""
smart-commute-assistant — repository statistics collector.

Produces the README stats line and the Scale table for docs/metrics.md.
Every figure is printed alongside the command that produced it, so the
derivation can be pasted straight into metrics.md.

Run from the repository root:

    python collect_repo_stats.py
    python collect_repo_stats.py --markdown > scale_table.md

Run this AFTER Phase 1 cleanup — deleting AI_Export.txt, the .bat files and
the duplicate requirements.txt changes the file and line counts.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP = REPO / "backend" / "app"
TESTS = REPO / "tests"


def sh(cmd: str) -> str:
    """Run a shell command in the repo root, return stdout stripped."""
    try:
        out = subprocess.run(
            cmd, shell=True, cwd=REPO,
            capture_output=True, text=True, timeout=60,
        )
        return out.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"<error: {exc}>"


def count_lines(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            pass
    return total


# --------------------------------------------------------------------------
# Individual metrics. Each returns (label, value, derivation).
# --------------------------------------------------------------------------

def m_commits():
    v = sh("git rev-list --count HEAD")
    return "Commits", v, "git rev-list --count HEAD"


def m_dev_window():
    first = sh('git log --reverse --format=%ad --date=short | head -1')
    last = sh('git log -1 --format=%ad --date=short')
    return "Development window", f"{first} to {last}", "git log --format=%ad --date=short"


def m_source_files():
    v = sh('git ls-files "*.py" "*.js" "*.html" | wc -l')
    return "Source files (.py .js .html)", v, 'git ls-files "*.py" "*.js" "*.html" | wc -l'


def m_python_loc():
    files = [REPO / f for f in sh('git ls-files "*.py"').splitlines() if f]
    return "Python LOC", str(count_lines(files)), 'git ls-files "*.py" | xargs wc -l'


def m_test_loc():
    files = sorted(TESTS.glob("test_*.py")) if TESTS.is_dir() else []
    return (
        "Test LOC",
        f"{count_lines(files)} across {len(files)} files",
        "wc -l tests/test_*.py",
    )


def m_routes():
    """Distinct route paths declared with @app/@router decorators."""
    pat = re.compile(
        r'@(?:app|router|api_router|ws_router|family_router|liff_router)'
        r'\.(get|post|put|patch|delete|websocket)\(\s*["\']([^"\']+)["\']'
    )
    paths, methods = set(), []
    for f in sorted(APP.rglob("*.py")):
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for method, path in pat.findall(src):
            paths.add(path)
            methods.append((method.upper(), path))
    return (
        "HTTP routes (distinct paths)",
        str(len(paths)),
        "grep -rhoP '@(app|router)\\.(get|post|...)\\(\"[^\"]+\"' backend/app",
    )


def m_route_detail():
    """Full route inventory — paste into docs/api.md as the starting point."""
    pat = re.compile(
        r'@(?:app|router|api_router|ws_router|family_router|liff_router)'
        r'\.(get|post|put|patch|delete|websocket)\(\s*["\']([^"\']+)["\']'
    )
    rows = []
    for f in sorted(APP.rglob("*.py")):
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for method, path in pat.findall(src):
            rows.append((method.upper(), path, f.relative_to(REPO).as_posix()))
    return sorted(set(rows), key=lambda r: (r[1], r[0]))


def m_tables():
    models = APP / "models.py"
    n = 0
    if models.exists():
        n = models.read_text(encoding="utf-8", errors="replace").count("__tablename__")
    return "Database tables", str(n), 'grep -c "__tablename__" backend/app/models.py'


def m_migrations():
    d = REPO / "backend" / "alembic" / "versions"
    n = len(list(d.glob("*.py"))) if d.is_dir() else 0
    return "Alembic revisions", str(n), "ls backend/alembic/versions/*.py | wc -l"


def m_jobs():
    f = APP / "reminder_scheduler.py"
    n = 0
    if f.exists():
        n = f.read_text(encoding="utf-8", errors="replace").count("scheduler.add_job(")
    return "Scheduled jobs", str(n), 'grep -c "scheduler.add_job(" backend/app/reminder_scheduler.py'


def m_api_endpoints():
    """Distinct external endpoints instrumented with log_api_health."""
    pat = re.compile(r'log_api_health\(\s*["\']([a-zA-Z0-9._]+)["\']')
    labels, sites = set(), 0
    for f in sorted(APP.rglob("*.py")):
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = pat.findall(src)
        labels.update(found)
        sites += src.count("log_api_health(")
    return labels, sites


def m_line_entrypoints():
    """LINE conversation entry points.

    Text commands are dispatched through the COMMAND_ALIASES dict in
    webhook.py, so its distinct keys are the real count. Postbacks are counted
    from action= literals. AMR_System quotes 11 entry points; verify whatever
    number goes in the README by reading webhook.py yourself.
    """
    aliases, actions = set(), set()
    for f in sorted(APP.rglob("*.py")):
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        aliases.update(re.findall(r'COMMAND_ALIASES\[\s*["\']([a-z_]+)["\']\s*\]', src))
        actions.update(re.findall(r'action=([a-z_]+)', src))
    return sorted(aliases), sorted(actions)


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true",
                    help="emit the Scale table for docs/metrics.md")
    ap.add_argument("--routes", action="store_true",
                    help="emit the full route inventory for docs/api.md")
    args = ap.parse_args()

    if not (REPO / ".git").exists():
        print("Not a git repository. Run this from the repo root.", file=sys.stderr)
        return 1

    metrics = [
        m_commits(),
        m_dev_window(),
        m_source_files(),
        m_python_loc(),
        m_test_loc(),
        m_routes(),
        m_tables(),
        m_migrations(),
        m_jobs(),
    ]

    labels, sites = m_api_endpoints()
    metrics.append((
        "Instrumented external endpoints", str(len(labels)),
        "distinct first arguments to log_api_health()",
    ))
    metrics.append((
        "log_api_health call sites", str(sites),
        'grep -rc "log_api_health(" backend/app',
    ))

    if args.routes:
        print("| Method | Path | Source |")
        print("|---|---|---|")
        for method, path, src in m_route_detail():
            print(f"| {method} | `{path}` | `{src}` |")
        return 0

    if args.markdown:
        print("## Scale\n")
        print("| Figure | Value | Derivation |")
        print("|---|---|---|")
        for label, value, deriv in metrics:
            print(f"| {label} | {value} | `{deriv}` |")
        print()
        print("Instrumented endpoints: " + ", ".join(f"`{x}`" for x in sorted(labels)))
        return 0

    width = max(len(m[0]) for m in metrics) + 2
    print("=" * 72)
    print("smart-commute-assistant — repository statistics")
    print("=" * 72)
    for label, value, deriv in metrics:
        print(f"{label:<{width}} {value}")
        print(f"{'':<{width}} └─ {deriv}")
    print()
    print("Instrumented external endpoints:")
    for x in sorted(labels):
        print(f"  - {x}")

    aliases, actions = m_line_entrypoints()
    print()
    print(f"LINE entry points: {len(aliases)} text commands, "
          f"{len(actions)} postback actions")
    print("  text commands (COMMAND_ALIASES keys):")
    for a in aliases:
        print(f"    - {a}")
    if actions:
        print("  postback actions:")
        for a in actions:
            print(f"    - action={a}")
    print()
    print("Reminders:")
    print("  - Run this AFTER Phase 1 cleanup; deletions change the counts.")
    print("  - Re-run --markdown to regenerate the metrics.md Scale table.")
    print("  - Re-run --routes to regenerate the api.md route inventory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
