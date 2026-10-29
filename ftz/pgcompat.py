"""PostgreSQL (Supabase) back end for the same service code that runs on SQLite.

The application keeps writing SQLite-flavoured SQL (`?` placeholders, `INSERT OR IGNORE`, `lastrowid`, ...). This module wraps a
psycopg 3 connection so those calls work unchanged, and builds the Postgres schema, triggers and security setup from the single
SQLite schema in db.py, so the two back ends cannot drift apart.

Selected with the DATABASE_URL environment variable (a postgresql:// connection string). Nothing here runs unless it is set.
"""
import atexit
import hashlib
import os
import re
import threading
import uuid
from urllib.parse import parse_qs, urlparse

try:
    import psycopg
    from psycopg import errors as pg_errors
    from psycopg.adapt import Dumper, Loader
    HAVE_PSYCOPG = True
except ImportError:                                       # SQLite-only installs do not need it
    psycopg = None
    HAVE_PSYCOPG = False
try:
    from psycopg_pool import ConnectionPool
except ImportError:
    ConnectionPool = None

WRITE_LOCK_KEY = 7214                                     # one writer at a time, like SQLite: keeps FIFO, counters and hash chains consistent
APP_ROLE = "ftz_app"


# ----------------------------------------------------------------------------------------------- connection string
def database_url():
    return (os.environ.get("DATABASE_URL") or os.environ.get("FTZ_DATABASE_URL") or "").strip()


def test_url():
    return (os.environ.get("FTZ_TEST_PG_URL") or "").strip()


def enabled():
    return bool(database_url() or test_url())


