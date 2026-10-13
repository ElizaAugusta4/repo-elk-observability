#!/usr/bin/env bash
set -euo pipefail
NS=lab

chaos() {
  for pod in $(kubectl -n $NS get pods -l app=python-orders -o jsonpath='{.items[*].metadata.name}'); do
    echo -n "$pod -> "
    kubectl -n $NS exec "$pod" -- python -c \
      "import urllib.request as u; print(u.urlopen(u.Request('http://localhost:8000/chaos?$1', method='POST')).read().decode())"
  done
}

restock() {
  for id in 1 2 3 4; do
    kubectl -n $NS exec deploy/python-orders -- python -c \
      "import urllib.request as u, json; r = u.Request('http://java-inventory:8080/products/$id/restock', data=json.dumps({'quantity': 500}).encode(), headers={'Content-Type': 'application/json'}, method='POST'); print(u.urlopen(r).read().decode())"
  done
}

case "${1:-}" in
  errors-on)   chaos "error_rate=0.3&latency_ms=0" ;;
  latency-on)  chaos "error_rate=0&latency_ms=800" ;;
  chaos-off)   chaos "error_rate=0&latency_ms=0" ;;
  java-down)   kubectl -n $NS scale deployment java-inventory --replicas=0 ;;
  java-up)     kubectl -n $NS scale deployment java-inventory --replicas=1 ;;
  db-down)     kubectl -n $NS scale statefulset postgres --replicas=0 ;;
  db-up)       kubectl -n $NS scale statefulset postgres --replicas=1 ;;
  restock)     restock ;;
  load-stop)   kubectl -n $NS scale deployment loadgen --replicas=0 ;;
  load-start)  kubectl -n $NS scale deployment loadgen --replicas=1 ;;
  status)      kubectl get pods -n lab; kubectl get pods -n logging; kubectl get pods -n monitoring ;;
  *)
    cat <<USAGE
Uso: ./scripts/demo.sh <cenário>

  errors-on    30% das requisições do python-orders retornam 500  -> alerta "taxa de erro" + "logs de ERRO"
  latency-on   adiciona ~800ms de latência                         -> alerta "latência p95"
  chaos-off    volta ao normal
  java-down    derruba o java-inventory                            -> alerta "serviço fora do ar"
  java-up      sobe o java-inventory de novo
  db-down      derruba o PostgreSQL                                -> alerta "banco fora do ar"
  db-up        sobe o PostgreSQL de novo
  restock      adiciona 500 unidades em todos os produtos          -> resolve "estoque baixo"
  load-stop    para o gerador de carga
  load-start   liga o gerador de carga
  status       mostra os pods
USAGE
    ;;
esac
