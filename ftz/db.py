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
CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, n INTEGER NOT NULL);
"""


def db_path():
    return os.environ.get("FTZ_DB", os.path.join(os.getcwd(), "ftz.db"))


def connect(path=None):
    con = sqlite3.connect(path or db_path(), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init(con):
    con.executescript(SCHEMA)
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


def audit(con, user, action, entity, entity_id, detail=None):
    con.execute(
        "INSERT INTO audit(ts,user,action,entity,entity_id,detail) VALUES(?,?,?,?,?,?)",
        (now(), user or "unknown", action, entity, str(entity_id),
         json.dumps(detail) if detail is not None else None))
