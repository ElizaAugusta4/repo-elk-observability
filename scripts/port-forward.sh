#!/usr/bin/env bash
set -uo pipefail

trap 'echo; echo "Encerrando port-forwards..."; kill $(jobs -p) 2>/dev/null' EXIT

kubectl -n monitoring port-forward svc/grafana        3000:3000  &
kubectl -n monitoring port-forward svc/prometheus     9090:9090  &
kubectl -n logging    port-forward svc/kibana         5601:5601  &
kubectl -n lab        port-forward svc/python-orders  8000:8000 &
kubectl -n lab        port-forward svc/java-inventory 8080:8080 >/dev/null &

cat <<MSG

  Grafana ........ http://localhost:3000   (admin / admin123)
  Prometheus ..... http://localhost:9090
  Kibana ......... http://localhost:5601
  python-orders .. http://localhost:8000/docs   (Swagger)
  java-inventory . http://localhost:8080/products

  Ctrl+C para encerrar.
MSG

wait
