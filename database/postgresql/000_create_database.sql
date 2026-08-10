-- Run with psql while connected to the default `postgres` database.
-- \gexec makes repeated execution safe.

SELECT 'CREATE DATABASE forge_sight_app ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'forge_sight_app')
\gexec

\connect forge_sight_app
