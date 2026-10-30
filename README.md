# AI Email Triage Agent

An AI agent that reads incoming customer-support emails and drafts a reply, using Claude (Anthropic API) with tool calling and structured outputs. A human reviews the draft before anything is sent.

I'm building it step by step with Claude Code, as a hands-on project to go deeper into Python and AI agents.

## What it does today

- **`POST /emails` endpoint (FastAPI):** receives an email (`message_id`, `sender`, `subject`, `body`), validated with Pydantic.
- **Idempotency:** an email with a `message_id` it has already processed is not sent to the LLM again, so duplicates cost nothing.
- **Claude drafts the reply** (`drafter.py`), using the async Anthropic SDK and a system prompt that forbids inventing data.
- **Tool calling:** Claude can call `buscar_pedido` (order lookup) to check an order's status in `data/pedidos.csv`. The tool only returns an order when the order number **and** the sender's email match, so a customer can't see someone else's order.
- **Structured output:** the response is a JSON schema with `borrador` (the reply, ready to send) and `notas_internas` (notes for the support team).
- **Error handling:** connection errors, rate limits (429) and 5xx from the API return `503` (retry later). Other API errors return `500`. A failed email is **not** marked as seen, so it can be retried.

## Stack

Python 3.12 · FastAPI · Pydantic · Anthropic SDK (Claude) · uv

## Run it

```bash
uv sync
cp .env.example .env   # add your ANTHROPIC_API_KEY
uv run fastapi dev main.py
```

Open http://127.0.0.1:8000/docs and send a test email.

## Tests

The tests replace the LLM call with a fake, so they are free, fast and don't need internet:

```bash
uv run python test_main.py
uv run python test_errores.py
uv run python tools.py
```

## Roadmap

- [ ] Connect a real inbox (Gmail API) instead of the JSON endpoint
- [ ] Classify emails by intent and urgency before drafting
- [ ] Persist processed IDs in a database instead of memory
- [ ] Evaluate draft quality on a set of sample emails
- [ ] Deploy and add monitoring / tracing
