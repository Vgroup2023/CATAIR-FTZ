-- Closes Supabase's public REST API for these tables and creates the least-privilege role the app connects as.
-- After applying: ALTER ROLE ftz_app PASSWORD '<a long random password>';  (run it yourself; never commit it)

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
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ftz_app') THEN
    CREATE ROLE ftz_app LOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO ftz_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ftz_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ftz_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO ftz_app;
REVOKE UPDATE, DELETE, TRUNCATE ON public.audit, public.movements FROM ftz_app;
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('DROP POLICY IF EXISTS ftz_app_all ON public.%I', t.tablename);
    EXECUTE format('CREATE POLICY ftz_app_all ON public.%I FOR ALL TO ftz_app USING (true) WITH CHECK (true)', t.tablename);
  END LOOP;
END $$;
ALTER ROLE ftz_app SET search_path = public, extensions;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
