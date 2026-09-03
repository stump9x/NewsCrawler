"""Authenticated feed reading and translation, independent of research/Celery."""
import time

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsStaffUser
from .trend_sources import CHANNELS, NEWSNOW, PROVIDER_URLS, SOURCE_ALIASES, collect_boards, trend_cache
from .trend_translation import cached_translation, translate_batch


class TrendCatalogView(APIView):
    permission_classes = [IsStaffUser]

    def get(self, request):
        return Response({
            "newsnow": [{"id": row[0], "source_id": SOURCE_ALIASES.get(row[0], row[0]), "name": row[1], "subtitle": row[2], "accent": row[3]} for row in NEWSNOW],
            "rebang": [{"id": key, "name": name} for key, name in CHANNELS.items()],
            "providers": PROVIDER_URLS,
        })


class TrendBoardsView(APIView):
    permission_classes = [IsStaffUser]

    def get(self, request):
        provider = request.query_params.get("provider", "newsnow")
        source = request.query_params.get("source", "baidu" if provider == "newsnow" else "all")
        if provider not in PROVIDER_URLS or (provider == "newsnow" and source not in {row[0] for row in NEWSNOW}) or (provider == "rebang" and source not in CHANNELS):
            return Response({"detail": "Nguồn xu hướng không hợp lệ."}, status=400)
        if provider == "sopilot":
            source = "all"
        cache = trend_cache()
        upstream_source = SOURCE_ALIASES.get(source, source) if provider == "newsnow" else source
        key = f"board:v2:{provider}:{upstream_source}"
        saved = cache.get(key)
        stale = False
        error = ""
        if not saved or time.time() - saved["fetched_at"] > 180:
            try:
                boards = collect_boards(provider, upstream_source)
                # Never replace last-good content with an empty/error response.
                if not any(board["items"] for board in boards) and saved:
                    raise ValueError("Nguồn chưa có bản cập nhật mới.")
                saved = {"boards": boards, "fetched_at": time.time()}
                cache.set(key, saved, timeout=86400 * 7)
            except Exception:
                stale = bool(saved)
                error = "Nguồn tạm thời chưa phản hồi. Đang giữ bản gần nhất." if saved else "Chưa kết nối được nguồn. Bảng sẽ tự thử lại."
        if not saved:
            return Response({"boards": [], "stale": False, "error": error}, status=502)
        for board in saved["boards"]:
            if provider == "newsnow":
                config = next(row for row in NEWSNOW if row[0] == source)
                board.update(id=f"newsnow:{source}", name=config[1], subtitle=config[2], accent=config[3])
            board["name_vi"] = cached_translation(board["name"])
            board["subtitle_vi"] = cached_translation(board["subtitle"])
            for item in board["items"]:
                item["title_vi"] = cached_translation(item["title"])
        return Response({**saved, "stale": stale, "error": error})


class TrendTranslateView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        texts = request.data.get("texts") if isinstance(request.data, dict) else None
        if not isinstance(texts, list) or not 1 <= len(texts) <= 48 or any(not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in texts) or sum(map(len, texts)) > 4200:
            return Response({"detail": "Mỗi lượt dịch tối đa 48 đoạn, tổng 4.200 ký tự."}, status=400)
        batch = translate_batch(texts)
        return Response({"items": [{"text": text, "vi": batch.translations.get(text), "status": "ok" if batch.translations.get(text) else "pending"} for text in texts], "reason": batch.reason, "retry_after": batch.retry_after})
