"""07 章: Stripe Checkout。本物の Stripe は呼ばない"""

from types import SimpleNamespace

import pytest
import stripe

from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services import payment
from marche.services.inventory import available_stock, create_order
from marche.services.payment import PaymentError


def order_via_cart(client, product, quantity=1):
    client.post(f"/cart/add/{product.id}", data={"quantity": str(quantity)})
    return client.post("/orders/")


# --- 注文から決済ページへ（views） ---


def test_決済ページを作れなければ注文は取り消されカートに戻る(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer, stock=3)
    fake_payment.error = PaymentError("決済ページを開けませんでした。")
    login(customer)
    response = order_via_cart(client, product, 2)
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    [order] = db.session.scalars(db.select(Order)).all()
    assert order.status == OrderStatus.CANCELLED
    assert order.stripe_checkout_session_id is None
    # 取り消した注文は在庫を押さえない
    assert available_stock(product) == 3
    assert "決済ページを開けませんでした" in client.get("/cart/").text


def test_完了ページは確認中と出る(client, producer, customer, make_product, login):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    login(customer)
    response = client.get(f"/orders/{order.id}/complete?session_id=cs_test_x")
    assert response.status_code == 200
    assert "お支払いを確認しています" in response.text


def test_中断ページで取り消すとcancelledになりexpireが呼ばれる(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer, stock=3)
    login(customer)
    order_via_cart(client, product, 2)
    order = db.session.scalar(db.select(Order))
    assert available_stock(product) == 1

    assert "この注文を取り消す" in client.get(f"/orders/{order.id}/cancel").text
    response = client.post(f"/orders/{order.id}/cancel")
    assert response.headers["Location"] == f"/orders/{order.id}"
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    assert fake_payment.expired == [order.stripe_checkout_session_id]
    assert available_stock(product) == 3


def test_支払い済みの注文は中断ページから取り消せない(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    order.status = OrderStatus.PAID
    db.session.commit()
    login(customer)
    client.post(f"/orders/{order.id}/cancel")
    db.session.refresh(order)
    assert order.status == OrderStatus.PAID
    assert fake_payment.expired == []


@pytest.mark.parametrize("path", ["cancel", "complete"])
def test_他人の注文の中断と完了のページは403(
    client, producer, customer, make_product, login, path
):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    login(producer)
    assert client.get(f"/orders/{order.id}/{path}").status_code == 403
    if path == "cancel":
        assert client.post(f"/orders/{order.id}/cancel").status_code == 403
        db.session.refresh(order)
        assert order.status == OrderStatus.PENDING


# --- services/payment.py（_client を差し替えて、Stripe に渡す値を確かめる） ---


class FakeSessions:
    def __init__(self, error=None):
        self.params = None
        self.expired = []
        self.error = error

    def create(self, params):
        if self.error:
            raise self.error
        self.params = params
        return SimpleNamespace(id="cs_test_123", url="https://checkout.stripe.com/c/x")

    def expire(self, session_id):
        if self.error:
            raise self.error
        self.expired.append(session_id)


def fake_client(sessions):
    return SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=sessions))
    )


def test_line_itemsは明細の単価と円とorder_idで作られる(
    app, producer, customer, make_product, monkeypatch
):
    tomato = make_product(producer, name="トマト", price=300)
    miso = make_product(producer, name="味噌", price=1300)
    order = create_order(customer, {tomato.id: 2, miso.id: 1})
    sessions = FakeSessions()
    monkeypatch.setattr(payment, "_client", lambda: fake_client(sessions))

    with app.test_request_context():
        session_id, url = payment.create_checkout_session(order)

    assert (session_id, url) == ("cs_test_123", "https://checkout.stripe.com/c/x")
    params = sessions.params
    assert params["mode"] == "payment"
    assert params["metadata"] == {"order_id": str(order.id)}
    assert params["customer_email"] == customer.email
    items = {i["price_data"]["product_data"]["name"]: i for i in params["line_items"]}
    # 円のまま（100倍しない）
    assert items["トマト"]["price_data"]["unit_amount"] == 300
    assert items["トマト"]["quantity"] == 2
    assert items["味噌"]["price_data"]["unit_amount"] == 1300
    assert all(i["price_data"]["currency"] == "jpy" for i in params["line_items"])
    assert params["success_url"].endswith(
        f"/orders/{order.id}/complete?session_id={{CHECKOUT_SESSION_ID}}"
    )
    assert params["cancel_url"].endswith(f"/orders/{order.id}/cancel")


def test_StripeのエラーはPaymentErrorになる(
    app, producer, customer, make_product, monkeypatch
):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    sessions = FakeSessions(error=stripe.APIConnectionError("network down"))
    monkeypatch.setattr(payment, "_client", lambda: fake_client(sessions))
    with app.test_request_context(), pytest.raises(PaymentError):
        payment.create_checkout_session(order)
    with pytest.raises(PaymentError):
        payment.expire_session("cs_test_123")


def test_expire_sessionはStripeのexpireを呼ぶ(app, monkeypatch):
    sessions = FakeSessions()
    monkeypatch.setattr(payment, "_client", lambda: fake_client(sessions))
    payment.expire_session("cs_test_123")
    assert sessions.expired == ["cs_test_123"]


def test_テストでは本物のStripeではなくフェイクが使われる(app, fake_payment):
    assert app.extensions["payment"] is fake_payment


# --- レビューの指摘で足したテスト ---


def test_決済ページを作れなくてもカートは残る(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer, name="残るトマト")
    fake_payment.error = PaymentError("決済ページを開けませんでした。")
    login(customer)
    order_via_cart(client, product)
    with client.session_transaction() as session:
        assert session["cart"] == {str(product.id): 1}


def test_期限切れの決済ページなら取り消せる(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer)
    login(customer)
    order_via_cart(client, product)
    order = db.session.scalar(db.select(Order))
    fake_payment.error = PaymentError("取り消せませんでした。")
    fake_payment.sessions[order.stripe_checkout_session_id] = {"status": "expired"}
    client.post(f"/orders/{order.id}/cancel")
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


def test_Stripeで支払い済みなら取り消さない(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer)
    login(customer)
    order_via_cart(client, product)
    order = db.session.scalar(db.select(Order))
    fake_payment.error = PaymentError("取り消せませんでした。")
    fake_payment.sessions[order.stripe_checkout_session_id] = {"status": "complete"}
    response = client.post(f"/orders/{order.id}/cancel", follow_redirects=True)
    assert "取り消せませんでした" in response.text
    db.session.refresh(order)
    assert order.status == OrderStatus.PENDING


def test_取り消し済みの注文をもう一度取り消しても何も変わらない(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer)
    login(customer)
    order_via_cart(client, product)
    order = db.session.scalar(db.select(Order))
    client.post(f"/orders/{order.id}/cancel")
    response = client.post(f"/orders/{order.id}/cancel", follow_redirects=True)
    assert "この注文は取り消せません（キャンセル）" in response.text
    assert fake_payment.expired == [order.stripe_checkout_session_id]


@pytest.mark.parametrize("path", ["cancel", "complete"])
def test_未ログインで中断と完了のページを開くとログイン画面へ(
    client, producer, customer, make_product, path
):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    response = client.get(f"/orders/{order.id}/{path}")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")
