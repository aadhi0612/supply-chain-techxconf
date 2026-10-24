# Provenance chatbot

Ask "where is package 1124544 coming from?" and get the end-to-end trace, narrated by Amazon Bedrock, with supplier confidentiality enforced by tier.
All data is synthetic (`siliconflow/trace/graph.py`: 19 suppliers over 5 tiers, 83 lots, 600 packages).

## The confidentiality rule
Each role sees itself, its customer chain, and its **direct suppliers by name**. Everything deeper is pseudonymous.

| Viewer | Names visible | Hidden |
|---|---|---|
| OEM | Tier 1 | Tier 2 and below |
| Tier-1 | Tier 1, Tier 2 | Tier 3 and below |
| Tier-2 | Tiers 1-3 | Tier 4 and below |

A hidden node shows a keyed alias (`Undisclosed Tier-3 supplier #A5FC`), a token instead of its lot id, a region instead of a country, an ISO week instead of a date. The deepest *visible* supplier vouches for everything beneath it with an attestation (HMAC over the upstream lots, suppliers and dates), which the resolver **recomputes on every query**, so "verified" is computed, not a label. A hold deep in the chain still reaches the OEM as an alert, without a name.

## Why a prompt cannot leak names
Redaction is code (`siliconflow/trace/resolve.py`), not a prompt instruction. The model only ever receives the already-redacted trace, so there is nothing to extract. Three more layers:
1. Names or real lot ids typed in the question are replaced by `[withheld]` before the model sees them, so it can neither confirm nor deny.
2. The model's output is scanned for any term hidden from that viewer; a hit swaps the answer for a deterministic template.
3. A real lot id of a hidden lot behaves exactly like an unknown id (no existence oracle).

`tests/test_trace.py` and `tests/test_chat_handler.py` assert these, including that no hidden name appears in the context sent to Bedrock for any role.

## API
- `POST /chat` `{message, viewer}` -> `{answer, mode: model|fallback|guard, result}`
- `GET /trace/{id}?viewer=oem|tier1|tier2` -> deterministic trace, no model

If Bedrock errors, the answer falls back to a template built from the same trace, so the feature degrades instead of failing.

## Cost and abuse controls (public demo endpoint)
API Gateway throttle 2 rps / burst 5 · Lambda reserved concurrency 3 · 8 requests per IP per minute and 1,500 per day (DynamoDB counters, TTL) · 2 KB body and 500-character message caps · 600 output tokens · model: Claude Haiku 4.5 · CORS limited to the deployed site and `localhost:8777` · least-privilege IAM · logs kept 30 days · alarm on Lambda errors.

Not included, on purpose, for a demo: authentication, WAF, a VPC, a secrets manager for the pseudonym key (it lives in Terraform state and the Lambda environment), or a budget alarm with a notification address. Add those before real use.

## Deploy
```bash
chat/build.sh                       # reproducible zip: handler + dependency-free siliconflow.trace
cd chat/infra && terraform init && terraform apply     # us-west-2; prints api_url
# put api_url in ui/chat/config.js, then redeploy the static UI
pytest tests/test_trace.py tests/test_chat_handler.py
```
Terraform state is local and git-ignored. Plan files must never be committed (they can embed generated secrets).
