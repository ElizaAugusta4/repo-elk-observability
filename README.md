# Observability Lab — Python + Java + PostgreSQL + Kubernetes + ELK + Grafana

Projeto de laboratório de observabilidade completo: duas aplicações (Python e Java) conversando entre si e com um banco PostgreSQL, rodando em Kubernetes (**kind**), com **logs centralizados no ELK** e **métricas, dashboards e alertas no Grafana**.

## Arquitetura

```
                        ┌──────────────── namespace: lab ────────────────┐
  loadgen ──HTTP──▶ python-orders (FastAPI, 2 réplicas) ──HTTP──▶ java-inventory (Spring Boot)
                        │                                         │
                        └──────────────▶ PostgreSQL ◀─────────────┘
                                     (+ postgres-exporter)
                        └─────────────────────────────────────────────────┘
         │ logs (stdout JSON)                              │ métricas (/metrics)
         ▼                                                 ▼
 ┌──── namespace: logging ─────┐              ┌──── namespace: monitoring ────┐
 │ Filebeat → Logstash →       │              │ Prometheus → Grafana          │
 │ Elasticsearch → Kibana      │──────────────▶   (dashboards + alertas)      │
 └─────────────────────────────┘  Grafana também lê os logs do Elasticsearch └─┘
```

**Fluxo de negócio:** o `python-orders` recebe um pedido, pede ao `java-inventory` para reservar o estoque (o Java dá baixa no PostgreSQL de forma atômica) e grava o pedido como `CONFIRMED` ou `REJECTED`.

| Componente | Tecnologia | Papel |
|---|---|---|
| python-orders | Python 3.12 + FastAPI | API de pedidos, métricas Prometheus, logs JSON, modo chaos |
| java-inventory | Java 21 + Spring Boot 3 | API de estoque, Actuator/Micrometer, logs JSON (logstash-encoder) |
| PostgreSQL | postgres:16 + postgres-exporter | Banco de dados e métricas do banco |
| Filebeat | DaemonSet | Coleta os logs dos containers do namespace `lab` |
| Logstash | Pipeline | Faz o parse do JSON, padroniza campos, envia ao Elasticsearch |
| Elasticsearch | 8.15 single-node | Armazena logs (índices `app-logs-AAAA.MM.DD`) |
| Kibana | 8.15 | Exploração de logs (Data View criada automaticamente) |
| Prometheus | 2.54 | Coleta métricas via descoberta automática de pods |
| Grafana | 11.2 | Dashboard + 6 alertas provisionados (métricas e logs) |

## Estrutura de arquivos

```
observability-lab/
├── apps/
│   ├── python-orders/         app.py, requirements.txt, Dockerfile
│   └── java-inventory/        pom.xml, Dockerfile, src/main/java/..., application.yml, logback-spring.xml
├── k8s/
│   ├── 00-namespaces.yaml
│   ├── database/postgres.yaml
│   ├── apps/                  java-inventory.yaml, python-orders.yaml, loadgen.yaml
│   ├── elk/                   elasticsearch.yaml, logstash.yaml, kibana.yaml, filebeat.yaml
│   └── monitoring/            kustomization.yaml, prometheus.yaml, grafana.yaml
│       └── grafana/           datasources.yaml, dashboard-providers.yaml, alert-rules.yaml, lab-overview.json
└── scripts/                   setup.sh, port-forward.sh, demo.sh, teardown.sh
```

---

## Passo a passo da execução

### Passo 0 — Pré-requisitos

Você precisa de **Docker**, **kind** e **kubectl** instalados. No Windows, rode tudo dentro do **WSL2** (Ubuntu) com o Docker Desktop integrado ao WSL.

```bash
docker --version
kind version
kubectl version --client
```

**Memória:** o kind não tem flag de memória. O cluster usa a memória que o Docker tem disponível. No **Docker Desktop**, vá em *Settings → Resources* e reserve **pelo menos 8 GB de RAM e 4 CPUs** (o Elasticsearch é o mais pesado). No Linux com Docker nativo isso não é necessário.

### Passo 1 — Subir o cluster Kubernetes

```bash
kind create cluster --config k8s/kind-config.yaml
kubectl cluster-info --context kind-observability-lab
kubectl get nodes
```

O arquivo `k8s/kind-config.yaml` cria um cluster chamado `observability-lab` com um nó. Se tiver RAM sobrando, descomente as linhas de `worker` para ter um cluster multi-nó. No vídeo, isso mostra o Filebeat rodando como DaemonSet em cada nó.

O kind já vem com um StorageClass padrão (`standard`, local-path), então os volumes do PostgreSQL e do Elasticsearch são criados automaticamente.

