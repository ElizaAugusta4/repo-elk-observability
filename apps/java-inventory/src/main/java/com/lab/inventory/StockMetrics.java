package com.lab.inventory;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Publica o estoque de cada produto como métrica (inventory_stock{product="..."}).
 * Usada no dashboard e no alerta de "estoque baixo" do Grafana.
 */
@Component
public class StockMetrics {

    private static final Logger log = LoggerFactory.getLogger(StockMetrics.class);

    private final ProductRepository repository;
    private final MeterRegistry registry;
    private final Map<String, AtomicInteger> gauges = new ConcurrentHashMap<>();

    public StockMetrics(ProductRepository repository, MeterRegistry registry) {
        this.repository = repository;
        this.registry = registry;
    }

    @Scheduled(fixedRate = 15000)
    public void update() {
        try {
            for (Product p : repository.findAll()) {
                gauges.computeIfAbsent(p.name(), name -> {
                    AtomicInteger value = new AtomicInteger();
                    Gauge.builder("inventory.stock", value, AtomicInteger::get)
                            .description("Estoque atual por produto")
                            .tag("product", name)
                            .register(registry);
                    return value;
                }).set(p.stock());
            }
        } catch (DataAccessException e) {
            log.error("Falha ao atualizar metricas de estoque: {}", e.getMostSpecificCause().getMessage());
        }
    }
}
