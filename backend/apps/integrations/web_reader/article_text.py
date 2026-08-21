"""Clean article extraction: title + plain body only.

Strips HTML chrome (nav, ads, scripts, sidebars, social share widgets),
images, tracking/ad URLs, and markdown media so Notebook digest/chat
prompts receive text-only excerpts.
"""

from __future__ import annotations

import re
from typing import Any

# Drop entire DOM subtrees before text extraction.
_STRIP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "select",
    "textarea",
    "nav",
    "aside",
    "footer",
    "header",
    "menu",
    "figure",
    "figcaption",
    "picture",
    "source",
    "img",
    "video",
    "audio",
    "track",
    "map",
    "area",
)

_BOILERPLATE_CLASS_ID = re.compile(
    r"(?:^|[\s_-])(?:"
    r"nav|menu|sidebar|side-bar|aside|footer|header|masthead|toolbar|"
    r"cookie|consent|gdpr|banner|promo|advert|ads?|sponsor|related|"
    r"share|social|newsletter|subscribe|signup|sign-up|login|paywall|"
    r"comment|disqus|breadcrumb|pagination|popup|modal|overlay|"
    r"recommended|trending|popular|widget|taboola|outbrain|recirc|"
    r"twitter|facebook|fb-|linkedin|youtube|instagram|addthis|addtoany|"
    r"a2a|shareaholic|dfp|doubleclick|adsense|adsbygoogle|gpt-ad|"
    r"googlesyndication|social-icons?|share-buttons?|share-options?|"
    # Do NOT match bare «featured» — sites wrap real body in featured-content
    # after mid-article images; nuking that caused title-only Studio text.
    r"featured-(?:stories|articles|posts|widget|rail|sidebar|module|card)s?|"
    r"newsletter-signup|flyout|offcanvas|hamburger"
    r")(?:$|[\s_-])",
    re.I,
)

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_DATA_URI_RE = re.compile(r"data:(?:image|font|application)\/[a-z0-9.+-]+;base64,[a-z0-9+/=\s]+", re.I)
_URL_ONLY_LINE_RE = re.compile(
    r"^\s*https?://\S+\.(?:png|jpe?g|gif|webp|svg|ico|bmp|mp4|webm|mp3)(?:\?\S*)?\s*$",
    re.I,
)
_BARE_IMAGE_URL_RE = re.compile(
    r"https?://\S+\.(?:png|jpe?g|gif|webp|svg|ico|bmp)(?:\?\S*)?",
    re.I,
)
# Ad / tracker / share-widget URLs — never article body.
_AD_TRACKING_URL_RE = re.compile(
    r"https?://\S*(?:"
    r"doubleclick\.net|googlesyndication\.com|googleadservices\.com|"
    r"adservice\.google|pagead2\.googlesyndication|adnxs\.com|"
    r"adsrvr\.org|moatads\.com|scorecardresearch\.com|"
    r"facebook\.com/(?:tr|sharer|share\.php)|"
    r"(?:platform\.)?twitter\.com/(?:intent|widgets|share)|"
    r"x\.com/intent|linkedin\.com/(?:shareArticle|sharing)|"
    r"addtoany\.com|addthis\.com|sharethis\.com"
    r")\S*",
    re.I,
)
_URL_ONLY_ANY_RE = re.compile(r"^\s*https?://\S+\s*$", re.I)
_MULTI_NL_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
# Optional markdown/list bullet before chrome labels (• Twitter, - Facebook).
_BULLET_PREFIX = r"(?:[•·●◦▪▸►]|\d+\.|[-*+])\s*"

