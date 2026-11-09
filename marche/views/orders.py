from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services import inventory
from marche.services.inventory import OrderError
from marche.services.payment import PaymentError
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
    payment = current_app.extensions["payment"]
    try:
        session_id, checkout_url = payment.create_checkout_session(order)
    except PaymentError as e:
        # 決済ページを作れなかった注文は、在庫を押さえ続けないように取り消す
        inventory.cancel(order)
        flash(str(e))
        return redirect(url_for("cart.show"))
    order.stripe_checkout_session_id = session_id
    db.session.commit()
    # 決済ページを作れてからカートを空にする（失敗したときは入れ直さずに済むように）
    session.pop("cart", None)
    # 303: POST の結果として、別の URL（Stripe の決済ページ）を GET で開いてもらう
    return redirect(checkout_url, code=303)


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)


def expire_checkout(session_id: str) -> None:
    """決済ページを使えなくする。既に期限切れなら、それでよい。支払い済みなどは PaymentError"""
    payment = current_app.extensions["payment"]
    try:
        payment.expire_session(session_id)
    except PaymentError:
        # expire は開いている Session にしか効かない。31分を過ぎて期限切れなら取り消してよい。
        # それ以外（complete = 支払い済みなど）は取り消さない
        checkout = payment.retrieve_session(session_id)
        if checkout["status"] != "expired":
            raise


@bp.route("/<int:order_id>/complete")
@login_required
def complete(order_id: int):
    """Stripe の決済ページで支払ったあとに戻ってくる画面（支払いの確定は 08 章で作る）"""
    order = get_own_order_or_403(order_id)
    return render_template("orders/complete.html", order=order)


@bp.route("/<int:order_id>/cancel", methods=["GET", "POST"])
@login_required
def cancel(order_id: int):
    """Stripe の決済ページで「戻る」を押したときの画面"""
    order = get_own_order_or_403(order_id)
    if request.method == "POST":
        # session_id が無い注文は、決済ページの URL を利用者に渡していないので、expire せずに取り消してよい
        if order.status == OrderStatus.PENDING and order.stripe_checkout_session_id:
            try:
                # 決済ページを使えなくしてから取り消す（取り消した後に支払われないように）
                expire_checkout(order.stripe_checkout_session_id)
            except PaymentError as e:
                flash(str(e))
                return redirect(url_for("orders.show", order_id=order.id))
        if inventory.cancel(order):
            flash("注文を取り消しました")
        else:
            flash(f"この注文は取り消せません（{order.status_label}）")
        return redirect(url_for("orders.show", order_id=order.id))
    return render_template("orders/cancel.html", order=order)
