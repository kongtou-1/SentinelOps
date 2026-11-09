"""Frozen sample application A (read-only fixture, never executed).

Orders are bookable when status is PAID, soft deletion uses the ``is_deleted``
flag, amounts are stored in cents, and reporting uses ``created_at``.
"""

from enum import Enum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func, text

AMOUNT_UNIT = "CENT"


class OrderStatus(Enum):
    PAID = "PAID"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class Order:
    __tablename__ = "orders"

    id = Column("id", Integer, primary_key=True)
    status = Column("status", String(20))
    amount = Column("amount", Integer)
    is_deleted = Column("is_deleted", Integer)
    deleted_at = Column("deleted_at", DateTime)
    created_at = Column("created_at", DateTime)
    paid_at = Column("paid_at", DateTime)
    user_id = Column("user_id", Integer, ForeignKey("users.id"))


def paid_orders(db):
    return (
        db.query(Order)
        .filter(Order.status == OrderStatus.PAID.value)
        .filter(Order.is_deleted == False)
        .filter(Order.status != OrderStatus.REFUNDED.value)
        .all()
    )


def revenue_in_yuan(db):
    return (
        db.query(func.sum(Order.amount) / 100)
        .filter(Order.created_at >= "2026-01-01")
        .scalar()
    )


def native_report(db):
    return db.execute(
        text(
            "SELECT status, SUM(amount) FROM orders "
            "WHERE is_deleted = 0 GROUP BY status"
        )
    )


def dynamic_report(db, status):
    return db.execute(text(f"SELECT status FROM orders WHERE status = '{status}'"))
