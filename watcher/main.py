import time
import logging
from datetime import datetime, timedelta
from collections import deque

import requests
from kubernetes import client, config

# ---- Configuration ----
NAMESPACE = "default"
TARGET_DEPLOYMENT = "mock-api"
PROMETHEUS_URL = "http://prometheus-service:9090"
POLL_INTERVAL_SECONDS = 20
RESTART_THRESHOLD = 3
RESTART_WINDOW_MINUTES = 5
ERROR_RATE_THRESHOLD = 0.5  # requests/sec of 5xx, sustained
COOLDOWN_SECONDS = 60
MAX_RECOVERY_CYCLES = 3
RECOVERY_WINDOW_MINUTES = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("watcher")

# ---- K8s client setup ----
config.load_incluster_config()  # uses the mounted ServiceAccount token automatically
apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()

# ---- State tracking (in-memory, resets if the watcher pod restarts) ----
restart_events = deque()      # timestamps of observed restart-count increases
recovery_events = deque()     # timestamps of triggered recovery cycles
last_known_restart_count = None
quarantined_until = None      # if set, we're mid-cooldown
manual_intervention_required = False


def get_pod_restart_count():
    pods = core_v1.list_namespaced_pod(
        namespace=NAMESPACE, label_selector=f"app={TARGET_DEPLOYMENT}"
    )
    if not pods.items:
        return None
    # Sum restart counts across all containers in the (single) pod
    pod = pods.items[0]
    return sum(cs.restart_count for cs in pod.status.container_statuses or [])


def get_error_rate():
    query = 'rate(app_requests_total{status_code="500"}[5m])'
    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5
        )
        resp.raise_for_status()
        results = resp.json()["data"]["result"]
        if not results:
            return 0.0
        # Sum across all label combinations matching status_code=500
        return sum(float(r["value"][1]) for r in results)
    except Exception as e:
        log.warning(f"Prometheus query failed: {e}")
        return 0.0


def prune_old_events(dq, window_minutes):
    cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
    while dq and dq[0] < cutoff:
        dq.popleft()


def scale_deployment(replicas):
    body = {"spec": {"replicas": replicas}}
    apps_v1.patch_namespaced_deployment_scale(
        name=TARGET_DEPLOYMENT, namespace=NAMESPACE, body=body
    )
    log.info(f"Scaled {TARGET_DEPLOYMENT} to {replicas} replicas")


def trigger_quarantine(reason):
    global quarantined_until, manual_intervention_required

    prune_old_events(recovery_events, RECOVERY_WINDOW_MINUTES)

    if len(recovery_events) >= MAX_RECOVERY_CYCLES:
        log.error(
            f"ESCALATION: {MAX_RECOVERY_CYCLES} recovery cycles hit within "
            f"{RECOVERY_WINDOW_MINUTES}min window. Reason: {reason}. "
            f"Quarantining {TARGET_DEPLOYMENT} and requiring MANUAL intervention."
        )
        scale_deployment(0)
        manual_intervention_required = True
        return

    log.warning(f"TRIGGER: {reason}. Quarantining {TARGET_DEPLOYMENT} for {COOLDOWN_SECONDS}s.")
    scale_deployment(0)
    recovery_events.append(datetime.utcnow())
    quarantined_until = datetime.utcnow() + timedelta(seconds=COOLDOWN_SECONDS)


def main_loop():
    global last_known_restart_count, quarantined_until, manual_intervention_required

    log.info("Watcher starting up.")
    while True:
        if manual_intervention_required:
            log.info("Manual intervention flag set. Watcher idle until externally reset.")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if quarantined_until:
            if datetime.utcnow() >= quarantined_until:
                log.info("Cooldown complete. Recovering.")
                scale_deployment(1)
                quarantined_until = None
                last_known_restart_count = None  # reset baseline post-recovery
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        restart_count = get_pod_restart_count()
        if restart_count is not None:
            if last_known_restart_count is not None and restart_count > last_known_restart_count:
                restart_events.append(datetime.utcnow())
                log.info(f"Observed restart count increase: {last_known_restart_count} -> {restart_count}")
            last_known_restart_count = restart_count

        prune_old_events(restart_events, RESTART_WINDOW_MINUTES)

        error_rate = get_error_rate()

        if len(restart_events) >= RESTART_THRESHOLD:
            trigger_quarantine(f"{len(restart_events)} restarts in {RESTART_WINDOW_MINUTES}min")
            restart_events.clear()
        elif error_rate >= ERROR_RATE_THRESHOLD:
            trigger_quarantine(f"error rate {error_rate:.2f} req/s >= threshold {ERROR_RATE_THRESHOLD}")
        else:
            log.info(f"Healthy. restart_events={len(restart_events)} error_rate={error_rate:.3f}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main_loop()
