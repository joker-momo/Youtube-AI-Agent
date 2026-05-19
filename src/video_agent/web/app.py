from fastapi import FastAPI

app = FastAPI(title="video-agent-web", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "app"}
