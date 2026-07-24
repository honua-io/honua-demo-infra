# Copyright (c) Honua. All rights reserved.
# Licensed under the Elastic License 2.0. See LICENSE in the project root.
#
# One-shot, in-VPC PostGIS bootstrap for the aws-demo stack.
#
# Honua's first schema migration (001_CreateHonuaSchema.sql) creates GEOMETRY
# columns and therefore requires the postgis extension to already exist, but
# the demo RDS instance lives in private subnets with no NAT/VPN — nothing
# outside the VPC can run `CREATE EXTENSION`. This Lambda runs inside the VPC,
# reads the connection string from Secrets Manager (via the Secrets Manager
# interface endpoint), and idempotently enables postgis + postgis_raster as
# the RDS master user. It is invoked once by Terraform (aws_lambda_invocation)
# right after the database is created.

import os
import ssl

import boto3
import pg8000.native

# Amazon RDS global CA bundle, vendored into the deployment zip at build time
# (see postgis-bootstrap.tf). Sits next to this module at the Lambda task root.
_RDS_CA_BUNDLE = os.path.join(os.path.dirname(__file__), "rds-global-bundle.pem")


def _parse_connection_string(connection_string):
    """Parse an ADO.NET-style 'Key=Value;Key=Value' connection string."""
    parts = {}
    for chunk in connection_string.split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parts[key.strip()] = value.strip()
    return parts


def handler(event, context):
    secret_arn = os.environ["DB_SECRET_ARN"]
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    params = _parse_connection_string(secret["SecretString"])

    # RDS PostgreSQL 15 defaults to rds.force_ssl=1. Verify the RDS server
    # certificate (chain + hostname) against the vendored Amazon RDS global CA
    # bundle so master credentials are never sent over an unverified channel.
    ssl_context = ssl.create_default_context(cafile=_RDS_CA_BUNDLE)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED

    connection = pg8000.native.Connection(
        user=params["Username"],
        password=params["Password"],
        host=params["Host"],
        port=int(params.get("Port", "5432")),
        database=params["Database"],
        ssl_context=ssl_context,
        timeout=30,
    )
    try:
        # Maintenance mode: the demo seeding workflow invokes this Lambda with
        # explicit statements (and an optional trailing query) because the RDS
        # instance is reachable only from inside the VPC. Invocation is gated
        # by IAM (lambda:InvokeFunction), the same trust boundary as the
        # original bootstrap behavior.
        statements = (event or {}).get("statements")
        query = (event or {}).get("query")
        if statements or query:
            results = []
            for statement in statements or []:
                connection.run(statement)
                results.append({"statement": statement[:120], "ok": True})
            payload = {"statements": results}
            if query:
                rows = connection.run(query)
                payload["rows"] = [
                    [None if cell is None else str(cell) for cell in row]
                    for row in rows[:1000]
                ]
            return payload

        for extension in ("postgis", "postgis_raster"):
            connection.run(f"CREATE EXTENSION IF NOT EXISTS {extension}")
        rows = connection.run(
            "SELECT extname, extversion FROM pg_extension WHERE extname LIKE 'postgis%' ORDER BY extname"
        )
    finally:
        connection.close()

    return {"extensions": [{"name": row[0], "version": row[1]} for row in rows]}
