#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CLUSTER=observability-lab

step() { echo; echo "==================== $1 ===================="; }

step "1/8 Criando o cluster (kind)"
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config k8s/kind-config.yaml
fi
kubectl config use-context "kind-$CLUSTER"
kubectl get nodes

step "2/8 Build das imagens e carga no kind"
docker build -t python-orders:1.0 apps/python-orders
docker build -t java-inventory:1.0 apps/java-inventory
kind load docker-image python-orders:1.0 --name "observability-lab"
kind load docker-image java-inventory:1.0 --name "kind-observability-lab"

step "3/8 Criando namespaces"
kubectl apply -f k8s/00-namespaces.yaml

step "4/8 Banco de dados (PostgreSQL)"
kubectl apply -f k8s/database/
kubectl -n lab rollout status statefulset/postgres --timeout=300s

step "5/8 Stack ELK (Elasticsearch, Logstash, Kibana, Filebeat)"
kubectl apply -f k8s/elk/
kubectl -n logging rollout status statefulset/elasticsearch --timeout=600s
kubectl -n logging rollout status deployment/logstash --timeout=600s
kubectl -n logging rollout status deployment/kibana --timeout=600s
kubectl -n logging rollout status daemonset/filebeat --timeout=300s

step "6/8 Monitoramento (Prometheus + Grafana com dashboards e alertas)"
kubectl apply -k k8s/monitoring
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
kubectl -n monitoring rollout status deployment/grafana --timeout=300s

step "7/8 Aplicações (java-inventory + python-orders)"
kubectl apply -f k8s/apps/java-inventory.yaml -f k8s/apps/python-orders.yaml
kubectl -n lab rollout status deployment/java-inventory --timeout=600s
kubectl -n lab rollout status deployment/python-orders --timeout=300s

step "8/8 Gerador de carga"
kubectl apply -f k8s/apps/loadgen.yaml

echo
echo "Tudo pronto! Pods em execução:"
kubectl get pods -A | grep -E "lab|logging|monitoring"
echo
echo "Agora rode:  ./scripts/port-forward.sh"
