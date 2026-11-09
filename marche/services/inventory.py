"""在庫と注文の計算。一覧・詳細・カート・注文の作成の全てで、ここの関数を使う"""

from datetime import timedelta

from sqlalchemy import func, select

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

# 支払い待ちの注文が在庫を押さえる時間。決済ページの期限（31分）より少し長くする
PENDING_MINUTES = 35

# 1商品あたり1回に買える数
MAX_QUANTITY = 10


def reserved_quantity(product: Product) -> int:
    """支払い待ち（作成から PENDING_MINUTES 分以内）の注文で押さえられている数"""
    since = now_jst() - timedelta(minutes=PENDING_MINUTES)
    total = db.session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order)
        .where(
            OrderItem.product_id == product.id,
            # paid の分は支払い確定のときに stock から減らすので、ここでは数えない
            Order.status == OrderStatus.PENDING,
            Order.created_at >= since,
        )
    )
    return int(total)


def available_stock(product: Product) -> int:
    """いま買える数 = 在庫数 − 支払い待ちで押さえられている数（0 未満にはしない）"""
    return max(0, product.stock - reserved_quantity(product))
