"""Basic Memory Backend Example for fastapi-idempotency-key.

Run this example:
    uvicorn examples.basic_memory:app --reload

Or run directly:
    python examples/basic_memory.py

Test with curl:
    # 1. First request executes handler:
    curl -i -X POST http://127.0.0.1:8000/payments \
         -H "Content-Type: application/json" \
         -H "Idempotency-Key: pay_unique_001" \
         -d '{"amount": 100, "currency": "USD", "destination": "acct_456"}'

    # 2. Second identical request replays cached response (Idempotency-Replayed: true):
    curl -i -X POST http://127.0.0.1:8000/payments \
         -H "Content-Type: application/json" \
         -H "Idempotency-Key: pay_unique_001" \
         -d '{"amount": 100, "currency": "USD", "destination": "acct_456"}'

    # 3. Request with same key but different payload returns HTTP 422 Unprocessable Entity:
    curl -i -X POST http://127.0.0.1:8000/payments \
         -H "Content-Type: application/json" \
         -H "Idempotency-Key: pay_unique_001" \
         -d '{"amount": 999, "currency": "EUR", "destination": "acct_456"}'
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_idempotency_key import IdempotencyMiddleware, MemoryBackend

app = FastAPI(
    title="FastAPI Idempotency Key - Memory Backend Example",
    description="Demonstrates global IdempotencyMiddleware with an in-memory LRU store.",
    version="0.1.0",
)

# 1. Initialize an in-memory store with optional LRU capacity limit
memory_backend = MemoryBackend(max_keys=10_000)

# 2. Register IdempotencyMiddleware globally
app.add_middleware(
    IdempotencyMiddleware,
    backend=memory_backend,
    header_name="Idempotency-Key",
    replay_header_name="Idempotency-Replayed",
    default_ttl=86400,  # 24 hours
    timeout=0.5,  # Wait up to 500ms if a concurrent request is in flight
)


class PaymentRequest(BaseModel):
    amount: float
    currency: str
    destination: str


class PaymentResponse(BaseModel):
    transaction_id: str
    amount: float
    currency: str
    destination: str
    processed_at: float


@app.post("/payments", response_model=PaymentResponse, status_code=201)
async def create_payment(payment: PaymentRequest) -> PaymentResponse:
    """Creates a payment.

    If re-requested with the same Idempotency-Key and payload within 24
    hours, the exact original response is replayed without charging the
    customer again.
    """
    return PaymentResponse(
        transaction_id=f"txn_{uuid.uuid4().hex[:12]}",
        amount=payment.amount,
        currency=payment.currency,
        destination=payment.destination,
        processed_at=time.time(),
    )


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Safe GET requests bypass idempotency processing automatically."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
