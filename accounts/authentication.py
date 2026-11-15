"""DRF 认证：`Authorization: Bearer <token>`，复用 accounts.AuthToken。"""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import AuthToken


class AuthTokenAuthentication(BaseAuthentication):
    """按 Bearer token 查 `auth_token` 表；失败返回 401，而不是 403。"""

    keyword = "Bearer"

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            return None
        if header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise AuthenticationFailed("Authorization 头格式应为：Bearer <token>")

        try:
            token = header[1].decode()
        except UnicodeError:
            raise AuthenticationFailed("token 编码不合法") from None

        try:
            auth = AuthToken.objects.select_related("user").get(token=token)
        except AuthToken.DoesNotExist:
            raise AuthenticationFailed("token 无效") from None

        if not auth.user.is_active:
            raise AuthenticationFailed("用户已被禁用")
        return auth.user, auth

    def authenticate_header(self, request):
        # 非空 → DRF 对未认证请求返回 401 + WWW-Authenticate
        return self.keyword
