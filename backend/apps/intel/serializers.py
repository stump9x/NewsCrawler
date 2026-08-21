from django.utils.text import slugify
from rest_framework import serializers

from apps.core.crypto import encrypt_secret, password_fingerprint
from .models import (
    AlertNotification,
    CompromisedCredential,
    DataLeak,
    DocumentScanKeyword,
    FeedSource,
    Indicator,
    ScannedDocument,
    Tag,
    Threat,
    ThreatFavorite,
    ThreatActor,
    WatchRule,
)


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("id", "name", "slug", "created_at", "updated_at")
        read_only_fields = ("id", "slug", "created_at", "updated_at")

    def create(self, validated_data):
        name = validated_data["name"]
        validated_data["slug"] = slugify(name)[:64] or "tag"
        return super().create(validated_data)


class ThreatActorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ThreatActor
        fields = (
            "id",
            "name",
            "aliases",
            "description",
            "country",
            "motivation",
            "references",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class IndicatorSerializer(serializers.ModelSerializer):
    tags = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Tag.objects.all(), required=False
    )
    threat_actors = serializers.PrimaryKeyRelatedField(
        many=True, queryset=ThreatActor.objects.all(), required=False
    )
    tag_names = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Indicator
        fields = (
            "id",
            "ioc_type",
            "value",
            "normalized_value",
            "description",
            "confidence",
            "tlp",
            "source",
            "source_url",
            "first_seen",
            "last_seen",
            "is_active",
            "tags",
            "tag_names",
            "threat_actors",
            "misp_attribute_uuid",
            "metadata",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "normalized_value",
            "created_by",
            "created_at",
            "updated_at",
        )

    def get_tag_names(self, obj):
        return list(obj.tags.values_list("name", flat=True))

    def validate(self, attrs):
        ioc_type = attrs.get("ioc_type") or getattr(self.instance, "ioc_type", None)
        value = attrs.get("value") or getattr(self.instance, "value", None)
        if ioc_type and value is not None:
            normalized = Indicator.normalize(ioc_type, value)
            qs = Indicator.objects.filter(
                ioc_type=ioc_type, normalized_value=normalized
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"value": "An indicator with this type and value already exists."}
                )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        return super().create(validated_data)


class ThreatSerializer(serializers.ModelSerializer):
    is_favorite = serializers.SerializerMethodField(read_only=True)
    wire_rank = serializers.IntegerField(read_only=True, allow_null=True)
    tags = TagSerializer(many=True, read_only=True)
    tag_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Tag.objects.all(),
        required=False,
        source="tags",
        write_only=True,
    )
    indicators = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Indicator.objects.all(), required=False
    )
    threat_actors = serializers.PrimaryKeyRelatedField(
        many=True, queryset=ThreatActor.objects.all(), required=False
    )

    class Meta:
        model = Threat
        fields = (
            "id",
            "is_favorite",
            "wire_rank",
            "title",
            "title_vi",
            "title_vi_status",
            "summary",
            "severity",
            "status",
            "source",
            "source_url",
            "published_at",
            "wire_priority",
            "evidence_score",
            "cvss_score",
            "epss_score",
            "is_kev",
            "cve_ids",
            "tags",
            "tag_ids",
            "indicators",
            "threat_actors",
            "raw_payload",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "is_favorite",
            "title_vi",
            "title_vi_status",
            "wire_priority",
            "created_by",
            "created_at",
            "updated_at",
        )
    def get_is_favorite(self, obj):
        cached = getattr(obj, "_current_user_favorites", None)
        if cached is not None:
            return bool(cached)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return ThreatFavorite.objects.filter(user=user, threat_id=obj.pk).exists()

    def create(self, validated_data):
        from apps.intel.wire_urls import find_threat_by_normalized_url, normalize_wire_url

        tags = validated_data.pop("tags", None)
        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        raw_url = str(validated_data.get("source_url") or "").strip()
        if raw_url:
            link = normalize_wire_url(raw_url)
            validated_data["source_url"] = link
            existing = find_threat_by_normalized_url(link)
            if existing is not None:
                if tags is not None:
                    existing.tags.set(tags)
                return existing
        instance = super().create(validated_data)
        if tags is not None:
            instance.tags.set(tags)
        return instance

    def update(self, instance, validated_data):
        tags = validated_data.pop("tags", None)
        instance = super().update(instance, validated_data)
        if tags is not None:
            instance.tags.set(tags)
        return instance


