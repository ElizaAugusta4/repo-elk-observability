#!/usr/bin/env bash
set -uo pipefail
kubectl delete namespace lab logging monitoring --ignore-not-found
kubectl delete clusterrole filebeat prometheus --ignore-not-found
kubectl delete clusterrolebinding filebeat prometheus --ignore-not-found
echo
echo "Recursos removidos. Para apagar o cluster inteiro: kind delete cluster --name observability-lab"
