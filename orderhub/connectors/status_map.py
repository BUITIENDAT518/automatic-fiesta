from orderhub.models import OrderStatus as S

SHOPEE = {
    "UNPAID": S.pending, "READY_TO_SHIP": S.ready_to_ship, "PROCESSED": S.ready_to_ship,
    "RETRY_SHIP": S.ready_to_ship, "SHIPPED": S.shipping, "TO_CONFIRM_RECEIVE": S.delivered,
    "COMPLETED": S.delivered, "IN_CANCEL": S.cancelled, "CANCELLED": S.cancelled, "TO_RETURN": S.returned,
}
TIKTOK = {
    "UNPAID": S.pending, "ON_HOLD": S.pending, "AWAITING_SHIPMENT": S.ready_to_ship,
    "PARTIALLY_SHIPPING": S.shipping, "AWAITING_COLLECTION": S.ready_to_ship, "IN_TRANSIT": S.shipping,
    "DELIVERED": S.delivered, "COMPLETED": S.delivered, "CANCELLED": S.cancelled,
}
LAZADA = {
    "unpaid": S.pending, "pending": S.pending, "packed": S.ready_to_ship, "ready_to_ship": S.ready_to_ship,
    "ready_to_ship_pending": S.ready_to_ship, "repacked": S.ready_to_ship, "shipped": S.shipping,
    "delivered": S.delivered, "confirmed": S.delivered, "canceled": S.cancelled, "cancelled": S.cancelled,
    "returned": S.returned, "failed": S.returned, "shipped_back": S.returned, "lost_by_3pl": S.returned,
}
