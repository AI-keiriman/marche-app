from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("cart", __name__, url_prefix="/cart")


def get_cart() -> dict[int, int]:
    """セッションのカートを {商品ID: 数量} で返す（セッションは JSON なのでキーは文字列）"""
    return {int(k): int(v) for k, v in session.get("cart", {}).items()}


def save_cart(cart: dict[int, int]) -> None:
    # カートには利用者が決めてよい値（商品IDと数量）だけを入れる。金額は入れない
    session["cart"] = {str(k): v for k, v in cart.items() if v > 0}


def get_published_or_404(product_id: int) -> Product:
    product = db.get_or_404(Product, product_id)
    if not product.is_published:
        abort(404)
    return product


@bp.route("/")
def show():
    lines = []
    for product_id, quantity in get_cart().items():
        product = db.session.get(Product, product_id)
        if product is None or not product.is_published:
            continue  # 非公開になった商品は表示しない（注文のときにも弾かれる）
        lines.append(
            {
                "product": product,
                "quantity": quantity,
                "subtotal": product.price * quantity,
                "max_quantity": max(
                    quantity, min(MAX_QUANTITY, available_stock(product))
                ),
            }
        )
    total = sum(line["subtotal"] for line in lines)
    return render_template("cart/show.html", lines=lines, total=total)


@bp.route("/add/<int:product_id>", methods=["POST"])
def add(product_id: int):
    product = get_published_or_404(product_id)
    quantity = request.form.get("quantity", type=int)
    cart = get_cart()
    new_quantity = cart.get(product.id, 0) + (quantity or 0)
    limit = min(MAX_QUANTITY, available_stock(product))
    if quantity is None or quantity < 1 or not 1 <= new_quantity <= limit:
        flash(f"「{product.name}」は1〜{limit}個までカートに入れられます")
        return redirect(url_for("shop.show", product_id=product.id))
    cart[product.id] = new_quantity
    save_cart(cart)
    flash(f"「{product.name}」をカートに入れました")
    return redirect(url_for("cart.show"))


@bp.route("/update/<int:product_id>", methods=["POST"])
def update(product_id: int):
    quantity = request.form.get("quantity", type=int)
    if quantity is None:
        flash("数量は数字で入力してください")
        return redirect(url_for("cart.show"))
    cart = get_cart()
    if product_id not in cart:
        return redirect(url_for("cart.show"))
    if quantity <= 0:
        cart.pop(product_id)
        flash("カートから外しました")
    else:
        # 上限を超える数でも、ここでは入れておき、注文のときに create_order が確かめる
        cart[product_id] = min(quantity, MAX_QUANTITY)
        flash("数量を変更しました")
    save_cart(cart)
    return redirect(url_for("cart.show"))
