from fastapi import FastAPI

from trustlayer.api.chat import router as chat_router

app = FastAPI(
    title="TrustLayer API",
    description="Dual-Grounding Policy Middleware for Agentic AI in High-Risk Financial Systems",
    version="0.1.0",
)

app.include_router(chat_router)


@app.get("/health")
def health():
    return {"status": "ok"}