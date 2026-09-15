-- Runs once, when the local development volume is first created.
-- Production databases are provisioned separately; the Alembic migration also
-- creates the extension (requires rds_superuser on AWS RDS).
CREATE EXTENSION IF NOT EXISTS vector;

-- Isolated database for the automated test suite so tests never touch dev data.
CREATE DATABASE support_test;
\connect support_test
CREATE EXTENSION IF NOT EXISTS vector;