_BOILERPLATE_LINE_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:"
    r"skip\s+to\s+(?:main\s+)?content|skip\s+navigation|"
    r"open\s+navigation|close\s+navigation|toggle\s+(?:navigation|search)|"
    r"search\s*(?:subscribe)?|subscribe(?:\s+to)?(?:\s+sign\s*in)?|"
    r"sign\s*in|log\s*in|sign\s+up|create\s+an?\s+account|"
    r"share\s+(?:this|on|options?|article)|share\s*options?|"
    r"share\s+a\s+link(?:\s+to\s+(?:this\s+)?article)?|"
    r"copy\s+link|follow\s+us|connect\s+with\s+us|"
    r"cookie(?:s)?\s+(?:policy|settings|notice|preferences)?|"
    r"advertisement|sponsored\s+(?:content|posts?)\b.*|"
    r"presented\s+by|"
    r"related\s+articles?|"
    r"you\s+may\s+also\s+like|read\s+more:?|click\s+here|"
    r"learn\s+more\s*(?:>>|>|…|\.\.\.)?|"
    r"featured(?:\s+(?:stories|articles|posts))?|"
    r"newsletter(?:\s+signup)?|sign\s+up\s+for\s+(?:our\s+)?newsletter|"
    r"markdown\s+content:?|"
    r"about\s+us|webinars?|careers?|contact\s+us|"
    r"privacy(?:\s+policy)?|terms(?:\s+of\s+(?:use|service))?|"
    r"(?:europe|us|uk|asia|global)\s+edition|"
    r"search\s+for\s*:?.*|"
    r"all\s+rights\s+reserved|©\s*\d{4}|"
    r"menu|home\s*\|\s*news|sections?"
    r").*$"
)
# Lone social/share control labels (Breaking Defense–style chrome).
_SOCIAL_LABEL_LINE_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:"
    r"twitter|facebook|youtube|linkedin|instagram|tiktok|reddit|rss|"
    r"envelope|email|e-?mail|mail|whatsapp|telegram|pinterest|threads|"
    r"\bx\b|"
    r"share|share\s*options?|copy\s*link|print|permalink"
    r")\s*$"
)
# Row of social icons collapsed to one line.
_SOCIAL_SHARE_ROW_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:(?:twitter|facebook|youtube|linkedin|instagram|tiktok|"
    r"reddit|rss|envelope|email|e-?mail|mail|whatsapp|telegram|pinterest|"
    r"threads|share|copy\s*link)"
    r"(?:\s*[|/·•,]\s*|\s+)){2,}(?:twitter|facebook|youtube|linkedin|"
    r"instagram|tiktok|reddit|rss|envelope|email|e-?mail|mail|whatsapp|"
    r"telegram|pinterest|threads|share|copy\s*link)?\s*$"
)
# Section chip vocabulary (Breaking Defense nav + IE topic rails).
_SECTION_CHIP = (
    r"air|land|naval|space|cyber|networks?|ai|business|congress|pentagon|global|"
    r"intel(?:ligence)?|special\s+ops|policy|budget|industry|tech(?:nology)?|"
    r"international|air\s+force|army|navy|marines?|coast\s+guard|"
    r"news|videos?|energy|science|military|health|transportation|"
    r"innovation|culture|future\s+of\s+defense"
)
# Defense / vertical section menus (Air Land Naval …) on one line.
_SECTION_MENU_LINE_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:" + _SECTION_CHIP + r")"
    r"(?:\s*[|/·•,]\s*|\s+)(?:" + _SECTION_CHIP + r")"
    r"(?:(?:\s*[|/·•,]\s*|\s+)(?:" + _SECTION_CHIP + r"))+\s*$"
)
# Lone section/topic chip (e.g. «• Pentagon», «• AI», «• Networks»).
_SECTION_CHIP_LINE_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:" + _SECTION_CHIP + r")\s*$"
)
# Promo / featured / share chrome lines.
_PROMO_LINE_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:"
    r"news\s+video\s*:.*|"
    r"special\s+features?|"
    r"air\s*&\s*space\s+chiefs|"
    r"manned[-\s]?unmanned\s+teaming|"
    r"featured\s*:?|"
    r"read\s+next\s*:?.*|"
    r"recommended\s+articles?\s*:?|"
    r"get\s+your\s+news\s+from\b.*|"
    r"google\s+news|"
    r"sign\s+up\s+for\s+free|"
    r"enter\s+your\s+email|"
    r"blueprint\s+by\s+interesting\s+engineering|"
    r"\d+\s+comments?|"
    r"share\s+a\s+link(?:\s+to\s+(?:this\s+)?article)?|"
    r"presented\s+by|"
    r"sponsored\s+posts?\b.*"
    r")\s*$"
)
# Trailing end-of-article rails (Recommended / Topics / newsletter).
_ARTICLE_TRAILER_START_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:"
    r"topics\s*:|"
    r"recommended\s+articles?\s*:?|"
    r"you\s+may\s+(?:also\s+)?like\s*:?|"
    r"related\s+(?:articles?|stories|posts?)\s*:?|"
    r"blueprint\s+by\s+interesting\s+engineering|"
    r"get\s+the\s+latest\s+in\s+engineering|"
    r"sign\s+up\s+for\s+free|"
    r"\d+\s+comments?"
    r")\s*"
)
# Block-extract stop: trailer headings (even when matched as chrome lines).
_TRAILER_HEADING_LINE_RE = re.compile(
    r"(?i)^\s*(?:"
    r"recommended\s+articles?\s*:?|"
    r"you\s+may\s+(?:also\s+)?like\s*:?|"
    r"related\s+(?:articles?|stories|posts?)\s*:?|"
    r"topics\s*:|"
    r"read\s+next\s*:?|"
    r"blueprint\s+by\s+interesting\s+engineering|"
    r"get\s+the\s+latest\s+in\s+engineering"
    r")\s*$"
)
# Numbered / section-chip related-card headlines (no sentence period — not prose).
_RELATED_CARD_LINE_RE = re.compile(
    r"(?i)^\s*(?:"
    r"\d{1,2}\s+[A-ZÀ-ỸĐ\"“«][^.]{20,140}|"
    r"(?:" + _SECTION_CHIP + r")\s+[A-ZÀ-ỸĐ\"“«][^.]{20,120}"
    r")\s*$"
)
# Photo / image captions: «… (Photo by Name)».
_PHOTO_CAPTION_LINE_RE = re.compile(
    r"(?i)^.+\((?:photo|image|credit|source)\s+by\s+[^)]+\)\s*$"
)
# Short site-chrome crumbs that often precede the headline.
_LEADING_CHROME_CRUMB_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:"
    r"markdown\s+content:?|"
    r"about\s+us|webinars?|careers?|contact(?:\s+us)?|home|"
    r"privacy(?:\s+policy)?|terms(?:\s+of\s+(?:use|service))?|"
    r"(?:europe|us|uk|asia|global)\s+edition(?:\s*newsletter\s*signup)?|"
    r"newsletter\s*signup|toggle\s+search|"
    r"search\s+for\s*:?\s*(?:search)?|"
    r"breaking\s+defense|interesting\s+engineering|future\s+of\s+defense|"
    r"farnborough|networks?\s*(?:&\s*|and\s+)?digital\s+warfare|"
    r"open\s+navigation.*|close\s+navigation.*|"
    r"toggle\s+(?:search|navigation).*"
    r")\s*$"
)
_MARKDOWN_CONTENT_LABEL_RE = re.compile(
    r"(?im)^\s*markdown\s+content\s*:?\s*\n?"
)
# Trailing topic list marker — cut from this line through EOF.
_TOPICS_TRAILER_START_RE = re.compile(r"(?im)^[ \t]*topics\s*:\s*")
# Mid-article «presented by» / Sponsored Post insert (keep body after it).
_PRESENTED_BY_LINE_RE = re.compile(r"(?i)^\s*presented\s+by\s*$")
_SPONSORED_POST_LINE_RE = re.compile(r"(?i)^\s*sponsored\s*posts?\b")
_BYLINE_SHORT_RE = re.compile(
    r"(?i)^\s*by\s+(?:breaking\s+defense|[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3})\s*$"
)
# Author bio blurb after article (Interesting Engineering).
_AUTHOR_BIO_START_RE = re.compile(
    r"(?im)^[ \t]*by\s+[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3}\s*$"
)
_AUTHOR_BIO_PROSE_RE = re.compile(
    r"(?i)^\s*(?:an\s+active\s+and\s+versatile|is\s+a\s+(?:journalist|reporter|editor)|"
    r"he\s+has\s+covered|she\s+has\s+covered|aman\s+holds\s+expertise)\b"
)
# Social share <a href=…> targets to drop from the DOM.
_SOCIAL_HREF_RE = re.compile(
    r"(?i)(?:twitter\.com|x\.com|facebook\.com|linkedin\.com|youtube\.com|"
    r"youtu\.be|instagram\.com|tiktok\.com|wa\.me|t\.me|"
    r"addtoany\.com|addthis\.com|sharethis\.com|"
    r"mailto:)"
)

