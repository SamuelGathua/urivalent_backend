import os
import sqlite3
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Attempt dotenv loading if installed
try:
    from dotenv import load_dotenv
    # Load .env from backend root or current directory
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("urivalent.database")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class DatabaseClient:
    """
    Dual-mode database client for Urivalent.
    Supports Cloudflare D1 REST API when USE_CLOUDFLARE_D1 is true,
    and falls back seamlessly to local SQLite storage otherwise.
    Append-only: schema design strictly avoids table truncations.
    """

    def __init__(
        self,
        use_cloudflare_d1: Optional[bool] = None,
        cf_account_id: Optional[str] = None,
        cf_d1_database_id: Optional[str] = None,
        cf_api_token: Optional[str] = None,
        local_sqlite_path: Optional[str] = None,
    ):
        if use_cloudflare_d1 is not None:
            self.use_cf_d1 = use_cloudflare_d1
        else:
            self.use_cf_d1 = os.getenv("USE_CLOUDFLARE_D1", "false").strip().lower() in ("true", "1", "yes")

        self.cf_account_id = cf_account_id or os.getenv("CF_ACCOUNT_ID", "")
        self.cf_d1_database_id = cf_d1_database_id or os.getenv("CF_D1_DATABASE_ID", "")
        self.cf_api_token = cf_api_token or os.getenv("CF_API_TOKEN", "")

        raw_db_path = local_sqlite_path or os.getenv("LOCAL_SQLITE_PATH", "database/urivalent.db")
        # Ensure path is resolved relative to backend root if relative
        backend_root = Path(__file__).resolve().parent.parent
        self.sqlite_path = Path(raw_db_path) if Path(raw_db_path).is_absolute() else (backend_root / raw_db_path).resolve()

        # Check if D1 credentials are fully provided if flag is true; fallback to sqlite if missing
        if self.use_cf_d1 and (not self.cf_account_id or not self.cf_d1_database_id or not self.cf_api_token):
            logger.warning("USE_CLOUDFLARE_D1 is true, but CF credentials missing. Falling back to local SQLite.")
            self.use_cf_d1 = False

        if not self.use_cf_d1:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Initialized DatabaseClient in Local SQLite mode -> {self.sqlite_path}")
        else:
            logger.info(f"Initialized DatabaseClient in Cloudflare D1 mode -> DB ID {self.cf_d1_database_id}")

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _d1_request(self, sql: str, params: Union[list, tuple] = ()) -> Dict[str, Any]:
        """Execute a query via Cloudflare D1 REST API."""
        import requests

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/d1/database/{self.cf_d1_database_id}/query"
        headers = {
            "Authorization": f"Bearer {self.cf_api_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "sql": sql,
            "params": list(params) if params else [],
        }

        response = requests.post(url, headers=headers, json=payload, timeout=30)
        data = response.json()

        if not response.ok or not data.get("success", False):
            errors = data.get("errors", [])
            err_msg = f"Cloudflare D1 query error: {errors} (Status: {response.status_code})"
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        return data

    def execute(self, query: str, params: Union[list, tuple] = ()) -> int:
        """Execute a write/update query. Returns lastrowid or affected rows."""
        if self.use_cf_d1:
            data = self._d1_request(query, params)
            result_list = data.get("result", [])
            if result_list and isinstance(result_list, list):
                meta = result_list[0].get("meta", {})
                return meta.get("last_row_id", meta.get("changes", 0))
            return 0
        else:
            conn = self._get_sqlite_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(query, tuple(params) if params else ())
                conn.commit()
                return cursor.lastrowid or cursor.rowcount
            finally:
                conn.close()

    def fetchall(self, query: str, params: Union[list, tuple] = ()) -> List[Dict[str, Any]]:
        """Execute a read query and return list of dicts."""
        if self.use_cf_d1:
            data = self._d1_request(query, params)
            result_list = data.get("result", [])
            if result_list and isinstance(result_list, list):
                rows = result_list[0].get("results", [])
                return [dict(row) for row in rows]
            return []
        else:
            conn = self._get_sqlite_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(query, tuple(params) if params else ())
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def fetchone(self, query: str, params: Union[list, tuple] = ()) -> Optional[Dict[str, Any]]:
        """Execute a read query and return single dict or None."""
        rows = self.fetchall(query, params)
        return rows[0] if rows else None

    def executemany(self, query: str, params_list: List[Union[list, tuple]]) -> int:
        """Execute query across multiple parameter tuples."""
        if not params_list:
            return 0
        if self.use_cf_d1:
            count = 0
            for params in params_list:
                self.execute(query, params)
                count += 1
            return count
        else:
            conn = self._get_sqlite_connection()
            try:
                cursor = conn.cursor()
                cursor.executemany(query, [tuple(p) for p in params_list])
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

    def init_db(self, schema_path: Optional[str] = None) -> None:
        """
        Execute schema.sql to ensure all append-only tables and indexes exist.
        Seeds a default test subscriber if none exists.
        """
        if schema_path is None:
            resolved_schema = Path(__file__).resolve().parent / "schema.sql"
        else:
            resolved_schema = Path(schema_path)

        if not resolved_schema.exists():
            raise FileNotFoundError(f"Schema file not found at: {resolved_schema}")

        with open(resolved_schema, "r", encoding="utf-8") as f:
            schema_sql = f.read()

        logger.info(f"Executing schema from {resolved_schema}...")

        if self.use_cf_d1:
            # Cloudflare D1 query API handles statements separated by semicolons
            # Clean comments and run statements
            statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
            for stmt in statements:
                self._d1_request(stmt)
        else:
            conn = self._get_sqlite_connection()
            try:
                conn.executescript(schema_sql)
                conn.commit()
            finally:
                conn.close()

        # Seed default test subscriber
        seed_query = """
        INSERT OR IGNORE INTO users (username, email, is_active, is_verified, is_subscribed)
        VALUES (?, ?, 1, 1, 1);
        """
        self.execute(seed_query, ("test_subscriber", "test@urivalent.com"))
        logger.info("Database schema initialized and default subscriber seeded successfully.")


# Global default client singleton
db_client = DatabaseClient()