class ThreatWireSerializer(ThreatSerializer):
    personal_interest_score = serializers.IntegerField(read_only=True, default=0)

    """Compact representation for the high-frequency News Station feed.

    The full ThreatSerializer is intentionally kept for detail/admin APIs.
    Wire cards only need publication metadata, tags and a few source fields;
    returning indicators, actors and the complete RSS payload made every
    page unnecessarily large and slow to render.
    """

    class Meta:
        model = Threat
        fields = (
            "id",
            "is_favorite",
            "wire_rank",
            "personal_interest_score",
            "title",
            "title_vi",
            "title_vi_status",
            "summary",
            "source",
            "source_url",
            "published_at",
            "wire_priority",
            "severity",
            "is_kev",
            "tags",
            "raw_payload",
            "created_at",
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        payload = data.get("raw_payload")
        if isinstance(payload, dict):
            # Keep only fields used by WireCard/ExternalTitleLink.
            keep = (
                "feed",
                "country",
                "description",
                "image_url",
                "image",
                "thumbnail",
                "enclosure_url",
                "feed_url",
                "link",
            )
            data["raw_payload"] = {
                key: payload[key]
                for key in keep
                if payload.get(key) not in (None, "")
            }
        else:
            data["raw_payload"] = {}
        return data


class DataLeakSerializer(serializers.ModelSerializer):
    tags = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Tag.objects.all(), required=False
    )
    related_indicators = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Indicator.objects.all(), required=False
    )
    credential_count = serializers.SerializerMethodField()

    class Meta:
        model = DataLeak
        fields = (
            "id",
            "title",
            "description",
            "leak_type",
            "severity",
            "status",
            "source",
            "source_url",
            "affected_organization",
            "affected_domain",
            "discovered_at",
            "record_count",
            "credential_count",
            "tags",
            "related_indicators",
            "metadata",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_by", "created_at", "updated_at")

    def get_credential_count(self, obj) -> int:
        return obj.credentials.count()

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        return super().create(validated_data)


class CompromisedCredentialSerializer(serializers.ModelSerializer):
    """Masks plaintext passwords and raw_line on read; accepts password on write only."""

    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=512
    )
    password_present = serializers.SerializerMethodField()

    class Meta:
        model = CompromisedCredential
        fields = (
            "id",
            "leak",
            "email",
            "username",
            "password",
            "password_present",
            "password_fingerprint",
            "url",
            "domain",
            "stealer_family",
            "infected_at",
            "country",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "password_fingerprint",
            "password_present",
            "created_at",
            "updated_at",
        )

    def get_password_present(self, obj) -> bool:
        return bool(obj.password)

    def create(self, validated_data):
        password = validated_data.pop("password", "")
        if password:
            validated_data["password"] = encrypt_secret(password)
            validated_data["password_fingerprint"] = password_fingerprint(password)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        if password is not None:
            instance.password = encrypt_secret(password) if password else ""
            instance.password_fingerprint = (
                password_fingerprint(password) if password else ""
            )
        return super().update(instance, validated_data)


class WatchRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = WatchRule
        fields = (
            "id",
            "name",
            "keyword",
            "target",
            "is_active",
            "case_sensitive",
            "min_severity",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_by", "created_at", "updated_at")

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        return super().create(validated_data)


class FeedSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeedSource
        fields = (
            "id",
            "name",
            "url",
            "category",
            "confidence",
            "country",
            "country_code",
            "is_active",
            "notes",
            "requires_tor",
            "last_fetched_at",
            "last_status",
            "last_error",
            "last_item_count",
            "consecutive_failures",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "last_fetched_at",
            "last_status",
            "last_error",
            "last_item_count",
            "consecutive_failures",
            "created_at",
            "updated_at",
        )

    def validate_url(self, value: str) -> str:
        from apps.core.security import (
            UnsafeURLError,
            is_onion_hostname,
            validate_onion_http_url,
            validate_public_http_url,
        )
        from urllib.parse import urlparse

        host = (urlparse(value).hostname or "").lower()
        try:
            if is_onion_hostname(host):
                return validate_onion_http_url(value, allow_http=True)
            return validate_public_http_url(value, allow_http=True)
        except UnsafeURLError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        from apps.core.security import is_onion_hostname
        from urllib.parse import urlparse

        url = attrs.get("url") or getattr(self.instance, "url", "")
        host = (urlparse(url).hostname or "").lower()
        if is_onion_hostname(host):
            attrs["requires_tor"] = True
        return attrs


class DocumentScanKeywordSerializer(serializers.ModelSerializer):
    query_preview = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = DocumentScanKeyword
        fields = (
            "id",
            "name",
            "keyword",
            "filetypes",
            "is_active",
            "priority",
            "notes",
            "last_scanned_at",
            "last_hit_count",
            "query_preview",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "last_scanned_at",
            "last_hit_count",
            "query_preview",
            "created_at",
            "updated_at",
        )

    def get_query_preview(self, obj):
        return obj.build_query()

    def validate_keyword(self, value: str) -> str:
        from apps.integrations.searx.document_scan import (
            DOCUMENT_KEYWORD_MAX_WORDS,
            document_keyword_too_long,
            normalize_document_keyword,
        )

        cleaned = normalize_document_keyword(value)
        if len(cleaned) < 2:
            raise serializers.ValidationError("Keyword too short.")
        # Operators belong in filetypes; keep keyword as a plain phrase.
        lowered = cleaned.lower()
        if "filetype:" in lowered:
            raise serializers.ValidationError(
                "Omit filetype: from keyword — set filetypes instead."
            )
        if document_keyword_too_long(cleaned):
            raise serializers.ValidationError(
                f"Use exactly {DOCUMENT_KEYWORD_MAX_WORDS} words "
                '(e.g. "cyber warfare", "Taiwan Strait").'
            )
        return cleaned

    def validate_filetypes(self, value: str) -> str:
        parts = [
            p.strip().lstrip(".").lower()
            for p in (value or "pdf").split(",")
            if p.strip()
        ]
        if not parts:
            return "pdf"
        allowed = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx"}
        bad = [p for p in parts if p not in allowed]
        if bad:
            raise serializers.ValidationError(
                f"Unsupported filetype(s): {', '.join(bad)}"
            )
        return ",".join(parts)


class ScannedDocumentSerializer(serializers.ModelSerializer):
    keyword_name = serializers.CharField(
        source="keyword.name", read_only=True, default=""
    )

    class Meta:
        model = ScannedDocument
        fields = (
            "id",
            "title",
            "title_vi",
            "title_vi_status",
            "title_vi_provider",
            "summary",
            "source_url",
            "file_path",
            "host",
            "filetype",
            "keyword",
            "keyword_name",
            "matched_keyword",
            "source",
            "engine",
            "status",
            "importance_score",
            "is_important",
            "published_at",
            "discovered_at",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "title",
            "title_vi",
            "title_vi_status",
            "title_vi_provider",
            "summary",
            "source_url",
            "file_path",
            "host",
            "filetype",
            "keyword",
            "keyword_name",
            "matched_keyword",
            "source",
            "engine",
            "importance_score",
            "is_important",
            "published_at",
            "discovered_at",
            "metadata",
            "created_at",
            "updated_at",
        )


class AlertNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertNotification
        fields = (
            "id",
            "rule",
            "title",
            "message",
            "severity",
            "is_read",
            "threat",
            "leak",
            "indicator",
            "document",
            "recipient",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "rule",
            "title",
            "message",
            "severity",
            "threat",
            "leak",
            "indicator",
            "document",
            "recipient",
            "created_at",
            "updated_at",
        )
