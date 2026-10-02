"""Route-Level Decorator Example for fastapi-idempotency-key.

Run this example:
    uvicorn examples.route_decorator:app --reload

Or run directly:
    python examples/route_decorator.py

Test with curl:
    # 1. Post to non-idempotent endpoint (every request creates a new item):
    curl -i -X POST http://127.0.0.1:8000/cart/items \
         -H "Content-Type: application/json" \
         -d '{"product_id": "prod_1", "quantity": 2}'

    # 2. Post to checkout with missing required Idempotency-Key (returns HTTP 400 Bad Request):
    curl -i -X POST http://127.0.0.1:8000/checkout \
         -H "Content-Type: application/json" \
         -d '{"cart_id": "cart_123", "payment_method": "pm_card_visa"}'

    # 3. Post to checkout with Idempotency-Key (first execution creates checkout):
    curl -i -X POST http://127.0.0.1:8000/checkout \
         -H "Content-Type: application/json" \
         -H "Idempotency-Key: chk_abc_999" \
         -d '{"cart_id": "cart_123", "payment_method": "pm_card_visa"}'

    # 4. Replay checkout (replays cached response with Idempotency-Replayed: true):
    curl -i -X POST http://127.0.0.1:8000/checkout \
         -H "Content-Type: application/json" \
         -H "Idempotency-Key: chk_abc_999" \
         -d '{"cart_id": "cart_123", "payment_method": "pm_card_visa"}'
"""

from __future__ import annotations

import time
from typing import Any, List
import uuid
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_idempotency_key import MemoryBackend, idempotent

app = FastAPI(
    title="FastAPI Idempotency Key - Route-Level Decorator Example",
    description="Demonstrates using @idempotent on critical endpoints without modifying global middleware.",
    version="0.1.0",
)

# Optional shared backend instance for decorators
shared_backend = MemoryBackend(max_keys=5_000)


class CartItem(BaseModel):
    product_id: str
    quantity: int


class CheckoutRequest(BaseModel):
    cart_id: str
    payment_method: str


class CheckoutResponse(BaseModel):
    checkout_id: str
    cart_id: str
    status: str
    completed_at: float


# In-memory store for demonstration
in_memory_cart: List[CartItem] = []


@app.post("/cart/items", status_code=201)
async def add_item_to_cart(item: CartItem) -> dict[str, Any]:
    """Standard endpoint without idempotency requirements.

    Multiple submissions add multiple items to the cart.
    """
    in_memory_cart.append(item)
    return {
        "status": "added",
        "cart_size": len(in_memory_cart),
        "item": item.model_dump(),
    }


@app.post("/checkout", response_model=CheckoutResponse, status_code=200)
@idempotent(backend=shared_backend, expire=300, required=True)
async def process_checkout(checkout: CheckoutRequest) -> CheckoutResponse:
    """Critical checkout endpoint protected by @idempotent(expire=300, required=True).

    Requires the Idempotency-Key header. Subsequent requests with the same key
    within 300 seconds (5 minutes) will replay the original response and prevent
    double charging.
    """
    return CheckoutResponse(
        checkout_id=f"chk_{uuid.uuid4().hex[:10]}",
        cart_id=checkout.cart_id,
        status="completed",
        completed_at=time.time(),
    )


@app.get("/products")
async def list_products() -> list[dict[str, Any]]:
    return [
        {"id": "prod_1", "name": "Mechanical Keyboard", "price": 129.0},
        {"id": "prod_2", "name": "Ergonomic Mouse", "price": 79.0},
    ]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
