from datetime import datetime

import pytest

from marche.extensions import db
from marche.models import JST, Order, OrderItem, OrderStatus
from marche.services.inventory import MAX_QUANTITY, OrderError, create_order


def all_orders():
    return db.session.scalars(db.select(Order)).all()


# --- create_order（サービス） ---


def test_合計はDBの価格と数量で計算される(producer, customer, make_product):
    tomato = make_product(producer, name="トマト", price=300)
    miso = make_product(producer, name="味噌", price=1300)
    order = create_order(customer, {tomato.id: 2, miso.id: 1})
    assert order.status == OrderStatus.PENDING
    assert order.user_id == customer.id
    assert order.total_amount == 300 * 2 + 1300
    items = {item.product_name: item for item in order.items}
    assert items["トマト"].unit_price == 300
    assert items["トマト"].subtotal == 600
    assert items["味噌"].quantity == 1


def test_明細には注文時点の商品名と単価が残る(producer, customer, make_product):
    product = make_product(producer, name="トマト", price=300)
    order = create_order(customer, {product.id: 1})
    product.name = "値上げしたトマト"
    product.price = 500
    db.session.commit()
    db.session.refresh(order.items[0])
    assert order.items[0].product_name == "トマト"
    assert order.items[0].unit_price == 300


def test_在庫を超えると注文は作られない(producer, customer, make_product):
    product = make_product(producer, stock=2)
    with pytest.raises(OrderError, match="在庫が足りません"):
        create_order(customer, {product.id: 3})
    assert all_orders() == []


def test_支払い待ちの分も在庫から引いて確かめる(producer, customer, make_product):
    product = make_product(producer, stock=2)
    create_order(customer, {product.id: 2})
    with pytest.raises(OrderError, match="残り 0"):
        create_order(customer, {product.id: 1})
    assert len(all_orders()) == 1


def test_非公開の商品は注文できない(producer, customer, make_product):
    product = make_product(producer, is_published=False)
    with pytest.raises(OrderError, match="販売を終了"):
        create_order(customer, {product.id: 1})
    assert all_orders() == []


def test_存在しない商品は注文できない(customer):
    with pytest.raises(OrderError, match="販売を終了"):
        create_order(customer, {999: 1})


@pytest.mark.parametrize("quantity", [0, -1, MAX_QUANTITY + 1])
def test_数量が範囲外なら注文できない(producer, customer, make_product, quantity):
    product = make_product(producer, stock=100)
    with pytest.raises(OrderError, match="数量は1〜10"):
        create_order(customer, {product.id: quantity})
    assert all_orders() == []


def test_空のカートは注文できない(customer):
    with pytest.raises(OrderError, match="カートが空"):
        create_order(customer, {})


def test_1つでも駄目なら他の商品の明細も残らない(producer, customer, make_product):
    ok = make_product(producer, name="OK", stock=5)
    ng = make_product(producer, name="NG", stock=0)
    with pytest.raises(OrderError):
        create_order(customer, {ok.id: 1, ng.id: 1})
    db.session.commit()  # 作りかけがセッションに残っていれば、ここで保存されてしまう
    assert all_orders() == []
    assert db.session.scalars(db.select(OrderItem)).all() == []


# --- カートと注文の画面 ---


def test_カートに入れると合計が出る(client, producer, make_product):
    tomato = make_product(producer, name="トマト", price=300)
    miso = make_product(producer, name="味噌", price=1300)
    client.post(f"/cart/add/{tomato.id}", data={"quantity": "2"})
    client.post(f"/cart/add/{miso.id}", data={"quantity": "1"})
    response = client.get("/cart/")
    assert "トマト" in response.text
    assert "1,900円" in response.text


def test_数量を0にするとカートから外れる(client, producer, make_product):
    product = make_product(producer, name="トマト")
    client.post(f"/cart/add/{product.id}", data={"quantity": "2"})
    client.post(f"/cart/update/{product.id}", data={"quantity": "0"})
    assert "カートは空です" in client.get("/cart/").text


@pytest.mark.parametrize("quantity", ["0", "11", "abc", ""])
def test_範囲外の数量はカートに入らない(client, producer, make_product, quantity):
    product = make_product(producer, stock=50)
    response = client.post(f"/cart/add/{product.id}", data={"quantity": quantity})
    assert response.status_code == 302
    assert "カートは空です" in client.get("/cart/").text


