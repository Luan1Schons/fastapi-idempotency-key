import uuid

from fastapi import FastAPI

from fastapi_idempotency_key import IdempotencyMiddleware, MemoryBackend

app = FastAPI()
app.add_middleware(IdempotencyMiddleware, backend=MemoryBackend())


@app.post("/payments", status_code=201)
async def pay(payment: dict) -> dict:
    # 💳 charge the customer
    return {"txn": f"txn_{uuid.uuid4().hex[:8]}", **payment}
