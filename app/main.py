from fastapi import FastAPI, Response
import time

app = FastAPI()

# Toggle this to simulate failure modes on demand later
FAILURE_MODE = {"enabled": False, "type": "none"}

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
    return {"simulating": failure_type}
