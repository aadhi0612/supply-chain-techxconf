"""AWS Lambda (HTTP API, payload v2) for the provenance chatbot.

POST /chat            {message, viewer}  -> deterministic trace + a Bedrock narration (falls back to a templated answer)
GET  /trace/{id}      ?viewer=oem|tier1|tier2 -> deterministic trace only, no model
Cost controls: per-IP and global-daily counters in DynamoDB, request size cap, token cap. Hidden supplier names are removed in
code before the model is called (see siliconflow.trace.resolve), so prompt injection cannot extract them.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from siliconflow.trace import narrate
from siliconflow.trace.resolve import VIEWERS, Resolver

log = logging.getLogger()
log.setLevel(logging.INFO)
MODEL = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
PER_IP_MIN = int(os.environ.get("PER_IP_PER_MIN", "8"))
DAILY_CAP = int(os.environ.get("DAILY_CAP", "1500"))
MAX_MSG = 500
_rs: Resolver | None = None
_clients: dict = {}


def resolver() -> Resolver:
    global _rs
    if _rs is None:
        _rs = Resolver(secret=os.environ.get("TRACE_SECRET", "demo-secret"))
    return _rs


def _client(name: str):
    if name not in _clients:
        import boto3

        _clients[name] = boto3.client(name) if name != "dynamodb" else boto3.resource("dynamodb").Table(os.environ["TABLE"])
    return _clients[name]


def _resp(code: int, body: dict) -> dict:
    return {"statusCode": code, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body, default=str)}


def allow(table, ip: str, now: float | None = None) -> str | None:
    """Atomic counters: returns None if allowed, else the reason. Items expire via DynamoDB TTL."""
    now = now or time.time()
    for pk, limit, ttl, why in (
        (f"ip#{ip}#{int(now // 60)}", PER_IP_MIN, 120, "rate"),
        (f"day#{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d}", DAILY_CAP, 172800, "daily"),
    ):
        try:
            table.update_item(
                Key={"pk": pk},
                UpdateExpression="ADD n :one SET #ttl = :t",
                ConditionExpression="attribute_not_exists(n) OR n < :lim",
                ExpressionAttributeNames={"#ttl": "ttl"},  # ttl is a DynamoDB reserved word
                ExpressionAttributeValues={":one": 1, ":lim": limit, ":t": int(now) + ttl},
            )
        except Exception as e:  # ConditionalCheckFailedException
            if "ConditionalCheckFailed" in type(e).__name__ or "ConditionalCheckFailed" in str(e):
                return why
            raise
    return None


def narrate_with_bedrock(bedrock, result: dict, question: str, viewer: str) -> tuple[str, dict]:
    msg = f"<trace>{narrate.context(result)}</trace>\n<question>{question}</question>"
    out = bedrock.converse(
        modelId=MODEL,
        system=[{"text": narrate.system_prompt(viewer)}],
        messages=[{"role": "user", "content": [{"text": msg}]}],
        inferenceConfig={"maxTokens": 600, "temperature": 0.2},
    )
    return "".join(b.get("text", "") for b in out["output"]["message"]["content"]), out.get("usage", {})


def handle(event, _ctx=None, *, bedrock=None, table=None, rs: Resolver | None = None) -> dict:
    rs = rs or resolver()
    http = event.get("requestContext", {}).get("http", {})
    method, path, ip = http.get("method", ""), http.get("path", ""), http.get("sourceIp", "unknown")
    t0 = time.time()
    try:
        if method == "GET" and path.startswith("/trace/"):
            viewer = (event.get("queryStringParameters") or {}).get("viewer", "oem")
            if viewer not in VIEWERS:
                return _resp(400, {"error": "viewer must be oem, tier1 or tier2"})
            return _resp(200, {"result": rs.lookup(path.rsplit("/", 1)[-1][:40], viewer)})
        if method != "POST" or path != "/chat":
            return _resp(404, {"error": "not found"})
        raw = event.get("body") or ""
        if len(raw) > 2048:
            return _resp(413, {"error": "request too large"})
        try:
            body = json.loads(raw)
        except ValueError:
            return _resp(400, {"error": "invalid JSON"})
        q, viewer = str(body.get("message", "")).strip(), body.get("viewer", "oem")
        if not q or len(q) > MAX_MSG:
            return _resp(400, {"error": f"message must be 1-{MAX_MSG} characters"})
        if viewer not in VIEWERS:
            return _resp(400, {"error": "viewer must be oem, tier1 or tier2"})
        why = allow(table or _client("dynamodb"), ip)
        if why:
            return _resp(429, {"error": "Too many requests, try again in a minute." if why == "rate" else "Daily demo budget reached, try again tomorrow."})
        result = rs.lookup(q, viewer)
        safe_q, masked = narrate.sanitize_question(q, rs, viewer)
        answer, usage, mode = None, {}, "model"
        try:
            answer, usage = narrate_with_bedrock(bedrock or _client("bedrock-runtime"), result, safe_q, viewer)
            leaked = narrate.guard(answer, rs, viewer)
            if leaked:
                log.warning(json.dumps({"event": "guard_blocked", "viewer": viewer}))
                answer, mode = None, "guard"
        except Exception as e:
            log.warning(json.dumps({"event": "bedrock_error", "type": type(e).__name__}))
            mode = "fallback"
        if not answer:
            answer = narrate.fallback(result)
        if masked:
            answer = "Supplier identities below your visibility are not shared and cannot be confirmed or denied.\n\n" + answer
        log.info(json.dumps({"event": "chat", "viewer": viewer, "kind": result["kind"], "mode": mode, "ms": int((time.time() - t0) * 1000), "usage": usage}))
        return _resp(200, {"answer": answer, "mode": mode, "result": result})
    except Exception as e:
        log.exception("unhandled")
        return _resp(500, {"error": "internal error", "type": type(e).__name__})


def lambda_handler(event, ctx):
    return handle(event, ctx)