def secure_url(url):
    """Supabase only accepts TLS. Require it for every host except a local test server, and never print the password."""
    u = urlparse(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise ValueError("DATABASE_URL must start with postgresql://")
    local = (u.hostname or "") in ("localhost", "127.0.0.1", "::1", "") or (u.hostname or "").startswith("/")
    q = parse_qs(u.query)
    if not local and "sslmode" not in q:
        url += ("&" if u.query else "?") + "sslmode=require"
    return url


def describe(url):
    u = urlparse(url)
    return f"{u.hostname}:{u.port or 5432}/{(u.path or '/').lstrip('/')} as {u.username}"


# ----------------------------------------------------------------------------------------------- rows and cursors
class Row(dict):
    """Like sqlite3.Row: readable by column name or by position, and dict(row) works."""
    def __getitem__(self, k):
        if isinstance(k, int):
            return list(self.values())[k]
        return super().__getitem__(k)


def _row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    return lambda values: Row(zip(names, values))


class Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount


if HAVE_PSYCOPG:
    class _BoolAsInt(Dumper):                             # the schema keeps flags in INTEGER columns, as SQLite does
        oid = psycopg.postgres.types["int2"].oid

        def dump(self, obj):
            return b"1" if obj else b"0"

    class _NumericAsFloat(Loader):                        # AVG() and friends: JSON cannot carry Decimal
        def load(self, data):
            return float(bytes(data).decode())


# ----------------------------------------------------------------------------------------------- SQL translation
_cache = {}
_NO_ID = {"settings", "counters"}                         # tables without an `id` column: no RETURNING id


def translate(sql):
    hit = _cache.get(sql)
    if hit:
        return hit
    s = sql
    s = re.sub(r"\bBEGIN IMMEDIATE\b", f"SELECT pg_advisory_xact_lock({WRITE_LOCK_KEY})", s)
    ignore = re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO", s, re.I)
    if ignore:
        s = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", s, count=1, flags=re.I).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    s = re.sub(r"GROUP_CONCAT\(\s*DISTINCT\s+([\w.]+)\s*\)", r"STRING_AGG(DISTINCT \1::text, ',')", s, flags=re.I)
    s = re.sub(r"GROUP_CONCAT\(\s*([\w.]+)\s*\)", r"STRING_AGG(\1::text, ',')", s, flags=re.I)
    s = re.sub(r"strftime\(\s*'%Y-W%W'\s*,\s*([\w.]+)\s*\)", r"to_char(\1::timestamp, 'IYYY-\"W\"IW')", s, flags=re.I)
    s = re.sub(r"\bchar\((\d+)\)", r"chr(\1)", s, flags=re.I)
    s = re.sub(r"(?<![\w.\"'])user(?![\w\"'(])", '"user"', s)          # USER is reserved in Postgres
    s = s.replace("%", "%%").replace("?", "%s")
    _cache[sql] = s
    return s


class PgConn:
    """The slice of the sqlite3.Connection API the service layer uses."""
    def __init__(self, raw, release=None, schema=None):
        self._c, self._release, self._schema = raw, release, schema
        self._locked = False
        # production: one global writer lock. Tests: one per throw-away schema, so a leaked transaction cannot block other tests
        self._key = WRITE_LOCK_KEY if not schema else int(hashlib.sha1(schema.encode()).hexdigest()[:7], 16)

    def execute(self, sql, params=()):
        t = translate(sql)
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", t, re.I)
        if m and m.group(1).lower() not in _NO_ID and "RETURNING" not in t.upper() and "ON CONFLICT DO NOTHING" not in t:
            cur = self._exec(t + " RETURNING id", params)
            row = cur.fetchone()
            return Cursor(cur, row[0] if row else None)
        cur = self._exec(t, params)
        return Cursor(cur)

    def _exec(self, t, params):
        verb = t.lstrip()[:12].upper()
        if verb.startswith(("INSERT", "UPDATE", "DELETE")) and not self._locked:
            self._c.execute(f"SELECT pg_advisory_xact_lock({self._key})")
            self._locked = True
        if t.lstrip().upper().startswith("SELECT PG_ADVISORY_XACT_LOCK"):
            t = t.replace(f"({WRITE_LOCK_KEY})", f"({self._key})")
            self._locked = True
        return self._c.execute(t, params)

    @property
    def in_transaction(self):
        return self._c.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def executescript(self, script):
        self._c.execute(script)

    def commit(self):
        self._c.commit()
        self._locked = False

    def rollback(self):
        self._c.rollback()
        self._locked = False

    def close(self):
        try:
            if self._c.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                self._c.rollback()
        except Exception:
            pass
        self._locked = False
        if self._release:
            self._release(self._c)
        else:
            self._c.close()


# ----------------------------------------------------------------------------------------------- connecting
_pool = None
_pool_lock = threading.Lock()
_test_schemas = []
_test_conns = []


def _configure(raw):
    raw.row_factory = _row_factory
    raw.prepare_threshold = None                           # works behind Supabase's pooler (Supavisor / PgBouncer)
    raw.adapters.register_dumper(bool, _BoolAsInt)
    raw.adapters.register_loader("numeric", _NumericAsFloat)


def _get_pool(url):
    global _pool
    with _pool_lock:
        if _pool is None:
            if ConnectionPool is None:
                raise RuntimeError("install psycopg_pool (pip install 'psycopg[binary,pool]')")
            size = int(os.environ.get("FTZ_DB_POOL", "8"))
            _pool = ConnectionPool(secure_url(url), min_size=1, max_size=size, timeout=20, configure=_configure, open=True,
                                   kwargs={"application_name": "ftz-control"})
        return _pool


def connect(path=None):
    """Open a connection. In test mode (FTZ_TEST_PG_URL) every distinct `path` gets its own throw-away schema."""
    if not HAVE_PSYCOPG:
        raise RuntimeError("PostgreSQL support needs psycopg: pip install 'psycopg[binary,pool]'")
    if test_url():
        key = str(path if path is not None else (os.environ.get("FTZ_DB") or ":memory:"))
        schema = "t_" + (uuid.uuid4().hex[:12] if key == ":memory:" else hashlib.sha1(key.encode()).hexdigest()[:12])
        raw = psycopg.connect(secure_url(test_url()), autocommit=True)
        _configure(raw)
        fresh = schema not in _test_schemas
        if fresh:
            raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            raw.execute(f'CREATE SCHEMA "{schema}"')
            _test_schemas.append(schema)
        raw.execute(f'SET search_path TO "{schema}", public')
        raw.autocommit = False
        _test_conns.append(raw)
        return PgConn(raw, schema=schema)
    pool = _get_pool(database_url())
    raw = pool.getconn()
    return PgConn(raw, release=pool.putconn)


@atexit.register
def _cleanup():
    if not test_url() or not _test_schemas:
        return
    for raw in _test_conns:                                # an open transaction would block DROP SCHEMA
        try:
            raw.close()
        except Exception:
            pass
    try:
        with psycopg.connect(secure_url(test_url()), autocommit=True) as c:
            for s in _test_schemas:
                c.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
    except Exception:
        pass


# ----------------------------------------------------------------------------------------------- start-up diagnosis
def explain(message):
    """Plain-language reason for a connection failure, from the server's own message. Returns (text, retryable)."""
    m = str(message)
    if "ECIRCUITBREAKER" in m:
        return ("Supabase is temporarily blocking logins from this host after repeated failures. Wait about 10 minutes; "
                "it clears by itself once the password is right", True)
    if "password authentication failed" in m:
        return ("The database refused the login: the password in DATABASE_URL does not match the password set for the "
                "ftz_app role. Run ALTER ROLE ftz_app PASSWORD '...' in the Supabase SQL editor with the same password, "
                "then update the DATABASE_URL secret", False)
    if "Tenant or user not found" in m:
        return ("The pooler does not know this user or project. The user must be written ftz_app.<project-ref> and the host must "
                "be the Session pooler host shown under Connect in the Supabase dashboard", False)
    if "could not translate host name" in m or "Name or service not known" in m:
        return ("The database host name could not be found: check the host in DATABASE_URL", False)
    if "timeout" in m.lower() or "timed out" in m.lower():
        return ("The database did not answer in time: check the host and port in DATABASE_URL and that the project is running", True)
    return ("Could not connect to the database: " + re.sub(r"://[^@\s]*@", "://***@", m).strip().splitlines()[0][:300], True)


def preflight(url=None, tries=3, pause=20, sleep=None, log=print):
    """Try one plain connection before the pool starts, so a wrong password is reported clearly (and not retried in a tight loop,
    which would trip Supabase's login circuit breaker). Returns None when it works, else the explanation text."""
    import time
    sleep = sleep or time.sleep
    url = url or database_url()
    last = None
    for i in range(tries):
        try:
            with psycopg.connect(secure_url(url), connect_timeout=10) as c:
                c.execute("SELECT 1")
            return None
        except Exception as ex:
            text, retry = explain(ex)
            last = text
            log(f"database check {i + 1}/{tries}: {text}")
            if not retry:
                return text
            if i + 1 < tries:
                sleep(pause)
    return last


def integrity_errors():
    return (pg_errors.IntegrityError,) if HAVE_PSYCOPG else ()


def message_of(ex):
    d = getattr(ex, "diag", None)
    return (d.message_primary if d is not None and d.message_primary else str(ex))


# ----------------------------------------------------------------------------------------------- schema
def schema_sql(sqlite_schema, new_columns):
    """Postgres DDL derived from the SQLite schema in db.py."""
    s = sqlite_schema
    s = re.sub(r"(\w+) TEXT([^,;()]*?) COLLATE NOCASE", r"\1 CITEXT\2", s)             # case-insensitive keys (user names, part numbers)
    s = s.replace("INTEGER PRIMARY KEY", "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"\buser TEXT", '"user" TEXT', s)
    out = [s.strip(), ""]
    for table, col, decl in new_columns:
        decl = re.sub(r"\bREAL\b", "DOUBLE PRECISION", decl)
        out.append(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {decl};")
    out.append(ROUND_FN)
    out.append(TRIGGERS_PG)
    return "\n".join(x for x in out if x)


ROUND_FN = """
CREATE OR REPLACE FUNCTION round(double precision, integer) RETURNS double precision
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, public AS $$ SELECT round($1::numeric, $2)::double precision $$;
"""

TRIGGERS_PG = """
CREATE OR REPLACE FUNCTION ftz_lots_no_negative() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.qty_on_hand < -0.00005 OR NEW.qty_out < -0.00005 OR COALESCE(NEW.qty2_on_hand,0) < -0.00005 OR COALESCE(NEW.qty2_out,0) < -0.00005 THEN
    RAISE EXCEPTION 'negative inventory blocked: this would remove stock that is not in the zone' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION ftz_lots_no_negative_ins() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.qty_on_hand < 0 OR NEW.qty_out < 0 THEN
    RAISE EXCEPTION 'negative inventory blocked' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION ftz_append_only() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = '23514';
END $$;
DROP TRIGGER IF EXISTS lots_no_negative ON lots;
CREATE TRIGGER lots_no_negative BEFORE UPDATE ON lots FOR EACH ROW EXECUTE FUNCTION ftz_lots_no_negative();
DROP TRIGGER IF EXISTS lots_no_negative_ins ON lots;
CREATE TRIGGER lots_no_negative_ins BEFORE INSERT ON lots FOR EACH ROW EXECUTE FUNCTION ftz_lots_no_negative_ins();
DROP TRIGGER IF EXISTS audit_no_update ON audit;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION ftz_append_only('the audit log is append-only');
DROP TRIGGER IF EXISTS audit_no_truncate ON audit;
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit FOR EACH STATEMENT EXECUTE FUNCTION ftz_append_only('the audit log is append-only');
DROP TRIGGER IF EXISTS movements_no_update ON movements;
CREATE TRIGGER movements_no_update BEFORE UPDATE OR DELETE ON movements FOR EACH ROW EXECUTE FUNCTION ftz_append_only('the inventory ledger is append-only');
DROP TRIGGER IF EXISTS movements_no_truncate ON movements;
CREATE TRIGGER movements_no_truncate BEFORE TRUNCATE ON movements FOR EACH STATEMENT EXECUTE FUNCTION ftz_append_only('the inventory ledger is append-only');
"""

# Supabase exposes every table in `public` through its REST API to the `anon` and `authenticated` roles. This app never uses that
# API, so it is closed: row level security on with no policy for those roles, and no table privileges for them either.
SECURITY_SQL = f"""
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM authenticated;
  END IF;
END $$;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
    CREATE ROLE {APP_ROLE} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO {APP_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {APP_ROLE};
REVOKE UPDATE, DELETE, TRUNCATE ON public.audit, public.movements FROM {APP_ROLE};
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('DROP POLICY IF EXISTS ftz_app_all ON public.%I', t.tablename);
    EXECUTE format('CREATE POLICY ftz_app_all ON public.%I FOR ALL TO {APP_ROLE} USING (true) WITH CHECK (true)', t.tablename);
  END LOOP;
END $$;
ALTER ROLE {APP_ROLE} SET search_path = public, extensions;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
"""
