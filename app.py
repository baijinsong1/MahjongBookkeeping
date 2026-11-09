from collections import defaultdict
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

import click
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, date, timedelta
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)


def load_or_create_secret_key():
    configured_secret = os.environ.get("SECRET_KEY", "").strip()
    if configured_secret:
        return configured_secret

    secret_path = Path(app.instance_path) / "secret_key"
    if secret_path.exists():
        saved_secret = secret_path.read_text(encoding="utf-8").strip()
        if len(saved_secret) >= 32:
            return saved_secret

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    generated_secret = secrets.token_hex(32)
    secret_path.write_text(generated_secret, encoding="utf-8")
    os.chmod(secret_path, 0o600)
    return generated_secret


app.config.update(
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///data.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=load_or_create_secret_key(),
    SESSION_COOKIE_NAME="majiang_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=1024 * 1024,
    ADMIN_AUTH_FILE=os.environ.get(
        "ADMIN_AUTH_FILE", str(Path(app.instance_path) / "admin.json")
    ),
)

if os.environ.get("TRUST_PROXY_HEADERS", "0") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

db = SQLAlchemy(app)
migrate = Migrate(app, db)


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True)


class Goods(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    price = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=True)  # 新增：是否启用


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer)
    start_time = db.Column(db.DateTime, default=datetime.now)
    end_time = db.Column(db.DateTime)
    charge_type = db.Column(db.String(20))
    table_fee = db.Column(db.Float, default=0)
    member_deduction = db.Column(db.Float, default=0, nullable=False)
    super_discount = db.Column(db.Float, default=0, nullable=False)
    total = db.Column(db.Float, default=0)
    remark = db.Column(db.String(200))
    finished = db.Column(db.Boolean, default=False)


class OrderGoods(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer)
    person_id = db.Column(db.Integer)
    goods_id = db.Column(db.Integer)
    num = db.Column(db.Integer, default=1)
    subtotal = db.Column(db.Float)


class OrderPerson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer)
    name = db.Column(db.String(50))
    is_member = db.Column(db.Boolean, default=False, nullable=False)
    member_id = db.Column(db.Integer)