# Collapsed nav chrome often arrives as one long run-on line after tag strip.
_NAV_CHROME_BLOB_RE = re.compile(
    r"(?i)(?:skip\s+to\s+(?:main\s+)?content|skip\s+navigation)"
    r"[\s\S]{0,240}?(?:search|subscribe|sign\s*in|log\s*in)"
    r"(?:[\s\S]{0,160}?(?:energy|science|politics|world|business|tech|sports|opinion|culture))*"
)

# Lookahead must accept Latin, Vietnamese, AND CJK article starts (Chinese etc.).
_LEADING_NAV_WORDS_RE = re.compile(
    r"(?i)^(?:\s*(?:home|news|world|politics|business|tech(?:nology)?|science|"
    r"energy|sports|opinion|culture|climate|health|video|podcasts?|"
    r"newsletter|account|search|subscribe|sign\s*in|log\s*in|"
    r"skip\s+to\s+(?:main\s+)?content)"
    r"(?:\s*[|/·•>»→,]?\s*)?)+(?=[A-ZÀ-ỸĐ\"“«\u3400-\u9fff\uf900-\ufaff]|\d)"
)

# Captcha / anti-bot interstitial markers (do not treat as article body).
_FETCH_BLOCK_RE = re.compile(
    r"(?i)(?:\bcaptcha\b|recaptcha|hcaptcha|cf-browser-verification|"
    r"just\s+a\s+moment|attention\s+required|access\s+denied|"
    r"unusual\s+traffic|verify\s+you\s+are\s+(?:a\s+)?human|"
    r"checking\s+your\s+browser|enable\s+javascript\s+and\s+cookies|"
    r"blocked\s+by\s+(?:cloudflare|challenge)|challenge-platform)"
)

