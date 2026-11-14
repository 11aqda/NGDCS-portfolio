"""账号与排行榜的数据模型。"""

from django.conf import settings
from django.db import models


class ScoreRecord(models.Model):
    """一局的成绩。由 TCP 服务端在结算时直接写入，客户端没有任何上报接口。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scores"
    )
    score = models.PositiveIntegerField()
    level = models.PositiveSmallIntegerField(default=1)
    kills = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "score_record"
        indexes = [
            # 排行榜聚合要按分数倒序、按用户分组
            models.Index(fields=["-score"], name="idx_score_desc"),
            models.Index(fields=["user", "-score"], name="idx_user_score"),
        ]
        ordering = ["-score", "id"]

    def __str__(self) -> str:  # pragma: no cover - 只在调试里用
        return f"{self.user.username}:{self.score}"


class AuthToken(models.Model):
    """登录后发给客户端的 token。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tokens"
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auth_token"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.user.username}:{self.token[:8]}…"