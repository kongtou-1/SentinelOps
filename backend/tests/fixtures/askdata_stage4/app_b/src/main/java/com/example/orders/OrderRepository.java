package com.example.orders;

// Frozen sample application B (read-only fixture, never compiled or executed).
// Same "orders" schema as application A, different business rules.

@Entity
@Table(name = "orders")
public class Order {
    @Id
    @Column(name = "id") private Long id;
    @Column(name = "status") private String status;
    @Column(name = "amount") private Long amount;
    @Column(name = "is_deleted") private Integer isDeleted;
    @Column(name = "paid_at") private Timestamp paidAt;
    @JoinColumn(name = "user_id") private User user;
}

public enum OrderStatus { SETTLED, REFUND, CANCELLED }

public interface OrderRepository extends JpaRepository<Order, Long> {

    @Query(value = "SELECT status, SUM(amount) FROM orders WHERE deleted_at IS NULL GROUP BY status",
           nativeQuery = true)
    List<Object[]> revenueByStatus();

    @Query("SELECT o FROM Order o WHERE o.status = 'SETTLED'")
    List<Order> settledOrders();

    List<Order> findByStatusAndDeletedAtIsNull(String status);
}

public class RevenueService {
    private static final String AMOUNT_UNIT = "YUAN";

    public BigDecimal yuan(BigDecimal amount) {
        return amount;
    }
}