_MENU_WORD_LINE_RE = re.compile(
    r"(?i)^\s*(?:" + _BULLET_PREFIX + r")?(?:home|news|world|politics|business|tech(?:nology)?|science|"
    r"energy|sports|opinion|culture|climate|health|video|podcasts?|"
    r"newsletter|account|profile|settings|help|about|contact|"
    r"air|land|naval|space|cyber)"
    r"(?:\s*[|/·•>»→]\s*(?:home|news|world|politics|business|tech(?:nology)?|"
    r"science|energy|sports|opinion|culture|climate|health|video|podcasts?|"
    r"newsletter|account|profile|settings|help|about|contact|search|"
    r"subscribe|sign\s*in|air|land|naval|space|cyber)){2,}\s*$"
)


def _looks_like_html(raw: str) -> bool:
    s = (raw or "").lstrip()[:500].lower()
    return s.startswith("<!doctype") or s.startswith("<html") or "<body" in s or "<article" in s


def _node_prose_len(node) -> int:
    try:
        return len(" ".join(node.get_text(" ", strip=True).split()))
    except Exception:  # noqa: BLE001
        return 0


def _pick_article_root(soup):
    """Choose the real article container — never the first tiny related ``<article>`` card.

    Interesting Engineering nests the headline in ``<main class=ie-new-article-page>``
    while several short related-story ``<article>`` cards appear first in DOM order.
    Picking ``soup.find('article')`` therefore yielded ~80 chars (title-only regression).
    """
    candidates: list[tuple[int, int, Any]] = []

    def _consider(node) -> None:
        if node is None:
            return
        prose = _node_prose_len(node)
        if prose < 120:
            return
        try:
            blocks = len(node.find_all(["p", "h1", "h2", "h3", "li"]))
        except Exception:  # noqa: BLE001
            blocks = 0
        # Prefer more prose; break ties with block count.
        candidates.append((prose, blocks, node))

    for node in soup.find_all("main"):
        _consider(node)
    for node in soup.find_all("article"):
        _consider(node)
    _consider(soup.find(attrs={"itemprop": "articleBody"}))
    for node in soup.find_all(
        class_=re.compile(
            r"(?:^|[\s_-])(?:article|story|post)(?:-?body|-?content)?(?:$|[\s_-])",
            re.I,
        )
    ):
        _consider(node)

    if candidates:
        candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
        best_prose, best_blocks, best = candidates[0]
        # Related cards are short; require real article mass.
        if best_prose >= 400 or best_blocks >= 3:
            return best

    return soup.body or soup


