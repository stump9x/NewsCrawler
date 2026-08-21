"""X/Twitter cookie search + status/replies enrich (GraphQL — v1.1 search is 404)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from django.conf import settings

from apps.integrations.web_reader.phrase import contains_phrase
from apps.integrations.web_reader.reader import ReadResult

logger = logging.getLogger(__name__)

_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_DEFAULT_TIMEOUT = 25.0
_STATUS_RE = re.compile(
    r"(?i)^https?://(?:www\.)?(?:twitter|x)\.com/([^/]+)/status/(\d+)"
)

# twikit PR #419 — X rotated SearchTimeline query id (v1.1 search/tweets.json → 404).
_DEFAULT_SEARCH_QUERY_ID = "R0u1RWRf748KzyGBXvOYRA"
_DEFAULT_TWEET_QUERY_ID = "Xl5pC_lBk_gcO2ItU39DQw"

_SEARCH_TIMELINE_FEATURES: dict[str, bool] = {
    "rweb_video_screen_enabled": False,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

_TWEET_RESULT_FEATURES: dict[str, bool] = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


def x_twitter_configured() -> bool:
    auth = (getattr(settings, "X_AUTH_TOKEN", "") or "").strip()
    ct0 = (getattr(settings, "X_CT0", "") or "").strip()
    enabled = bool(getattr(settings, "X_TWITTER_ENABLED", True))
    return enabled and bool(auth) and bool(ct0)


def doctor_x_twitter() -> dict[str, Any]:
    auth = bool((getattr(settings, "X_AUTH_TOKEN", "") or "").strip())
    ct0 = bool((getattr(settings, "X_CT0", "") or "").strip())
    enabled = bool(getattr(settings, "X_TWITTER_ENABLED", True))
    ok = enabled and auth and ct0
    missing = []
    if not auth:
        missing.append("X_AUTH_TOKEN")
    if not ct0:
        missing.append("X_CT0")
    return {
        "id": "x_twitter",
        "label": "X / Twitter cookie search",
        "role": "discover",
        "ok": ok,
        "configured": ok,
        "detail": (
            "ready (GraphQL)"
            if ok
            else ("disabled" if not enabled else f"missing {', '.join(missing)}")
        ),
    }


def is_x_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host not in {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }:
        return False
    return bool(_STATUS_RE.match((url or "").strip()))


def _parse_status(url: str) -> tuple[str, str] | None:
    m = _STATUS_RE.match((url or "").strip().split("?")[0])
    if not m:
        return None
    return m.group(1), m.group(2)


def _search_query_id() -> str:
    return (
        getattr(settings, "X_SEARCH_QUERY_ID", "") or _DEFAULT_SEARCH_QUERY_ID
    ).strip()


def _tweet_query_id() -> str:
    return (
        getattr(settings, "X_TWEET_QUERY_ID", "") or _DEFAULT_TWEET_QUERY_ID
    ).strip()


def _gql_url(query_id: str, operation: str) -> str:
    return f"https://x.com/i/api/graphql/{query_id}/{operation}"


def _headers() -> dict[str, str]:
    auth = (getattr(settings, "X_AUTH_TOKEN", "") or "").strip()
    ct0 = (getattr(settings, "X_CT0", "") or "").strip()
    return {
        "authorization": f"Bearer {_X_BEARER}",
        "cookie": f"auth_token={auth}; ct0={ct0}",
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "accept": "*/*",
        "referer": "https://x.com/search",
    }


def _gql_call(
    client: httpx.Client,
    *,
    query_id: str,
    operation: str,
    variables: dict[str, Any],
    features: dict[str, bool],
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    url = _gql_url(query_id, operation)
    body = {"variables": variables, "features": features, "queryId": query_id}
    headers = _headers()
    # POST first (required since ~2025); GET as fallback.
    for method in ("post", "get"):
        try:
            if method == "post":
                response = client.post(url, json=body, headers=headers)
            else:
                params = {
                    "variables": json.dumps(variables, separators=(",", ":")),
                    "features": json.dumps(features, separators=(",", ":")),
                }
                if extra_params:
                    for key, val in extra_params.items():
                        params[key] = json.dumps(val, separators=(",", ":"))
                response = client.get(url, params=params, headers=headers)
            if response.status_code == 404:
                continue
            if response.status_code in {401, 403}:
                return {"_error": response.status_code}
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except httpx.HTTPError as exc:
            logger.info("X GraphQL %s %s failed: %s", method, operation, exc)
            continue
        except ValueError as exc:
            logger.info("X GraphQL %s JSON error: %s", operation, exc)
            continue
    return None


def _tweet_text(node: dict[str, Any]) -> str:
    leg = node.get("legacy") if isinstance(node.get("legacy"), dict) else {}
    text = str(leg.get("full_text") or leg.get("text") or "")
    if text:
        return text
    note = node.get("note_tweet")
    if isinstance(note, dict):
        note_res = note.get("note_tweet_results")
        if isinstance(note_res, dict):
            res = note_res.get("result")
            if isinstance(res, dict):
                return str(res.get("text") or "")
    return ""


def _screen_name(node: dict[str, Any]) -> str:
    core = node.get("core") if isinstance(node.get("core"), dict) else {}
    user_res = core.get("user_results") if isinstance(core, dict) else {}
    user = user_res.get("result") if isinstance(user_res, dict) else {}
    if isinstance(user, dict):
        # ~2026 GraphQL: screen_name lives under user.core (not legacy).
        user_core = user.get("core") if isinstance(user.get("core"), dict) else {}
        sn = user_core.get("screen_name")
        if sn:
            return str(sn)
        leg = user.get("legacy") if isinstance(user.get("legacy"), dict) else {}
        sn = leg.get("screen_name")
        if sn:
            return str(sn)
    user_legacy = node.get("user") if isinstance(node.get("user"), dict) else {}
    if isinstance(user_legacy, dict):
        leg = user_legacy.get("legacy") if isinstance(user_legacy.get("legacy"), dict) else user_legacy
        return str(leg.get("screen_name") or "")
    return ""


def _tweet_id(node: dict[str, Any]) -> str:
    leg = node.get("legacy") if isinstance(node.get("legacy"), dict) else {}
    tid = leg.get("id_str") or leg.get("id") or node.get("rest_id")
    return str(tid or "").strip()


def _collect_tweet_nodes(node: Any, out: list[dict[str, Any]], *, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(node, list):
        for item in node:
            _collect_tweet_nodes(item, out, depth=depth + 1)
        return
    if not isinstance(node, dict):
        return
    typename = str(node.get("__typename") or "")
    if typename in {"Tweet", "TweetWithVisibilityResults"}:
        payload = node.get("tweet") if typename == "TweetWithVisibilityResults" else node
        if isinstance(payload, dict) and _tweet_text(payload):
            out.append(payload)
    if "tweet_results" in node:
        tr = node.get("tweet_results")
        if isinstance(tr, dict):
            res = tr.get("result")
            if isinstance(res, dict):
                _collect_tweet_nodes(res, out, depth=depth + 1)
    for val in node.values():
        if isinstance(val, (dict, list)):
            _collect_tweet_nodes(val, out, depth=depth + 1)


def _hits_from_search_payload(
    payload: dict[str, Any],
    *,
    term: str,
    limit: int,
    match_from_user: str | None = None,
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    _collect_tweet_nodes(payload, nodes)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    want_user = (match_from_user or "").lstrip("@").strip().casefold()
    for node in nodes:
        tid = _tweet_id(node)
        if not tid:
            continue
        text = _tweet_text(node)[:4000]
        screen = _screen_name(node) or "i"
        if want_user:
            if screen.casefold() != want_user:
                continue
        elif not contains_phrase(text, term):
            continue
        url = f"https://x.com/{quote(screen)}/status/{tid}"
        if url in seen:
            continue
        seen.add(url)
        title = f"@{screen}: {text[:80]}" if text else f"X post {tid}"
        leg = node.get("legacy") if isinstance(node.get("legacy"), dict) else {}
        created = str(leg.get("created_at") or "")[:64]
        hits.append(
            {
                "title": title[:512],
                "url": url[:2048],
                "content": text,
                "engine": "x_twitter",
                "score": None,
                "published": created,
                "screen_name": screen,
            }
        )
        if len(hits) >= limit:
            break
    return hits


def fetch_x_user_posts(screen_name: str, *, limit: int = 12) -> dict[str, Any]:
    """
    Latest posts from one account via SearchTimeline `from:user`.

    Unlike keyword search, does not require the query string inside tweet text.
    """
    empty: dict[str, Any] = {
        "hits": [],
        "error": None,
        "configured": x_twitter_configured(),
        "screen_name": "",
    }
    handle = (screen_name or "").lstrip("@").strip()
    if not handle:
        empty["error"] = "empty_screen_name"
        return empty
    empty["screen_name"] = handle
    if not x_twitter_configured():
        empty["error"] = "not_configured"
        return empty

    limit_n = max(1, min(int(limit or 12), 40))
    fetch = max(limit_n, min(limit_n * 2, 40))
    query = f"from:{handle}"
    last_error = None
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            for product in ("Latest", "Top"):
                payload = _search_timeline(
                    client, query, product=product, count=fetch
                )
                if payload is None:
                    last_error = "GraphQL SearchTimeline unavailable (404)"
                    continue
                if payload.get("_error"):
                    code = payload["_error"]
                    return {
                        "hits": [],
                        "error": f"HTTP {code} — refresh X cookies",
                        "configured": True,
                        "screen_name": handle,
                    }
                hits = _hits_from_search_payload(
                    payload,
                    term=handle,
                    limit=limit_n,
                    match_from_user=handle,
                )
                if hits:
                    return {
                        "hits": hits,
                        "error": None,
                        "configured": True,
                        "screen_name": handle,
                    }
    except httpx.HTTPError as exc:
        last_error = str(exc)[:160]
        logger.warning("X user timeline %s failed: %s", handle, exc)

    return {
        "hits": [],
        "error": last_error or "no_hits",
        "configured": True,
        "screen_name": handle,
    }


def _search_timeline(
    client: httpx.Client, query: str, *, product: str, count: int
) -> dict[str, Any] | None:
    variables = {
        "rawQuery": query,
        "count": count,
        "querySource": "typed_query",
        "product": product,
        "withGrokTranslatedBio": True,
    }
    return _gql_call(
        client,
        query_id=_search_query_id(),
        operation="SearchTimeline",
        variables=variables,
        features=_SEARCH_TIMELINE_FEATURES,
    )


def search_x_twitter(query: str, *, limit: int = 15) -> list[dict[str, Any]]:
    detail = search_x_twitter_detail(query, limit=limit)
    return detail.get("hits") or []


def search_x_twitter_detail(query: str, *, limit: int = 15) -> dict[str, Any]:
    empty = {"hits": [], "error": None, "configured": x_twitter_configured()}
    if not x_twitter_configured():
        empty["error"] = "not_configured"
        return empty
    term = " ".join((query or "").split()).strip()
    if not term:
        empty["error"] = "empty_query"
        return empty
    if is_x_url(term):
        parsed = _parse_status(term)
        if parsed:
            screen, tid = parsed
            url = f"https://x.com/{quote(screen)}/status/{tid}"
            return {
                "hits": [
                    {
                        "title": f"X status {tid}",
                        "url": url,
                        "content": "",
                        "engine": "x_twitter",
                        "score": None,
                    }
                ],
                "error": None,
                "configured": True,
            }
    limit = max(1, min(int(limit or 15), 50))
    fetch = max(limit, min(limit * 3, 40))
    # Multi-word: try quoted first (precision), then unquoted (recall).
    # Local NFC/diacritic phrase filter still enforces title/body match.
    if " " in term:
        search_queries = [f'"{term[:398]}"', term[:400]]
    else:
        search_queries = [term[:400]]
    last_error = None
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            for search_q in search_queries:
                for product in ("Latest", "Top"):
                    payload = _search_timeline(
                        client, search_q, product=product, count=fetch
                    )
                    if payload is None:
                        last_error = "GraphQL SearchTimeline unavailable (404)"
                        continue
                    if payload.get("_error"):
                        code = payload["_error"]
                        return {
                            "hits": [],
                            "error": f"HTTP {code} — refresh X cookies",
                            "configured": True,
                        }
                    hits = _hits_from_search_payload(payload, term=term, limit=limit)
                    if hits:
                        return {"hits": hits, "error": None, "configured": True}
    except httpx.HTTPError as exc:
        last_error = str(exc)[:160]
        logger.warning("X search failed: %s", exc)

    return {
        "hits": [],
        "error": last_error or "no_hits",
        "configured": True,
    }


def _fetch_tweet_gql(client: httpx.Client, tweet_id: str) -> dict[str, Any] | None:
    variables = {
        "tweetId": tweet_id,
        "withCommunity": False,
        "includePromotedContent": False,
        "withVoice": False,
    }
    extra = {
        "fieldToggles": {
            "withArticleRichContentState": True,
            "withArticlePlainText": False,
            "withGrokAnalyze": False,
        }
    }
    url = _gql_url(_tweet_query_id(), "TweetResultByRestId")
    body = {
        "variables": variables,
        "features": _TWEET_RESULT_FEATURES,
        "queryId": _tweet_query_id(),
    }
    headers = _headers()
    for method in ("get", "post"):
        try:
            if method == "get":
                params = {
                    "variables": json.dumps(variables, separators=(",", ":")),
                    "features": json.dumps(_TWEET_RESULT_FEATURES, separators=(",", ":")),
                    "fieldToggles": json.dumps(extra["fieldToggles"], separators=(",", ":")),
                }
                response = client.get(url, params=params, headers=headers)
            else:
                response = client.post(url, json=body, headers=headers)
            if response.status_code in {401, 403, 404}:
                continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
        except (httpx.HTTPError, ValueError):
            continue
    return None


def _fetch_replies(client: httpx.Client, tweet_id: str, *, limit: int = 40) -> list[str]:
    """Best-effort replies via SearchTimeline conversation search."""
    q = f"conversation_id:{tweet_id}"
    payload = _search_timeline(client, q, product="Latest", count=min(limit, 40))
    if not payload or payload.get("_error"):
        return []
    nodes: list[dict[str, Any]] = []
    _collect_tweet_nodes(payload, nodes)
    texts: list[str] = []
    for node in nodes:
        tid = _tweet_id(node)
        if tid == tweet_id:
            continue
        text = _tweet_text(node).strip()
        if text:
            texts.append(text[:2000])
        if len(texts) >= limit:
            break
    return texts


def read_x_status(url: str) -> ReadResult:
    if not x_twitter_configured():
        return ReadResult(False, "x_twitter", "", "X cookies not configured")
    parsed = _parse_status(url)
    if not parsed:
        return ReadResult(False, "x_twitter", "", "not an X status url")
    screen_hint, tid = parsed
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            payload = _fetch_tweet_gql(client, tid)
            if not payload:
                return ReadResult(
                    False,
                    "x_twitter",
                    "",
                    "tweet fetch failed — refresh X cookies or set X_TWEET_QUERY_ID",
                )
            nodes: list[dict[str, Any]] = []
            _collect_tweet_nodes(payload, nodes)
            main_node = nodes[0] if nodes else {}
            screen = _screen_name(main_node) or screen_hint
            main = _tweet_text(main_node).strip()
            replies = _fetch_replies(client, tid, limit=50)
    except httpx.HTTPError as exc:
        return ReadResult(False, "x_twitter", "", str(exc)[:200])

    parts = [f"@{screen}: {main}" if main else f"@{screen} status {tid}"]
    for reply in replies:
        parts.append(f"reply: {reply}")
    text = "\n\n".join(parts)[:200_000]
    if not text.strip():
        return ReadResult(False, "x_twitter", "", "empty status text")
    return ReadResult(True, "x_twitter", text)
