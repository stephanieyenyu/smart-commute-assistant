Scrubbed exports backing docs/metrics.md.

Produce with:

    export DATABASE_URL="postgresql+psycopg2://..."
    python scripts/export_data_scrubbed.py
    python scripts/export_data_scrubbed.py --verify

Writes commute_logs.json, api_health_logs.json and export_manifest.json with
home addresses, coordinates and LINE User IDs removed. Read the output before
committing it; the automated check is a backstop, not a substitute.
