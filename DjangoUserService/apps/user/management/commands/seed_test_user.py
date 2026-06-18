import os

from django.core.management.base import BaseCommand, CommandError
from apps.user.models import User, UserStatusChoice


class Command(BaseCommand):
    help = '创建仅用于本地开发的测试用户；密码需通过参数或环境变量提供'

    def add_arguments(self, parser):
        parser.add_argument('--username', default=os.getenv('SEED_TEST_USER_USERNAME', 'localtest'))
        parser.add_argument('--email', default=os.getenv('SEED_TEST_USER_EMAIL', 'localtest@example.com'))
        parser.add_argument('--password', default=os.getenv('SEED_TEST_USER_PASSWORD'))

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        password = options['password']

        if not password:
            raise CommandError('请通过 --password 或 SEED_TEST_USER_PASSWORD 提供仅用于本地开发的测试密码')

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'status': UserStatusChoice.ACTIVE,
                'bio': '测试用户',
            }
        )

        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f'本地测试用户创建成功: {username}'))
        else:
            # 确保已有用户也是激活状态
            if user.status != UserStatusChoice.ACTIVE:
                user.status = UserStatusChoice.ACTIVE
                user.save()
                self.stdout.write(self.style.WARNING(f'本地测试用户已存在，已更新为激活状态: {username}'))
            else:
                self.stdout.write(self.style.WARNING(f'本地测试用户已存在: {username}'))
