from fastapi import FastAPI, Response, Request
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
import time

app = FastAPI()

# Toggle this to simulate FAILURE manully
FAILURE_MODE = {"enabled": False, "type": "none"}

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


# It serializes all registered metrics into Prometheus's plaintext format
@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
