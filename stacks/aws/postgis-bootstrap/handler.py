# Copyright (c) Honua. All rights reserved.
# Licensed under the Elastic License 2.0. See LICENSE in the project root.
"""In-VPC PostGIS bootstrap, managed STAC seed, and query-only receipt entrypoints."""

import hashlib
import json
import os
import re
import ssl
import urllib.request

import boto3
import pg8000.native


_RDS_CA_BUNDLE = os.path.join(os.path.dirname(__file__), "rds-global-bundle.pem")
_SAFE_ENVIRONMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_IMMUTABLE_SEED_URL = re.compile(
    r"^https://raw\.githubusercontent\.com/honua-io/honua-server/"
    r"([0-9a-f]{40})/tests/seed/demo-stac-imagery-v1\.sql$"
)
_DOLLAR_QUOTE = re.compile(r"\$\$|\$[A-Za-z_][A-Za-z0-9_]*\$")
_RECEIPT_ROLE = "honua_demo_seed_receipt"


def _parse_connection_string(connection_string):
    parts = {}
    for chunk in connection_string.split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parts[key.strip()] = value.strip()
    return parts


def _read_secret(secret_arn):
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    return _parse_connection_string(secret["SecretString"])


def _connect(secret_arn):
    params = _read_secret(secret_arn)
    ssl_context = ssl.create_default_context(cafile=_RDS_CA_BUNDLE)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return pg8000.native.Connection(
        user=params["Username"],
        password=params["Password"],
        host=params["Host"],
        port=int(params.get("Port", "5432")),
        database=params["Database"],
        ssl_context=ssl_context,
        timeout=30,
    )


def _sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def _render_seed(source_bytes, environment):
    if not _SAFE_ENVIRONMENT.fullmatch(environment):
        raise ValueError("configured seed environment is invalid")
    source = source_bytes.decode("utf-8")
    begin = source.find("\nBEGIN;")
    commit = source.rfind("\nCOMMIT;")
    if begin < 0 or commit <= begin or source[commit + len("\nCOMMIT;") :].strip():
        raise ValueError("seed must contain one outer transaction boundary")
    rendered = source[begin + len("\nBEGIN;") : commit]
    rendered = rendered.replace(':"schema"', '"honua"')
    rendered = rendered.replace(":'schema'", "'honua'")
    rendered = rendered.replace(":'env'", _sql_literal(environment))
    if re.search(r"(?m)^\\", rendered) or re.search(r":[\"']", rendered):
        raise ValueError("seed still contains psql-only directives or substitutions")
    return rendered.encode("utf-8")


def _split_postgresql_statements(script):
    """Split trusted SQL without cutting quoted text, comments, or DO blocks.

    Each returned slice preserves its original whitespace and terminating
    semicolon. Concatenating the slices therefore reproduces the exact SQL
    program covered by the execution digest.
    """
    statements = []
    start = 0
    index = 0
    state = "normal"
    dollar_tag = None
    block_comment_depth = 0

    while index < len(script):
        if state == "normal":
            if script.startswith("--", index):
                state = "line-comment"
                index += 2
                continue
            if script.startswith("/*", index):
                state = "block-comment"
                block_comment_depth = 1
                index += 2
                continue

            character = script[index]
            if character == "'":
                state = "single-quote"
                index += 1
                continue
            if character == '"':
                state = "double-quote"
                index += 1
                continue
            if character == "$":
                match = _DOLLAR_QUOTE.match(script, index)
                if match:
                    dollar_tag = match.group(0)
                    state = "dollar-quote"
                    index = match.end()
                    continue
            if character == ";":
                statements.append(script[start : index + 1])
                start = index + 1
            index += 1
            continue

        if state == "line-comment":
            if script[index] in "\r\n":
                state = "normal"
            index += 1
            continue

        if state == "block-comment":
            if script.startswith("/*", index):
                block_comment_depth += 1
                index += 2
                continue
            if script.startswith("*/", index):
                block_comment_depth -= 1
                index += 2
                if block_comment_depth == 0:
                    state = "normal"
                continue
            index += 1
            continue

        if state == "single-quote":
            if script[index] == "\\" and index + 1 < len(script):
                index += 2
                continue
            if script[index] == "'":
                if index + 1 < len(script) and script[index + 1] == "'":
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "double-quote":
            if script[index] == '"':
                if index + 1 < len(script) and script[index + 1] == '"':
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "dollar-quote":
            if script.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
                state = "normal"
                continue
            index += 1

    if state not in ("normal", "line-comment"):
        raise ValueError(f"unterminated PostgreSQL {state}")

    tail = script[start:]
    if tail.strip():
        statements.append(tail)
    elif tail and statements:
        statements[-1] += tail
    return [statement for statement in statements if statement.strip()]


