import os
import re
import tempfile
import unittest


TEST_DATABASE_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = (
    f"sqlite:///{os.path.join(TEST_DATABASE_DIR.name, 'test.db')}"
)
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password-123"
os.environ.pop("ADMIN_PASSWORD_HASH", None)
os.environ.pop("SKIP_DB_BOOTSTRAP", None)

from app import (  # noqa: E402
    Expense,
    Goods,
    Member,
    Order,
    OrderGoods,
    OrderPerson,
    Revenue,
    Room,
    app,
    db,
)


class AppTestCase(unittest.TestCase):
    admin_username = "admin"
    admin_password = "test-password-123"

    def setUp(self):
        app.config.update(TESTING=True)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

        self.room = Room(name="555")
        self.goods = Goods(name="可乐", price=5, is_active=True)
        db.session.add_all([self.room, self.goods])
        db.session.commit()

        self.client = app.test_client()
        self.csrf_token = self.login()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def extract_csrf(self, response):
        match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match, "页面中缺少 CSRF Token")
        return match.group(1)

    def login(self, client=None, password=None):
        client = client or self.client
        login_page = client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        token = self.extract_csrf(login_page)
        response = client.post(
            "/login",
            data={
                "username": self.admin_username,
                "password": password or self.admin_password,
                "csrf_token": token,
            },
        )
        self.assertEqual(response.status_code, 302)
        home = client.get("/")
        self.assertEqual(home.status_code, 200)
        return self.extract_csrf(home)

    def post(self, path, data=None, **kwargs):
        payload = dict(data or {})
        payload.setdefault("csrf_token", self.csrf_token)
        return self.client.post(path, data=payload, **kwargs)

    def open_order(self, charge_type="gear100"):
        response = self.post(
            "/order/open",
            data={"room_id": self.room.id, "charge_type": charge_type},
        )
        self.assertEqual(response.status_code, 302)
        return Order.query.filter_by(room_id=self.room.id, finished=False).one()

    def add_member(self, name, fee=100, super_member=False):
        endpoint = "/members/add_super" if super_member else "/members/add"
        data = {"name": name}
        if not super_member:
            data["fee"] = str(fee)
        response = self.post(endpoint, data=data)
        self.assertEqual(response.status_code, 302)
        return Member.query.filter_by(name=name).one()

    def add_person(self, order, name):
        response = self.post(
            "/order/add_person", data={"oid": order.id, "name": name}
        )
        self.assertEqual(response.status_code, 302)
        return OrderPerson.query.filter_by(order_id=order.id, name=name).one()

    def close_order(self, order):
        response = self.post("/order/close", data={"oid": order.id})
        self.assertEqual(response.status_code, 302)
        return db.session.get(Order, order.id)

    def test_pages_render_without_inline_styles(self):
        public_paths = [
            "/",
            f"/room/preview/{self.room.id}",
            "/order/list",
            "/members",
            "/goods",
            "/expense/add",
            "/expense/list",
            "/report?type=d",
            "/report?type=w",
            "/report?type=m",
            "/report?type=y",
        ]

        for path in public_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"css/base.css", response.data)
                self.assertNotIn(b"<style", response.data)

        order = self.open_order()
        response = self.client.get(f"/order/{order.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"css/base.css", response.data)
        self.assertNotIn(b"<style", response.data)

    def test_unauthenticated_access_redirects_to_login(self):
        guest = app.test_client()
        for path in ("/", "/order/list", "/members", "/report?type=d"):
            with self.subTest(path=path):
                response = guest.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.headers["Location"])

        self.assertEqual(guest.get("/health").status_code, 200)
        static_response = guest.get("/static/css/base.css")
        self.assertEqual(static_response.status_code, 200)
        static_response.close()

    def test_login_rejects_bad_password_and_requires_csrf(self):
        guest = app.test_client()
        missing_csrf = guest.post(
            "/login",
            data={"username": self.admin_username, "password": self.admin_password},
        )
        self.assertEqual(missing_csrf.status_code, 400)

        login_page = guest.get("/login")
        token = self.extract_csrf(login_page)
        invalid = guest.post(
            "/login",
            data={
                "username": self.admin_username,
                "password": "wrong-password",
                "csrf_token": token,
            },
        )
        self.assertEqual(invalid.status_code, 401)

    def test_authenticated_post_requires_csrf(self):
        response = self.client.post(
            "/expense/add", data={"title": "水电", "amount": "10"}
        )
        self.assertEqual(response.status_code, 400)

        response = self.post(
            "/expense/add",
            data={"title": "水电", "amount": "10", "csrf_token": "invalid"},
        )
        self.assertEqual(response.status_code, 400)

    def test_logout_clears_session(self):
        response = self.post("/logout")
        self.assertEqual(response.status_code, 302)
        protected = self.client.get("/order/list")
        self.assertEqual(protected.status_code, 302)
        self.assertIn("/login", protected.headers["Location"])

    def test_security_headers_and_session_cookie(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("script-src 'self' 'unsafe-inline'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        guest = app.test_client()
        login_page = guest.get("/login")
        cookie = login_page.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_static_styles_are_available(self):
        for filename in ("base.css", "pages.css", "members.css", "orders.css", "report.css"):
            with self.subTest(filename=filename):
                response = self.client.get(f"/static/css/{filename}")
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.data), 100)
                response.close()

    def test_mutation_endpoints_reject_get(self):
        for path in (
            f"/goods/toggle/{self.goods.id}",
            f"/goods/delete/{self.goods.id}",
            "/order/delete",
            "/members/delete/1",
            "/initdb",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)

    def test_member_fee_is_revenue_and_delete_reverts_it(self):
        member = self.add_member("张会员", fee=100)
        revenue = Revenue.query.filter_by(
            category="member_fee", member_id=member.id
        ).one()
        self.assertEqual(revenue.amount, 100)

        response = self.client.get("/report?type=d")
        self.assertIn("¥100.00".encode(), response.data)

        response = self.post(f"/members/delete/{member.id}")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(Member, member.id))
        self.assertEqual(
            Revenue.query.filter_by(category="member_fee", member_id=member.id).count(),
            0,
        )
        self.client.get("/members")
        response = self.client.get("/report?type=d")
        self.assertNotIn("¥100.00".encode(), response.data)

    def test_super_member_does_not_pay_or_split_table_fee(self):
        member = self.add_member("王超级", super_member=True)
        order = self.open_order("gear100")
        response = self.post(
            "/order/add_member", data={"oid": order.id, "member_id": member.id}
        )
        self.assertEqual(response.status_code, 302)

        closed = self.close_order(order)
        self.assertTrue(closed.finished)
        self.assertEqual(closed.member_deduction, 0)
        self.assertEqual(closed.super_discount, 25)
        self.assertEqual(closed.total, 100)
        self.assertEqual(Revenue.query.filter_by(member_id=member.id).count(), 0)

    def test_normal_member_table_fee_is_deducted_from_balance(self):
        member = self.add_member("李会员", fee=100)
        order = self.open_order("gear100")
        self.post(
            "/order/add_member", data={"oid": order.id, "member_id": member.id}
        )

        closed = self.close_order(order)
        db.session.refresh(member)
        self.assertEqual(closed.member_deduction, 25)
        self.assertEqual(closed.super_discount, 0)
        self.assertEqual(member.fee_balance, 75)

        report = self.client.get("/report?type=d")
        self.assertIn("¥175.00".encode(), report.data)

    def test_order_list_shows_person_names(self):
        order = self.open_order("gear100")
        self.add_person(order, "赵小明")
        self.close_order(order)

        response = self.client.get("/order/list")
        self.assertIn("赵小明".encode(), response.data)

    def test_order_cannot_be_closed_twice(self):
        order = self.open_order("gear80")
        closed = self.close_order(order)
        original_end_time = closed.end_time
        original_total = closed.total

        second = self.post("/order/close", data={"oid": order.id})
        self.assertEqual(second.status_code, 302)
        db.session.refresh(closed)
        self.assertEqual(closed.end_time, original_end_time)
        self.assertEqual(closed.total, original_total)

    def test_expense_validation_and_creation(self):
        invalid = self.post(
            "/expense/add", data={"title": "水电", "amount": "abc"}
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertIn("金额格式错误".encode(), invalid.data)
        self.assertEqual(Expense.query.count(), 0)

        valid = self.post(
            "/expense/add", data={"title": "水电", "amount": "12.50"}
        )
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(Expense.query.one().amount, 12.5)

    def test_goods_quantity_is_validated(self):
        order = self.open_order("gear80")
        person = self.add_person(order, "钱四")

        response = self.post(
            "/order/add_goods",
            data={
                "oid": order.id,
                "pid": person.id,
                "goods_id": self.goods.id,
                "num": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OrderGoods.query.count(), 0)

        response = self.post(
            "/order/add_goods",
            data={
                "oid": order.id,
                "pid": person.id,
                "goods_id": self.goods.id,
                "num": "2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OrderGoods.query.one().subtotal, 10)


if __name__ == "__main__":
    unittest.main()
