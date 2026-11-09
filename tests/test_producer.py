import io
from datetime import timedelta
from pathlib import Path

import pytest

from marche.extensions import db
from marche.models import Product, now_jst


def product_data(**overrides):
    """登録・編集のフォームに送る値。None を渡した項目は送らない"""
    data = {
        "name": "トマト 3個",
        "price": "300",
        "stock": "5",
        "description": "甘いトマトです",
        "is_published": "y",
    } | overrides
    return {key: value for key, value in data.items() if value is not None}


def all_products():
    return db.session.scalars(db.select(Product)).all()


# 判定は先頭のバイトしか見ないので、テストでは正しい先頭のバイト＋適当なバイトで作る
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
GIF = b"GIF89a" + b"\x00" * 100


def upload(data, filename):
    return (io.BytesIO(data), filename)


def uploaded_files(app):
    folder = Path(app.config["UPLOAD_FOLDER"])
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# --- 正常系 ---


def test_生産者は商品を登録できる(client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=product_data())
    assert response.status_code == 302
    assert response.headers["Location"] == "/producer/products"
    [product] = all_products()
    assert product.producer_id == producer.id
    assert product.name == "トマト 3個"
    assert product.price == 300
    assert product.stock == 5
    assert product.is_published is True
    assert product.image_filename is None


def test_在庫0で登録できる(client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(stock="0"))
    assert response.status_code == 302
    assert all_products()[0].stock == 0


def test_公開に印を付けなければ非公開で登録される(client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(is_published=None))
    assert all_products()[0].is_published is False


def test_一覧には自分の商品だけが出る(
    client, producer, other_producer, login, make_product
):
    make_product(producer, name="自分のトマト")
    make_product(other_producer, name="他人のキャベツ")
    login(producer)
    response = client.get("/producer/products")
    assert response.status_code == 200
    assert "自分のトマト" in response.text
    assert "他人のキャベツ" not in response.text


def test_自分の商品を編集できる(client, producer, login, make_product):
    product = make_product(producer)
    old = now_jst() - timedelta(days=1)
    product.updated_at = old
    db.session.commit()
    login(producer)

    assert client.get(f"/producer/products/{product.id}/edit").status_code == 200
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(name="ミニトマト", price="500"),
    )
    assert response.status_code == 302
    assert product.name == "ミニトマト"
    assert product.price == 500
    assert product.updated_at != old


def test_公開と非公開を切り替えられる(client, producer, login, make_product):
    product = make_product(producer, is_published=False)
    login(producer)
    client.post(f"/producer/products/{product.id}/toggle")
    assert product.is_published is True
    client.post(f"/producer/products/{product.id}/toggle")
    assert product.is_published is False


def test_自分の商品を削除できる(client, producer, login, make_product):
    product = make_product(producer)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 302
    assert all_products() == []


# --- 不正な入力 ---


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"price": "49"}, "価格は50〜100,000円で入力してください"),
        ({"price": "100001"}, "価格は50〜100,000円で入力してください"),
        ({"price": ""}, "価格を入力してください"),
        ({"price": "abc"}, "無効な整数です"),
        ({"stock": "1000"}, "在庫数は0〜999で入力してください"),
        ({"name": "あ" * 51}, "商品名は50文字以内で入力してください"),
        ({"name": ""}, "商品名を入力してください"),
    ],
)
def test_不正な入力では登録できない(client, producer, login, overrides, message):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(**overrides))
    assert response.status_code == 422
    assert message in response.text
    assert all_products() == []


def test_フォームに他人のproducer_idを混ぜても自分の商品になる(
    client, producer, other_producer, login
):
    login(producer)
    client.post(
        "/producer/products/new", data=product_data(producer_id=other_producer.id)
    )
    assert all_products()[0].producer_id == producer.id


def test_編集で不正な入力なら商品は変わらない(client, producer, login, make_product):
    product = make_product(producer, price=200)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit", data=product_data(price="10")
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.price == 200


# --- 権限なし ---


