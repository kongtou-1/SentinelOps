"""Frozen sample application B (read-only fixture, never executed).

Same ``orders`` schema as A. Orders count only when SETTLED, soft deletion
uses ``deleted_at IS NULL``, amounts are already in yuan, and reporting uses
``paid_at``.
"""

from enum import Enum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func, text

AMOUNT_UNIT = "YUAN"


class OrderStatus(Enum):
    SETTLED = "SETTLED"
    REFUND = "REFUND"
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


def settled_orders(db):
    return (
        db.query(Order)
        .filter(Order.status == OrderStatus.SETTLED.value)
        .filter(Order.deleted_at.is_(None))
        .filter(Order.status != OrderStatus.REFUND.value)
        .all()
    )


def revenue_in_yuan(db):
    return (
        db.query(func.sum(Order.amount))
        .filter(Order.paid_at >= "2026-01-01")
        .scalar()
    )


def native_report(db):
    return db.execute(
        text(
            "SELECT status, SUM(amount) FROM orders "
            "WHERE deleted_at IS NULL GROUP BY status"
        )
    )


def dynamic_report(db, status):
    return db.execute(text(f"SELECT status FROM orders WHERE status = '{status}'"))
