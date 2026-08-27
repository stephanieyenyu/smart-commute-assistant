#!/usr/bin/env python3
"""
smart-commute-assistant — scrubbed data export for the public repo.

AMR_System commits data/packages.json so every figure in its README can be
verified independently. Same purpose here, with one difference that matters:
AMR_System's rows were test accounts, and these rows contain a real home
address, real coordinates and a real LINE user ID. Nothing identifying leaves
the database.

Produces:
    data/commute_logs.json     — the decision log, the dataset the README claims
    data/api_health_logs.json  — external API reliability, the measurement backbone
    data/export_manifest.json  — row counts, scrub policy, snapshot timestamp

Usage:
    pip install sqlalchemy pg8000
    export DATABASE_URL="postgresql+pg8000://user:pass@host:5432/dbname"
    python export_data_scrubbed.py

    # inspect before committing
    python export_data_scrubbed.py --verify

If the Render connection string starts with plain postgresql://, either add the
+pg8000 prefix or install psycopg2-binary instead. See PORTFOLIO_TODO.md 0.4 —
which driver is actually live is one of the facts still to confirm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

try:
    from sqlalchemy import create_engine, text
except ImportError:
    print("pip install sqlalchemy pg8000", file=sys.stderr)
    raise SystemExit(1)

# Repo root is one level up from scripts/, so data/ lands beside docs/.
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data"

# --------------------------------------------------------------------------
# Scrub policy.
#
# DROP     — column is removed entirely from the export.
# PSEUDO   — replaced with a stable label (user_1, user_2, ...) so rows can
#            still be grouped per user without identifying anyone.
#
# Nothing here is exported raw. If a column is added to the schema later and is
# not listed, it is exported as-is — review the manifest before committing.
# --------------------------------------------------------------------------

DROP_COLUMNS = {
    # direct identifiers
    "line_user_id", "display_name", "invite_code",
    # home and workplace location
    "home_address", "home_lat", "home_lng", "home_place_name",
    "office_address", "office_lat", "office_lng", "office_place_name",
    "origin_address", "origin_lat", "origin_lng", "origin_name",
    "dest_address", "dest_lat", "dest_lng", "dest_name",
    # chosen stops reveal the neighbourhood
    "selected_bus_stop_id", "selected_bus_stop_name",
    "selected_bus_stop_lat", "selected_bus_stop_lng",
    "selected_metro_station_id", "selected_metro_station_name",
    "selected_metro_station_lat", "selected_metro_station_lng",
}

PSEUDO_COLUMNS = {"user_id", "household_id", "schedule_id", "group_id"}

# Coarsen these so a home city is not inferable from the export.
COARSEN_COLUMNS = {"home_city", "home_township", "office_city", "office_township"}


_pseudo_map: dict[tuple[str, object], str] = {}


def pseudonymise(column: str, value: object) -> object:
    if value is None:
        return None
    key = (column, value)
    if key not in _pseudo_map:
        prefix = column.replace("_id", "")
        seen = sum(1 for k in _pseudo_map if k[0] == column)
        _pseudo_map[key] = f"{prefix}_{seen + 1}"
    return _pseudo_map[key]


def normalise(url: str) -> str:
    """Render hands out postgres://; SQLAlchemy needs a driver in the scheme."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        for driver in ("psycopg2", "pg8000"):
            try:
                __import__(driver)
                return url.replace("postgresql://", f"postgresql+{driver}://", 1)
            except ImportError:
                continue
        print("No PostgreSQL driver found.\n"
              "  pip install psycopg2-binary   (or, if it will not compile:  pip install pg8000)",
              file=sys.stderr)
        raise SystemExit(1)
    return url