def _run_sql_script(connection, script):
    statements = _split_postgresql_statements(script)
    if not statements:
        raise ValueError("SQL script contains no statement")
    for statement in statements:
        connection.run(statement)


def _break_glass(event):
    connection = _connect(os.environ["DB_SECRET_ARN"])
    try:
        if not event:
            for extension in ("postgis", "postgis_raster"):
                connection.run(f"CREATE EXTENSION IF NOT EXISTS {extension}")
            rows = connection.run(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname LIKE 'postgis%' ORDER BY extname"
            )
            return {"extensions": [{"name": row[0], "version": row[1]} for row in rows]}

        allowed = {"operation", "statements", "query"}
        if event.get("operation") != "break-glass-sql" or set(event) - allowed:
            raise ValueError("break-glass bootstrap accepts only explicit break-glass-sql events")
        results = []
        for statement in event.get("statements") or []:
            connection.run(statement)
            results.append({"statement": statement[:120], "ok": True})
        payload = {"statements": results}
        if event.get("query"):
            rows = connection.run(event["query"])
            payload["rows"] = [[None if cell is None else str(cell) for cell in row] for row in rows[:1000]]
        return payload
    finally:
        connection.close()


def _managed_seed(event):
    if event != {"operation": "apply-demo-stac-seed"}:
        raise ValueError("managed seed accepts only the allowlisted operation")

    source_url = os.environ["STAC_SEED_SOURCE_URL"]
    source_sha256 = os.environ["STAC_SEED_SOURCE_SHA256"]
    server_commit = os.environ["STAC_SEED_SERVER_COMMIT"]
    environment = os.environ["STAC_SEED_METADATA_ENVIRONMENT"]
    match = _IMMUTABLE_SEED_URL.fullmatch(source_url)
    if not match or match.group(1) != server_commit:
        raise ValueError("configured seed URL and server commit are not one immutable source")

    with urllib.request.urlopen(source_url, timeout=30) as response:
        source_bytes = response.read()
    actual_source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if actual_source_sha256 != source_sha256:
        raise ValueError("downloaded seed source digest does not match the repository-pinned digest")
    execution_bytes = _render_seed(source_bytes, environment)
    execution_sha256 = hashlib.sha256(execution_bytes).hexdigest()

    receipt_params = _read_secret(os.environ["RECEIPT_DB_SECRET_ARN"])
    if receipt_params.get("Username") != _RECEIPT_ROLE:
        raise ValueError("receipt secret is not bound to the allowlisted database role")
    receipt_password = _sql_literal(receipt_params["Password"])
    connection = _connect(os.environ["DB_SECRET_ARN"])
    try:
        connection.run("BEGIN")
        try:
            # execution_sha256 covers the concatenated exact UTF-8 statement slices,
            # independently of the caller. pg8000's extended-query path accepts one
            # statement per Parse operation, so split only at PostgreSQL boundaries.
            _run_sql_script(connection, execution_bytes.decode("utf-8"))
            rows = connection.run(
                "SELECT revision FROM honua.metadata_v2_current WHERE environment = :environment",
                environment=environment,
            )
            if len(rows) != 1:
                raise RuntimeError("managed seed did not activate exactly one metadata revision")
            revision = int(rows[0][0])
            _run_sql_script(
                connection,
                """
                CREATE TABLE IF NOT EXISTS honua.demo_seed_revisions (
                    seed_id text PRIMARY KEY,
                    server_commit text,
                    source_url text,
                    source_sha256 text NOT NULL,
                    execution_sha256 text,
                    metadata_environment text NOT NULL,
                    metadata_revision bigint NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                );
                ALTER TABLE honua.demo_seed_revisions ADD COLUMN IF NOT EXISTS server_commit text;
                ALTER TABLE honua.demo_seed_revisions ADD COLUMN IF NOT EXISTS source_url text;
                ALTER TABLE honua.demo_seed_revisions ADD COLUMN IF NOT EXISTS execution_sha256 text;
                """
            )
            connection.run(
                """
                INSERT INTO honua.demo_seed_revisions
                    (seed_id, server_commit, source_url, source_sha256, execution_sha256,
                     metadata_environment, metadata_revision, applied_at)
                VALUES ('demo-stac-imagery-v1', :server_commit, :source_url, :source_sha256,
                        :execution_sha256, :environment, :revision, now())
                ON CONFLICT (seed_id) DO UPDATE SET
                    server_commit = EXCLUDED.server_commit,
                    source_url = EXCLUDED.source_url,
                    source_sha256 = EXCLUDED.source_sha256,
                    execution_sha256 = EXCLUDED.execution_sha256,
                    metadata_environment = EXCLUDED.metadata_environment,
                    metadata_revision = EXCLUDED.metadata_revision,
                    applied_at = EXCLUDED.applied_at
                """,
                server_commit=server_commit,
                source_url=source_url,
                source_sha256=source_sha256,
                execution_sha256=execution_sha256,
                environment=environment,
                revision=revision,
            )
            _run_sql_script(
                connection,
                f"""
                DO $role$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_RECEIPT_ROLE}') THEN
                        CREATE ROLE {_RECEIPT_ROLE};
                    END IF;
                END
                $role$;
                ALTER ROLE {_RECEIPT_ROLE} WITH LOGIN PASSWORD {receipt_password}
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
                DO $memberships$
                DECLARE granted_role record;
                BEGIN
                    FOR granted_role IN
                        SELECT parent.rolname
                          FROM pg_auth_members membership
                          JOIN pg_roles member ON member.oid = membership.member
                          JOIN pg_roles parent ON parent.oid = membership.roleid
                         WHERE member.rolname = '{_RECEIPT_ROLE}'
                    LOOP
                        EXECUTE format('REVOKE %I FROM {_RECEIPT_ROLE}', granted_role.rolname);
                    END LOOP;
                END
                $memberships$;
                DO $database$
                BEGIN
                    EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM {_RECEIPT_ROLE}', current_database());
                    -- PostgreSQL's default PUBLIC TEMP privilege cannot be denied to one
                    -- role while PUBLIC retains it. Remove any direct privilege here; the
                    -- Lambda executes one fixed query and accepts no caller SQL, so a temp
                    -- schema cannot be used as an escalation path through this surface.
                    EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM {_RECEIPT_ROLE}', current_database());
                    EXECUTE format('GRANT CONNECT ON DATABASE %I TO {_RECEIPT_ROLE}', current_database());
                END
                $database$;
                REVOKE ALL ON SCHEMA public FROM {_RECEIPT_ROLE};
                REVOKE ALL ON SCHEMA honua FROM {_RECEIPT_ROLE};
                GRANT USAGE ON SCHEMA honua TO {_RECEIPT_ROLE};
                REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA honua FROM {_RECEIPT_ROLE};
                REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA honua FROM {_RECEIPT_ROLE};
                REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA honua FROM {_RECEIPT_ROLE};
                ALTER DEFAULT PRIVILEGES IN SCHEMA honua REVOKE ALL ON TABLES FROM {_RECEIPT_ROLE};
                ALTER DEFAULT PRIVILEGES IN SCHEMA honua REVOKE ALL ON SEQUENCES FROM {_RECEIPT_ROLE};
                ALTER DEFAULT PRIVILEGES IN SCHEMA honua REVOKE ALL ON FUNCTIONS FROM {_RECEIPT_ROLE};
                REVOKE ALL ON honua.demo_seed_revisions, honua.metadata_v2_current FROM {_RECEIPT_ROLE};
                GRANT SELECT ON honua.demo_seed_revisions, honua.metadata_v2_current TO {_RECEIPT_ROLE};
                DO $privileges$
                BEGIN
                    IF EXISTS (
                           SELECT 1 FROM pg_class
                            WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = '{_RECEIPT_ROLE}')
                       ) OR EXISTS (
                           SELECT 1 FROM pg_namespace
                            WHERE nspowner = (SELECT oid FROM pg_roles WHERE rolname = '{_RECEIPT_ROLE}')
                       ) OR EXISTS (
                           SELECT 1 FROM pg_proc
                            WHERE proowner = (SELECT oid FROM pg_roles WHERE rolname = '{_RECEIPT_ROLE}')
                       ) OR EXISTS (
                           SELECT 1 FROM pg_database
                            WHERE datdba = (SELECT oid FROM pg_roles WHERE rolname = '{_RECEIPT_ROLE}')
                       ) OR has_schema_privilege('{_RECEIPT_ROLE}', 'honua', 'CREATE')
                       OR has_table_privilege('{_RECEIPT_ROLE}', 'honua.demo_seed_revisions', 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                       OR has_table_privilege('{_RECEIPT_ROLE}', 'honua.metadata_v2_current', 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') THEN
                        RAISE EXCEPTION 'receipt database role is not query-only or owns database objects';
                    END IF;
                END
                $privileges$;
                """
            )
            connection.run("COMMIT")
        except Exception:
            connection.run("ROLLBACK")
            raise
    finally:
        connection.close()

    return {
        "format": "honua.demo.stac-seed-attestation.v1",
        "seedId": "demo-stac-imagery-v1",
        "serverCommit": server_commit,
        "sourceUrl": source_url,
        "sourceSha256": source_sha256,
        "executionSha256": execution_sha256,
        "metadataEnvironment": environment,
        "metadataRevision": revision,
        "receiptRole": _RECEIPT_ROLE,
        "receiptRoleReconciled": True,
    }


