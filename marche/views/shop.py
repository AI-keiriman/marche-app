from flask import Blueprint, abort, render_template
from flask_login import current_user

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("shop", __name__)


def can_view(product: Product) -> bool:
    """公開中なら誰でも。非公開は出品した生産者と管理者だけ"""
    if product.is_published:
        return True
    if not current_user.is_authenticated:
        return False
    return current_user.is_admin or product.producer_id == current_user.id


@bp.route("/")
def index():
    products = db.session.scalars(
        db.select(Product)
        .filter_by(is_published=True)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    # 商品ごとに SQL が走る（N+1）。8商品程度なら問題ないので、拡張 E16 で扱う
    stocks = {product.id: available_stock(product) for product in products}
    return render_template("shop/index.html", products=products, stocks=stocks)


@bp.route("/products/<int:product_id>")
def show(product_id: int):
    product = db.get_or_404(Product, product_id)
    if not can_view(product):
        abort(404)  # 非公開の商品は「存在しない」と同じ扱いにする
    available = available_stock(product)
    return render_template(
        "shop/show.html",
        product=product,
        available=available,
        max_quantity=min(MAX_QUANTITY, available),
    )
