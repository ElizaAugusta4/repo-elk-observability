"""
python-orders - API de pedidos

- Cria pedidos reservando estoque no java-inventory (comunicação entre serviços)
- Persiste os pedidos no PostgreSQL
- Expõe métricas Prometheus em /metrics (coletadas pelo Prometheus -> Grafana)
- Loga em JSON no stdout (coletado pelo Filebeat -> Logstash -> Elasticsearch -> Kibana)
- Tem um modo "chaos" (/chaos) para simular erros e lentidão e disparar alertas
"""
import asyncio
import json
import logging
import os
import random
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone

import httpx
import psycopg2
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from pydantic import BaseModel, Field

SERVICE_NAME = "python-orders"
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://java-inventory:8080")
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "postgres"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "labdb"),
    "user": os.getenv("DB_USER", "labuser"),
    "password": os.getenv("DB_PASSWORD", "labpass"),
}


# ------------------------------------------------------------------ logging JSON
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "@timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
log = logging.getLogger("orders")
logging.getLogger("httpx").setLevel(logging.WARNING)  # evita log duplicado de cada chamada HTTP


# ------------------------------------------------------------------ métricas
REQUESTS = Counter("http_requests_total", "Total de requisições HTTP", ["method", "endpoint", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Latência das requisições HTTP",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
ORDERS = Counter("orders_created_total", "Pedidos processados por status", ["status"])
CHAOS_ERROR = Gauge("chaos_error_rate", "Taxa de erro simulada (0 a 1)")
CHAOS_LATENCY = Gauge("chaos_latency_ms", "Latência extra simulada em ms")

chaos = {"error_rate": 0.0, "latency_ms": 0}
SKIP_PATHS = {"/metrics", "/health", "/ready", "/chaos"}


# ------------------------------------------------------------------ banco de dados
pool: ThreadedConnectionPool | None = None


def init_db_pool(retries: int = 30, delay: int = 2) -> None:
    global pool
    for attempt in range(1, retries + 1):
        try:
            pool = ThreadedConnectionPool(1, 10, **DB_CONFIG)
            log.info("Conectado ao PostgreSQL", extra={"fields": {"db_host": DB_CONFIG["host"]}})
            return
        except psycopg2.OperationalError as exc:
            log.warning(
                "PostgreSQL indisponível, tentando novamente",
                extra={"fields": {"attempt": attempt, "error": str(exc)}},
            )
            time.sleep(delay)
    raise RuntimeError("Não foi possível conectar ao PostgreSQL")


@contextmanager
def db_cursor():
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        if not conn.closed:
            conn.rollback()
        raise
    finally:
        # se o banco reiniciou, a conexão quebrada é descartada do pool
        pool.putconn(conn, close=bool(conn.closed))


http_client = httpx.Client(base_url=INVENTORY_URL, timeout=5.0)


@asynccontextmanager
async def lifespan(_: FastAPI):
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = [handler]
        uv_logger.propagate = False
    init_db_pool()
    log.info("python-orders iniciado", extra={"fields": {"inventory_url": INVENTORY_URL}})
    yield
    http_client.close()
    if pool:
        pool.closeall()


app = FastAPI(title="python-orders", lifespan=lifespan)


# ------------------------------------------------------------------ middleware de observabilidade
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    path = request.url.path
    if path in SKIP_PATHS:
        return await call_next(request)

    start = time.perf_counter()

    if chaos["latency_ms"] > 0:
        await asyncio.sleep(chaos["latency_ms"] * random.uniform(0.5, 1.5) / 1000)

    if random.random() < chaos["error_rate"]:
        log.error("Erro simulado pelo modo chaos", extra={"fields": {"path": path, "method": request.method}})
        response = JSONResponse(status_code=500, content={"detail": "Erro simulado (chaos)"})
    else:
        try:
            response = await call_next(request)
        except Exception:
            log.exception("Erro não tratado", extra={"fields": {"path": path, "method": request.method}})
            response = JSONResponse(status_code=500, content={"detail": "Erro interno"})

    duration = time.perf_counter() - start
    endpoint = getattr(request.scope.get("route"), "path", path)
    REQUESTS.labels(request.method, endpoint, str(response.status_code)).inc()
    LATENCY.labels(request.method, endpoint).observe(duration)
    log.info(
        "request",
        extra={
            "fields": {
                "method": request.method,
                "path": path,
                "http_status": response.status_code,
                "duration_ms": round(duration * 1000, 2),
            }
        },
    )
    return response


# ------------------------------------------------------------------ endpoints
class OrderIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)


@app.get("/health")
def health():
    return {"status": "UP"}


@app.get("/ready")
def ready():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "READY"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Banco indisponível: {exc}")


@app.get("/products")
def list_products():
    try:
        resp = http_client.get("/products")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        log.error("Falha ao consultar java-inventory", extra={"fields": {"error": str(exc)}})
        raise HTTPException(status_code=502, detail="java-inventory indisponível")


@app.post("/orders", status_code=201)
def create_order(order: OrderIn):
    fields = {"product_id": order.product_id, "quantity": order.quantity}
    try:
        resp = http_client.post(f"/products/{order.product_id}/reserve", json={"quantity": order.quantity})
    except httpx.HTTPError as exc:
        ORDERS.labels("ERROR").inc()
        log.error("java-inventory indisponível ao reservar estoque", extra={"fields": {**fields, "error": str(exc)}})
        raise HTTPException(status_code=502, detail="java-inventory indisponível")

    if resp.status_code == 404:
        ORDERS.labels("REJECTED").inc()
        log.warning("Produto não encontrado", extra={"fields": fields})
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    if resp.status_code == 200:
        order_status = "CONFIRMED"
    elif resp.status_code == 409:
        order_status = "REJECTED"
    else:
        ORDERS.labels("ERROR").inc()
        log.error("Resposta inesperada do java-inventory", extra={"fields": {**fields, "upstream_status": resp.status_code}})
        raise HTTPException(status_code=502, detail="Erro no java-inventory")

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO orders (product_id, quantity, status) VALUES (%s, %s, %s) "
            "RETURNING id, product_id, quantity, status, created_at",
            (order.product_id, order.quantity, order_status),
        )
        row = cur.fetchone()

    ORDERS.labels(order_status).inc()
    if order_status == "REJECTED":
        log.warning("Pedido rejeitado: estoque insuficiente", extra={"fields": {**fields, "order_id": row["id"]}})
        return JSONResponse(status_code=409, content=jsonable_encoder(row))

    log.info("Pedido confirmado", extra={"fields": {**fields, "order_id": row["id"], "order_status": order_status}})
    return row


@app.get("/orders")
def list_orders():
    with db_cursor() as cur:
        cur.execute("SELECT id, product_id, quantity, status, created_at FROM orders ORDER BY id DESC LIMIT 50")
        return cur.fetchall()


@app.post("/chaos")
def set_chaos(error_rate: float = 0.0, latency_ms: int = 0):
    chaos["error_rate"] = max(0.0, min(1.0, error_rate))
    chaos["latency_ms"] = max(0, latency_ms)
    CHAOS_ERROR.set(chaos["error_rate"])
    CHAOS_LATENCY.set(chaos["latency_ms"])
    log.warning("Modo chaos alterado", extra={"fields": dict(chaos)})
    return chaos


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
