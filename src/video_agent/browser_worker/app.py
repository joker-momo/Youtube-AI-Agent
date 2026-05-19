from fastapi import FastAPI

app = FastAPI(title="video-agent-browser-worker", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "browser-worker"}
