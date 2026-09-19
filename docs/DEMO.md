# Demo Walkthrough

A live, end-to-end demonstration of the full system: real LLM inference, 
then automated failure detection and recovery, with observability visible 
throughout.

**Prerequisites**: cluster running, all 5 pods `1/1 Running` 
(`kubectl get pods`).

## Setup - three terminals

```bash
# Terminal 1: Grafana (view dashboard at localhost:3000)
kubectl port-forward svc/grafana-service 3000:3000

# Terminal 2: mock-api (the app under test)
kubectl port-forward svc/mock-api-service 8000:8000

# Terminal 3: watcher logs (live commentary)
kubectl logs -f deployment/watcher
```

## Part 1 - Real inference

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Say hello in one sentence."}'
```

Expect a real, locally-generated response from `smollm2:135m` in 
~4-17 seconds (first call after idle is slower - Ollama reloads the model 
into memory after a 5-minute idle timeout).

## Part 2 - Inject a failure

```bash
curl -X POST http://localhost:8000/simulate/500
for i in {1..40}; do curl -s http://localhost:8000/health > /dev/null; sleep 0.3; done
```

Watch Terminal 3. Expected sequence over the next ~90 seconds:
1. `Observed restart count increase` - the liveness probe is failing and 
   Kubernetes is restarting the container.
2. After 3 restarts within 5 minutes: `WARNING TRIGGER: 3 restarts in 5min. 
   Quarantining mock-api for 60s.`
3. `Scaled mock-api to 0 replicas` - the watcher takes real action, not just 
   an alert.
4. 60 seconds later: `Cooldown complete. Recovering.` → 
   `Scaled mock-api to 1 replicas`.

Optionally, cross-verify against Kubernetes' own event log (independent of 
the watcher's self-reported logs):
```bash
kubectl describe deployment mock-api | grep -A 6 "Events:"
```

## Part 3 - Watch it in Grafana

Open `localhost:3000`, the saved dashboard. The request-rate-by-status panel 
should show a visible spike in 500-status traffic during Part 2, then a gap 
while the service was quarantined (0 replicas = 0 traffic), then recovery.

## Part 4 - Reset

```bash
curl -X POST http://localhost:8000/simulate/none
```

## What this demonstrates

- A real, small LLM actually running and responding (Stage 6).
- Kubernetes' own probe-based restart mechanism (Stage 3).
- A custom escalation layer that goes beyond default K8s behavior - 
  detecting a *pattern* of failures, not just one, and taking a real 
  corrective action (Stage 5).
- Observability showing the whole story visually (Stage 4).
- All of it running independently of the LLM's own health - the healing 
  loop above never touched Ollama at all, proving the two systems are 
  properly decoupled.