### Passo 2 — Build das imagens das aplicações

O kind não enxerga as imagens do seu Docker local. Por isso as imagens são construídas com `docker build` e depois **carregadas para dentro do cluster** com `kind load`. Os manifests usam `imagePullPolicy: Never` para o Kubernetes não tentar baixá-las de um registry.

```bash
docker build -t python-orders:1.0 apps/python-orders
docker build -t java-inventory:1.0 apps/java-inventory   

kind load docker-image python-orders:1.0 --name observability-lab
kind load docker-image java-inventory:1.0 --name observability-lab

docker exec observability-lab-control-plane crictl images | grep -E "python-orders|java-inventory"
```

> **Importante:** sempre que alterar o código, repita o `docker build` + `kind load` e reinicie o deployment, por exemplo:
> `kubectl -n lab rollout restart deployment/python-orders`

### Passo 3 — Namespaces

```bash
kubectl apply -f k8s/00-namespaces.yaml
```

### Passo 4 — Banco de dados

```bash
kubectl apply -f k8s/database/
kubectl -n lab rollout status statefulset/postgres
```

O `init.sql` cria as tabelas `products` e `orders` e insere 4 produtos. Para conferir:

```bash
kubectl -n lab exec -it postgres-0 -c postgres -- psql -U labuser -d labdb -c "SELECT * FROM products;"
```

### Passo 5 — Stack ELK

```bash
kubectl apply -f k8s/elk/
kubectl -n logging rollout status statefulset/elasticsearch
kubectl -n logging rollout status deployment/logstash
kubectl -n logging rollout status deployment/kibana
kubectl -n logging get pods
```

O Job `kibana-setup` cria automaticamente a Data View **"Logs das Aplicações"** (`app-logs-*`) assim que o Kibana fica pronto.

### Passo 6 — Prometheus + Grafana

```bash
kubectl apply -k k8s/monitoring
kubectl -n monitoring rollout status deployment/prometheus
kubectl -n monitoring rollout status deployment/grafana
```

O Kustomize transforma os arquivos da pasta `grafana/` em ConfigMaps, e o Grafana já sobe com os datasources (Prometheus e Elasticsearch), o dashboard e as regras de alerta.

### Passo 7 — Aplicações

```bash
kubectl apply -f k8s/apps/java-inventory.yaml -f k8s/apps/python-orders.yaml
kubectl -n lab rollout status deployment/java-inventory
kubectl -n lab rollout status deployment/python-orders
```

### Passo 8 — Gerador de carga

```bash
kubectl apply -f k8s/apps/loadgen.yaml
```

Ele cria um pedido, lista produtos e lista pedidos a cada 2 segundos, para os gráficos e logs terem dados.

> **Atalho:** os passos 1 a 8 estão automatizados em `./scripts/setup.sh`. Para o vídeo, vale mostrar os passos manualmente.

### Passo 9 — Acessar as interfaces

Em um terminal separado (deixe aberto):

```bash
./scripts/port-forward.sh
```

| Interface | URL | Login |
|---|---|---|
| Grafana | http://localhost:3000 | admin / admin123 |
| Prometheus | http://localhost:9090 | — |
| Kibana | http://localhost:5601 | — |
| Swagger do python-orders | http://localhost:8000/docs | — |
| java-inventory | http://localhost:8080/products | — |

### Passo 10 — Testar as APIs

```bash
# Lista produtos (python chama o java)
curl http://localhost:8000/products

# Cria um pedido
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "quantity": 2}'

# Lista os últimos pedidos
curl http://localhost:8000/orders

# Métricas expostas por cada app
curl -s http://localhost:8000/metrics | grep http_requests_total
curl -s http://localhost:8080/actuator/prometheus | grep inventory_stock
```

### Passo 11 — Explorar os logs no Kibana

1. Abra http://localhost:5601 → menu **Analytics → Discover**.
2. Selecione a Data View **"Logs das Aplicações"**.
3. Adicione as colunas `app`, `level` e `message`.
4. Experimente consultas KQL:
   - `app : "java-inventory"`
   - `level : "ERROR"`
   - `message : "Pedido rejeitado*"`
   - `app : "python-orders" and http_status >= 500`
   - `duration_ms > 500`

### Passo 12 — Dashboard no Grafana

Abra http://localhost:3000 → **Dashboards → Lab → "Lab - Visão Geral"**. Ele mostra pods UP, taxa de erro, pedidos/min, requisições por status, latência p50/p95/p99, requisições do Java por endpoint, estoque por produto, heap da JVM, conexões do PostgreSQL, erros por minuto vindos do Elasticsearch e o painel de logs.

