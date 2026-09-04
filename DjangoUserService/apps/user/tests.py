from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.user.blacklist import RevocationUnavailable, is_blacklisted
from apps.user.models import User, UserStatusChoice


class UserAuthFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.password = "secret123"
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password=self.password,
            telephone="13800000001",
            status=UserStatusChoice.ACTIVE,
        )

    def test_register_returns_active_user_and_token(self):
        response = self.client.post(
            "/user/register/",
            {
                "username": "bob",
                "email": "bob@example.com",
                "telephone": "13800000002",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], 201)
        self.assertTrue(response.data["token"])
        self.assertEqual(response.data["user"]["email"], "bob@example.com")
        self.assertEqual(User.objects.get(email="bob@example.com").status, UserStatusChoice.ACTIVE)
    def test_register_accepts_blank_optional_telephone(self):
        response = self.client.post(
            "/user/register/",
            {
                "username": "no-phone-user",
                "email": "no-phone@example.com",
                "telephone": "",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(User.objects.get(email="no-phone@example.com").telephone)

    def test_register_accepts_omitted_optional_telephone(self):
        response = self.client.post(
            "/user/register/",
            {
                "username": "omitted-phone-user",
                "email": "omitted-phone@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(User.objects.get(email="omitted-phone@example.com").telephone)

    def test_login_with_username_returns_token(self):
        response = self.client.post(
            "/user/login/",
            {
                "username": "alice",
                "password": self.password,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["email"], "alice@example.com")
        self.assertTrue(response.data["token"])

    def test_user_detail_requires_valid_bearer_token(self):
        login_response = self.client.post(
            "/user/login/",
            {
                "username": "alice",
                "password": self.password,
            },
            format="json",
        )
        token = login_response.data["token"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get("/user/detail/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["email"], "alice@example.com")

    def test_logout_blacklists_token(self):
        login_response = self.client.post(
            "/user/login/",
            {
                "username": "alice",
                "password": self.password,
            },
            format="json",
        )
        token = login_response.data["token"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        logout_response = self.client.post("/user/logout/")
        detail_response = self.client.get("/user/detail/")

        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_token_blacklists_old_token_and_returns_new_one(self):
        login_response = self.client.post(
            "/user/login/",
            {
                "username": "alice",
                "password": self.password,
            },
            format="json",
        )
        old_token = login_response.data["token"]

        refresh_response = self.client.post(
            "/user/refresh-token/",
            {"token": old_token},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(refresh_response.data["token"], old_token)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {old_token}")
        old_token_detail = self.client.get("/user/detail/")
        self.assertEqual(old_token_detail.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_response.data['token']}")
        new_token_detail = self.client.get("/user/detail/")
        self.assertEqual(new_token_detail.status_code, status.HTTP_200_OK)

    def test_register_rate_limit_blocks_fourth_request_in_window(self):
        for idx in range(3):
            response = self.client.post(
                "/user/register/",
                {
                    "username": f"user{idx}",
                    "email": f"user{idx}@example.com",
                    "telephone": f"1380000001{idx}",
                    "password": "secret123",
                    "confirm_password": "secret123",
                },
                REMOTE_ADDR="203.0.113.10",
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        blocked_response = self.client.post(
            "/user/register/",
            {
                "username": "user4",
                "email": "user4@example.com",
                "telephone": "13800000019",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            REMOTE_ADDR="203.0.113.10",
            format="json",
        )

        self.assertEqual(blocked_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class RevocationFailClosedTests(TestCase):
    """撤销服务（共享 Redis）不可用时，认证相关操作失败关闭（503），不伪报成功。"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.password = "secret123"
        self.user = User.objects.create_user(
            username="carol",
            email="carol@example.com",
            password=self.password,
            status=UserStatusChoice.ACTIVE,
        )
        login = self.client.post(
            "/user/login/",
            {"username": "carol", "password": self.password},
            format="json",
        )
        self.token = login.data["token"]

    def _simulate_redis_down(self):
        """让黑名单走共享 Redis 且连接失败，模拟生产 Redis 宕机。"""
        self._patch_use = patch("apps.user.blacklist._use_shared_redis", return_value=True)
        self._patch_client = patch("apps.user.blacklist._client", side_effect=ConnectionError("redis down"))
        self._patch_use.start()
        self._patch_client.start()
        self.addCleanup(self._patch_use.stop)
        self.addCleanup(self._patch_client.stop)

    def _restore_redis(self):
        self._patch_use.stop()
        self._patch_client.stop()

    def test_logout_returns_503_when_revocation_unavailable(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self._simulate_redis_down()

        response = self.client.post("/user/logout/")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        # 撤销未确认：故障恢复后旧 token 仍可访问（不能伪报"已退出"）
        self._restore_redis()
        detail = self.client.get("/user/detail/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

    def test_reset_password_503_does_not_change_password(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self._simulate_redis_down()

        response = self.client.post(
            "/user/reset-password/",
            {"old_password": self.password, "new_password": "brand-new-pass", "confirm_password": "brand-new-pass"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        # 密码必须保持原样（撤销失败不得进入密码修改）
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertFalse(self.user.check_password("brand-new-pass"))

    def test_refresh_returns_503_when_revocation_unavailable(self):
        self._simulate_redis_down()

        response = self.client.post(
            "/user/refresh-token/",
            {"token": self.token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_authenticated_request_503_when_blacklist_check_unavailable(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self._simulate_redis_down()

        response = self.client.get("/user/detail/")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class BlacklistModuleTests(TestCase):
    """黑名单模块契约：键前缀兼容、共享 Redis 失败关闭。"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="dave",
            email="dave@example.com",
            password="secret123",
            status=UserStatusChoice.ACTIVE,
        )
        login = self.client.post(
            "/user/login/",
            {"username": "dave", "password": "secret123"},
            format="json",
        )
        self.token = login.data["token"]

    def _payload(self):
        import jwt as pyjwt
        from django.conf import settings as dj_settings

        return pyjwt.decode(self.token, dj_settings.SECRET_KEY, algorithms=["HS256"])

    def test_django_cache_fallback_writes_and_reads(self):
        from apps.user.blacklist import blacklist_token

        # 显式 LocMem（未配置共享 Redis）：写入后可读
        with patch("apps.user.blacklist._use_shared_redis", return_value=False):
            self.assertTrue(blacklist_token(self.token))
            self.assertTrue(is_blacklisted(self._payload()["jti"]))

    def test_legacy_prefixed_key_still_recognized(self):
        cache.set(":1:blacklist:legacy-jti", "1", 60)
        with patch("apps.user.blacklist._use_shared_redis", return_value=False):
            self.assertTrue(is_blacklisted("legacy-jti"))

    def test_unprefixed_key_still_recognized(self):
        cache.set("blacklist:plain-jti", "1", 60)
        with patch("apps.user.blacklist._use_shared_redis", return_value=False):
            self.assertTrue(is_blacklisted("plain-jti"))

    def test_shared_redis_unavailable_raises(self):
        with (
            patch("apps.user.blacklist._use_shared_redis", return_value=True),
            patch("apps.user.blacklist._client", side_effect=ConnectionError("down")),
            self.assertRaises(RevocationUnavailable),
        ):
            is_blacklisted("some-jti")

    def test_no_jti_returns_false_without_redis(self):
        with (
            patch("apps.user.blacklist._use_shared_redis", return_value=True),
            patch("apps.user.blacklist._client", side_effect=ConnectionError("down")),
        ):
            # 无 jti 无需查库，不应触发 Redis
            self.assertFalse(is_blacklisted(None))
            self.assertFalse(is_blacklisted(""))
