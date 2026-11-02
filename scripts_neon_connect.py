import getpass
import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

ENV_FILE = Path("secrets/neon.env")


def main():
    print("===== NEON POSTGRESQL =====")

    if ENV_FILE.exists():
        print("Neon config đã tồn tại, không ghi đè.")
        return

    database_url = getpass.getpass(
        "Dán Neon Connection String: "
    ).strip()

    if not database_url:
        print("ERROR: Connection String rỗng.")
        return

    parsed = urlsplit(database_url)

    if parsed.scheme not in ("postgresql", "postgres"):
        print("ERROR: PostgreSQL URL không hợp lệ.")
        return

    if not parsed.hostname:
        print("ERROR: Thiếu database hostname.")
        return

    if "sslmode=require" not in database_url:
        print("WARNING: Hãy sử dụng Neon URL có sslmode=require.")
        return

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=15,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_database(), current_user"
                )
                database, username = cursor.fetchone()

    except psycopg.Error as error:
        print("CONNECTION FAILED:", type(error).__name__)
        return

    # Chỉ lưu thông tin sau khi kết nối thành công.
    fd = os.open(
        str(ENV_FILE),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(f"DATABASE_URL={database_url}\n")

    print("===== CONNECTION SUCCESS =====")
    print("Database:", database)
    print("User:", username)
    print("Neon credentials saved securely.")
    print("DONE")


if __name__ == "__main__":
    main()