Em **Connections → Data sources** você vê os dois datasources (Prometheus e Elasticsearch) já configurados.

### Passo 13 — Alertas no Grafana

Vá em **Alerting → Alert rules → pasta Lab**. Já existem 6 regras provisionadas:

| Alerta | Fonte | Condição |
|---|---|---|
| python-orders: taxa de erro acima de 5% | Prometheus | 5xx / total > 5% por 1 min |
| python-orders: latência p95 acima de 500ms | Prometheus | p95 > 0,5s por 1 min |
| java-inventory: serviço fora do ar | Prometheus | nenhuma instância UP por 30s |
| PostgreSQL: banco de dados fora do ar | Prometheus | `pg_up` < 1 por 30s |
| Estoque baixo | Prometheus | estoque < 20 (um alerta por produto) |
| Muitos logs de ERRO nas aplicações | **Elasticsearch** | > 10 logs ERROR em 5 min |

**Receber notificações (Discord, Telegram, e-mail...):**

1. **Alerting → Contact points → + Add contact point**.
2. Dê um nome (ex.: `discord-lab`), escolha a integração **Discord** e cole a URL do webhook do seu canal (no Discord: Configurações do canal → Integrações → Webhooks → Novo webhook → Copiar URL).
3. Clique em **Test** para receber uma mensagem de teste e depois **Save**.
4. **Alerting → Notification policies → Default policy → Edit** e selecione `discord-lab` como contact point.

Para Telegram o processo é igual: crie um bot com o @BotFather, pegue o token e o chat ID.

**Criar um alerta novo pela interface (ótimo para o vídeo):** Alerting → Alert rules → **New alert rule** → escolha o datasource Prometheus → query `sum(rate(orders_created_total{status="REJECTED"}[5m])) * 60` → threshold `IS ABOVE 5` → pasta `Lab`, grupo `lab-alertas` → Save.

### Passo 14 — Simular falhas e ver os alertas disparando

```bash
./scripts/demo.sh errors-on    # 30% de erro 500 → alertas "taxa de erro" e "logs de ERRO"
./scripts/demo.sh chaos-off

./scripts/demo.sh latency-on   # ~800ms de latência → alerta "latência p95"
./scripts/demo.sh chaos-off

./scripts/demo.sh java-down    # derruba o Java → alerta "serviço fora do ar"
./scripts/demo.sh java-up

./scripts/demo.sh db-down      # derruba o banco → alerta "banco fora do ar"
./scripts/demo.sh db-up

./scripts/demo.sh restock      # repõe estoque → resolve "estoque baixo"
```

Os alertas passam por **Normal → Pending → Firing** e voltam para **Normal** quando o problema é resolvido. O alerta de estoque baixo acontece naturalmente depois de uns 8 minutos de carga (o Monitor começa com 150 unidades).

### Passo 15 — Limpeza

```bash
./scripts/teardown.sh   # remove os recursos do projeto
kind delete cluster --name observability-lab   # (opcional) apaga o cluster
```

---

## Troubleshooting

| Sintoma | Causa provável / solução |
|---|---|
| Pod em `Pending` | Falta de memória. Aumente a RAM do Docker Desktop (Settings → Resources) ou reduza réplicas. Veja o motivo com `kubectl describe pod <nome>`. |
| `ErrImageNeverPull` | A imagem não foi carregada no kind. Rode o `kind load docker-image ...` do passo 2. |
| `kubectl` falando com o cluster errado | `kubectl config use-context kind-observability-lab` |
| Elasticsearch reiniciando | Pouca memória. Confira com `kubectl -n logging describe pod elasticsearch-0`. |
| Nenhum log no Kibana | Veja `kubectl -n logging logs ds/filebeat` e `kubectl -n logging logs deploy/logstash`. Confirme que existe índice: `kubectl -n logging exec elasticsearch-0 -- curl -s localhost:9200/_cat/indices`. |
| Data View não apareceu | Veja `kubectl -n logging logs job/kibana-setup`, ou crie manualmente em Stack Management → Data Views (`app-logs-*`, campo de tempo `@timestamp`). |
| Painéis do Grafana vazios | Abra http://localhost:9090/targets e veja se os alvos do namespace `lab` estão UP. |
| java-inventory demorando | É normal o Spring Boot levar ~30–60s no primeiro start; o `startupProbe` aguarda até 3 min. |
| Ver logs de uma app | `kubectl -n lab logs -f deploy/python-orders` ou `deploy/java-inventory` |

> **Atenção:** este projeto é para estudo. A segurança do Elasticsearch está desligada e as senhas estão em texto simples nos Secrets. Em produção, habilite TLS/autenticação e use um gerenciador de segredos.
