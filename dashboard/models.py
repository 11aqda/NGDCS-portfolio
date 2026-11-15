"""看板侧的数据模型。"""

from django.conf import settings
from django.db import models

from accounts.models import ScoreRecord


class RunNote(models.Model):
    """管理员给一场对局写的复核备注。"""

    run = models.ForeignKey(
        ScoreRecord, on_delete=models.CASCADE, related_name="notes"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="run_notes",
    )
    content = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "run_note"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["run", "-created_at"], name="idx_run_note_run_time"),
        ]

    def __str__(self) -> str:  # pragma: no cover - 只在调试里用
        return f"{self.run_id}:{self.content[:20]}"
