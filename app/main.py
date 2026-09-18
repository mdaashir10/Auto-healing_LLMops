from fastapi import FastAPI, Response, Request
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
import time
import httpx

app = FastAPI()

# Toggle this to simulate FAILURE manully
FAILURE_MODE = {"enabled": False, "type": "none"}

OLLAMA_URL = "http://ollama-service:11434"
OLLAMA_MODEL = "smollm2:135m"
OLLAMA_TIMEOUT_SECONDS = 30.0

REQUEST_COUNT = Counter(
    "app_requests_total",
    "Total requests received",
    ["endpoint", "status_code"]
)
REQUEST_LATENCY = Histogram(
    "app_request_latency_seconds",
    "Request latency in seconds",
    ["endpoint"]
)
FAILURE_STATE = Gauge(
    "app_failure_mode_active",
    "Whether failure simulation is currently active (1) or not (0)"
)
INFERENCE_LATENCY = Histogram(
    "app_inference_latency_seconds",
    "Time spent waiting on Ollama inference calls"
)


class GenerateRequest(BaseModel):
    prompt: str


# Mechanism for running code around every request
@app.middleware("http")
async def track_metrics(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    REQUEST_LATENCY.labels(endpoint=request.url.path).observe(duration)
    REQUEST_COUNT.labels(
        endpoint=request.url.path,
        status_code=response.status_code
    ).inc()

    return response


@app.get("/health")
def health():
    if FAILURE_MODE["enabled"]:
        if FAILURE_MODE["type"] == "500":
            return Response(status_code=500)
        elif FAILURE_MODE["type"] == "slow":
            time.sleep(3)
    return {"status": "ok", "timestamp": time.time()}


@app.post("/simulate/{failure_type}")
def simulate(failure_type: str):
    FAILURE_MODE["enabled"] = failure_type != "none"
    FAILURE_MODE["type"] = failure_type
    FAILURE_STATE.set(1 if FAILURE_MODE["enabled"] else 0)
    return {"simulating": failure_type}


@app.post("/generate")
async def generate(req: GenerateRequest):
    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": req.prompt,
                    "stream": False
                }
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.TimeoutException:
        return Response(
            content='{"error": "Ollama request timed out"}',
            status_code=504,
            media_type="application/json"
        )
    except httpx.HTTPError as e:
        return Response(
            content=f'{{"error": "Ollama request failed: {str(e)}"}}',
            status_code=502,
            media_type="application/json"
        )
    finally:
        INFERENCE_LATENCY.observe(time.time() - start_time)

    return {"response": result.get("response", ""), "model": OLLAMA_MODEL}


# It serializes all registered metrics into Prometheus's plaintext format
@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