@pytest.mark.parametrize(
    ("method", "action"),
    [("get", "edit"), ("post", "edit"), ("post", "toggle"), ("post", "delete")],
)
def test_他の生産者の商品は操作できない(
    client, producer, other_producer, login, make_product, method, action
):
    product = make_product(other_producer, name="他人のキャベツ", is_published=False)
    login(producer)
    url = f"/producer/products/{product.id}/{action}"
    if method == "get":
        response = client.get(url)
    else:
        response = client.post(url, data=product_data(name="書き換え"))
    assert response.status_code == 403
    db.session.refresh(product)
    assert product.name == "他人のキャベツ"
    assert product.is_published is False


@pytest.mark.parametrize("method", ["get", "post"])
def test_購入者は商品を登録できない(client, customer, login, method):
    login(customer)
    response = getattr(client, method)("/producer/products/new", data=product_data())
    assert response.status_code == 403
    assert all_products() == []


def test_存在しない商品は404(client, producer, login):
    login(producer)
    assert client.get("/producer/products/999/edit").status_code == 404


# --- 写真 ---


def test_PNGの写真を付けて登録できる(app, client, producer, login):
    login(producer)
    response = client.post(
        "/producer/products/new",
        data=product_data(image=upload(PNG, "tomato.png")),
    )
    assert response.status_code == 302
    [product] = all_products()
    assert product.image_filename.endswith(".png")
    assert product.image_filename != "tomato.png"  # 元のファイル名は使わない
    assert uploaded_files(app) == [product.image_filename]


def test_写真を差し替えると古い写真が消える(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(image=upload(PNG, "a.png")))
    [product] = all_products()
    old = product.image_filename

    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(image=upload(JPEG, "b.jpg")),
    )
    assert response.status_code == 302
    assert product.image_filename != old
    assert product.image_filename.endswith(".jpg")
    assert uploaded_files(app) == [product.image_filename]


def test_写真を選ばずに編集すると今の写真が残る(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(image=upload(PNG, "a.png")))
    [product] = all_products()
    filename = product.image_filename

    client.post(f"/producer/products/{product.id}/edit", data=product_data())
    assert product.image_filename == filename
    assert uploaded_files(app) == [filename]


def test_編集画面に今の写真が出る(client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(image=upload(PNG, "a.png")))
    [product] = all_products()
    response = client.get(f"/producer/products/{product.id}/edit")
    assert f"/static/uploads/{product.image_filename}" in response.text


def test_商品を削除すると写真も消える(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(image=upload(PNG, "a.png")))
    [product] = all_products()
    client.post(f"/producer/products/{product.id}/delete")
    assert uploaded_files(app) == []


@pytest.mark.parametrize(
    ("data", "filename", "message"),
    [
        (b"not an image", "fake.png", "JPEG・PNG・WebP の画像を選んでください"),
        (GIF, "anim.gif", "JPEG・PNG・WebP の画像を選んでください"),
        (PNG + b"\x00" * (5 * 1024 * 1024), "big.png", "写真は5MB 以下にしてください"),
    ],
)
def test_不正な写真では登録できない(
    app, client, producer, login, data, filename, message
):
    login(producer)
    response = client.post(
        "/producer/products/new", data=product_data(image=upload(data, filename))
    )
    assert response.status_code == 422
    assert message in response.text
    assert all_products() == []
    assert uploaded_files(app) == []


def test_編集で中身が画像でなければ今の写真が残る(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(image=upload(PNG, "a.png")))
    [product] = all_products()
    filename = product.image_filename

    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(name="書き換え", image=upload(b"not an image", "x.png")),
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.name == "トマト 3個"
    assert product.image_filename == filename
    assert uploaded_files(app) == [filename]


def test_6MBを超えるリクエストは413(app, client, producer, login):
    login(producer)
    big = PNG + b"\x00" * (6 * 1024 * 1024)
    response = client.post(
        "/producer/products/new", data=product_data(image=upload(big, "huge.png"))
    )
    assert response.status_code == 413
    assert "ファイルが大きすぎます" in response.text
    assert all_products() == []
    assert uploaded_files(app) == []


def test_他の生産者の商品に写真を送っても保存されない(
    app, client, producer, other_producer, login, make_product
):
    product = make_product(other_producer)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(image=upload(PNG, "a.png")),
    )
    assert response.status_code == 403
    db.session.refresh(product)
    assert product.image_filename is None
    assert uploaded_files(app) == []
