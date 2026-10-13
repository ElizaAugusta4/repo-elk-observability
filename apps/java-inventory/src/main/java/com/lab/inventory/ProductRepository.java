package com.lab.inventory;

import java.util.List;
import java.util.Optional;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

@Repository
public class ProductRepository {

    private static final RowMapper<Product> MAPPER =
            (rs, rowNum) -> new Product(rs.getInt("id"), rs.getString("name"), rs.getInt("stock"));

    private final JdbcTemplate jdbc;

    public ProductRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<Product> findAll() {
        return jdbc.query("SELECT id, name, stock FROM products ORDER BY id", MAPPER);
    }

    public Optional<Product> findById(int id) {
        return jdbc.query("SELECT id, name, stock FROM products WHERE id = ?", MAPPER, id)
                .stream()
                .findFirst();
    }

    /** Reserva atômica: só baixa o estoque se houver quantidade suficiente. */
    public boolean reserve(int id, int quantity) {
        return jdbc.update(
                "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                quantity, id, quantity) == 1;
    }

    public boolean restock(int id, int quantity) {
        return jdbc.update("UPDATE products SET stock = stock + ? WHERE id = ?", quantity, id) == 1;
    }
}
