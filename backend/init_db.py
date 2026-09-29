"""
Database Initialization Module
Groundwater Usage Meter with Pay-on-Excess

Reads schema.sql and executes DDL to create devices, readings, and bills tables in PostgreSQL.
Prints created tables and seeded device configuration.
"""

import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("init_db")

# 1. Load .env file from search paths
search_dirs = [
    Path.cwd(),
    Path.cwd().parent,
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent.parent
]
env_loaded = False
for d in search_dirs:
    candidate = d / ".env"
    if candidate.is_file():
        load_dotenv(dotenv_path=candidate, override=False)
        logger.info(f"Loaded environment variables from: {candidate}")
        env_loaded = True
        break

if not env_loaded:
    load_dotenv()


def init_database() -> bool:
    """
    Connects to the PostgreSQL database defined in DATABASE_URL,
    executes schema.sql, and verifies created tables and seeded devices.
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url or database_url.startswith("postgresql://user:password@host"):
        logger.error(
            "FAIL [Config]: DATABASE_URL is not set or has placeholder value.\n"
            "  Please set DATABASE_URL in your .env file with your Neon / Supabase connection string."
        )
        return False

    schema_file = Path(__file__).resolve().parent / "schema.sql"
    if not schema_file.exists():
        logger.error(f"FAIL: Schema file not found at {schema_file}")
        return False

    try:
        import psycopg2
    except ImportError:
        logger.error(
            "FAIL: psycopg2-binary is not installed.\n"
            "  Please run: pip install psycopg2-binary"
        )
        return False

    logger.info("Connecting to PostgreSQL database...")
    try:
        # Neon and Supabase require SSL. If not specified in the URL, enforce sslmode=require.
        connect_kwargs = {}
        if "sslmode=" not in database_url:
            connect_kwargs["sslmode"] = "require"

        conn = psycopg2.connect(database_url, **connect_kwargs)
        conn.autocommit = False

        schema_sql = schema_file.read_text(encoding="utf-8-sig").strip()

        with conn.cursor() as cur:
            logger.info("Executing schema.sql DDL statements...")
            cur.execute(schema_sql)
            conn.commit()

            # Query and display created tables
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                  AND table_name IN ('devices', 'readings', 'bills')
                ORDER BY table_name;
            """)
            tables = [row[0] for row in cur.fetchall()]
            logger.info(f"Active public tables verified: {', '.join(tables)}")

            # Query and display seeded device rows
            cur.execute("SELECT id, name, monthly_limit_l, rate_per_l FROM devices ORDER BY id;")
            devices = cur.fetchall()
            logger.info("==================================================")
            logger.info("   DATABASE INITIALIZATION: SUCCESS")
            logger.info("==================================================")
            logger.info(f"Seeded Devices ({len(devices)}):")
            for dev in devices:
                logger.info(f"  - Device ID: {dev[0]} | Name: {dev[1]} | Monthly Limit: {dev[2]} L | Rate: Rs {dev[3]}/L")

        conn.close()
        return True

    except psycopg2.OperationalError as op_err:
        logger.error(f"FAIL [Database Connection]: Operational error connecting to PostgreSQL:\n  {op_err}")
        return False
    except Exception as ex:
        logger.error(f"FAIL [Database Schema]: Error executing schema: {ex}", exc_info=True)
        return False


if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)
