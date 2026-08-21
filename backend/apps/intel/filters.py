import unicodedata
import re

import django_filters

from django.db.models import Q

from .models import (
    CompromisedCredential,
    DataLeak,
    Indicator,
    ScannedDocument,
    Threat,
    ThreatActor,
)


COUNTRY_SEARCH_ALIASES = {
    "viet nam": "vietnam",
    "vietnam": "vietnam",
    "my": "geo-united-states",
    "hoa ky": "geo-united-states",
    "united states": "geo-united-states",
    "usa": "geo-united-states",
    "us": "geo-united-states",
    "trung quoc": "geo-china",
    "china": "geo-china",
    "dai loan": "geo-taiwan",
    "taiwan": "geo-taiwan",
    "nhat ban": "geo-japan",
    "japan": "geo-japan",
    "philippines": "geo-philippines",
    "phillipines": "geo-philippines",
    "lao": "geo-laos",
    "laos": "geo-laos",
    "thai lan": "geo-thailand",
    "thailand": "geo-thailand",
    "campuchia": "geo-cambodia",
    "cambodia": "geo-cambodia",
    "indonesia": "geo-indonesia",
    "malaysia": "geo-malaysia",
    "australia": "geo-australia",
    "uc": "geo-australia",
    "nga": "geo-russia",
    "russia": "geo-russia",
    "ukraine": "geo-ukraine",
    "ukraina": "geo-ukraine",
    "myanmar": "geo-myanmar",
    "myanma": "geo-myanmar",
    "mien dien": "geo-myanmar",
    "burma": "geo-myanmar",
}

# Must match frontend WIRE_COUNTRY_FILTER_OPTIONS / flag geography tags exactly.
WIRE_COUNTRY_FILTER_SLUGS = frozenset(
    {
        "geo-china",
        "geo-united-states",
        "geo-philippines",
        "geo-taiwan",
        "geo-thailand",
        "geo-indonesia",
        "geo-malaysia",
        "vietnam",
        "geo-japan",
        "geo-cambodia",
        "geo-laos",
        "geo-australia",
        "geo-russia",
        "geo-ukraine",
        "geo-myanmar",
    }
)


def _normalize_country_search(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_value.casefold().replace("-", " ").split())


