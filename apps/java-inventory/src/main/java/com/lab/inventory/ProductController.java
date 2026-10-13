package com.lab.inventory;

import static net.logstash.logback.argument.StructuredArguments.kv;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/products")
public class ProductController {

    private static final Logger log = LoggerFactory.getLogger(ProductController.class);

    private final ProductRepository repository;
    private final MeterRegistry registry;

    public ProductController(ProductRepository repository, MeterRegistry registry) {
        this.repository = repository;
        this.registry = registry;
    }

    @GetMapping
    public List<Product> list() {
        return repository.findAll();
    }

    @GetMapping("/{id}")
    public ResponseEntity<Product> get(@PathVariable int id) {
        return repository.findById(id)
                .map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PostMapping("/{id}/reserve")
    public ResponseEntity<?> reserve(@PathVariable int id, @RequestBody QuantityRequest request) {
        int quantity = request.quantity();
        if (quantity <= 0) {
            return ResponseEntity.badRequest().body(Map.of("error", "quantity deve ser maior que zero"));
        }

        if (repository.reserve(id, quantity)) {
            Product product = repository.findById(id).orElseThrow();
            reservations("CONFIRMED").increment();
            log.info("Reserva confirmada {} {} {}",
                    kv("product_id", id), kv("quantity", quantity), kv("stock", product.stock()));
            return ResponseEntity.ok(product);
        }

        Optional<Product> existing = repository.findById(id);
        if (existing.isEmpty()) {
            reservations("NOT_FOUND").increment();
            log.warn("Produto nao encontrado {}", kv("product_id", id));
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", "Produto não encontrado"));
        }

        reservations("INSUFFICIENT_STOCK").increment();
        log.warn("Estoque insuficiente {} {} {}",
                kv("product_id", id), kv("quantity", quantity), kv("stock", existing.get().stock()));
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(Map.of("error", "Estoque insuficiente", "stock", existing.get().stock()));
    }

    @PostMapping("/{id}/restock")
    public ResponseEntity<?> restock(@PathVariable int id, @RequestBody QuantityRequest request) {
        int quantity = request.quantity();
        if (quantity <= 0) {
            return ResponseEntity.badRequest().body(Map.of("error", "quantity deve ser maior que zero"));
        }
        if (!repository.restock(id, quantity)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", "Produto não encontrado"));
        }
        Product product = repository.findById(id).orElseThrow();
        log.info("Estoque reabastecido {} {} {}",
                kv("product_id", id), kv("quantity", quantity), kv("stock", product.stock()));
        return ResponseEntity.ok(product);
    }

    private Counter reservations(String result) {
        return Counter.builder("inventory.reservations")
                .description("Reservas de estoque por resultado")
                .tag("result", result)
                .register(registry);
    }
}