def _soup_extract(raw: str) -> tuple[str, str]:
    """BeautifulSoup path: drop chrome/media tags, keep full article prose.

    Images/figures are removed in-place so extraction **continues** with
    paragraphs after mid-article media (never treat media as end-of-article).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw or "", "html.parser")
    # Strip media/chrome tags but keep following siblings (join across images).
    # Keep ``<header>`` that still holds the article ``<h1>`` (IE puts title/dek
    # there — decomposing it dropped the headline and left related cards only).
    for tag in soup(_STRIP_TAGS):
        if tag.name == "header" and tag.find("h1"):
            continue
        tag.decompose()
    # Drop nodes whose class/id looks like chrome — but never nuke a wrapper
    # that still holds substantial article paragraphs (post-image body).
    for node in list(soup.find_all(True)):
        try:
            attrs = " ".join(
                [
                    " ".join(node.get("class") or []),
                    str(node.get("id") or ""),
                    str(node.get("role") or ""),
                    str(node.get("aria-label") or ""),
                ]
            )
        except Exception:  # noqa: BLE001
            continue
        if attrs and _BOILERPLATE_CLASS_ID.search(attrs):
            prose = " ".join(node.get_text(" ", strip=True).split())
            # Keep large prose containers even if class matched loosely.
            if len(prose) >= 240 and node.find(["p", "h1", "h2", "h3"]):
                continue
            # Never drop the heading wrapper.
            if node.find("h1") and prose >= 80:
                continue
            node.decompose()

    # Drop social/share anchor chrome (labels like Twitter / Facebook).
    for a in list(soup.find_all("a", href=True)):
        href = str(a.get("href") or "")
        label = " ".join(a.get_text(" ", strip=True).split())
        if _SOCIAL_HREF_RE.search(href) and (
            not label
            or len(label) < 40
            or _SOCIAL_LABEL_LINE_RE.match(label)
        ):
            a.decompose()

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = " ".join(h1.get_text(" ", strip=True).split()).strip()
    if not title and soup.title and soup.title.string:
        title = " ".join(str(soup.title.string).split()).strip()

    root = _pick_article_root(soup)

    block_tags = ("h1", "h2", "h3", "p", "li", "blockquote")
    parts: list[str] = []
    seen: set[str] = set()
    for el in root.find_all(block_tags + ("div",)):
        if el.name == "div":
            # Leaf text divs only — skip structural wrappers (children handled).
            if el.find(block_tags + ("div", "ul", "ol", "section")):
                continue
        t = " ".join(el.get_text(" ", strip=True).split())
        # Trailer heading → stop (do not keep following related cards).
        if _TRAILER_HEADING_LINE_RE.match(t) or _ARTICLE_TRAILER_START_RE.match(t):
            break
        if _is_chrome_line(t):
            continue
        # Skip mid-article related-story cards (IE injects these between <p>s).
        if _RELATED_CARD_LINE_RE.match(t):
            continue
        min_len = 12 if re.search(r"[\u3400-\u9fff\uf900-\ufaff]", t) else 25
        if len(t) >= min_len:
            key = t.casefold()
            if key in seen:
                continue
            seen.add(key)
            parts.append(t)

    # Drop trailing related-card headlines (numbered / «Military …» chips).
    while parts and _RELATED_CARD_LINE_RE.match(parts[-1]):
        parts.pop()

    joined = "\n\n".join(parts)
    # Fallback: if we only captured title/dek but root still has long prose
    # (common when body after <img> lives in anonymous divs), take root text.
    root_flat = " ".join(root.get_text(" ", strip=True).split())
    title_len = len(title) if title else 0
    if len(joined) < max(200, title_len + 120) and len(root_flat) > len(joined) + 180:
        # Preserve rough paragraph breaks from block tags already stripped of media.
        joined = root.get_text("\n", strip=True)
    if not joined and root_flat:
        joined = root_flat
    return title[:400], joined


def looks_like_title_only(text: str, title: str = "") -> bool:
    """True when cleaned output is essentially just the headline/dek (regression).

    Title + short dek (~title+160) still counts as title-only — Studio needs
    real body paragraphs (typically hundreds of chars after the headline).
    Multi-paragraph prose is never treated as title-only.
    """
    raw = str(text or "")
    body = " ".join(raw.split()).strip()
    if not body:
        return True
    # Real articles have multiple blocks even when compact.
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    main = _title_main(title)
    if len(paras) >= 3 and len(body) >= max(240, (len(main) if main else 0) + 120):
        return False
    if main and body.casefold() == main.casefold():
        return True
    if main and body.casefold().startswith(main.casefold()):
        rest = body[len(main) :].strip(" \t-:|–—")
        if len(rest) < 400:
            return True
    # Very short relative to a normal article (title + dek is not enough).
    if main and len(body) < max(400, len(main) + 200):
        return True
    if not main and len(body) < 400:
        return True
    return False


# Back-compat alias used inside this module / older callers.
_looks_title_only = looks_like_title_only


def _is_chrome_line(line: str) -> bool:
    """True when a single line is nav / share / ad chrome, not prose."""
    s = str(line or "").strip()
    if not s:
        return False
    if _BOILERPLATE_LINE_RE.match(s):
        return True
    if _SOCIAL_LABEL_LINE_RE.match(s):
        return True
    if _SOCIAL_SHARE_ROW_RE.match(s):
        return True
    if _SECTION_MENU_LINE_RE.match(s):
        return True
    if _SECTION_CHIP_LINE_RE.match(s):
        return True
    if _PROMO_LINE_RE.match(s):
        return True
    if _PHOTO_CAPTION_LINE_RE.match(s):
        return True
    if _MENU_WORD_LINE_RE.match(s):
        return True
    if _LEADING_CHROME_CRUMB_RE.match(s):
        return True
    if _URL_ONLY_LINE_RE.match(s) or _URL_ONLY_ANY_RE.match(s):
        return True
    if _AD_TRACKING_URL_RE.search(s) and len(s) < 400:
        return True
    return False


def _title_main(title: str) -> str:
    """Drop site-name suffixes like « | Breaking Defense» / « - Site»."""
    t = " ".join(str(title or "").split()).strip()
    if not t:
        return ""
    parts = re.split(r"\s+[|\u2013\u2014\-]\s+", t, maxsplit=1)
    main = (parts[0] or "").strip()
    return main if len(main) >= 8 else t


def _find_title_index(text: str, title: str) -> int:
    """Index where the headline begins in ``text``, or -1 if not found.

    Prefers a same-line match of the full (or near-full) title so a lone
    category chip like «Pentagon» cannot steal the cut point.
    """
    raw_title = " ".join(str(title or "").split()).strip()
    if not raw_title or len(raw_title) < 8:
        return -1
    candidates = [raw_title]
    main = _title_main(raw_title)
    if main and main.casefold() != raw_title.casefold():
        candidates.append(main)

    body = str(text or "")
    # Jina / reader dumps often prefix the headline with «Title:».
    _title_line_prefix = r"(?:Title\s*:\s*)?"
    for cand in candidates:
        words = cand.split()
        if len(words) < 3 and len(cand) < 24:
            continue
        esc = [re.escape(w) for w in words]
        # Same-line match only ([ \\t]+ does not cross newlines).
        line_pat = re.compile(
            r"(?im)^[ \t]*(?:"
            + _BULLET_PREFIX
            + r")?"
            + _title_line_prefix
            + r"[ \t]+".join(esc)
        )
        m = line_pat.search(body)
        if m:
            # Prefer starting at the headline words, not the «Title:» label.
            inner = re.search(
                r"(?i)" + r"[ \t]+".join(esc), body[m.start() : m.start() + len(cand) + 40]
            )
            if inner:
                return m.start() + inner.start()
            return m.start()
        # Partial same-line: first 5–8 words (still must be one line).
        if len(words) >= 5:
            partial = words[: min(8, len(words))]
            if len(" ".join(partial)) >= 20:
                p_esc = [re.escape(w) for w in partial]
                p_pat = re.compile(
                    r"(?im)^[ \t]*(?:"
                    + _BULLET_PREFIX
                    + r")?"
                    + _title_line_prefix
                    + r"[ \t]+".join(p_esc)
                )
                m = p_pat.search(body)
                if m:
                    # Reject if the matched line is far shorter than the title
                    # (category crumbs / truncated nav).
                    line_end = body.find("\n", m.start())
                    line = body[m.start() : line_end if line_end >= 0 else None].strip()
                    if len(line) >= max(24, int(len(cand) * 0.45)):
                        inner = re.search(
                            r"(?i)" + r"[ \t]+".join(p_esc),
                            body[m.start() : m.start() + 120],
                        )
                        if inner:
                            return m.start() + inner.start()
                        return m.start()
    return -1


def cut_at_title(text: str, title: str = "") -> str:
    """Discard everything before the article title when title is found in text."""
    s = str(text or "")
    if not s.strip():
        return ""
    idx = _find_title_index(s, title)
    if idx > 0:
        return s[idx:].lstrip("\r\n \t")
    return s


def strip_leading_chrome_block(text: str) -> str:
    """Drop «Markdown Content:» and leading social/nav bullet chrome.

    Used when title match fails (or as a first pass before cut-at-title).
    """
    s = _MARKDOWN_CONTENT_LABEL_RE.sub("", str(text or ""), count=1)
    lines = s.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        bare = re.sub(r"^\s*" + _BULLET_PREFIX, "", line).strip()
        if _is_chrome_line(line) or _is_chrome_line(bare):
            i += 1
            continue
        # Glued run-on nav still sitting on one line.
        low = line.casefold()
        if (
            "open navigation" in low
            or "close navigation" in low
            or "toggle search" in low
            or "newsletter signup" in low.replace(" ", "")
        ) and len(line) < 220:
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def strip_sponsored_inserts(text: str) -> str:
    """Remove mid-article «presented by» / Sponsored Post blocks; keep real body after."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    def _skip_blanks(idx: int) -> int:
        while idx < n and not lines[idx].strip():
            idx += 1
        return idx

    while i < n:
        bare = lines[i].strip()
        if _PRESENTED_BY_LINE_RE.match(bare) or _SPONSORED_POST_LINE_RE.match(bare):
            if _PRESENTED_BY_LINE_RE.match(bare):
                i += 1
                i = _skip_blanks(i)
                if i < n and _SPONSORED_POST_LINE_RE.match(lines[i].strip()):
                    i += 1
            else:
                i += 1
            i = _skip_blanks(i)
            # Sponsored headline
            if i < n and 8 <= len(lines[i].strip()) <= 180:
                i += 1
            i = _skip_blanks(i)
            # Sponsored blurb paragraph
            if i < n and len(lines[i].strip()) >= 40:
                i += 1
            i = _skip_blanks(i)
            # Optional short byline
            if i < n and _BYLINE_SHORT_RE.match(lines[i].strip()):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def strip_topics_trailer(text: str) -> str:
    """Cut trailing Topics / Recommended / newsletter / comments rails through EOF."""
    s = str(text or "")
    # Prefer the broader end-rail matcher; fall back to Topics-only.
    m = _ARTICLE_TRAILER_START_RE.search(s) or _TOPICS_TRAILER_START_RE.search(s)
    if m:
        return s[: m.start()].rstrip()
    return s


