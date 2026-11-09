import pytest

from marche import create_app
from marche.extensions import db
from marche.models import Product, Role, User
from marche.services import payment

# 本物の外部サービス（Stripe、拡張で足す Claude API）は、テストでは呼ばない。
# フェイク（偽物）のクラスを create_app() の設定で差し替える。


class FakePayment:
    """本物の Stripe（services/payment.py）の代わり。同じ名前の関数を持ち、呼ばれた引数を記録する"""

    def __init__(self):
        self.created = []  # create_checkout_session が呼ばれた注文の ID
        self.expired = []  # expire_session が呼ばれた session_id
        self.sessions = {}  # session_id -> retrieve_session で返す dict（08 章）
        self.error = None  # 例外を入れておくと、それを投げる

    def create_checkout_session(self, order):
        if self.error:
            raise self.error
        session_id = f"cs_test_fake_{order.id}"
        self.created.append(order.id)
        return session_id, f"https://checkout.stripe.test/{session_id}"

    def retrieve_session(self, session_id):
        return self.sessions[session_id]

    def expire_session(self, session_id):
        if self.error:
            raise self.error
        self.expired.append(session_id)


@pytest.fixture
def fake_payment():
    return FakePayment()


@pytest.fixture(autouse=True)
def no_real_stripe(monkeypatch):
    """保険: テストの中で本物の Stripe のクライアントを作ろうとしたら、必ず失敗させる"""

    def refuse():
        raise AssertionError("テストで本物の Stripe を呼ぼうとしました")

    monkeypatch.setattr(payment, "_client", refuse)


@pytest.fixture
def app(fake_payment, tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            # テストの写真が marche/static/uploads に溜まらないように、一時フォルダーに保存する
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "STRIPE_SECRET_KEY": "sk_test_dummy",
            "PAYMENT_SERVICE": fake_payment,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(role, email, name):
    user = User(name=name, email=email, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def customer(app):
    return _make_user(Role.CUSTOMER, "customer@example.com", "購入者")


@pytest.fixture
def producer(app):
    return _make_user(Role.PRODUCER, "producer@example.com", "高原ファーム")


@pytest.fixture
def other_producer(app):
    return _make_user(Role.PRODUCER, "other@example.com", "別の農園")


@pytest.fixture
def admin(app):
    return _make_user(Role.ADMIN, "admin@example.com", "管理者")


@pytest.fixture
def make_product(app):
    """商品を1つ作る。make_product(producer, name="トマト", is_published=False) のように使う"""

    def _make(producer, **values):
        values = {
            "name": "ほうれん草 1束",
            "price": 200,
            "stock": 10,
            "is_published": True,
        } | values
        product = Product(producer_id=producer.id, **values)
        db.session.add(product)
        db.session.commit()
        return product

    return _make


@pytest.fixture
def login(client):
    def do_login(user):
        return client.post("/login", data={"email": user.email, "password": "password"})

    return do_login
