from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

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
