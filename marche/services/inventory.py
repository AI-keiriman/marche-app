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


class OrderError(Exception):
    """注文を作れなかった理由。メッセージは画面にそのまま出せる日本語にする"""


def create_order(user, cart: dict[int, int]) -> Order:
    """カートの中身から支払い待ちの注文を作る。金額は DB の価格から計算する"""
    # 先に全ての商品を確かめる。途中で注文や明細を作り始めると、
    # エラーのときに作りかけのものがセッションに残ってしまう
    lines = []
    for product_id, quantity in cart.items():
        product = db.session.get(Product, product_id)
        if product is None or not product.is_published:
            raise OrderError(
                "販売を終了した商品がカートに入っています。カートを確認してください。"
            )
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError(
                f"「{product.name}」の数量は1〜{MAX_QUANTITY}で選んでください。"
            )
        available = available_stock(product)
        if quantity > available:
            raise OrderError(
                f"「{product.name}」の在庫が足りません（残り {available}）。"
            )
        lines.append((product, quantity))

    if not lines:
        raise OrderError("カートが空です。")

    # 確認が全て通ってから、注文と明細を作る
    order = Order(user=user, status=OrderStatus.PENDING, total_amount=0)
    for product, quantity in lines:
        order.items.append(
            OrderItem(
                product=product,
                product_name=product.name,
                unit_price=product.price,  # 単価は必ず DB の商品から取る
                quantity=quantity,
                subtotal=product.price * quantity,
            )
        )
    order.total_amount = sum(item.subtotal for item in order.items)
    db.session.add(order)
    db.session.commit()
    return order