def strip_author_bio_trailer(text: str) -> str:
    """Drop trailing author-bio blocks after the article body."""
    s = str(text or "")
    lines = s.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Find last substantial prose line; if a late "By Name" + bio follows, cut.
    cut = None
    for i, line in enumerate(lines):
        bare = line.strip()
        if not bare:
            continue
        if _AUTHOR_BIO_START_RE.match(bare) and i > 0:
            # Only treat as bio when followed by bio-ish prose or near EOF.
            nxt = ""
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip():
                    nxt = lines[j].strip()
                    break
            if nxt and (
                _AUTHOR_BIO_PROSE_RE.match(nxt)
                or len(nxt) > 120
                or i >= max(3, int(len(lines) * 0.55))
            ):
                # Avoid cutting an in-article byline right under the title.
                before = "\n".join(lines[:i]).strip()
                if len(before) >= 200:
                    cut = i
                    break
    if cut is not None:
        return "\n".join(lines[:cut]).rstrip()
    return s


def ensure_title_prefix(text: str, title: str = "") -> str:
    """Guarantee cleaned Studio text starts with the headline when known."""
    s = str(text or "").strip()
    main = _title_main(title)
    if not main:
        return s
    if _text_starts_with_title(s, main):
        return s
    if not s:
        return main
    return f"{main}\n\n{s}".strip()


