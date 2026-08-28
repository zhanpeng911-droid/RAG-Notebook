from django.core.management.base import BaseCommand

from apps.user.models import User


class Command(BaseCommand):
    help = '重置指定用户的密码'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='用户名')
        parser.add_argument('password', type=str, help='新密码')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']

        try:
            user = User.objects.get(username=username)
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f'用户 {username} 密码已重置'))
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'用户 {username} 不存在'))