class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    fee_balance = db.Column(db.Float, default=0, nullable=False)
    is_super = db.Column(db.Boolean, default=False, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    create_time = db.Column(db.DateTime, default=datetime.now)
    title = db.Column(db.String(100))
    amount = db.Column(db.Float)
    remark = db.Column(db.String(200))


class Revenue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    create_time = db.Column(db.DateTime, default=datetime.now, nullable=False)
    category = db.Column(db.String(30), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    member_id = db.Column(db.Integer)
    order_id = db.Column(db.Integer)
    remark = db.Column(db.String(200))


def get_fee_info(charge_type):
    if charge_type in ("gear80", "台费80"):
        return 80, 20
    elif charge_type in ("gear100", "台费100"):
        return 100, 25
    else:
        return 0, 0


def calculate_table_split(charge_type, member_count, super_member_count=0):
    table_fee, per_person = get_fee_info(charge_type)
    member_deduction = min(table_fee, per_person * max(0, member_count))
    remaining_fee = max(0, table_fee - member_deduction)
    super_discount = min(remaining_fee, per_person * max(0, super_member_count))
    cash_table_fee = max(0, remaining_fee - super_discount)
    return table_fee, per_person, member_deduction, super_discount, cash_table_fee


def parse_positive_money(raw_value):
    try:
        amount = round(float((raw_value or "").strip()), 2)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


CHARGE_TYPE_LABELS = {
    "gear80": "台费 80",
    "gear100": "台费 100",
    "台费80": "台费 80",
    "台费100": "台费 100",
}

MAX_ORDER_GOODS_QUANTITY = 999
MEMBER_FEE_CATEGORY = "member_fee"
LOGIN_MAX_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 300
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
PUBLIC_ENDPOINTS = frozenset({"login", "static", "health"})

_login_failures = {}


def build_env_admin_credentials():
    password_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password_hash and not password:
        return None
    username = os.environ.get("ADMIN_USERNAME", "admin").strip()
    if not username:
        return None
    if not password_hash:
        password_hash = generate_password_hash(
            password, method="pbkdf2:sha256:600000"
        )
    return {"username": username, "password_hash": password_hash}


ENV_ADMIN_CREDENTIALS = build_env_admin_credentials()


def load_admin_credentials():
    if ENV_ADMIN_CREDENTIALS:
        return ENV_ADMIN_CREDENTIALS

    auth_path = Path(app.config["ADMIN_AUTH_FILE"])
    if not auth_path.exists():
        return None
    try:
        credentials = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        app.logger.error("管理员凭据文件无法读取：%s", auth_path)
        return None

    username = str(credentials.get("username", "")).strip()
    password_hash = str(credentials.get("password_hash", "")).strip()
    if not username or not password_hash:
        app.logger.error("管理员凭据文件格式无效：%s", auth_path)
        return None
    return {"username": username, "password_hash": password_hash}


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf_token():
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("csrf_token") or request.headers.get(
        "X-CSRF-Token", ""
    )
    return bool(
        expected
        and supplied
        and hmac.compare_digest(str(expected), str(supplied))
    )


def safe_next_url(target):
    parsed = urlsplit(target or "")
    if (
        not target
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or parsed.scheme
        or parsed.netloc
    ):
        return url_for("index")
    return target


def login_attempt_key():
    return request.remote_addr or "unknown"


def login_retry_after():
    key = login_attempt_key()
    now = time.monotonic()
    attempts = [
        attempt
        for attempt in _login_failures.get(key, [])
        if now - attempt < LOGIN_ATTEMPT_WINDOW_SECONDS
    ]
    _login_failures[key] = attempts
    if len(attempts) < LOGIN_MAX_ATTEMPTS:
        return 0
    return max(1, int(LOGIN_ATTEMPT_WINDOW_SECONDS - (now - attempts[0])))


def record_login_failure():
    key = login_attempt_key()
    _login_failures.setdefault(key, []).append(time.monotonic())


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def enforce_authentication_and_csrf():
    if request.method not in SAFE_METHODS and not validate_csrf_token():
        abort(400, description="CSRF 校验失败，请刷新页面后重试。")

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    if not session.get("authenticated"):
        next_target = request.path
        if request.query_string:
            next_target = f"{next_target}?{request.query_string.decode('utf-8')}"
        return redirect(url_for("login", next=next_target))
    return None


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'"
    )
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.cli.command("create-admin")
@click.option("--username", default="admin", prompt="管理员用户名")
@click.option(
    "--password",
    prompt="管理员密码",
    hide_input=True,
    confirmation_prompt=True,
)
def create_admin(username, password):
    """创建管理员账号，密码以哈希形式保存到 instance/admin.json。"""
    username = username.strip()
    if not username or len(username) > 50:
        raise click.ClickException("管理员用户名不能为空且不能超过 50 个字符。")
    if len(password) < 12:
        raise click.ClickException("管理员密码至少需要 12 个字符。")

    auth_path = Path(app.config["ADMIN_AUTH_FILE"])
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = auth_path.with_suffix(".tmp")
    payload = {
        "username": username,
        "password_hash": generate_password_hash(
            password, method="pbkdf2:sha256:600000"
        ),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, auth_path)
    click.echo(f"管理员账号「{username}」已创建：{auth_path}")
    if ENV_ADMIN_CREDENTIALS:
        click.echo(
            "注意：当前 ADMIN_USERNAME/ADMIN_PASSWORD 环境变量优先于此文件。"
        )


@app.route("/login", methods=["GET", "POST"])
def login():
    credentials = load_admin_credentials()
    if request.method == "POST":
        if not credentials:
            flash("管理员账号尚未初始化，请先在终端运行 create-admin 命令。")
            return (
                render_template("login.html", auth_configured=False),
                503,
            )

        retry_after = login_retry_after()
        if retry_after:
            flash(f"登录尝试过多，请在 {retry_after} 秒后重试。")
            return render_template("login.html", auth_configured=True), 429

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        username_ok = hmac.compare_digest(
            username.encode("utf-8"), credentials["username"].encode("utf-8")
        )
        try:
            password_ok = check_password_hash(
                credentials["password_hash"], password
            )
        except (ValueError, AttributeError):
            app.logger.error("管理员密码哈希格式无效或当前运行环境不支持")
            password_ok = False
        if username_ok and password_ok:
            _login_failures.pop(login_attempt_key(), None)
            next_url = safe_next_url(request.args.get("next", ""))
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            session["admin_username"] = credentials["username"]
            csrf_token()
            flash("登录成功", "success")
            return redirect(next_url)

        record_login_failure()
        flash("用户名或密码错误")
        return render_template("login.html", auth_configured=True), 401

    if session.get("authenticated"):
        return redirect(safe_next_url(request.args.get("next", "")))
    return render_template("login.html", auth_configured=bool(credentials))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("已安全退出", "success")
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok"}


def add_member_fee_revenue(member, amount, title, create_time=None):
    db.session.add(
        Revenue(
            create_time=create_time or datetime.now(),
            category=MEMBER_FEE_CATEGORY,
            title=title,
            amount=round(amount, 2),
            member_id=member.id,
            remark=f"会员：{member.name}",
        )
    )


def ensure_database_schema():
    db.create_all()
    person_columns = {
        column["name"] for column in inspect(db.engine).get_columns("order_person")
    }
    if "is_member" not in person_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE order_person "
                    "ADD COLUMN is_member BOOLEAN NOT NULL DEFAULT 0"
                )
            )
    if "member_id" not in person_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE order_person ADD COLUMN member_id INTEGER")
            )

    member_columns = {
        column["name"] for column in inspect(db.engine).get_columns("member")
    }
    if "fee_balance" not in member_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE member "
                    "ADD COLUMN fee_balance FLOAT NOT NULL DEFAULT 0"
                )
            )
    if "is_super" not in member_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE member "
                    "ADD COLUMN is_super BOOLEAN NOT NULL DEFAULT 0"
                )
            )

    order_columns = {
        column["name"] for column in inspect(db.engine).get_columns("order")
    }
    if "member_deduction" not in order_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    'ALTER TABLE "order" '
                    "ADD COLUMN member_deduction FLOAT NOT NULL DEFAULT 0"
                )
            )
    if "super_discount" not in order_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    'ALTER TABLE "order" '
                    "ADD COLUMN super_discount FLOAT NOT NULL DEFAULT 0"
                )
            )

    with db.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE order_person SET member_id = ("
                "SELECT member.id FROM member "
                "WHERE member.name = order_person.name"
                ") WHERE is_member = 1 AND member_id IS NULL"
            )
        )

    members = Member.query.filter(Member.fee_balance > 0).all()
    member_ids = [member.id for member in members]
    existing_member_ids = set()
    if member_ids:
        existing_member_ids = {
            member_id
            for (member_id,) in db.session.query(Revenue.member_id)
            .filter(
                Revenue.category == MEMBER_FEE_CATEGORY,
                Revenue.member_id.in_(member_ids),
            )
            .all()
        }
    for member in members:
        if member.id in existing_member_ids:
            continue
        add_member_fee_revenue(
            member,
            member.fee_balance,
            "历史会员费",
            create_time=member.create_time or datetime.now(),
        )

    valid_member_ids = {member_id for (member_id,) in db.session.query(Member.id)}
    for revenue in Revenue.query.filter_by(category=MEMBER_FEE_CATEGORY).all():
        if revenue.member_id not in valid_member_ids:
            db.session.delete(revenue)
    db.session.commit()


