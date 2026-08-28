"""
文件上传 API 测试：鉴权、合法上传、非法类型、超大文件。
"""
import io

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.user.models import User, UserStatusChoice


def _tiny_png_bytes() -> bytes:
    """生成 Pillow 可识别的 1x1 PNG。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


class FileUploadApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.password = "secret123"
        self.user = User.objects.create_user(
            username="fileuser",
            email="fileuser@example.com",
            password=self.password,
            telephone="13900000001",
            status=UserStatusChoice.ACTIVE,
        )
        login = self.client.post(
            "/user/login/",
            {"username": "fileuser", "password": self.password},
            format="json",
        )
        self.token = login.data["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_upload_requires_auth(self):
        anon = APIClient()
        png = SimpleUploadedFile("a.png", _tiny_png_bytes(), content_type="image/png")
        response = anon.post("/file/upload/", {"img": png}, format="multipart")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_upload_png_success(self):
        png = SimpleUploadedFile("avatar.png", _tiny_png_bytes(), content_type="image/png")
        response = self.client.post("/file/upload/", {"img": png}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.data if hasattr(response, "data") else response.json()
        self.assertTrue(body.get("success") or body.get("data", {}).get("url"))
        url = body.get("data", {}).get("url") or body.get("url")
        self.assertTrue(url)
        self.assertIn("/media/img/", url)

        # avatar should be updated
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)

    def test_upload_rejects_disallowed_extension(self):
        bad = SimpleUploadedFile("evil.exe", b"MZ\x00\x00", content_type="application/octet-stream")
        response = self.client.post("/file/upload/", {"img": bad}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_oversized_image(self):
        # > 1MB
        big = SimpleUploadedFile(
            "big.png",
            b"\x00" * (1024 * 1024 + 10),
            content_type="image/png",
        )
        response = self.client.post("/file/upload/", {"img": big}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