def test_在庫を超える数はカートに入らない(client, producer, make_product):
    product = make_product(producer, stock=2)
    client.post(f"/cart/add/{product.id}", data={"quantity": "2"})
    client.post(f"/cart/add/{product.id}", data={"quantity": "1"})
    with client.session_transaction() as session:
        assert session["cart"] == {str(product.id): 2}


def test_非公開の商品はカートに入れられない(client, producer, make_product):
    product = make_product(producer, is_published=False)
    response = client.post(f"/cart/add/{product.id}", data={"quantity": "1"})
    assert response.status_code == 404


def test_カートから注文するとStripeへ移動する(
    client, producer, customer, make_product, login, fake_payment
):
    product = make_product(producer, price=300)
    login(customer)
    client.post(f"/cart/add/{product.id}", data={"quantity": "2"})
    # フォームに金額を混ぜて送っても無視される
    response = client.post("/orders/", data={"total_amount": "1", "unit_price": "1"})
    [order] = all_orders()
    assert response.status_code == 303
    assert response.headers["Location"] == (
        f"https://checkout.stripe.test/cs_test_fake_{order.id}"
    )
    assert order.stripe_checkout_session_id == f"cs_test_fake_{order.id}"
    assert fake_payment.created == [order.id]
    assert order.total_amount == 600
    assert order.items[0].unit_price == 300
    # 注文したらカートは空になる
    assert "カートは空です" in client.get("/cart/").text


def test_在庫が足りなければカートに戻る(
    client, producer, customer, make_product, login
):
    product = make_product(producer, stock=2)
    login(customer)
    client.post(f"/cart/add/{product.id}", data={"quantity": "2"})
    product.stock = 1
    db.session.commit()
    response = client.post("/orders/", follow_redirects=True)
    assert "在庫が足りません" in response.text
    assert all_orders() == []


def test_未ログインで注文するとログイン画面へ(client, producer, make_product):
    product = make_product(producer)
    client.post(f"/cart/add/{product.id}", data={"quantity": "1"})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")
    assert all_orders() == []


def test_未ログインのカートにはログインして注文するリンクが出る(
    client, producer, make_product
):
    product = make_product(producer)
    client.post(f"/cart/add/{product.id}", data={"quantity": "1"})
    text = client.get("/cart/").text
    assert "ログインして注文する" in text
    assert "/login?next=/cart/" in text
    assert "注文して支払いへ進む" not in text


def test_自分の注文詳細を見られる(client, producer, customer, make_product, login):
    product = make_product(producer, name="トマト")
    order = create_order(customer, {product.id: 1})
    login(customer)
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 200
    assert "支払い待ち" in response.text


def test_他人の注文詳細は403(client, app, producer, customer, make_product, login):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    login(producer)
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_管理者は他人の注文詳細を見られる(
    client, producer, customer, admin, make_product, login
):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    login(admin)
    assert client.get(f"/orders/{order.id}").status_code == 200


def test_注文がある商品は削除できない(client, producer, customer, make_product, login):
    product = make_product(producer)
    create_order(customer, {product.id: 1})
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/delete", follow_redirects=True
    )
    assert "注文があるので削除できません" in response.text
    assert db.session.get(type(product), product.id) is not None


def test_非公開になった商品はカートから外れて残りは注文できる(
    client, producer, customer, make_product, login
):
    hidden = make_product(producer, name="非公開にする商品")
    kept = make_product(producer, name="残る商品", price=400)
    login(customer)
    client.post(f"/cart/add/{hidden.id}", data={"quantity": "1"})
    client.post(f"/cart/add/{kept.id}", data={"quantity": "1"})
    hidden.is_published = False
    db.session.commit()

    assert "販売を終了した商品をカートから外しました" in client.get("/cart/").text
    client.post("/orders/")
    [order] = all_orders()
    assert order.total_amount == 400


def test_注文詳細の日時は日本の形式(client, producer, customer, make_product, login):
    product = make_product(producer)
    order = create_order(customer, {product.id: 1})
    order.created_at = datetime(2026, 11, 14, 10, 0, tzinfo=JST).replace(tzinfo=None)
    db.session.commit()
    login(customer)
    assert "11月14日(土) 10:00" in client.get(f"/orders/{order.id}").text
