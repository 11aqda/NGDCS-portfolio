"""DRF 只读接口：`GET /api/v2/runs/`（对局记录）。"""

from __future__ import annotations

from rest_framework import filters, serializers, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser

from accounts.models import ScoreRecord


class ScoreRecordSerializer(serializers.ModelSerializer):
    """对局记录（只读）。"""

    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = ScoreRecord
        fields = ["id", "username", "score", "level", "kills", "created_at"]
        read_only_fields = fields


class RunPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class ScoreRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """`/api/v2/runs/` 与 `/api/v2/runs/<id>/`。"""

    queryset = ScoreRecord.objects.select_related("user").all()
    serializer_class = ScoreRecordSerializer
    permission_classes = [IsAdminUser]
    pagination_class = RunPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["user__username"]
    ordering_fields = ["score", "level", "kills", "created_at"]
    ordering = ["-score", "-created_at"]