def resolve_wire_country_slug(value: str) -> str | None:
    """
    Resolve a country filter value to the exact geography tag slug used for flags.

    Accepts allowlisted slugs (geo-china, vietnam, …) or known aliases (Mỹ, Nhật Bản).
    Never invents slugs from free text — unknown input yields None.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.casefold()
    if lowered in WIRE_COUNTRY_FILTER_SLUGS:
        return lowered
    normalized = _normalize_country_search(raw)
    if not normalized:
        return None
    slug = COUNTRY_SEARCH_ALIASES.get(normalized)
    if slug in WIRE_COUNTRY_FILTER_SLUGS:
        return slug
    return None


def normalize_publisher_query(value: str) -> tuple[str, str, str]:
    """
    Return (raw_token, slug_token, host_token).

    slug_token is hyphenated (japan-mod, secrss).
    host_token is the first path segment when the query looks like a domain.
    """
    raw = " ".join(str(value or "").strip().split())
    lowered = raw.casefold()
    cleaned = (
        lowered.replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .strip(" /")
    )
    host = cleaned.split("/")[0].strip()
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return raw, slug, host


def publisher_match_q(value: str) -> Q | None:
    """
    Precise publisher matching — avoids substring false positives.

    Matches:
    - exact feed name (japan-mod)
    - feed name prefix group (secrss → secrss, secrss-apt)
    - exact site-* tag (site-secrss-com, site-mod-go-jp)
    - domain in article/feed URL host (mod.go.jp, secrss.com)
    """
    raw, slug, host = normalize_publisher_query(value)
    if not raw or len(slug) < 2:
        return None

    query = Q(raw_payload__feed__iexact=raw) | Q(raw_payload__feed__iexact=slug)
    # Prefix groups (secrss → secrss-apt); skip for very short tokens.
    if len(slug) >= 4:
        query |= Q(raw_payload__feed__istartswith=f"{slug}-")

    # site-* tags: exact, or site-{slug}-* for longer names (secrss → site-secrss-com).
    # Short tokens like "mod" must NOT prefix-match site-mod-go-jp.
    if slug.startswith("site-"):
        query |= Q(tags__slug=slug)
        if len(slug) >= 9:  # "site-x" + meaningful rest
            query |= Q(tags__slug__startswith=f"{slug}-")
    else:
        query |= Q(tags__slug=f"site-{slug}")
        if len(slug) >= 4:
            query |= Q(tags__slug__startswith=f"site-{slug}-")

    # Domain / host queries only — never bare icontains on full URL path.
    if "." in host and " " not in host:
        host_variants = {host}
        if not host.startswith("www."):
            host_variants.add(f"www.{host}")
        for variant in host_variants:
            query |= Q(source_url__icontains=f"://{variant}/")
            query |= Q(source_url__icontains=f"://{variant}?")
            query |= Q(source_url__iendswith=f"://{variant}")
            query |= Q(raw_payload__feed_url__icontains=f"://{variant}/")
            query |= Q(raw_payload__feed_url__icontains=f"://{variant}?")
            query |= Q(raw_payload__feed_url__icontains=f"://{variant}&")
            # feed_url may be https://host/path without trailing slash edge cases
            query |= Q(raw_payload__feed_url__icontains=f"://{variant}/")
        # Also allow host as the registered domain fragment in feed_url query-less forms.
        query |= Q(raw_payload__feed_url__icontains=f"://{host}/")
        query |= Q(source_url__icontains=f".{host}/")

    return query


class IndicatorFilter(django_filters.FilterSet):
    value = django_filters.CharFilter(field_name="value", lookup_expr="icontains")
    domain = django_filters.CharFilter(
        field_name="normalized_value", lookup_expr="icontains"
    )
    tag = django_filters.CharFilter(field_name="tags__slug")
    first_seen_after = django_filters.IsoDateTimeFilter(
        field_name="first_seen", lookup_expr="gte"
    )
    last_seen_before = django_filters.IsoDateTimeFilter(
        field_name="last_seen", lookup_expr="lte"
    )

    class Meta:
        model = Indicator
        fields = {
            "ioc_type": ["exact"],
            "confidence": ["exact"],
            "tlp": ["exact"],
            "source": ["exact", "icontains"],
            "is_active": ["exact"],
        }


class ThreatFilter(django_filters.FilterSet):
    title = django_filters.CharFilter(field_name="title", lookup_expr="icontains")
    cve = django_filters.CharFilter(method="filter_cve")
    tag = django_filters.CharFilter(field_name="tags__slug")
    country = django_filters.CharFilter(method="filter_country")
    # Free-text publisher / feed name (e.g. secrss, japan-mod, mod.go.jp).
    publisher = django_filters.CharFilter(method="filter_publisher")
    published_after = django_filters.IsoDateTimeFilter(
        field_name="published_at", lookup_expr="gte"
    )
    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    # Uniform 30-day window; capped to the newest configured Wire item count.
    wire_feed = django_filters.BooleanFilter(method="filter_wire_feed")

    class Meta:
        model = Threat
        fields = {
            "severity": ["exact"],
            "status": ["exact"],
            "source": ["exact"],
            "is_kev": ["exact"],
        }

    def filter_cve(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(cve_ids__contains=[value.upper()])

    def filter_country(self, queryset, name, value):
        """Filter Wire items by exact geography/flag tag slug only."""
        slug = resolve_wire_country_slug(value)
        if not slug:
            # Unknown country token — do not fall back to fuzzy text matching.
            return queryset.none()
        return queryset.filter(tags__slug=slug).distinct()

    def filter_publisher(self, queryset, name, value):
        """Match RSS feed name, site-* tag, or URL host — no loose substring noise."""
        query = publisher_match_q(value)
        if query is None:
            return queryset.none() if str(value or "").strip() else queryset
        return queryset.filter(query).distinct()

    def filter_wire_feed(self, queryset, name, value):
        if not value:
            return queryset
        from datetime import timedelta

        from django.conf import settings
        from django.db.models import Exists, OuterRef, Q, Subquery
        from django.utils import timezone

        now = timezone.now()
        general_days = int(getattr(settings, "WIRE_MAX_AGE_DAYS", 30) or 30)
        vietnam_days = int(
            getattr(settings, "WIRE_VIETNAM_MAX_AGE_DAYS", general_days)
            or general_days
        )
        general_cut = now - timedelta(days=general_days)
        vietnam_cut = now - timedelta(days=vietnam_days)
        # Long window only for tagged Vietnam stories — never trust wire_priority alone.
        # Use an EXISTS subquery instead of joining the many-to-many tags table.
        # The previous join forced DISTINCT + a large sort on every Wire request.
        vietnam_tag = Threat.objects.filter(
            pk=OuterRef("pk"),
            tags__slug="vietnam",
        )
        eligible = (
            queryset.filter(wire_relevant=True)
            .filter(
                Q(published_at__gte=general_cut)
                | (
                    Q(published_at__gte=vietnam_cut)
                    & Exists(vietnam_tag)
                )
            )
            .filter(
                Q(title_vi_status__in=["ok", "rule", "skipped"])
                & ~Q(title_vi="")
            )
        )
        max_items = max(1, int(getattr(settings, "WIRE_MAX_ITEMS", 5000) or 5000))
        top_ids = eligible.order_by("-published_at", "-id").values("pk")[:max_items]
        return eligible.filter(pk__in=Subquery(top_ids))


class DataLeakFilter(django_filters.FilterSet):
    title = django_filters.CharFilter(field_name="title", lookup_expr="icontains")
    domain = django_filters.CharFilter(
        field_name="affected_domain", lookup_expr="icontains"
    )
    org = django_filters.CharFilter(
        field_name="affected_organization", lookup_expr="icontains"
    )
    discovered_after = django_filters.IsoDateTimeFilter(
        field_name="discovered_at", lookup_expr="gte"
    )

    class Meta:
        model = DataLeak
        fields = {
            "leak_type": ["exact"],
            "severity": ["exact"],
            "status": ["exact"],
            "source": ["exact"],
        }


class CompromisedCredentialFilter(django_filters.FilterSet):
    email = django_filters.CharFilter(field_name="email", lookup_expr="icontains")
    username = django_filters.CharFilter(field_name="username", lookup_expr="icontains")
    domain = django_filters.CharFilter(field_name="domain", lookup_expr="icontains")

    class Meta:
        model = CompromisedCredential
        fields = {
            "leak": ["exact"],
            "stealer_family": ["exact"],
            "country": ["exact", "icontains"],
        }


class ThreatActorFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ThreatActor
        fields = {
            "is_active": ["exact"],
            "country": ["exact", "icontains"],
        }


class ScannedDocumentFilter(django_filters.FilterSet):
    title = django_filters.CharFilter(field_name="title", lookup_expr="icontains")
    keyword = django_filters.CharFilter(
        field_name="matched_keyword", lookup_expr="icontains"
    )
    host = django_filters.CharFilter(field_name="host", lookup_expr="icontains")
    discovered_after = django_filters.IsoDateTimeFilter(
        field_name="discovered_at", lookup_expr="gte"
    )
    feed = django_filters.BooleanFilter(method="filter_feed")

    class Meta:
        model = ScannedDocument
        fields = {
            "filetype": ["exact"],
            "status": ["exact"],
            "source": ["exact"],
            "is_important": ["exact"],
        }

    def filter_feed(self, queryset, name, value):
        if not value:
            return queryset
        from datetime import timedelta

        from django.conf import settings
        from django.utils import timezone

        days = int(getattr(settings, "DOCUMENT_SCAN_MAX_AGE_DAYS", 31) or 31)
        cut = timezone.now() - timedelta(days=max(1, days))
        # Feed shows recently *published* docs only (not scan/discovery time).
        return queryset.filter(
            is_important=True,
            published_at__isnull=False,
            published_at__gte=cut,
        )
