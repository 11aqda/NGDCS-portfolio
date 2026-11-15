"""对局记录与其复核备注的只读 / 写入接口。"""

from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import filters, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import ScoreRecord
from dashboard.models import RunNote


class ScoreRecordSerializer(serializers.ModelSerializer):
    """对局记录（只读）。"""

    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = ScoreRecord
        fields = ["id", "username", "score", "level", "kills", "created_at"]
        read_only_fields = fields


class RunNoteSerializer(serializers.ModelSerializer):
    """对局复核备注。"""

    author = serializers.CharField(source="author.username", read_only=True)

    class Meta:
        model = RunNote
        fields = ["id", "author", "content", "created_at"]
        read_only_fields = ["id", "author", "created_at"]

    def validate_content(self, value: str) -> str:
        text = value.strip()
        if not text:
            raise serializers.ValidationError("备注不能为空")
        return text


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

    @action(detail=True, methods=["get", "post"], url_path="notes")
    def notes(self, request, pk=None):
        """GET 列出备注；POST 新建一条（作者取当前用户）。"""
        run = self.get_object()
        if request.method == "GET":
            items = run.notes.select_related("author").all()
            return Response(RunNoteSerializer(items, many=True).data)

        serializer = RunNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            note = serializer.save(run=run, author=request.user)
        return Response(
            RunNoteSerializer(note).data, status=status.HTTP_201_CREATED
        )

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"notes/(?P<note_id>\d+)",
    )
    def delete_note(self, request, pk=None, note_id=None):
        """删除自己写的备注。"""
        run = self.get_object()
        note = get_object_or_404(RunNote, id=note_id, run=run)
        if note.author_id != request.user.id:
            raise PermissionDenied("只能删除自己写的备注")
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
