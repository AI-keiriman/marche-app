"""Stripe を呼ぶのはこのファイルだけ。ビューは current_app.extensions["payment"] 経由で呼ぶ"""

import time

import stripe
from flask import current_app, url_for

# Stripe の Checkout Session は30分より短くできない。時計のずれを見込んで31分
# （在庫の押さえ PENDING_MINUTES=35分 より先に、決済ページが使えなくなるようにする）
CHECKOUT_EXPIRES_MINUTES = 31


class PaymentError(Exception):
    """決済サービスの呼び出しに失敗した。メッセージは画面にそのまま出せる日本語"""


def _client() -> stripe.StripeClient:
    # stripe.api_key = ... のようにモジュール全体の設定は書き換えず、キーを持ったクライアントを作る
    return stripe.StripeClient(current_app.config["STRIPE_SECRET_KEY"])


def create_checkout_session(order) -> tuple[str, str]:
    """Checkout Session を作り、(session_id, 決済ページの URL) を返す"""
    line_items = [
        {
            "quantity": item.quantity,
            "price_data": {
                "currency": "jpy",
                # 金額は注文明細（DB）から取る。日本円は小数の無い通貨なので円のまま（100倍しない）
                "unit_amount": item.unit_price,
                "product_data": {"name": item.product_name},
            },
        }
        for item in order.items
    ]
    complete_url = url_for("orders.complete", order_id=order.id, _external=True)
    try:
        session = _client().v1.checkout.sessions.create(
            {
                "mode": "payment",
                "line_items": line_items,
                "customer_email": order.user.email,
                "metadata": {"order_id": str(order.id)},
                # {CHECKOUT_SESSION_ID} は Stripe が置き換える。f-string にしないで + でつなぐ
                "success_url": complete_url + "?session_id={CHECKOUT_SESSION_ID}",
                "cancel_url": url_for(
                    "orders.cancel", order_id=order.id, _external=True
                ),
                "expires_at": int(time.time()) + CHECKOUT_EXPIRES_MINUTES * 60,
            }
        )
    except stripe.StripeError as e:
        current_app.logger.warning("Stripe の呼び出しに失敗: %r", e)
        raise PaymentError(
            "決済ページを開けませんでした。時間をおいてもう一度お試しください。"
        ) from e
    return session.id, session.url


def retrieve_session(session_id: str):
    """Checkout Session を取得する（支払い完了ページで支払い状態を確かめるため。08 章）"""
    try:
        return _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError as e:
        current_app.logger.warning("Stripe の呼び出しに失敗: %r", e)
        raise PaymentError(
            "支払いの状態を確かめられませんでした。時間をおいて再読み込みしてください。"
        ) from e


def expire_session(session_id: str) -> None:
    """決済ページを使えなくする（注文を取り消す前に呼ぶ）"""
    try:
        _client().v1.checkout.sessions.expire(session_id)
    except stripe.StripeError as e:
        current_app.logger.warning("Stripe の呼び出しに失敗: %r", e)
        raise PaymentError(
            "注文を取り消せませんでした。支払いが済んでいる可能性があります。"
        ) from e
