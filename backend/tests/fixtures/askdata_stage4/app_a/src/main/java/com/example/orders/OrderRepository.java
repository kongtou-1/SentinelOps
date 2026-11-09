package com.example.orders;

// Frozen sample application A (read-only fixture, never compiled or executed).
// Same "orders" schema as application B, different business rules.

@Entity
@Table(name = "orders")
public class Order {
    @Id
    @Column(name = "id") private Long id;
    @Column(name = "status") private String status;
    @Column(name = "amount") private Long amount;
    @Column(name = "is_deleted") private Integer isDeleted;
    @Column(name = "created_at") private Timestamp createdAt;
    @JoinColumn(name = "user_id") private User user;
}

public enum OrderStatus { PAID, REFUNDED, CANCELLED }

public interface OrderRepository extends JpaRepository<Order, Long> {

    @Query(value = "SELECT status, SUM(amount) FROM orders WHERE is_deleted = 0 GROUP BY status",
           nativeQuery = true)
    List<Object[]> revenueByStatus();

    @Query("SELECT o FROM Order o WHERE o.status = 'PAID'")
    List<Order> paidOrders();

    List<Order> findByStatusAndIsDeletedFalse(String status);
}

public class RevenueService {
    private static final String AMOUNT_UNIT = "CENT";

    public BigDecimal yuan(BigDecimal amount) {
        return amount.divide(new BigDecimal("100"));
    }
}
