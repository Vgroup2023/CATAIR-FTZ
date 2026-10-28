"""PostgreSQL / Supabase support: SQL translation, connection safety, generated migrations, and (with a test server) locking."""
import os
import threading
import unittest

from ftz import db, pgcompat

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class TestTranslate(unittest.TestCase):
    def test_placeholders_and_percent_signs(self):
        self.assertEqual(pgcompat.translate("SELECT * FROM t WHERE a=? AND b LIKE '50%'"), "SELECT * FROM t WHERE a=%s AND b LIKE '50%%'")

    def test_insert_or_ignore(self):
        out = pgcompat.translate("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)")
        self.assertTrue(out.startswith("INSERT INTO settings(key,value) VALUES(%s,%s)") and out.endswith("ON CONFLICT DO NOTHING"))

    def test_begin_immediate_becomes_the_writer_lock(self):
        self.assertIn("pg_advisory_xact_lock", pgcompat.translate("BEGIN IMMEDIATE"))

    def test_sqlite_functions(self):
        self.assertIn("STRING_AGG(DISTINCT m.ref_no::text, ',')", pgcompat.translate("SELECT GROUP_CONCAT(DISTINCT m.ref_no) e FROM m"))
        self.assertIn("chr(10)", pgcompat.translate("SELECT remarks||char(10)"))
        self.assertIn("to_char(m.ts::timestamp", pgcompat.translate("SELECT strftime('%Y-W%W', m.ts) week"))

    def test_user_is_quoted_but_users_and_literals_are_not(self):
        out = pgcompat.translate("INSERT INTO audit(ts,user,action) VALUES(?,?,?)")
        self.assertIn('(ts,"user",action)', out)
        self.assertEqual(pgcompat.translate("SELECT * FROM users WHERE role='user'"), "SELECT * FROM users WHERE role='user'")
        self.assertIn('"user"', pgcompat.translate("UPDATE audit SET user='x'"))
        self.assertEqual(pgcompat.translate('SELECT "user" FROM audit'), 'SELECT "user" FROM audit')


class TestConnectionSafety(unittest.TestCase):
    def test_tls_is_required_for_remote_hosts_only(self):
        self.assertIn("sslmode=require", pgcompat.secure_url("postgresql://u:p@db.example.supabase.co:5432/postgres"))
        self.assertEqual(pgcompat.secure_url("postgresql://u:p@db.example.com/x?sslmode=verify-full"), "postgresql://u:p@db.example.com/x?sslmode=verify-full")
        self.assertNotIn("sslmode", pgcompat.secure_url("postgresql://u:p@127.0.0.1:5433/x"))
        with self.assertRaises(ValueError):
            pgcompat.secure_url("mysql://u:p@h/x")

    def test_the_banner_never_shows_the_password(self):
        text = pgcompat.describe("postgresql://ftz_app:SuperSecret@aws-0.pooler.supabase.com:5432/postgres")
        self.assertNotIn("SuperSecret", text)
        self.assertIn("ftz_app", text)


class TestMigrationFiles(unittest.TestCase):
    def read(self, suffix):
        d = os.path.join(ROOT, "supabase", "migrations")
        name = next(n for n in os.listdir(d) if n.endswith(suffix))
        with open(os.path.join(d, name)) as fh:
            return fh.read()

    def test_schema_migration_covers_every_table_and_column(self):
        sql = self.read("_ftz_schema.sql")
        for line in db.SCHEMA.splitlines():
            if line.startswith("CREATE TABLE IF NOT EXISTS"):
                self.assertIn(line.split("(")[0], sql)
        for table, col, _ in db.NEW_COLUMNS:
            self.assertIn(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} ", sql)
        for guard in ("lots_no_negative", "audit_no_update", "movements_no_update"):
            self.assertIn(guard, sql)
        self.assertIn("TRUNCATE", sql)

    def test_security_migration_locks_the_public_api_and_stores_no_secret(self):
        sql = self.read("_ftz_security.sql")
        for needle in ("ENABLE ROW LEVEL SECURITY", "FROM anon", "FROM authenticated", "NOSUPERUSER", "NOBYPASSRLS", "REVOKE UPDATE, DELETE, TRUNCATE ON public.audit, public.movements"):
            self.assertIn(needle, sql)
        self.assertNotRegex(sql.replace("PASSWORD '<a long random password>'", ""), r"(?i)password\s+'")


@unittest.skipUnless(pgcompat.test_url(), "set FTZ_TEST_PG_URL to run against a PostgreSQL test server")
class TestWriterLock(unittest.TestCase):
    def test_a_second_writer_waits_for_the_first_to_commit(self):
        a, b = db.connect("pg-lock-test"), db.connect("pg-lock-test")
        a.execute("BEGIN IMMEDIATE")
        got = threading.Event()

        def second():
            b.execute("BEGIN IMMEDIATE")
            got.set()
        t = threading.Thread(target=second)
        t.start()
        self.assertFalse(got.wait(0.7), "the second writer must wait while the first transaction is open")
        a.commit()
        self.assertTrue(got.wait(5), "the second writer must proceed once the first commits")
        t.join()
        b.commit()
        a.close()
        b.close()

    def test_flags_and_averages_round_trip(self):
        con = db.connect(":memory:")
        db.init(con)
        con.execute("INSERT INTO users(username,password_hash,active,is_demo,created_at) VALUES(?,?,?,?,?)", ("Mixed.Case", "x", True, False, "now"))
        row = con.execute("SELECT * FROM users WHERE username=?", ("mixed.case",)).fetchone()    # case-insensitive, like SQLite NOCASE
        self.assertEqual((row["active"], row["is_demo"], row[1]), (1, 0, "Mixed.Case"))
        self.assertIsInstance(con.execute("SELECT AVG(active) a FROM users").fetchone()["a"], float)
        con.close()


if __name__ == "__main__":
    unittest.main()