def finalize_article_plain(text: str, title: str = "") -> str:
    """Post-clean: leading chrome → cut at title → drop leftover trailers."""
    s = strip_leading_chrome_block(text)
    if title:
        s = cut_at_title(s, title)
        if s and not _text_starts_with_title(s, title):
            s = strip_leading_chrome_block(s)
            s = cut_at_title(s, title)
    # Safety: markers may still remain if cleaners missed them.
    s = strip_sponsored_inserts(s)
    s = strip_topics_trailer(s)
    s = strip_author_bio_trailer(s)
    s = ensure_title_prefix(s, title)
    s = _MULTI_NL_RE.sub("\n\n", s).strip()
    return s


def extract_article_text(
    raw: str,
    *,
    title_hint: str = "",
    max_chars: int = 80_000,
) -> dict[str, Any]:
    """
    Return ``{title, text, chars}`` — plain text only.

    Keeps title + full body paragraphs. Skips mid-article images/figures and
    continues with following prose. Strips trailing Topics/tags, sponsored
    inserts, and obvious nav/share chrome — never truncates to title-only.
    """
    title = " ".join(str(title_hint or "").split()).strip()[:400]
    body_src = str(raw or "")
    if not body_src.strip():
        return {"title": title, "text": "", "chars": 0}

    used_html = False
    if _looks_like_html(body_src):
        try:
            t2, body = _soup_extract(body_src)
            used_html = True
            if t2 and not title:
                title = t2[:400]
        except Exception:  # noqa: BLE001
            body = _regex_strip_html(body_src)
    elif "<" in body_src and ">" in body_src:
        body = _regex_strip_html(body_src)
    else:
        body = body_src

    def _pipeline(src: str) -> str:
        s = strip_sponsored_inserts(src)
        s = strip_topics_trailer(s)
        s = strip_author_bio_trailer(s)
        s = _clean_plain(s)
        return finalize_article_plain(s, title)

    text = _pipeline(body)
    # Hard recovery: never ship title-only when source clearly had a body.
    if title and looks_like_title_only(text, title) and len(body_src) > len(title) + 400:
        if used_html:
            text = _pipeline(_regex_strip_html(body_src))
        if looks_like_title_only(text, title):
            # Last resort: light clean of original without cutting to crumbs.
            # Always strip tags first — never feed raw HTML into _clean_plain.
            loose_src = (
                _regex_strip_html(body_src)
                if ("<" in body_src and ">" in body_src)
                else body_src
            )
            loose = _clean_plain(
                strip_topics_trailer(strip_sponsored_inserts(loose_src))
            )
            if title:
                loose = cut_at_title(loose, title)
            loose = strip_topics_trailer(strip_sponsored_inserts(loose))
            loose = ensure_title_prefix(loose, title)
            if len(loose) > len(text):
                text = loose

    text = ensure_title_prefix(text, title)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] or text[:max_chars]
        text = text.strip()
    return {"title": title, "text": text, "chars": len(text)}


def _text_starts_with_title(text: str, title: str) -> bool:
    head = " ".join(str(text or "").split())[:240].casefold()
    main = _title_main(title).casefold()
    if not main or len(main) < 8:
        return False
    # Compare on first ~48 chars / first 5 words to tolerate truncation.
    words = main.split()
    probe = " ".join(words[: min(5, len(words))])
    if len(probe) < 12:
        probe = main[:48]
    return head.startswith(probe) or probe in head[: max(80, len(probe) + 20)]