if os.environ.get("SKIP_DB_BOOTSTRAP") != "1":
    with app.app_context():
        ensure_database_schema()


def format_duration(start_time, end_time):
    if not start_time or not end_time:
        return "—"
    minutes = max(0, int((end_time - start_time).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


@app.route("/initdb", methods=["POST"])
def initdb():
    db.create_all()
    room_count = Room.query.count()
    goods_count = Goods.query.count()
    if room_count == 0:
        room_names = ["555", "666", "777", "888", "999"]
        for n in room_names:
            db.session.add(Room(name=n))
    if goods_count == 0:
        goods_list = [("可乐", 5), ("雪碧", 5), ("矿泉水", 3), ("槟榔", 20)]
        for name, price in goods_list:
            g = Goods(name=name, price=price, is_active=True)
            db.session.add(g)
    db.session.commit()
    return (
        "初始化完成，已有数据不会重复新增房间商品。后续表结构修改请使用 flask db migrate"
    )


@app.route("/")
def index():
    rooms = Room.query.all()
    orders = Order.query.filter_by(finished=False).all()
    order_map = {o.room_id: o for o in orders}
    return render_template("index.html", rooms=rooms, order_map=order_map)


# ========== 会员管理 ==========
@app.route("/members")
def member_list():
    members = Member.query.order_by(Member.create_time.desc(), Member.id.desc()).all()
    normal_members = [member for member in members if not member.is_super]
    super_member_count = sum(1 for member in members if member.is_super)
    return render_template(
        "members.html",
        members=members,
        normal_members=normal_members,
        super_member_count=super_member_count,
    )


@app.route("/members/add", methods=["POST"])
def member_add():
    name = request.form.get("name", "").strip()
    fee_balance = parse_positive_money(request.form.get("fee"))
    if not name:
        flash("请输入会员姓名")
        return redirect(url_for("member_list"))
    if len(name) > 50:
        flash("会员姓名不能超过 50 个字符")
        return redirect(url_for("member_list"))
    if fee_balance is None:
        flash("请输入有效的会员费金额")
        return redirect(url_for("member_list"))
    if Member.query.filter_by(name=name).first():
        flash("该会员已存在")
        return redirect(url_for("member_list"))

    member = Member(name=name, fee_balance=fee_balance, is_super=False)
    db.session.add(member)
    try:
        db.session.flush()
        add_member_fee_revenue(member, fee_balance, "新会员开卡")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("该会员已存在")
        return redirect(url_for("member_list"))

    flash(f"会员「{name}」已添加，会员费余额 ¥{fee_balance:.2f}", "success")
    return redirect(url_for("member_list"))


@app.route("/members/add_super", methods=["POST"])
def member_add_super():
    name = request.form.get("name", "").strip()
    if not name:
        flash("请输入超级会员姓名")
        return redirect(url_for("member_list"))
    if len(name) > 50:
        flash("超级会员姓名不能超过 50 个字符")
        return redirect(url_for("member_list"))
    if Member.query.filter_by(name=name).first():
        flash("该会员已存在")
        return redirect(url_for("member_list"))

    member = Member(name=name, fee_balance=0, is_super=True)
    db.session.add(member)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("该会员已存在")
        return redirect(url_for("member_list"))

    flash(f"超级会员「{name}」已添加，无需会员费且上桌免台费", "success")
    return redirect(url_for("member_list"))


@app.route("/members/recharge", methods=["POST"])
def member_recharge():
    member_id = request.form.get("member_id", type=int)
    member = db.session.get(Member, member_id) if member_id else None
    if not member:
        flash("会员不存在或已被删除")
        return redirect(url_for("member_list"))
    if member.is_super:
        flash("超级会员无需补充会员费")
        return redirect(url_for("member_list"))

    amount = parse_positive_money(request.form.get("amount"))
    if amount is None:
        flash("请输入有效的补充金额")
        return redirect(url_for("member_list"))

    member.fee_balance = round((member.fee_balance or 0) + amount, 2)
    add_member_fee_revenue(member, amount, "补充会员费")
    db.session.commit()
    flash(
        f"会员「{member.name}」已补充 ¥{amount:.2f}，"
        f"当前余额 ¥{member.fee_balance:.2f}",
        "success",
    )
    return redirect(url_for("member_list"))


@app.route("/members/delete/<int:mid>", methods=["POST"])
def member_delete(mid):
    member = db.session.get(Member, mid)
    if not member:
        flash("会员不存在或已被删除")
        return redirect(url_for("member_list"))
    active_person = (
        OrderPerson.query.join(Order, Order.id == OrderPerson.order_id)
        .filter(
            Order.finished.is_(False),
            OrderPerson.is_member.is_(True),
            OrderPerson.name == member.name,
        )
        .first()
    )
    if active_person:
        flash("该会员正在上桌，结账后才能删除")
        return redirect(url_for("member_list"))

    name = member.name
    member_fee_income = (
        db.session.query(func.sum(Revenue.amount))
        .filter_by(category=MEMBER_FEE_CATEGORY, member_id=member.id)
        .scalar()
        or 0
    )
    Revenue.query.filter_by(
        category=MEMBER_FEE_CATEGORY, member_id=member.id
    ).delete(synchronize_session=False)
    db.session.delete(member)
    db.session.commit()
    if member_fee_income:
        flash(
            f"会员「{name}」已删除，已撤销会员费收入 ¥{member_fee_income:.2f}",
            "success",
        )
    else:
        flash(f"会员「{name}」已删除", "success")
    return redirect(url_for("member_list"))


# ========== 商品管理 新增路由 ==========
@app.route("/goods")
def goods_list():
    goods = Goods.query.all()
    return render_template("goods.html", goods=goods)


@app.route("/goods/add", methods=["POST"])
def goods_add():
    name = request.form.get("name", "").strip()
    price = request.form.get("price")
    if not name or not price:
        flash("名称和价格不能为空")
        return redirect(url_for("goods_list"))
    try:
        price = float(price)
    except:
        flash("价格格式错误")
        return redirect(url_for("goods_list"))
    g = Goods(name=name, price=price, is_active=True)
    db.session.add(g)
    db.session.commit()
    flash("商品添加成功")
    return redirect(url_for("goods_list"))


@app.route("/goods/toggle/<int:gid>", methods=["POST"])
def goods_toggle(gid):
    g = Goods.query.get_or_404(gid)
    g.is_active = not g.is_active
    db.session.commit()
    flash("状态已切换")
    return redirect(url_for("goods_list"))


@app.route("/goods/delete/<int:gid>", methods=["POST"])
def goods_delete(gid):
    g = Goods.query.get_or_404(gid)
    # 判断该商品是否已经被订单使用，有记录就不让删
    used = OrderGoods.query.filter_by(goods_id=gid).first()
    if used:
        flash("该商品已经存在订单记录，不能删除，只能停用！")
        return redirect(url_for("goods_list"))
    db.session.delete(g)
    db.session.commit()
    flash("商品已删除")
    return redirect(url_for("goods_list"))


# ----------------【新增路由：空闲包间预览页】----------------
@app.route("/room/preview/<int:room_id>")
def room_preview(room_id):
    room = Room.query.get_or_404(room_id)
    # 判断这个包间是否已经有未结订单
    exist_order = Order.query.filter_by(room_id=room_id, finished=False).first()
    if exist_order:
        # 已经开台，直接跳转到订单详情
        return redirect(url_for("order_detail", oid=exist_order.id))
    # 空闲包间，渲染预览页面，不传order
    return render_template("room_preview.html", room=room)


@app.route("/order/open", methods=["POST"])
def order_open():
    try:
        room_id = int(request.form.get("room_id"))
        charge_type = request.form.get("charge_type", "")
        if charge_type not in ("gear80", "gear100"):
            flash("请选择台费类型")
            return redirect(url_for("room_preview", room_id=room_id))
        # 二次校验防止重复开台
        exist_order = Order.query.filter_by(room_id=room_id, finished=False).first()
        if exist_order:
            flash("该包间已经开台！")
            return redirect(url_for("order_detail", oid=exist_order.id))

        # ✅ 新建全新空白订单，没有任何人员、商品
        new_order = Order(room_id=room_id, charge_type=charge_type)
        db.session.add(new_order)
        db.session.commit()
        return redirect(url_for("order_detail", oid=new_order.id))
    except Exception as e:
        flash("开台失败：参数错误")
        return redirect("/")


@app.route("/order/<int:oid>")
def order_detail(oid):
    order = db.session.get(Order, oid)
    if not order:
        flash("订单不存在")
        return redirect("/")

    room = db.session.get(Room, order.room_id)
    goods_list = Goods.query.filter_by(is_active=True).all()
    persons = OrderPerson.query.filter_by(order_id=oid).all()
    member_ids = {person.member_id for person in persons if person.member_id}
    member_records = {
        member.id: member
        for member in (
            Member.query.filter(Member.id.in_(member_ids)).all() if member_ids else []
        )
    }
    super_person_ids = {
        person.id
        for person in persons
        if person.is_member
        and person.member_id in member_records
        and member_records[person.member_id].is_super
    }
    super_member_count = len(super_person_ids)
    normal_member_count = sum(
        1
        for person in persons
        if person.is_member and person.id not in super_person_ids
    )
    member_count = normal_member_count + super_member_count
    members_on_order = {person.name for person in persons}
    available_members = [
        member
        for member in Member.query.order_by(Member.name.asc()).all()
        if member.name not in members_on_order
    ]

    order_goods = (
        db.session.query(OrderGoods, Goods)
        .select_from(OrderGoods)
        .join(Goods, OrderGoods.goods_id == Goods.id)
        .filter(OrderGoods.order_id == oid)
        .all()
    )

    person_goods_sum = {}
    person_items = {}
    for p in persons:
        person_goods_sum[p.id] = 0
        person_items[p.id] = []

    for og, g in order_goods:
        pid = og.person_id
        if pid in person_goods_sum:
            person_goods_sum[pid] += og.subtotal
            person_items[pid].append((og, g))

    full_table_fee, per_table, member_deduction, super_discount, cash_table_fee = (
        calculate_table_split(
            order.charge_type, normal_member_count, super_member_count
        )
    )
    member_balances = {
        person.id: (
            member_records[person.member_id].fee_balance
            if person.member_id in member_records
            else 0
        )
        for person in persons
        if person.is_member
    }
    person_table_fee = {
        person.id: 0 if person.id in super_person_ids else per_table
        for person in persons
    }
    person_total = {}
    for pid in person_goods_sum:
        person_total[pid] = person_goods_sum[pid] + person_table_fee[pid]

    return render_template(
        "order.html",
        order=order,
        room=room,
        charge_label=CHARGE_TYPE_LABELS.get(
            order.charge_type, order.charge_type or "未设置"
        ),
        goods_list=goods_list,
        persons=persons,
        person_goods_sum=person_goods_sum,
        person_total=person_total,
        person_items=person_items,
        per_table=per_table,
        member_count=member_count,
        normal_member_count=normal_member_count,
        super_member_count=super_member_count,
        super_person_ids=super_person_ids,
        person_table_fee=person_table_fee,
        full_table_fee=full_table_fee,
        member_deduction=member_deduction,
        super_discount=super_discount,
        cash_table_fee=cash_table_fee,
        member_balances=member_balances,
        available_members=available_members,
    )


@app.route("/order/add_person", methods=["POST"])
def order_add_person():
    oid = request.form.get("oid", type=int)
    order = db.session.get(Order, oid) if oid else None
    if not order:
        flash("订单不存在或已被删除")
        return redirect(url_for("order_list_page"))
    if order.finished:
        flash("订单已结账，不能继续添加人员")
        return redirect(url_for("order_list_page"))

    name = request.form.get("name", "").strip()
    if not name:
        flash("请输入人员姓名")
        return redirect(url_for("order_detail", oid=order.id))
    if len(name) > 50:
        flash("人员姓名不能超过 50 个字符")
        return redirect(url_for("order_detail", oid=order.id))
    if Member.query.filter_by(name=name).first():
        flash("该姓名已登记为会员，请使用“会员上桌”添加")
        return redirect(url_for("order_detail", oid=order.id))

    p = OrderPerson(order_id=order.id, name=name, is_member=False)
    db.session.add(p)
    db.session.commit()
    return redirect(url_for("order_detail", oid=order.id))


@app.route("/order/add_member", methods=["POST"])
def order_add_member():
    oid = request.form.get("oid", type=int)
    order = db.session.get(Order, oid) if oid else None
    if not order:
        flash("订单不存在或已被删除")
        return redirect(url_for("order_list_page"))
    if order.finished:
        flash("订单已结账，不能继续添加会员")
        return redirect(url_for("order_list_page"))

    member_id = request.form.get("member_id", type=int)
    member = db.session.get(Member, member_id) if member_id else None
    if not member:
        flash("请选择有效会员")
        return redirect(url_for("order_detail", oid=order.id))

    if OrderPerson.query.filter_by(order_id=order.id, member_id=member.id).first():
        flash("该会员已在当前订单中")
        return redirect(url_for("order_detail", oid=order.id))

    active_elsewhere = (
        OrderPerson.query.join(Order, Order.id == OrderPerson.order_id)
        .filter(
            Order.finished.is_(False),
            OrderPerson.member_id == member.id,
            Order.id != order.id,
        )
        .first()
    )
    if active_elsewhere:
        flash("该会员已在其他包间上桌")
        return redirect(url_for("order_detail", oid=order.id))

    _, per_table, _, _, _ = calculate_table_split(order.charge_type, 1)
    if not member.is_super and (member.fee_balance or 0) < per_table:
        flash(f"会员费余额不足，请先补充（本单需扣 ¥{per_table:.2f}）")
        return redirect(url_for("order_detail", oid=order.id))

    person = OrderPerson(
        order_id=order.id,
        name=member.name,
        is_member=True,
        member_id=member.id,
    )
    db.session.add(person)
    db.session.commit()
    if member.is_super:
        flash(f"超级会员「{member.name}」已上桌，本单免台费", "success")
    else:
        flash(f"会员「{member.name}」已上桌，个人台费将从会员费扣除", "success")
    return redirect(url_for("order_detail", oid=order.id))


# ========= 修改：删除人员改为POST接口（原来GET不安全） =========
@app.route("/order/del_person", methods=["POST"])
def order_del_person():
    oid = int(request.form.get("oid"))
    pid = int(request.form.get("pid"))
    # 删除这个人对应的所有商品记录
    OrderGoods.query.filter_by(order_id=oid, person_id=pid).delete()
    # 删除人员本身
    p = OrderPerson.query.get_or_404(pid)
    db.session.delete(p)
    db.session.commit()
    flash("人员已移除")
    return redirect(url_for("order_detail", oid=oid))


@app.route("/order/add_goods", methods=["POST"])
def order_add_goods():
    oid = request.form.get("oid", type=int)
    order = db.session.get(Order, oid) if oid else None
    if not order:
        flash("订单不存在或已被删除")
        return redirect(url_for("order_list_page"))
    if order.finished:
        flash("订单已结账，不能继续添加商品")
        return redirect(url_for("order_list_page"))

    person_id = request.form.get("pid", type=int)
    person = (
        OrderPerson.query.filter_by(id=person_id, order_id=order.id).first()
        if person_id
        else None
    )
    if not person:
        flash("请选择该订单内的有效人员")
        return redirect(url_for("order_detail", oid=order.id))

    goods_id = request.form.get("goods_id", type=int)
    goods = db.session.get(Goods, goods_id) if goods_id else None
    if not goods or not goods.is_active:
        flash("商品不存在或已停用")
        return redirect(url_for("order_detail", oid=order.id))

    num = request.form.get("num", type=int)
    if num is None or not 1 <= num <= MAX_ORDER_GOODS_QUANTITY:
        flash(f"商品数量必须是 1 到 {MAX_ORDER_GOODS_QUANTITY} 之间的整数")
        return redirect(url_for("order_detail", oid=order.id))

    if (
        goods.price is None
        or not math.isfinite(goods.price)
        or goods.price < 0
    ):
        flash("商品价格异常，无法添加")
        return redirect(url_for("order_detail", oid=order.id))

    subtotal = round(goods.price * num, 2)
    og = OrderGoods(
        order_id=order.id,
        person_id=person.id,
        goods_id=goods.id,
        num=num,
        subtotal=subtotal,
    )
    db.session.add(og)
    db.session.commit()
    return redirect(url_for("order_detail", oid=order.id))


@app.route("/order/close", methods=["POST"])
def order_close():
    oid = request.form.get("oid", type=int)
    order = db.session.get(Order, oid) if oid else None
    if not order:
        flash("订单不存在或已被删除")
        return redirect(url_for("order_list_page"))
    if order.finished:
        flash(f"订单 #{order.id} 已结账，不能重复结账")
        return redirect(url_for("order_list_page"))

    remark = request.form.get("remark", "").strip()
    if len(remark) > 200:
        flash("备注不能超过 200 个字符")
        return redirect(url_for("order_detail", oid=order.id))

    member_persons = OrderPerson.query.filter_by(
        order_id=order.id, is_member=True
    ).all()
    members_on_order = []
    for person in member_persons:
        member = (
            db.session.get(Member, person.member_id)
            if person.member_id
            else Member.query.filter_by(name=person.name).first()
        )
        if not member:
            flash(f"会员「{person.name}」记录不存在，无法结账")
            return redirect(url_for("order_detail", oid=order.id))
        members_on_order.append(member)

    normal_members = [member for member in members_on_order if not member.is_super]
    super_member_count = sum(1 for member in members_on_order if member.is_super)
    table_fee, per_table, member_deduction, super_discount, cash_table_fee = (
        calculate_table_split(
            order.charge_type, len(normal_members), super_member_count
        )
    )

    member_charges = []
    for member in normal_members:
        if (member.fee_balance or 0) < per_table:
            flash(f"会员「{member.name}」会员费余额不足，请先补充")
            return redirect(url_for("order_detail", oid=order.id))
        member_charges.append((member, per_table))

    goods_sum = (
        db.session.query(func.sum(OrderGoods.subtotal)).filter_by(order_id=oid).scalar()
        or 0
    )

    updated = (
        Order.query.filter(Order.id == order.id, Order.finished.is_(False))
        .update(
            {
                Order.end_time: datetime.now(),
                Order.remark: remark,
                Order.table_fee: table_fee,
                Order.member_deduction: member_deduction,
                Order.super_discount: super_discount,
                Order.total: round(table_fee + goods_sum, 2),
                Order.finished: True,
            },
            synchronize_session=False,
        )
    )
    if not updated:
        db.session.rollback()
        flash(f"订单 #{order.id} 已结账，不能重复结账")
        return redirect(url_for("order_list_page"))

    for member, charge in member_charges:
        deducted = Member.query.filter(
            Member.id == member.id,
            Member.fee_balance >= charge,
        ).update(
            {Member.fee_balance: Member.fee_balance - charge},
            synchronize_session=False,
        )
        if not deducted:
            db.session.rollback()
            flash(f"会员「{member.name}」会员费余额不足，请先补充")
            return redirect(url_for("order_detail", oid=order.id))

    db.session.commit()
    return redirect("/")


@app.route("/report")
def report():
    t = request.args.get("type", "d")
    now = datetime.now()
    today = date.today()
    start = None
    title = ""

    if t == "d":
        start = datetime.combine(today, datetime.min.time())
        title = "今日统计"
    elif t == "w":
        monday = today - timedelta(days=today.weekday())
        start = datetime.combine(monday, datetime.min.time())
        title = "本周统计"
    elif t == "m":
        start = datetime(now.year, now.month, 1)
        title = "本月统计"
    elif t == "y":
        start = datetime(now.year, 1, 1)
        title = "本年统计"
    else:
        t = "d"
        start = datetime.combine(today, datetime.min.time())
        title = "今日统计"

    # ✅ 按 end_time 结束时间倒序
    orders = (
        Order.query.filter(Order.end_time >= start)
        .order_by(Order.end_time.desc())
        .all()
    )
    room_ids = [o.room_id for o in orders]
    rooms = Room.query.filter(Room.id.in_(room_ids)).all()
    room_map = {r.id: r for r in rooms}

    order_ids = [o.id for o in orders]
    order_person_goods_all = (
        db.session.query(OrderGoods, Goods, OrderPerson)
        .select_from(OrderGoods)
        .join(Goods, OrderGoods.goods_id == Goods.id)
        .join(OrderPerson, OrderGoods.person_id == OrderPerson.id)
        .filter(OrderGoods.order_id.in_(order_ids))
        .all()
    )

    order_person_goods_map = defaultdict(list)
    for og, g, op in order_person_goods_all:
        order_person_goods_map[og.order_id].append((op.name, og, g))

    order_person_names_map = defaultdict(list)
    if order_ids:
        order_people = (
            OrderPerson.query.filter(OrderPerson.order_id.in_(order_ids))
            .order_by(OrderPerson.order_id.asc(), OrderPerson.id.asc())
            .all()
        )
        for person in order_people:
            order_person_names_map[person.order_id].append(person.name)

    order_cash_income = round(
        sum(
            (o.total or 0)
            - (o.member_deduction or 0)
            - (o.super_discount or 0)
            for o in orders
        ),
        2,
    )
    super_discount_total = round(
        sum(o.super_discount or 0 for o in orders), 2
    )

    member_revenues = (
        Revenue.query.filter(
            Revenue.category == MEMBER_FEE_CATEGORY,
            Revenue.create_time >= start,
        )
        .order_by(Revenue.create_time.desc(), Revenue.id.desc())
        .all()
    )
    member_fee_income = round(
        sum(revenue.amount or 0 for revenue in member_revenues), 2
    )
    income = round(order_cash_income + member_fee_income, 2)

    exp_sum = (
        db.session.query(func.sum(Expense.amount))
        .filter(Expense.create_time >= start)
        .scalar()
        or 0
    )
    net = income - exp_sum
    order_count = len(orders)
    average_order_income = (
        round(order_cash_income / order_count, 2) if order_count else 0
    )

    return render_template(
        "report.html",
        title=title,
        period=t,
        period_start=start,
        period_end=now,
        orders=orders,
        room_map=room_map,
        order_person_goods_map=order_person_goods_map,
        order_person_names_map=order_person_names_map,
        durations={
            order.id: format_duration(order.start_time, order.end_time)
            for order in orders
        },
        charge_labels={
            order.id: CHARGE_TYPE_LABELS.get(
                order.charge_type, order.charge_type or "未设置"
            )
            for order in orders
        },
        income=income,
        order_cash_income=order_cash_income,
        member_fee_income=member_fee_income,
        member_revenues=member_revenues,
        super_discount_total=super_discount_total,
        order_count=order_count,
        average_order_income=average_order_income,
        exp_sum=exp_sum,
        net=net,
    )


@app.route("/expense/add", methods=["GET", "POST"])
def expense_add():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        amount_text = request.form.get("amount", "").strip()
        remark = request.form.get("remark", "").strip()
        error = None

        if not title:
            error = "请输入支出项目名称"
        elif len(title) > 100:
            error = "支出项目名称不能超过 100 个字符"
        elif not amount_text:
            error = "请输入支出金额"
        else:
            try:
                amount = round(float(amount_text), 2)
            except (TypeError, ValueError):
                error = "金额格式错误，请输入有效数字"
            else:
                if not math.isfinite(amount) or amount <= 0:
                    error = "金额必须是大于 0 的有效数字"

        if error is None and len(remark) > 200:
            error = "备注不能超过 200 个字符"

        if error:
            return render_template("expense_add.html", error=error)

        exp = Expense(title=title, amount=amount, remark=remark)
        db.session.add(exp)
        db.session.commit()
        return redirect("/expense/list")
    return render_template("expense_add.html")


@app.route("/expense/list")
def expense_list():
    today = date.today()
    today_exp = (
        Expense.query.filter(Expense.create_time >= today)
        .order_by(Expense.create_time.desc())
        .all()
    )
    sum_exp = sum(e.amount for e in today_exp)
    return render_template("expense_list.html", expense_list=today_exp, sum_exp=sum_exp)


@app.route("/expense/delete/<int:exp_id>", methods=["POST"])
def expense_delete(exp_id):
    exp = Expense.query.get(exp_id)
    if exp:
        db.session.delete(exp)
        db.session.commit()
    return redirect("/expense/list")


# ===================== 已结订单列表 =====================
@app.route("/order/list")
def order_list_page():
    order_list = (
        Order.query.filter_by(finished=True).order_by(Order.end_time.desc()).all()
    )
    order_ids = [order.id for order in order_list]

    room_names = {}
    person_counts = {}
    person_names = defaultdict(list)
    if order_ids:
        room_ids = {order.room_id for order in order_list}
        room_names = {
            room.id: room.name
            for room in Room.query.filter(Room.id.in_(room_ids)).all()
        }
        persons = (
            OrderPerson.query.filter(OrderPerson.order_id.in_(order_ids))
            .order_by(OrderPerson.order_id.asc(), OrderPerson.id.asc())
            .all()
        )
        for person in persons:
            person_names[person.order_id].append(person.name)
        person_counts = {
            order_id: len(names) for order_id, names in person_names.items()
        }

    total_income = round(
        sum(
            (order.total or 0) - (order.member_deduction or 0)
            - (order.super_discount or 0)
            for order in order_list
        ),
        2,
    )
    return render_template(
        "order_list.html",
        order_list=order_list,
        room_names=room_names,
        person_counts=person_counts,
        person_names=person_names,
        durations={order.id: format_duration(order.start_time, order.end_time) for order in order_list},
        charge_labels={
            order.id: CHARGE_TYPE_LABELS.get(order.charge_type, order.charge_type or "未设置")
            for order in order_list
        },
        total_income=total_income,
        average_income=total_income / len(order_list) if order_list else 0,
    )


def order_delete_redirect_target():
    target = request.form.get("next", "")
    allowed_targets = {url_for("order_list_page")}
    allowed_targets.update(url_for("report", type=period) for period in ("d", "w", "m", "y"))
    if target in allowed_targets:
        return target
    return url_for("order_list_page")


# 修改：订单列表删除订单改为POST
@app.route("/order/delete", methods=["POST"])
def order_delete():
    redirect_target = order_delete_redirect_target()
    order_id = request.form.get("order_id", type=int)
    if not order_id:
        flash("订单参数错误")
        return redirect(redirect_target)

    order = db.session.get(Order, order_id)
    if not order:
        flash("订单不存在或已删除")
        return redirect(redirect_target)

    OrderGoods.query.filter_by(order_id=order_id).delete()
    OrderPerson.query.filter_by(order_id=order_id).delete()
    db.session.delete(order)
    db.session.commit()
    flash(f"订单 #{order_id} 已删除")
    return redirect(redirect_target)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
