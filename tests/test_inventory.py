from datetime import timedelta

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, now_jst
from marche.services.inventory import (
    PENDING_MINUTES,
    available_stock,
    reserved_quantity,
)


def add_order(user, product, quantity, status=OrderStatus.PENDING, created_at=None):
    """注文を直接作る（画面を通さずに在庫の計算だけを確かめるため）"""
    order = Order(
        user=user,
        status=status,
        total_amount=product.price * quantity,
        created_at=created_at or now_jst(),
    )
    order.items.append(
        OrderItem(
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
            subtotal=product.price * quantity,
        )
    )
    db.session.add(order)
    db.session.commit()
    return order


def test_注文が無ければ在庫数がそのまま買える(producer, make_product):
    product = make_product(producer, stock=5)
    assert reserved_quantity(product) == 0
    assert available_stock(product) == 5


def test_支払い待ちの注文の分だけ減る(producer, customer, make_product):
    product = make_product(producer, stock=5)
    add_order(customer, product, 2)
    add_order(customer, product, 1)
    assert reserved_quantity(product) == 3
    assert available_stock(product) == 2


def test_古い支払い待ちは在庫を押さえない(producer, customer, make_product):
    product = make_product(producer, stock=5)
    old = now_jst() - timedelta(minutes=PENDING_MINUTES + 1)
    add_order(customer, product, 2, created_at=old)
    assert available_stock(product) == 5


def test_取り消した注文と支払い済みの注文は引かない(producer, customer, make_product):
    product = make_product(producer, stock=5)
    add_order(customer, product, 2, status=OrderStatus.CANCELLED)
    # 支払い済みの分は確定のときに stock から減らすので、ここで引くと二重になる
    add_order(customer, product, 1, status=OrderStatus.PAID)
    assert available_stock(product) == 5


def test_他の商品の注文は引かない(producer, customer, make_product):
    product = make_product(producer, stock=5)
    other = make_product(producer, name="キャベツ", stock=5)
    add_order(customer, other, 3)
    assert available_stock(product) == 5


def test_買える数は0未満にならない(producer, customer, make_product):
    product = make_product(producer, stock=3)
    add_order(customer, product, 2)
    product.stock = 1  # 生産者が在庫を減らした
    db.session.commit()
    assert available_stock(product) == 0