def _receipt(event):
    if event != {"operation": "read-demo-stac-seed-receipt"}:
        raise ValueError("receipt reader accepts only its fixed query operation")
    connection = _connect(os.environ["DB_SECRET_ARN"])
    try:
        rows = connection.run(
            """
            SELECT marker.seed_id, marker.server_commit, marker.source_url,
                   marker.source_sha256, marker.execution_sha256,
                   marker.metadata_environment, marker.metadata_revision,
                   current.revision
              FROM honua.demo_seed_revisions AS marker
              JOIN honua.metadata_v2_current AS current
                ON current.environment = marker.metadata_environment
               AND current.revision = marker.metadata_revision
             WHERE marker.seed_id = 'demo-stac-imagery-v1'
            """
        )
        if len(rows) != 1:
            raise RuntimeError("no attested active demo STAC seed revision")
        row = rows[0]
        return {
            "format": "honua.demo.stac-seed-receipt.v1",
            "seedId": row[0],
            "serverCommit": row[1],
            "sourceUrl": row[2],
            "sourceSha256": row[3],
            "executionSha256": row[4],
            "metadataEnvironment": row[5],
            "metadataRevision": int(row[6]),
            "currentRevision": int(row[7]),
            "receiptRole": _RECEIPT_ROLE,
        }
    finally:
        connection.close()


def handler(event, context):
    mode = os.environ.get("OPERATION_MODE", "break-glass-bootstrap")
    if mode == "break-glass-bootstrap":
        return _break_glass(event or {})
    if mode == "managed-stac-seed":
        return _managed_seed(event or {})
    if mode == "stac-seed-receipt":
        return _receipt(event or {})
    raise ValueError("unsupported operation mode")