def clean_article_body(
    raw: str,
    *,
    title_hint: str = "",
    max_chars: int = 80_000,
) -> str:
    """Convenience: cleaned plain body only."""
    return str(
        extract_article_text(raw, title_hint=title_hint, max_chars=max_chars).get("text")
        or ""
    )


def _regex_strip_html(raw: str) -> str:
    # Remove script/style blocks even without a parser.
    text = re.sub(
        r"(?is)<(?:script|style|noscript|svg|iframe|form)[^>]*>.*?</(?:script|style|noscript|svg|iframe|form)>",
        " ",
        raw or "",
    )
    text = re.sub(r"(?is)<(?:img|source|video|audio|track|embed|object|picture)[^>]*/?>", " ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    return text


def _strip_nav_chrome_blob(text: str) -> str:
    """Drop run-on nav blobs like «Skip to content Search SubscribeSign In Energy…»."""
    s = str(text or "")
    if not s:
        return ""
    s = _NAV_CHROME_BLOB_RE.sub(" ", s)
    # Glued tokens: SubscribeSignInEnergyScience → space before capitals after chrome verbs.
    # Keep lookahead case-sensitive so "signed" / "energy" prose is not split.
    s = re.sub(
        r"(?i)\b(subscribe|signin|sign|login|search|energy|science|politics)"
        r"(?-i:(?=[A-Z]))",
        r"\1 ",
        s,
    )
    s = _LEADING_NAV_WORDS_RE.sub("", s)
    return s


def looks_like_fetch_block(text: str = "", *, error: str = "") -> bool:
    """True when fetch looks captcha-/challenge-blocked (escalate to Wigolo/JS)."""
    err = str(error or "").casefold()
    if any(
        x in err
        for x in (
            "block",
            "challenge",
            "captcha",
            "403",
            "451",
            "429",
            "cloudflare",
        )
    ):
        return True
    head = str(text or "")[:1200]
    return bool(head and _FETCH_BLOCK_RE.search(head))


def looks_like_page_chrome(text: str) -> bool:
    """True when cleaned text still smells like site chrome / nav, not article body."""
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return False
    if looks_like_fetch_block(s):
        return True
    head = s[:400].casefold()
    if "skip to content" in head or "skip to main content" in head:
        return True
    if "open navigation" in head or "close navigation" in head:
        return True
    if "share options" in head and "copy link" in head:
        return True
    if "subscribesign" in head.replace(" ", "") or "signinenergy" in head.replace(" ", ""):
        return True
    if "doubleclick.net" in head or "googlesyndication" in head:
        return True
    social_hits = sum(
        1
        for w in ("twitter", "facebook", "youtube", "linkedin", "share options")
        if w in head
    )
    if social_hits >= 3 and len(s) < 1200:
        return True
    nav_hits = sum(
        1
        for w in (
            "subscribe",
            "sign in",
            "log in",
            "newsletter",
            "cookie",
            "advertisement",
        )
        if w in head
    )
    if nav_hits >= 2 and len(s) < 900:
        return True
    return bool(_MENU_WORD_LINE_RE.match(s[:240]) or _SECTION_MENU_LINE_RE.match(s[:240]))


def is_usable_article_body(text: str, *, min_chars: int = 160) -> bool:
    """True when text is long enough and not chrome/captcha interstitial."""
    s = str(text or "").strip()
    if len(s) < int(min_chars):
        return False
    return not looks_like_page_chrome(s)


def _clean_plain(text: str) -> str:
    s = str(text or "")
    s = _DATA_URI_RE.sub(" ", s)
    s = _MD_IMAGE_RE.sub(" ", s)
    # Keep link labels, drop URLs (token waste) — unless label itself is chrome.
    def _md_link_keep(m: re.Match[str]) -> str:
        label = (m.group(1) or "").strip()
        if _is_chrome_line(label) or _SOCIAL_LABEL_LINE_RE.match(label):
            return " "
        return label

    s = _MD_LINK_RE.sub(_md_link_keep, s)
    s = _BARE_IMAGE_URL_RE.sub(" ", s)
    s = _AD_TRACKING_URL_RE.sub(" ", s)
    s = _strip_nav_chrome_blob(s)
    lines: list[str] = []
    for line in s.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        if not line:
            lines.append("")
            continue
        if _is_chrome_line(line):
            continue
        # Skip tiny chrome crumbs.
        if len(line) < 3:
            continue
        lines.append(line)
    # Collapse excess blank lines; keep paragraph breaks.
    out = "\n".join(lines)
    out = _MULTI_NL_RE.sub("\n\n", out).strip()
    return out
