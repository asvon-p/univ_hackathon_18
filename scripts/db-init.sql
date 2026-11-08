-- Runs only on first container start (empty volume).
CREATE DATABASE fitjang_test OWNER fitjang;
\connect fitjang
CREATE EXTENSION IF NOT EXISTS vector;
\connect fitjang_test
CREATE EXTENSION IF NOT EXISTS vector;
