###############################################################################
# Feature streaming — snapshot payload budget and the controlled-conformance
# surface (honua-server#3181, honua-server#3038 REQ-005, honua-demo-infra#67).
#
# Why this file exists
# -------------------
# demo runs Honua as a Lambda behind API Gateway / CloudFront. That path
# BUFFERS the whole response before returning it and caps one invoke response
# at ~6 MB. Anything larger is discarded and the gateway substitutes its own
# untyped `{"message":"Internal Server Error"}` — a 500 the server never
# emitted. Measured on the live demo (2026-08-17):
#
#   /rest/services/maui-parcels/FeatureServer/1/query?resultRecordCount=7000
#     -> HTTP 200, 5,183,111 bytes
#   ...&resultRecordCount=9000
#     -> HTTP 500, 35 bytes, {"message":"Internal Server Error"}
#
# The same size dependence is what made snapshot subscriptions look broken in
# honua-server#3181: the server logged `responded 200`, the gateway returned
# 500. Nothing in the server produced it, and the server's own RFC 7807
# problem documents still pass through that hop intact.
#
# honua-server#3206 bounds one baseline snapshot with
# `FeatureStreaming:MaxSnapshotBytes` (server default 4 MiB). This file sets
# it EXPLICITLY and lower, because the default is not sufficient here:
#
#   * the budget bounds one baseline, not the response. In
#     `snapshot-then-delta` the delta frames keep appending to the SAME
#     response body, so a 4 MiB baseline plus enough deltas still crosses the
#     6 MB ceiling behind a buffering hop and reproduces the identical untyped
#     500.
#   * 2 MiB therefore leaves ~4 MB of headroom for the delta stream and the
#     SSE framing overhead charged against the same invoke response.
#
# This is a bound, not a fix. The underlying buffered-response architecture is
# honua-demo-infra#67 (REQ-003 there decides whether demo keeps the buffered
# Lambda path at all — an SSE subscription that stays open delivers nothing to
# the client until the invoke ends, which is a separate blocker for live
# streaming evidence).
#
# The controlled-conformance surface
# ----------------------------------
# honua-server#3038 REQ-005 adds a bounded mutation workflow so a scheduled SDK
# evidence run can drive ONE correlated create/update/delete against a live
# deployment and observe it on every advertised transport. It is off by default
# in the server and stays off here until an operator has provisioned a
# DEDICATED source — see runbook/streaming-snapshot-conformance.md for the
# provisioning steps and the two constraints that decide whether it can work at
# all (a small layer, and an operator-issued credential for the admin-scoped
# `ConformanceMutate` policy).
###############################################################################

locals {
  # Emitted unconditionally: the payload budget must be explicit on this
  # deployment, never inherited from the server default (see the header).
  feature_streaming_budget_environment = {
    FeatureStreaming__MaxSnapshotBytes = tostring(var.streaming_max_snapshot_bytes)
  }

  # Fails closed exactly like the server does: no source configured means the
  # mutation surface refuses every caller, however authorized.
  feature_streaming_conformance_environment = var.streaming_conformance_enabled ? {
    FeatureStreaming__Conformance__Enabled   = "true"
    FeatureStreaming__Conformance__ServiceId = var.streaming_conformance_service_id
    FeatureStreaming__Conformance__LayerId   = tostring(var.streaming_conformance_layer_id)
  } : {}

  feature_streaming_environment = merge(
    local.feature_streaming_budget_environment,
    local.feature_streaming_conformance_environment
  )
}
