"""手写索引：score_record.created_at。

不改 accounts/models.py，所以用 RunSQL 建索引（Django 迁移状态不感知）。
回滚时删掉索引。仅 MySQL 语法。
"""

from django.db import migrations

CREATE_SQL = "CREATE INDEX idx_score_created_at ON score_record (created_at)"
DROP_SQL = "DROP INDEX idx_score_created_at ON score_record"


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_initial"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, DROP_SQL),
    ]
