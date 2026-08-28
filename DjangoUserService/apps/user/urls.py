from django.urls import path

from .views import (
    LoginView,
    RegisterView,
    ResetPasswordView,
    TokenRefreshView,
    UserDetailView,
    UserLogOutView,
    UserUpdateView,
)

app_name = 'user'

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('reset-password/', ResetPasswordView.as_view(), name='reset-password'),
    path('refresh-token/', TokenRefreshView.as_view(), name='refresh-token'),
    path('detail/', UserDetailView.as_view(), name='user-detail'),
    path('update/', UserUpdateView.as_view(), name='user-update'),
    path('logout/', UserLogOutView.as_view(), name='user-logout'),
]