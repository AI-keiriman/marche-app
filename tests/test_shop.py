from marche.extensions import db
from marche.models import Product, User


def test_一覧には公開中の商品だけが出る(client, producer, make_product):
    make_product(producer, name="公開のトマト", price=1200)
    make_product(producer, name="非公開のキャベツ", is_published=False)
    response = client.get("/")
    assert response.status_code == 200
    assert "公開のトマト" in response.text
    assert "1,200円" in response.text
    assert "高原ファーム" in response.text
    assert "非公開のキャベツ" not in response.text


def test_在庫0は売り切れと出る(client, producer, make_product):
    make_product(producer, name="ピクルス", stock=0)
    assert "売り切れ" in client.get("/").text


def test_公開中の商品の詳細を見られる(client, producer, make_product):
    product = make_product(producer, description="1行目\n2行目", stock=7)
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    assert "1行目\n2行目" in response.text
    assert "いま買える数: 7" in response.text


def test_紹介文のタグはエスケープされる(client, producer, make_product):
    product = make_product(producer, description="<script>alert(1)</script>")
    response = client.get(f"/products/{product.id}")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_非公開の詳細は未ログインには404(client, producer, make_product):
    product = make_product(producer, is_published=False)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は購入者には404(client, producer, customer, login, make_product):
    product = make_product(producer, is_published=False)
    login(customer)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は他の生産者には404(
    client, producer, other_producer, login, make_product
):
    product = make_product(producer, is_published=False)
    login(other_producer)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は出品した生産者には見える(client, producer, login, make_product):
    product = make_product(producer, is_published=False)
    login(producer)
    assert client.get(f"/products/{product.id}").status_code == 200


def test_非公開の詳細は管理者には見える(client, producer, admin, login, make_product):
    product = make_product(producer, is_published=False)
    login(admin)
    assert client.get(f"/products/{product.id}").status_code == 200


def test_存在しない商品の詳細は404(client):
    assert client.get("/products/999").status_code == 404


def test_seedで5人と8商品が入る(app, producer):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["seed", "--yes"])
    assert result.exit_code == 0
    # 2回実行しても増えない（既存のデータを消してから入れる）
    runner.invoke(args=["seed", "--yes"])
    assert len(db.session.scalars(db.select(User)).all()) == 5
    products = db.session.scalars(db.select(Product)).all()
    assert len(products) == 8
    assert sum(not p.is_published for p in products) == 1
