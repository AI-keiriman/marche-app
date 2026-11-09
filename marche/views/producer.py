from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from marche.decorators import role_required
from marche.extensions import db
from marche.forms import ProductForm
from marche.models import OrderItem, Product, Role
from marche.uploads import delete_image, detect_media_type, save_image

bp = Blueprint("producer", __name__, url_prefix="/producer")


def get_own_product_or_403(product_id: int) -> Product:
    """商品を取得する。無ければ 404、ログイン中の生産者の商品でなければ 403"""
    product = db.get_or_404(Product, product_id)
    if product.producer_id != current_user.id:
        abort(403)
    return product


def apply_form(form: ProductForm, product: Product) -> None:
    """フォームの値を商品に入れる。producer_id はフォームから受け取らない"""
    product.name = form.name.data.strip()
    product.price = form.price.data
    product.stock = form.stock.data
    product.description = (form.description.data or "").strip()
    product.is_published = form.is_published.data


def read_image(form: ProductForm) -> tuple[bytes, str] | None:
    """写真が選ばれていれば、中身を確かめて (データ, 種類) を返す。選ばれていなければ None。
    中身が JPEG / PNG / WebP でなければ、フォームのエラーを足して None を返す"""
    file = form.image.data
    if not file:
        return None
    data = file.read()
    media_type = detect_media_type(data)
    if media_type is None:
        form.image.errors.append("JPEG・PNG・WebP の画像を選んでください")
        return None
    return data, media_type


@bp.route("/products")
@role_required(Role.PRODUCER)
def index():
    products = db.session.scalars(
        db.select(Product)
        .filter_by(producer_id=current_user.id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    return render_template("producer/index.html", products=products)


@bp.route("/products/new", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def new():
    form = ProductForm()
    if form.validate_on_submit():
        image = read_image(form)
        if not form.image.errors:
            product = Product(producer_id=current_user.id)
            apply_form(form, product)
            if image:
                product.image_filename = save_image(*image)
            db.session.add(product)
            db.session.commit()
            flash(f"「{product.name}」を登録しました")
            return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template("producer/form.html", form=form, product=None), status


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def edit(product_id: int):
    product = get_own_product_or_403(product_id)
    form = ProductForm(obj=product)
    if form.validate_on_submit():
        image = read_image(form)
        if not form.image.errors:
            apply_form(form, product)
            old_filename = None
            if image:
                old_filename = product.image_filename
                product.image_filename = save_image(*image)
            db.session.commit()
            # 古い写真は、DB の更新が済んでから消す（先に消すと、失敗したときに写真が無くなる）
            delete_image(old_filename)
            flash(f"「{product.name}」を更新しました")
            return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template("producer/form.html", form=form, product=product), status


@bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@role_required(Role.PRODUCER)
def toggle(product_id: int):
    product = get_own_product_or_403(product_id)
    product.is_published = not product.is_published
    db.session.commit()
    state = "公開" if product.is_published else "非公開に"
    flash(f"「{product.name}」を{state}しました")
    return redirect(url_for("producer.index"))


@bp.route("/products/<int:product_id>/delete", methods=["POST"])
@role_required(Role.PRODUCER)
def delete(product_id: int):
    product = get_own_product_or_403(product_id)
    # 注文がある商品は削除させない（SQLite は restrict を確かめないので、ここでも確かめる）
    has_orders = db.session.scalar(
        db.select(OrderItem.id).filter_by(product_id=product.id).limit(1)
    )
    if has_orders:
        flash(f"「{product.name}」は注文があるので削除できません。非公開にしてください")
        return redirect(url_for("producer.index"))
    name, filename = product.name, product.image_filename
    db.session.delete(product)
    db.session.commit()
    delete_image(filename)
    flash(f"「{name}」を削除しました")
    return redirect(url_for("producer.index"))
