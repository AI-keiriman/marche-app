from flask import Blueprint, abort, flash, redirect, render_template, session, url_for
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order
from marche.services import inventory
from marche.services.inventory import OrderError
from marche.views.cart import get_cart

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """注文を取得する。無ければ 404、本人と管理者以外は 403"""
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


@bp.route("/", methods=["POST"])
@login_required
def create():
    try:
        # 送られてくるのはカート（商品IDと数量）だけ。金額はフォームから受け取らない
        order = inventory.create_order(current_user, get_cart())
    except OrderError as e:
        flash(str(e))
        return redirect(url_for("cart.show"))
    session.pop("cart", None)
    return redirect(url_for("orders.show", order_id=order.id))


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)
