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
PANCAKE = {
    "0": S.pending, "17": S.pending, "11": S.pending, "12": S.ready_to_ship, "13": S.ready_to_ship,
    "20": S.ready_to_ship, "1": S.ready_to_ship, "8": S.ready_to_ship, "9": S.ready_to_ship, "2": S.shipping,
    "3": S.delivered, "16": S.delivered, "4": S.returned, "15": S.returned, "5": S.returned, "6": S.cancelled,
    "7": S.cancelled,
}
NHANH = {
    "54": S.pending, "55": S.pending, "57": S.pending, "56": S.ready_to_ship, "42": S.ready_to_ship,
    "40": S.ready_to_ship, "43": S.ready_to_ship, "73": S.ready_to_ship, "59": S.shipping, "60": S.delivered,
    "58": S.cancelled, "63": S.cancelled, "64": S.cancelled, "68": S.cancelled, "61": S.returned,
    "71": S.returned, "72": S.returned, "74": S.returned,
}
GHN = {
    "ready_to_pick": S.ready_to_ship, "picking": S.ready_to_ship, "money_collect_picking": S.ready_to_ship,
    "picked": S.shipping, "storing": S.shipping, "transporting": S.shipping, "sorting": S.shipping,
    "delivering": S.shipping, "money_collect_delivering": S.shipping, "delivery_fail": S.shipping,
    "delivered": S.delivered, "cancel": S.cancelled, "waiting_to_return": S.returned, "return": S.returned,
    "return_transporting": S.returned, "return_sorting": S.returned, "returning": S.returned,
    "return_fail": S.returned, "returned": S.returned,
}
GHN_TEXT = {
    "ready_to_pick": "Mới tạo đơn", "picking": "Đang lấy hàng", "money_collect_picking": "Đang thu tiền người gửi",
    "picked": "Đã lấy hàng", "storing": "Hàng đang nằm ở kho", "transporting": "Đang luân chuyển",
    "sorting": "Đang phân loại", "delivering": "Đang giao hàng", "money_collect_delivering": "Đang thu tiền người nhận",
    "delivered": "Giao thành công", "delivery_fail": "Giao thất bại", "waiting_to_return": "Chờ trả hàng",
    "return": "Trả hàng", "return_transporting": "Đang luân chuyển hàng trả", "return_sorting": "Đang phân loại hàng trả",
    "returning": "Đang trả hàng", "return_fail": "Trả hàng thất bại", "returned": "Đã trả hàng", "cancel": "Đã hủy",
}
GHTK = {
    "-1": S.cancelled, "1": S.ready_to_ship, "2": S.ready_to_ship, "12": S.ready_to_ship, "8": S.ready_to_ship,
    "7": S.ready_to_ship, "3": S.shipping, "4": S.shipping, "9": S.shipping, "10": S.shipping, "5": S.delivered,
    "6": S.delivered, "20": S.returned, "21": S.returned, "11": S.returned,
}
