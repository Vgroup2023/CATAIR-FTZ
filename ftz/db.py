import hashlib
import json
import os
import sqlite3
from datetime import datetime

from .rules import DEFAULT_SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zones(
  id INTEGER PRIMARY KEY, zone_no TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  grantee TEXT, port_code TEXT, address TEXT);
CREATE TABLE IF NOT EXISTS parties(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, ident TEXT, address TEXT);
CREATE TABLE IF NOT EXISTS admissions(
  id INTEGER PRIMARY KEY, doc_no TEXT UNIQUE NOT NULL, zone_id INTEGER, operator_id INTEGER,
  importer_id INTEGER, carrier_id INTEGER, transport_mode TEXT, transport_doc TEXT,
  vessel_voyage TEXT, port_of_entry TEXT, entry_date TEXT, inbond_ref TEXT,
  census_stat INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft', cbp_ref TEXT,
  remarks TEXT, created_by TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS admission_lines(
  id INTEGER PRIMARY KEY, admission_id INTEGER NOT NULL REFERENCES admissions(id),
  line_no INTEGER, description TEXT, htsus TEXT, coo TEXT, qty REAL, uom TEXT, value REAL,
  zone_status TEXT, duty_rate REAL, marks TEXT, location TEXT);
CREATE TABLE IF NOT EXISTS lots(
  id INTEGER PRIMARY KEY, lot_no TEXT UNIQUE NOT NULL, admission_id INTEGER, line_id INTEGER,
  zone_id INTEGER, description TEXT, htsus TEXT, coo TEXT, uom TEXT, zone_status TEXT,
  unit_value REAL, duty_rate REAL, qty_admitted REAL, qty_on_hand REAL, qty_out REAL DEFAULT 0,
  location TEXT, parent_lot_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS movements(
  id INTEGER PRIMARY KEY, ts TEXT, lot_id INTEGER, kind TEXT, qty REAL, value REAL,
  ref_type TEXT, ref_no TEXT, note TEXT, user TEXT, duty REAL);
CREATE TABLE IF NOT EXISTS permits(
  id INTEGER PRIMARY KEY, permit_no TEXT UNIQUE NOT NULL, zone_id INTEGER, operator_id INTEGER,
  kind TEXT, activities TEXT, description TEXT, valid_from TEXT, valid_to TEXT,
  status TEXT NOT NULL DEFAULT 'draft', cbp_ref TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS activities(
  id INTEGER PRIMARY KEY, act_no TEXT UNIQUE NOT NULL, permit_id INTEGER, lot_id INTEGER,
  kind TEXT, qty REAL, output_qty REAL, output_desc TEXT, output_htsus TEXT, output_lot_id INTEGER,
  performed_on TEXT, location TEXT, note TEXT, expected_return TEXT, returned_qty REAL DEFAULT 0,
  returned_on TEXT, status TEXT, created_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS inbonds(
  id INTEGER PRIMARY KEY, doc_no TEXT UNIQUE NOT NULL, cbp_inbond_no TEXT, type TEXT NOT NULL,
  zone_id INTEGER, carrier_id INTEGER, consignee_id INTEGER, surety_id INTEGER, bond_ref TEXT,
  mode TEXT, origin_port TEXT, dest_port TEXT, bl_no TEXT, issued_date TEXT, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'issued', arrival_date TEXT, entry_no TEXT, export_date TEXT,
  export_carrier TEXT, foreign_dest TEXT, closed_date TEXT, remarks TEXT,
  created_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS inbond_lines(
  id INTEGER PRIMARY KEY, inbond_id INTEGER NOT NULL REFERENCES inbonds(id), line_no INTEGER,
  lot_id INTEGER, description TEXT, htsus TEXT, coo TEXT, qty REAL, uom TEXT, value REAL,
  zone_status TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT, user TEXT, action TEXT, entity TEXT, entity_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE NOT NULL, email TEXT COLLATE NOCASE,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', is_demo INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reset_tokens(
  id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS parts(
  id INTEGER PRIMARY KEY, part_no TEXT UNIQUE COLLATE NOCASE NOT NULL, description TEXT, htsus TEXT, coo TEXT,
  uom TEXT, uom2 TEXT, conv REAL, duty_rate REAL, default_status TEXT, pga_agencies TEXT,
  active INTEGER NOT NULL DEFAULT 1, source TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS wms_receipts(
  id INTEGER PRIMARY KEY, receipt_no TEXT NOT NULL, part_no TEXT NOT NULL COLLATE NOCASE, qty REAL NOT NULL, uom TEXT,
  received_on TEXT, ref TEXT COLLATE NOCASE, source TEXT, imported_at TEXT, UNIQUE(receipt_no, part_no));
CREATE TABLE IF NOT EXISTS wms_inventory(
  id INTEGER PRIMARY KEY, snapshot_on TEXT NOT NULL, part_no TEXT NOT NULL COLLATE NOCASE, qty REAL NOT NULL, uom TEXT,
  source TEXT, imported_at TEXT, UNIQUE(snapshot_on, part_no));
CREATE TABLE IF NOT EXISTS api_tokens(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, created_by TEXT, created_at TEXT,
  last_used TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS recon_runs(
  id INTEGER PRIMARY KEY, run_at TEXT, kind TEXT, run_by TEXT, issues INTEGER, summary TEXT);
"""


def db_path():
    return os.environ.get("FTZ_DB", os.path.join(os.getcwd(), "ftz.db"))


def connect(path=None):
    con = sqlite3.connect(path or db_path(), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


# Columns added after the first release: applied to old databases and new ones alike.
NEW_COLUMNS = [
    ("admission_lines", "part_no", "TEXT"), ("admission_lines", "qty2", "REAL"), ("admission_lines", "uom2", "TEXT"),
    ("admission_lines", "pga_ref", "TEXT"),
    ("lots", "part_no", "TEXT"), ("lots", "received_on", "TEXT"), ("lots", "uom2", "TEXT"),
    ("lots", "qty2_admitted", "REAL"), ("lots", "qty2_on_hand", "REAL"), ("lots", "qty2_out", "REAL"),
    ("movements", "qty2", "REAL"), ("movements", "prev_hash", "TEXT"), ("movements", "hash", "TEXT"),
    ("audit", "prev_hash", "TEXT"), ("audit", "hash", "TEXT"),
    ("activities", "qty2", "REAL"),
    ("inbond_lines", "qty2", "REAL"), ("inbond_lines", "part_no", "TEXT"),
]

# Database-level safeguards: they hold even if application code has a bug.
TRIGGERS = """
DROP TRIGGER IF EXISTS lots_no_negative;
CREATE TRIGGER lots_no_negative BEFORE UPDATE ON lots
WHEN NEW.qty_on_hand < -0.00005 OR NEW.qty_out < -0.00005 OR COALESCE(NEW.qty2_on_hand,0) < -0.00005 OR COALESCE(NEW.qty2_out,0) < -0.00005
BEGIN SELECT RAISE(ABORT, 'negative inventory blocked: this would remove stock that is not in the zone'); END;
DROP TRIGGER IF EXISTS lots_no_negative_ins;
CREATE TRIGGER lots_no_negative_ins BEFORE INSERT ON lots
WHEN NEW.qty_on_hand < 0 OR NEW.qty_out < 0
BEGIN SELECT RAISE(ABORT, 'negative inventory blocked'); END;
DROP TRIGGER IF EXISTS audit_no_update;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
DROP TRIGGER IF EXISTS audit_no_delete;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
DROP TRIGGER IF EXISTS movements_no_update;
CREATE TRIGGER movements_no_update BEFORE UPDATE ON movements BEGIN SELECT RAISE(ABORT, 'the inventory ledger is append-only'); END;
DROP TRIGGER IF EXISTS movements_no_delete;
CREATE TRIGGER movements_no_delete BEFORE DELETE ON movements BEGIN SELECT RAISE(ABORT, 'the inventory ledger is append-only'); END;
"""


def _migrate(con):
    for table, col, decl in NEW_COLUMNS:
        if col not in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Lots created before receipt dates existed: use the admission date (or creation day).
    con.execute("""UPDATE lots SET received_on=COALESCE((SELECT entry_date FROM admissions a WHERE a.id=lots.admission_id),
                   substr(created_at,1,10)) WHERE received_on IS NULL""")
    con.executescript(TRIGGERS)


def init(con):
    con.executescript(SCHEMA)
    _migrate(con)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    con.commit()


def now():
    return datetime.now().isoformat(timespec="seconds")


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def next_no(con, prefix, year):
    """Sequential document number such as e214-2026-0001."""
    name = f"{prefix}-{year}"
    con.execute("INSERT OR IGNORE INTO counters(name,n) VALUES(?,0)", (name,))
    con.execute("UPDATE counters SET n=n+1 WHERE name=?", (name,))
    n = con.execute("SELECT n FROM counters WHERE name=?", (name,)).fetchone()[0]
    return f"{prefix}-{year}-{n:04d}"


def _norm(v):
    if isinstance(v, bool) or v is None:
        return "" if v is None else str(v)
    if isinstance(v, (int, float)):
        return repr(float(v))
    return str(v)


def chain_hash(prev, *parts):
    """SHA-256 over the previous row's hash and this row's fields: editing any earlier row breaks every later hash."""
    return hashlib.sha256((prev + "\x1f" + "\x1f".join(_norm(p) for p in parts)).encode()).hexdigest()


def last_hash(con, table):
    r = con.execute(f"SELECT hash FROM {table} WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else ""


AUDIT_FIELDS = ("ts", "user", "action", "entity", "entity_id", "detail")
MOVE_FIELDS = ("ts", "lot_id", "kind", "qty", "qty2", "value", "ref_type", "ref_no", "note", "user", "duty")


def audit(con, user, action, entity, entity_id, detail=None):
    ts, user = now(), user or "unknown"
    detail = json.dumps(detail, default=str) if detail is not None else None
    prev = last_hash(con, "audit")
    h = chain_hash(prev, ts, user, action, entity, str(entity_id), detail)
    con.execute("INSERT INTO audit(ts,user,action,entity,entity_id,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?)",
                (ts, user, action, entity, str(entity_id), detail, prev, h))
