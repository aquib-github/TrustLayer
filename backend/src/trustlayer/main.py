from fastapi import FastAPI

from trustlayer.api.approvals import router as approvals_router
from trustlayer.api.chat import router as chat_router

app = FastAPI(
    title="TrustLayer API",
    description="Dual-Grounding Policy Middleware for Agentic AI in High-Risk Financial Systems",
    version="0.1.0",
)

# Register API routers
app.include_router(chat_router)
app.include_router(approvals_router)



@app.get("/health")
def health():
    return {"status": "ok"}