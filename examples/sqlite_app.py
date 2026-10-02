"""SQLite Backend Example for fastapi-idempotency-key.

Run this example:
    uvicorn examples.sqlite_app:app --reload

Or run directly:
    python examples/sqlite_app.py

Test with curl:
    # 1. Place order (first execution writes to SQLite idempotency.db):
    curl -i -X POST http://127.0.0.1:8000/orders \
         -H "Content-Type: application/json" \
         -H "Idempotency-Key: ord_778899" \
         -d '{"sku": "LAPTOP-M3", "quantity": 1, "price": 1299.99}'

    # 2. Replay order (replays from SQLite without re-executing):
    curl -i -X POST http://127.0.0.1:8000/orders \
         -H "Content-Type: application/json" \
         -H "Idempotency-Key: ord_778899" \
         -d '{"sku": "LAPTOP-M3", "quantity": 1, "price": 1299.99}'
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import time
from typing import Any, AsyncIterator
import uuid
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_idempotency_key import IdempotencyMiddleware, SQLiteBackend

# Initialize SQLite backend pointing to a persistent database file
sqlite_backend = SQLiteBackend(database_path="idempotency.db")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Lifespan context manager cleanly manages the SQLite connection lifecycle
    yield
    await sqlite_backend.close()


app = FastAPI(
    title="FastAPI Idempotency Key - SQLite Persistent Storage",
    description="Demonstrates durable file-backed idempotency using SQLite and WAL mode.",
    version="0.1.0",
    lifespan=lifespan,
)

# Register middleware with SQLite backend
app.add_middleware(
    IdempotencyMiddleware,
    backend=sqlite_backend,
    header_name="Idempotency-Key",
    default_ttl=604800,  # 7 days
)


class OrderRequest(BaseModel):
    sku: str
    quantity: int
    price: float


class OrderResponse(BaseModel):
    order_id: str
    sku: str
    quantity: int
    total_price: float
    created_at: float


@app.post("/orders", response_model=OrderResponse, status_code=201)
async def create_order(order: OrderRequest) -> OrderResponse:
    """Creates a new customer order.

    Idempotency state and response payload persist across server restarts
    in idempotency.db.
    """
    total = round(order.quantity * order.price, 2)
    return OrderResponse(
        order_id=f"ord_{uuid.uuid4().hex[:8]}",
        sku=order.sku,
        quantity=order.quantity,
        total_price=total,
        created_at=time.time(),
    )


@app.get("/health")
async def health_check() -> dict[str, Any]:
    return {"status": "healthy", "storage": "sqlite"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
