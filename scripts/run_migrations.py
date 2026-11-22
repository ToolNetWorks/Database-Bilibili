#!/usr/bin/env python3
"""
Database Migration Runner for Dual Neon PostgreSQL.

Runs migrations on both primary and standby databases.
"""

import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def get_database_urls() -> dict:
    """Get database URLs from environment."""
    return {
        "primary": os.getenv("DATABASE_PRIMARY_URL", "").strip(),
        "standby": os.getenv("DATABASE_STANDBY_URL", "").strip(),
    }


def run_migrations_on_url(database_url: str, name: str) -> bool:
    """Run all migrations on a single database."""
    if not database_url:
        print(f"SKIP {name}: No URL configured")
        return True

    print(f"\n=== Running migrations on {name} ===")

    try:
        with psycopg.connect(database_url, connect_timeout=30, row_factory=dict_row) as conn:
            # Create migrations tracking table
            with conn.transaction():
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(50) PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

            # Get applied migrations
            applied = set()
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM schema_migrations ORDER BY version")
                for row in cur.fetchall():
                    applied.add(row["version"])

            # Find migration files
            migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

            for migration_file in migration_files:
                version = migration_file.stem.split("_")[0]

                if version in applied:
                    print(f"  SKIP {migration_file.name} (already applied)")
                    continue

                print(f"  APPLY {migration_file.name}...")

                sql = migration_file.read_text(encoding="utf-8")

                try:
                    with conn.transaction():
                        conn.execute(sql)
                        conn.execute(
                            "INSERT INTO schema_migrations (version) VALUES (%s)",
                            (version,)
                        )
                    print(f"  OK {migration_file.name}")
                except Exception as e:
                    print(f"  FAILED {migration_file.name}: {e}")
                    return False

            print(f"=== {name} migrations complete ===")
            return True

    except Exception as e:
        print(f"ERROR connecting to {name}: {e}")
        return False


def main():
    """Run migrations on all configured databases."""
    urls = get_database_urls()

    if not urls["primary"]:
        print("ERROR: No primary database URL configured")
        sys.exit(1)

    success = True

    # Run on primary
    success = run_migrations_on_url(urls["primary"], "PRIMARY") and success

    # Run on standby if configured
    if urls["standby"]:
        success = run_migrations_on_url(urls["standby"], "STANDBY") and success
    else:
        print("\n=== SKIP STANDBY: Not configured ===")

    if success:
        print("\n✓ All migrations completed successfully")
        sys.exit(0)
    else:
        print("\n✗ Some migrations failed")
        sys.exit(1)


if __name__ == "__main__":
    main()