def json_safe(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return hashlib.sha256(value).hexdigest()[:16]
    return value


def scrub_row(row: dict) -> dict:
    out = {}
    for col, val in row.items():
        if col in DROP_COLUMNS:
            continue
        if col in PSEUDO_COLUMNS:
            out[col] = pseudonymise(col, val)
        elif col in COARSEN_COLUMNS:
            out[col] = "REDACTED" if val else None
        else:
            out[col] = json_safe(val)
    return out


def export_table(engine, table: str) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {table}"))
        columns = list(result.keys())
        rows = [dict(zip(columns, r)) for r in result.fetchall()]
    scrubbed = [scrub_row(r) for r in rows]
    dropped = sorted(set(columns) & DROP_COLUMNS)
    pseudo = sorted(set(columns) & PSEUDO_COLUMNS)
    coarsened = sorted(set(columns) & COARSEN_COLUMNS)
    print(f"  {table:<22} {len(rows):>6} rows")
    if dropped:
        print(f"    dropped:     {', '.join(dropped)}")
    if pseudo:
        print(f"    pseudonymised: {', '.join(pseudo)}")
    if coarsened:
        print(f"    coarsened:   {', '.join(coarsened)}")
    return scrubbed


def verify(path: Path) -> bool:
    """Re-read an export and fail loudly if anything identifying survived."""
    blob = path.read_text(encoding="utf-8")
    problems = []
    # LINE user IDs are 33 chars starting with U.
    if re.search(r'"U[0-9a-f]{32}"', blob):
        problems.append("a LINE user ID pattern is present")
    # Taipei-area coordinates.
    if re.search(r'2[0-5]\.\d{4,}', blob) and re.search(r'12[01]\.\d{4,}', blob):
        problems.append("coordinate-shaped values are present")
    # Chinese address markers.
    for marker in ("路", "街", "巷", "弄", "號", "區", "市"):
        if marker in blob:
            problems.append(f"address marker {marker!r} is present")
            break
    if problems:
        print(f"  FAIL {path.name}")
        for p in problems:
            print(f"    - {p}")
        return False
    print(f"  ok   {path.name}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="check existing exports for leaked identifiers and exit")
    args = ap.parse_args()

    if args.verify:
        files = sorted(OUT_DIR.glob("*.json"))
        if not files:
            print(f"No exports found in {OUT_DIR}", file=sys.stderr)
            return 1
        print("Verifying exports:")
        ok = all(verify(f) for f in files if f.name != "export_manifest.json")
        print()
        print("Clean." if ok else "Do NOT commit these files until resolved.")
        return 0 if ok else 1

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL first.", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(normalise(url))

    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:                                    # noqa: BLE001
        print(f"Cannot connect: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Check DATABASE_URL is the *External* URL from Render.", file=sys.stderr)
        return 1

    snapshot = datetime.now().isoformat(timespec="seconds")

    print(f"Snapshot {snapshot}")
    print("Exporting:")

    targets = {
        "commute_logs": "commute_logs.json",
        "api_health_logs": "api_health_logs.json",
    }

    manifest = {
        "snapshot_taken_at": snapshot,
        "scrub_policy": {
            "dropped_columns": sorted(DROP_COLUMNS),
            "pseudonymised_columns": sorted(PSEUDO_COLUMNS),
            "coarsened_columns": sorted(COARSEN_COLUMNS),
        },
        "tables": {},
    }

    failed = False
    for table, filename in targets.items():
        try:
            rows = export_table(engine, table)
        except Exception as exc:  # noqa: BLE001
            print(f"  {table:<22} FAILED — {type(exc).__name__}: {exc}")
            failed = True
            continue
        path = OUT_DIR / filename
        path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest["tables"][table] = {
            "file": f"data/{filename}",
            "rows": len(rows),
            "columns": sorted(rows[0].keys()) if rows else [],
        }

    (OUT_DIR / "export_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    if failed or not manifest["tables"]:
        print("NO TABLES EXPORTED. Nothing usable was written.", file=sys.stderr)
        return 1
    print(f"Written to {OUT_DIR}")
    print()
    print("Before committing:")
    print("  1. python export_data_scrubbed.py --verify")
    print("  2. Open each file and read it. The automated check is a backstop,")
    print("     not a substitute for looking.")
    print("  3. Cite these files from docs/metrics.md so the figures are auditable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
