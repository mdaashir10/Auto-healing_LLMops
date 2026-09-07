# Auto-healing LLMops

A Kubernetes-native, self-healing deployment pipeline for an LLM inference 
service, with Prometheus/Grafana observability and a custom healing layer 
(in progress). Built and tested entirely locally on constrained hardware 
(ThinkPad T410, 1st-gen i5, 4GB RAM, spinning disk) - no cloud dependency.

The LLM inference service is the workload under management; the project's 
focus is the surrounding DevOps/observability/self-healing tooling.

## Architecture

FastAPI mock inference service -> Docker -> k3s (single-node) -> 
Prometheus + Grafana -> custom healing/escalation layer (Stage 5) -> 
real Ollama-served model (Stage 6)

## Local Environment Setup

- 4GB swapfile added and persisted via `/etc/fstab`, since 4GB RAM alone 
  isn't enough headroom to run k3s plus multiple workload pods.
- k3s installed with Traefik and ServiceLB disabled 
  (`/etc/rancher/k3s/config.yaml`), since this project uses 
  `kubectl port-forward` exclusively rather than Ingress or a LoadBalancer 
  Service. This is a memory-reclaim step, not a functional requirement.
- k3s's kubectl requires `export KUBECONFIG=~/.kube/config` explicitly — it 
  does not read the default kubeconfig path automatically. Added to `~/.bashrc`.

**Operational note - graceful shutdown**: stopping k3s directly 
(`systemctl stop k3s`) without first scaling deployments to zero can orphan 
running containers mid-execution instead of sending a clean shutdown signal. 
Observed directly: a Prometheus pod restarted with `Reason: Unknown, Exit Code: 
255` after a raw stop/start cycle, versus `Reason: Completed, Exit Code: 0` 
for a pod shut down cleanly. Practice: `kubectl scale deployment --all 
--replicas=0` before stopping k3s, scale back up after restart.

## Stage 0: Base Infrastructure
- 4GB swapfile, k3s install (see setup notes above).

## Stage 1: Mock LLM Inference Service
- FastAPI app (`app/main.py`) exposing `/health`, with a `/simulate/{type}` 
  endpoint to trigger simulated failure modes (500 errors, slow responses) 
  for testing self-healing behavior on demand.
- Containerized with Docker. k3s's containerd has a separate image store from 
  Docker, so images aren't automatically visible to the cluster — workflow: 
  `docker build` → `docker save` → `k3s ctr images import` → 
  `imagePullPolicy: Never` in the manifest (fully local, no registry).

## Stage 2: CI Gate (flake8 + Trivy)
- `flake8` lint job (PEP8, max line length 100).
- `trivy` container vulnerability scan, gating on CRITICAL/HIGH CVEs.
- Base image: `python:3.14-slim-bookworm` (Debian 12) rather than 
  `slim`/`trixie`, for better patch coverage; `apt-get upgrade` in the 
  Dockerfile to pick up latest patches at build time.
- `.trivyignore` documents 18 explicitly justified suppressions: OS-layer CVEs 
  with no upstream fix, two pip-vendored false positives (Trivy flagging 
  `pip/_vendor`'s internal copies of msgpack/setuptools as if they were the 
  actually-installed packages — confirmed via direct container inspection), 
  and real tracked CVEs pending an upstream fix in a compatible dependency 
  version. Every suppression has a stated reason, not a blanket ignore.

## Stage 3: Kubernetes Deployment + Self-Healing Probes
- `k8s/deployment.yaml`: liveness + readiness probes on `/health` 
  (`initialDelaySeconds: 5, periodSeconds: 5, timeoutSeconds: 2, 
  failureThreshold: 3`).
- Verified end-to-end: triggering `/simulate/500` causes the readiness probe 
  to fail (pod drops out of Service rotation), then the liveness probe 
  restarts the container, then it recovers — observed live via 
  `kubectl get pods -w`.

## Stage 4: Observability (Prometheus + Grafana)

### Prometheus
- Custom instrumentation in the app (`prometheus-client==0.21.1`): request 
  counter, latency histogram, failure-mode gauge, exposed at `/metrics`.
- Hand-rolled deployment (not the kube-prometheus-stack Helm chart — too 
  heavy for 4GB RAM). Scrape interval: 10s.
- Retention capped at 6h, memory limited to 400Mi, to bound TSDB growth on 
  constrained disk/RAM.

### Grafana
- **Version pinned to `10.4.2`, not `latest`.** `grafana/grafana:latest` 
  triggers a "unified storage" migration on first boot involving heavy SQLite 
  write I/O — on this hardware (spinning disk) this stalled indefinitely 
  rather than completing in seconds as on typical dev machines. Pinning 
  avoids the migration path entirely.
- **Persistent storage**: initially deployed without a PVC — discovered 
  Grafana's SQLite DB (dashboards, datasources, users) lives in the 
  container's ephemeral filesystem, so any pod recreation (including a 
  routine reboot) wiped manually-created dashboards. Fixed with a PVC mounted 
  at `/var/lib/grafana`, backed by k3s's default `local-path-provisioner`.
- Prometheus datasource is pre-provisioned via a ConfigMap 
  (`k8s/grafana-datasource.yaml`) rather than added manually through the UI.
- Memory capped at 250Mi limit / 150Mi request.

![Grafana dashboard: request rate by endpoint and status code](docs/images/grafana-dashboard-stage4.png)

