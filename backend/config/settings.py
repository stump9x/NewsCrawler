"""
NewsCrawler Django settings.

All secrets and host-specific values come from environment variables.
See repository root `.env.example`.
"""

from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from repo root (../.env) or backend/.env when running locally
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    MISP_VERIFY_SSL=(bool, True),
)

environ.Env.read_env(BASE_DIR.parent / ".env")
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-only-change-me")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "backend"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    # Local
    "apps.core.apps.CoreConfig",
    "apps.intel.apps.IntelConfig",
    "apps.workers.apps.WorkersConfig",
    "apps.integrations.apps.IntegrationsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="newscrawler"),
        "USER": env("POSTGRES_USER", default="newscrawler"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="change-me-db-password"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

# Optional local/dev fallback when Postgres is unavailable
if env.bool("USE_SQLITE", default=False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- CORS / CSRF ---
CORS_ALLOWED_ORIGINS = env.list(
    "DJANGO_CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000"],
)
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:3000", "http://localhost:8000"],
)

# --- DRF ---
# SPA uses short-lived DRF Token (Authorization: Token <key>).
# BasicAuthentication removed — password never stored in sessionStorage.
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.core.authentication.ExpiringTokenAuthentication",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.FlexiblePagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "300/min",
        "auth": "30/min",
        "github_scan_create": "5/hour",
        "last30days_create": "10/hour",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "NewsCrawler API",
    "DESCRIPTION": "NewsCrawler — Indo-Pacific military and defense OSINT",
    "VERSION": "0.7.0",
}

# --- Auth / secrets ---
AUTH_TOKEN_TTL_HOURS = env.int("AUTH_TOKEN_TTL_HOURS", default=24)
DJANGO_SUPERUSER_USERNAME = env("DJANGO_SUPERUSER_USERNAME", default="admin")
DJANGO_SUPERUSER_EMAIL = env(
    "DJANGO_SUPERUSER_EMAIL", default="admin@newscrawler.local"
)
DJANGO_SUPERUSER_PASSWORD = env("DJANGO_SUPERUSER_PASSWORD", default="")
# When true, ensure_superuser resets the bootstrap account password from env.
# Default false so UI password changes survive backend restarts.
DJANGO_SUPERUSER_FORCE_PASSWORD_SYNC = env.bool(
    "DJANGO_SUPERUSER_FORCE_PASSWORD_SYNC", default=False
)
CREDENTIAL_PEPPER = env("CREDENTIAL_PEPPER", default="")
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")  # Fernet key (url-safe base64)

# --- Celery / Redis ---
REDIS_PASSWORD = env("REDIS_PASSWORD", default="")
# Prefer explicit CELERY_* from env; compose injects password-aware URLs.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
REDIS_URL = env("REDIS_URL", default=CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 15
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 10
# Prefer fair scheduling so long RSS sweeps do not starve translate/Searx tasks.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Drop Celery result keys automatically (Redis DB 1) — safe memory hygiene.
CELERY_RESULT_EXPIRES = env.int("CELERY_RESULT_EXPIRES", default=3600)
# Auto-purge Wire rows past retention windows (safe; never --from-today).
WIRE_HOUSEKEEPING_ENABLED = env.bool("WIRE_HOUSEKEEPING_ENABLED", default=True)
# Recover cover images from article og:image when the RSS feed omits one.
WIRE_OG_IMAGE_ENABLED = env.bool("WIRE_OG_IMAGE_ENABLED", default=True)

CELERY_BEAT_SCHEDULE = {
    "ingest-defense-rss-every-10m": {
        "task": "workers.ingest_cert_rss",
        "schedule": 600.0,
        "kwargs": {"limit_per_feed": 25},
    },
    "translate-wire-titles-every-60s": {
        "task": "integrations.translate_threat_titles",
        # Pace for free-tier Groq but clear Dòng tin backlog (priority-ordered).
        "schedule": 45.0,
        "kwargs": {"limit": 12},
    },
    "ai-daily-briefing": {
        "task": "integrations.generate_daily_briefing",
        "schedule": crontab(hour=6, minute=0),
        "kwargs": {"window_hours": 24},
    },
    "ai-weekly-digest": {
        "task": "integrations.generate_weekly_digest",
        "schedule": crontab(hour=7, minute=0, day_of_week="mon"),
    },
    "wire-housekeeping-daily": {
        "task": "workers.wire_housekeeping",
        "schedule": crontab(hour=3, minute=40),
        "kwargs": {"reset_feed_cache": False},
    },
    "backfill-wire-images-every-5m": {
        "task": "workers.backfill_wire_images",
        "schedule": 300.0,
        "kwargs": {"limit": 60},
    },
    # Document scan / PDF dork + document title translate: only when enabled
    # (off by default — Playwright/Searx burn RAM and AI quota).
}

# Optional document-scan beat jobs (gated — see DOCUMENT_SCAN_ENABLED below).
if env.bool("DOCUMENT_SCAN_ENABLED", default=False):
    CELERY_BEAT_SCHEDULE["translate-document-titles-every-90s"] = {
        "task": "integrations.translate_document_titles",
        "schedule": 120.0,
        "kwargs": {"limit": 3},
    }
    CELERY_BEAT_SCHEDULE["scan-document-pdfs-every-30m"] = {
        "task": "integrations.kick_document_scan",
        "schedule": 1800.0,
        "kwargs": {"limit_per_keyword": 10, "force": False},
    }


# --- External integrations (Phase 6) ---
OSINT_SERVICE_URL = env("OSINT_SERVICE_URL", default="http://localhost:8080")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", default="claude-3-haiku-20240307")
GROQ_POOL_NAMESPACE = env("GROQ_POOL_NAMESPACE", default="newscrawler")
GROQ_API_KEY = env("GROQ_API_KEY", default="")
# Extra keys (comma / newline separated) for rate-limit rotation with GROQ_API_KEY.
GROQ_API_KEYS = env("GROQ_API_KEYS", default="")
# Per-task pools (disjoint). Empty → fall back to GROQ_API_KEY + GROQ_API_KEYS.
GROQ_API_KEYS_TRANSLATE = env("GROQ_API_KEYS_TRANSLATE", default="")
GROQ_API_KEYS_BRIEFING = env("GROQ_API_KEYS_BRIEFING", default="")
NOTEBOOK_GROQ_API_KEY = env("NOTEBOOK_GROQ_API_KEY", default="")
NOTEBOOK_GROQ_API_KEYS = env("NOTEBOOK_GROQ_API_KEYS", default="")
# Dedicated paid Notebook key must never fall back to title/briefing pools.
NOTEBOOK_GROQ_STRICT_POOL = env.bool("NOTEBOOK_GROQ_STRICT_POOL", default=True)
# GPT-OSS 120B is stronger for Vietnamese military titles; GPT-OSS 20B is the fast fallback.
GROQ_MODEL = env("GROQ_MODEL", default="openai/gpt-oss-120b")
# Separate free-tier quota — used when primary model hits TPD/429.
GROQ_BRIEFING_FALLBACK_MODEL = env(
    "GROQ_BRIEFING_FALLBACK_MODEL", default="openai/gpt-oss-20b"
)
# Keep title calls snappy; shared Redis RPM + min-interval prevent 429 storms.
GROQ_TIMEOUT_SEC = env.float("GROQ_TIMEOUT_SEC", default=12)
GROQ_KEY_COOLDOWN_SEC = env.float("GROQ_KEY_COOLDOWN_SEC", default=480)
# One key per title — rotating on 429 burns the free-tier pool.
GROQ_MAX_KEY_ATTEMPTS = env.int("GROQ_MAX_KEY_ATTEMPTS", default=1)
GROQ_MIN_INTERVAL_SEC = env.float("GROQ_MIN_INTERVAL_SEC", default=3.5)
GROQ_MAX_REQUESTS_PER_MIN = env.int("GROQ_MAX_REQUESTS_PER_MIN", default=12)
GROQ_BATCH_PAUSE_SEC = env.float("GROQ_BATCH_PAUSE_SEC", default=3.5)
# Use Groq for Wire/document title translation (multi-key pool).
TITLE_TRANSLATE_GROQ = env.bool("TITLE_TRANSLATE_GROQ", default=True)
# Prefer Groq over Google/Ollama; only leave Groq after per-item stuck window.
TITLE_TRANSLATE_PREFER_GROQ = env.bool("TITLE_TRANSLATE_PREFER_GROQ", default=True)
# Pending longer than this → Google → Ollama for *that item only* (default 15 minutes).
TITLE_TRANSLATE_STUCK_SEC = env.int("TITLE_TRANSLATE_STUCK_SEC", default=300)
# Hard cap on unfinished NEWS title translations at once. Excess rows are deleted
# (lowest wire priority / oldest first) so Dòng tin never piles up awaiting_groq.
# Used only by cleanup_untranslated_wire --trim (not auto-delete on translate beat).
TITLE_TRANSLATE_MAX_PENDING = env.int("TITLE_TRANSLATE_MAX_PENDING", default=120)
GROQ_CIRCUIT_TTL_SEC = env.int("GROQ_CIRCUIT_TTL_SEC", default=900)
# Global Groq circuit disabled in code; kept for env compatibility.
GROQ_FAIL_TRIP_THRESHOLD = env.int("GROQ_FAIL_TRIP_THRESHOLD", default=0)
# --- OpenRouter (briefing + Notebook only; NOT used for Dòng tin title translate) ---
OPENROUTER_ENABLED = env.bool("OPENROUTER_ENABLED", default=False)
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_API_KEYS = env("OPENROUTER_API_KEYS", default="")
OPENROUTER_POOL_NAMESPACE = env("OPENROUTER_POOL_NAMESPACE", default="newscrawler")
OPENROUTER_MODEL = env("OPENROUTER_MODEL", default="openrouter/free")
# Optional comma list; empty → curated + live free catalog refresh.
OPENROUTER_FALLBACK_MODELS = env("OPENROUTER_FALLBACK_MODELS", default="")
OPENROUTER_REFRESH_FREE_MODELS = env.bool("OPENROUTER_REFRESH_FREE_MODELS", default=True)
OPENROUTER_TIMEOUT_SEC = env.float("OPENROUTER_TIMEOUT_SEC", default=45)
OPENROUTER_KEY_COOLDOWN_SEC = env.float("OPENROUTER_KEY_COOLDOWN_SEC", default=120)
OPENROUTER_MAX_KEY_ATTEMPTS = env.int("OPENROUTER_MAX_KEY_ATTEMPTS", default=2)
OPENROUTER_MIN_INTERVAL_SEC = env.float("OPENROUTER_MIN_INTERVAL_SEC", default=1.0)
OPENROUTER_MAX_REQUESTS_PER_MIN = env.int("OPENROUTER_MAX_REQUESTS_PER_MIN", default=30)
OPENROUTER_HTTP_REFERER = env(
    "OPENROUTER_HTTP_REFERER", default="https://newscrawler.local"
)
OPENROUTER_X_TITLE = env("OPENROUTER_X_TITLE", default="NewsCrawler")
# --- ShopAIKey (paid gateway, Notebook interactive paths only) ---
NOTEBOOK_SHOPAIKEY_ENABLED = env.bool(
    "NOTEBOOK_SHOPAIKEY_ENABLED", default=True
)
NOTEBOOK_SHOPAIKEY_API_KEY = env("NOTEBOOK_SHOPAIKEY_API_KEY", default="")
NOTEBOOK_SHOPAIKEY_BASE_URL = env(
    "NOTEBOOK_SHOPAIKEY_BASE_URL",
    default="https://api.shopaikey.com/v1",
)
NOTEBOOK_SHOPAIKEY_MODEL_FAST = env(
    "NOTEBOOK_SHOPAIKEY_MODEL_FAST", default="qwen3-235b-a22b"
)
NOTEBOOK_SHOPAIKEY_MODEL_FAST_FALLBACK = env(
    "NOTEBOOK_SHOPAIKEY_MODEL_FAST_FALLBACK", default="qwen3-next-80b-a3b-instruct"
)
NOTEBOOK_SHOPAIKEY_MODEL_DEEP = env(
    "NOTEBOOK_SHOPAIKEY_MODEL_DEEP",
    default="qwen3-next-80b-a3b-instruct",
)
NOTEBOOK_SHOPAIKEY_MODEL_FALLBACK = env(
    "NOTEBOOK_SHOPAIKEY_MODEL_FALLBACK", default="gpt-5-mini"
)
NOTEBOOK_SHOPAIKEY_TIMEOUT_SECONDS = env.float(
    "NOTEBOOK_SHOPAIKEY_TIMEOUT_SECONDS", default=9.0
)
# Paid primary route for Quick Briefing. It reuses the configured gateway key,
# while keeping its own model and timeout controls.
BRIEFING_SHOPAIKEY_ENABLED = env.bool("BRIEFING_SHOPAIKEY_ENABLED", default=True)
BRIEFING_SHOPAIKEY_MODEL = env(
    "BRIEFING_SHOPAIKEY_MODEL", default="qwen3-235b-a22b"
)
BRIEFING_SHOPAIKEY_TIMEOUT_SECONDS = env.float(
    "BRIEFING_SHOPAIKEY_TIMEOUT_SECONDS", default=30.0
)
# --- Cerebras Cloud (Notebook primary + optional briefing mid-tier; NOT Dòng tin) ---
CEREBRAS_ENABLED = env.bool("CEREBRAS_ENABLED", default=False)
CEREBRAS_API_KEY = env("CEREBRAS_API_KEY", default="")
CEREBRAS_API_KEYS = env("CEREBRAS_API_KEYS", default="")
CEREBRAS_POOL_NAMESPACE = env("CEREBRAS_POOL_NAMESPACE", default="newscrawler")
CEREBRAS_BASE_URL = env("CEREBRAS_BASE_URL", default="https://api.cerebras.ai/v1")
# Llama retired on public endpoints (2026); gpt-oss-120b = production 131k ctx.
CEREBRAS_MODEL = env("CEREBRAS_MODEL", default="gpt-oss-120b")
CEREBRAS_FALLBACK_MODELS = env(
    "CEREBRAS_FALLBACK_MODELS", default="gemma-4-31b,zai-glm-4.7"
)
CEREBRAS_TIMEOUT_SEC = env.float("CEREBRAS_TIMEOUT_SEC", default=45)
CEREBRAS_KEY_COOLDOWN_SEC = env.float("CEREBRAS_KEY_COOLDOWN_SEC", default=120)
CEREBRAS_MAX_KEY_ATTEMPTS = env.int("CEREBRAS_MAX_KEY_ATTEMPTS", default=2)
CEREBRAS_MIN_INTERVAL_SEC = env.float("CEREBRAS_MIN_INTERVAL_SEC", default=0.5)
CEREBRAS_MAX_REQUESTS_PER_MIN = env.int("CEREBRAS_MAX_REQUESTS_PER_MIN", default=60)
# Briefing: insert Cerebras between Groq and OpenRouter when enabled.
CEREBRAS_BRIEFING_FALLBACK = env.bool("CEREBRAS_BRIEFING_FALLBACK", default=True)
HUGGINGFACE_API_TOKEN = env("HUGGINGFACE_API_TOKEN", default="")
HUGGINGFACE_NER_MODEL = env("HUGGINGFACE_NER_MODEL", default="dslim/bert-base-NER")
HUGGINGFACE_SUMMARIZE_MODEL = env(
    "HUGGINGFACE_SUMMARIZE_MODEL", default="google/flan-t5-base"
)
# Wire title VN translation: Google Translate first, optional Ollama polish.
TITLE_TRANSLATE_ENABLED = env.bool("TITLE_TRANSLATE_ENABLED", default=True)
TITLE_TRANSLATE_AI_REFINE = env.bool("TITLE_TRANSLATE_AI_REFINE", default=False)
# When AI refine is on, only polish titles at/above this Wire priority (50=impact, 100=VN).
TITLE_TRANSLATE_AI_MIN_PRIORITY = env.int("TITLE_TRANSLATE_AI_MIN_PRIORITY", default=50)
# Translate during ingest: cache/rule/VI only by default. Groq is paced by Celery
# so RSS bursts do not stampede free-tier keys. Google only after stuck window.
TITLE_TRANSLATE_INLINE = env.bool("TITLE_TRANSLATE_INLINE", default=True)
TITLE_TRANSLATE_INLINE_GROQ = env.bool("TITLE_TRANSLATE_INLINE_GROQ", default=False)
# Allow inline Google only for stuck-item failover (not every ingest miss).
TITLE_TRANSLATE_INLINE_GOOGLE = env.bool("TITLE_TRANSLATE_INLINE_GOOGLE", default=True)
GOOGLE_TRANSLATE_TIMEOUT_SEC = env.float("GOOGLE_TRANSLATE_TIMEOUT_SEC", default=20)
GOOGLE_TRANSLATE_SOURCE_LANGUAGE = env(
    "GOOGLE_TRANSLATE_SOURCE_LANGUAGE", default="auto"
)
# Unofficial Google endpoint — never retry 429 in-loop.
GOOGLE_TRANSLATE_MAX_RETRIES = env.int("GOOGLE_TRANSLATE_MAX_RETRIES", default=0)
GOOGLE_TRANSLATE_RETRY_BACKOFF_SEC = env.float(
    "GOOGLE_TRANSLATE_RETRY_BACKOFF_SEC", default=1.0
)
GOOGLE_TRANSLATE_BATCH_PAUSE_SEC = env.float(
    "GOOGLE_TRANSLATE_BATCH_PAUSE_SEC", default=4.0
)
TITLE_TRANSLATE_BATCH_PAUSE_SEC = env.float(
    "TITLE_TRANSLATE_BATCH_PAUSE_SEC", default=3.0
)
GOOGLE_TRANSLATE_MIN_INTERVAL_SEC = env.float(
    "GOOGLE_TRANSLATE_MIN_INTERVAL_SEC", default=4.0
)
GOOGLE_TRANSLATE_MAX_REQUESTS_PER_MIN = env.int(
    "GOOGLE_TRANSLATE_MAX_REQUESTS_PER_MIN", default=5
)
# After a Google block/sorry page, skip Google for this many seconds and use Ollama.
GOOGLE_TRANSLATE_CIRCUIT_TTL_SEC = env.int(
    "GOOGLE_TRANSLATE_CIRCUIT_TTL_SEC", default=1800
)
TITLE_TRANSLATE_OLLAMA_FALLBACK = env.bool(
    "TITLE_TRANSLATE_OLLAMA_FALLBACK", default=True
)
# Prefer local Qwen for Chinese/Japanese titles (better CN→VI than free Google).
TITLE_TRANSLATE_CJK_PREFER_OLLAMA = env.bool(
    "TITLE_TRANSLATE_CJK_PREFER_OLLAMA", default=True
)
OLLAMA_ENABLED = env.bool("OLLAMA_ENABLED", default=False)
# Prefer the compose service `ollama`; host.docker.internal is a desktop fallback.
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://ollama:11434")
OLLAMA_TRANSLATE_MODEL = env("OLLAMA_TRANSLATE_MODEL", default="qwen2.5:3b")
OLLAMA_BRIEFING_MODEL = env("OLLAMA_BRIEFING_MODEL", default="")
OLLAMA_BRIEFING_TIMEOUT_SEC = env.float("OLLAMA_BRIEFING_TIMEOUT_SEC", default=180)
OLLAMA_TIMEOUT_SEC = env.float("OLLAMA_TIMEOUT_SEC", default=120)
OLLAMA_NUM_PREDICT = env.int("OLLAMA_NUM_PREDICT", default=128)
# Keep context small for title-only translation so 3B fits in ~4GB RAM hosts.
OLLAMA_NUM_CTX = env.int("OLLAMA_NUM_CTX", default=2048)
OLLAMA_KEEP_ALIVE = env("OLLAMA_KEEP_ALIVE", default="15m")
# Optional refine prompt; must include {title} and {draft}.
TITLE_TRANSLATE_REFINE_PROMPT = env("TITLE_TRANSLATE_REFINE_PROMPT", default="")
TITLE_TRANSLATE_FALLBACK_PROMPT = env(
    "TITLE_TRANSLATE_FALLBACK_PROMPT", default=""
)
TITLE_TRANSLATE_PROMPT = env("TITLE_TRANSLATE_PROMPT", default="")
HUDSON_ROCK_API_KEY = env("HUDSON_ROCK_API_KEY", default="")
# Delete after N consecutive failures; only permanent 404/410/unsafe URLs delete immediately.
FEED_DELETE_AFTER_FAILURES = env.int("FEED_DELETE_AFTER_FAILURES", default=3)
FEED_MAX_REDIRECTS = env.int("FEED_MAX_REDIRECTS", default=5)
# Keep up to 5,000 Wire stories from the latest 30 days (newest by published_at).
WIRE_MAX_AGE_DAYS = env.int("WIRE_MAX_AGE_DAYS", default=30)
WIRE_VIETNAM_MAX_AGE_DAYS = env.int("WIRE_VIETNAM_MAX_AGE_DAYS", default=30)
WIRE_SECRSS_MAX_AGE_DAYS = env.int("WIRE_SECRSS_MAX_AGE_DAYS", default=90)
WIRE_VIETNAM_PRIORITY = env.int("WIRE_VIETNAM_PRIORITY", default=100)
WIRE_STRATEGIC_PRIORITY = env.int("WIRE_STRATEGIC_PRIORITY", default=50)
WIRE_SECRSS_PRIORITY = env.int("WIRE_SECRSS_PRIORITY", default=45)
# Re-scan WordPress sitemap deltas hourly to recover regional posts omitted by
# short rolling RSS feeds. RSS cache is invalidated when parser policy changes.
WIRE_WORDPRESS_BACKFILL_INTERVAL_MINUTES = env.int(
    "WIRE_WORDPRESS_BACKFILL_INTERVAL_MINUTES", default=360
)
WIRE_WORDPRESS_SOURCES_PER_SWEEP = env.int(
    "WIRE_WORDPRESS_SOURCES_PER_SWEEP", default=3
)
RSS_PROCESSING_VERSION = env.int("RSS_PROCESSING_VERSION", default=14)
WIRE_MAX_ITEMS = env.int("WIRE_MAX_ITEMS", default=5000)
TOR_ENABLED = env.bool("TOR_ENABLED", default=False)
TOR_SOCKS_PROXY = env("TOR_SOCKS_PROXY", default="socks5h://tor:9150")
PROXYNOVA_API_KEY = env("PROXYNOVA_API_KEY", default="")
BREACHDIRECTORY_API_KEY = env("BREACHDIRECTORY_API_KEY", default="")
MISP_URL = env("MISP_URL", default="")
MISP_API_KEY = env("MISP_API_KEY", default="")
MISP_VERIFY_SSL = env("MISP_VERIFY_SSL")
THEHIVE_URL = env("THEHIVE_URL", default="")
THEHIVE_API_KEY = env("THEHIVE_API_KEY", default="")
SEARXNG_URL = env("SEARXNG_URL", default="")
SEARXNG_ENGINES = env(
    "SEARXNG_ENGINES",
    default="duckduckgo,brave,bing,gitlab,bitbucket,npm,stackoverflow,qwant,ahmia",
)
# Wigolo sidecar — selective fallback for briefing/OSINT fetch (Jina → Wigolo → Searx).
# Keep WIGOLO_EAGER_WARMUP=0 — idle Chromium burns RAM. Dòng tin titles do NOT use Wigolo.
WIGOLO_ENABLED = env.bool("WIGOLO_ENABLED", default=True)
WIGOLO_URL = env("WIGOLO_URL", default="http://wigolo:3333")
WIGOLO_API_TOKEN = env("WIGOLO_API_TOKEN", default="")
WIGOLO_TIMEOUT_SEC = env.float("WIGOLO_TIMEOUT_SEC", default=45.0)
WIGOLO_QUERY_COUNT = env.int("WIGOLO_QUERY_COUNT", default=2)
WIGOLO_CATEGORY = env("WIGOLO_CATEGORY", default="news")
WIGOLO_TIME_RANGE = env("WIGOLO_TIME_RANGE", default="month")
WIGOLO_SEARCH_DEPTH = env("WIGOLO_SEARCH_DEPTH", default="deep")
WIGOLO_OSINT_MODE = env("WIGOLO_OSINT_MODE", default="fallback")  # fallback|always|off
WIGOLO_OSINT_MIN_HITS = env.int("WIGOLO_OSINT_MIN_HITS", default=5)
WIGOLO_LEAK_MODE = env("WIGOLO_LEAK_MODE", default="fallback")
WIGOLO_LEAK_MIN_HITS = env.int("WIGOLO_LEAK_MIN_HITS", default=5)
WIGOLO_DOCUMENT_MODE = env("WIGOLO_DOCUMENT_MODE", default="fallback")
WIGOLO_DOCUMENT_MIN_HITS = env.int("WIGOLO_DOCUMENT_MIN_HITS", default=3)
WIGOLO_DOCUMENT_TIME_RANGE = env("WIGOLO_DOCUMENT_TIME_RANGE", default="year")
WIGOLO_DOCUMENT_INCLUDE_DOMAINS = env("WIGOLO_DOCUMENT_INCLUDE_DOMAINS", default="")
# Fetch as fallback when Jina/RSS snippets are weak (not for every wire title).
WIGOLO_FETCH_ENABLED = env.bool("WIGOLO_FETCH_ENABLED", default=True)
WIGOLO_FETCH_MAX_CHARS = env.int("WIGOLO_FETCH_MAX_CHARS", default=12000)
WIGOLO_FETCH_PREFER = env.bool("WIGOLO_FETCH_PREFER", default=False)
WIGOLO_LAST30DAYS_WEB = env.bool("WIGOLO_LAST30DAYS_WEB", default=True)
WIGOLO_DEEP_BRIEF_ENABLED = env.bool("WIGOLO_DEEP_BRIEF_ENABLED", default=True)
# Briefings: search mode uses Wigolo when configured; titles stay Groq-first.
WIGOLO_BRIEFING_ENABLED = env.bool("WIGOLO_BRIEFING_ENABLED", default=True)
WIGOLO_BRIEFING_MODE = env("WIGOLO_BRIEFING_MODE", default="search")  # search|research|off
WIGOLO_BRIEFING_MAX_HITS = env.int("WIGOLO_BRIEFING_MAX_HITS", default=8)
WIGOLO_BRIEFING_SEARCH_DEPTH = env("WIGOLO_BRIEFING_SEARCH_DEPTH", default="deep")
WIGOLO_BRIEFING_FALLBACK_DEPTH = env("WIGOLO_BRIEFING_FALLBACK_DEPTH", default="standard")
WIGOLO_BRIEFING_RESEARCH_DEPTH = env("WIGOLO_BRIEFING_RESEARCH_DEPTH", default="standard")
WIGOLO_BRIEFING_RESEARCH_MAX_SOURCES = env.int(
    "WIGOLO_BRIEFING_RESEARCH_MAX_SOURCES", default=80
)
WIGOLO_BRIEFING_FALLBACK_RESEARCH = env.bool(
    "WIGOLO_BRIEFING_FALLBACK_RESEARCH", default=True
)
AI_BRIEFING_STUCK_MINUTES = env.int("AI_BRIEFING_STUCK_MINUTES", default=18)
# How many Wire/web bodies to fetch per briefing (no tiny 28 hard-cap).
AI_BRIEFING_FETCH_MAX = env.int("AI_BRIEFING_FETCH_MAX", default=12)
# Per-article crawl budget — longer excerpts feed 3–5 sentence digests.
AI_BRIEFING_FETCH_CHARS = env.int("AI_BRIEFING_FETCH_CHARS", default=14000)
AI_BRIEFING_DOSSIER_BODY_CHARS = env.int("AI_BRIEFING_DOSSIER_BODY_CHARS", default=4500)
AI_BRIEFING_DIGEST_SENTENCES = env.int("AI_BRIEFING_DIGEST_SENTENCES", default=5)
AI_BRIEFING_POLISH_MAX_TOKENS = env.int("AI_BRIEFING_POLISH_MAX_TOKENS", default=3200)
AI_BRIEFING_REVIEW_MAX_TOKENS = env.int("AI_BRIEFING_REVIEW_MAX_TOKENS", default=2800)
AI_BRIEFING_REVIEW_FAST_MAX_TOKENS = env.int(
    "AI_BRIEFING_REVIEW_FAST_MAX_TOKENS", default=2000
)
# Detailed reports: prefer quality models; set true to try 8b first under load.
AI_BRIEFING_REVIEW_PREFER_FAST = env.bool("AI_BRIEFING_REVIEW_PREFER_FAST", default=False)
AI_BRIEFING_MIN_RAW_DRAFT_CHARS = env.int("AI_BRIEFING_MIN_RAW_DRAFT_CHARS", default=500)
AI_BRIEFING_POLISH_DOSSIER_CHARS = env.int("AI_BRIEFING_POLISH_DOSSIER_CHARS", default=36000)
AI_BRIEFING_SEARCH_LIMIT = env.int("AI_BRIEFING_SEARCH_LIMIT", default=60)
MINDMAP_SHOPAIKEY_MODEL = env("MINDMAP_SHOPAIKEY_MODEL", default="qwen-flash")
MINDMAP_AI_TIMEOUT_SECONDS = env.float("MINDMAP_AI_TIMEOUT_SECONDS", default=12.0)
AI_BRIEFING_MODEL_WIRE_LIMIT = env.int("AI_BRIEFING_MODEL_WIRE_LIMIT", default=0)
AI_BRIEFING_REPORT_SOURCE_LIMIT = env.int("AI_BRIEFING_REPORT_SOURCE_LIMIT", default=0)
AI_BRIEFING_SHOPAIKEY_PROMPT_CHARS = env.int(
    "AI_BRIEFING_SHOPAIKEY_PROMPT_CHARS", default=22000
)
# Preferred wire-item selection cap for AI briefings (was hard-capped ~28).
AI_BRIEFING_MAX_WIRE_ITEMS = env.int("AI_BRIEFING_MAX_WIRE_ITEMS", default=200)
# Legacy alias — prefer AI_BRIEFING_MAX_WIRE_ITEMS when both are set.
AI_BRIEFING_WIRE_LIMIT = env.int(
    "AI_BRIEFING_WIRE_LIMIT", default=AI_BRIEFING_MAX_WIRE_ITEMS
)
# Keep under Groq free-tier request size (HTTP 413 if too large).
AI_BRIEFING_GROQ_PROMPT_CHARS = env.int("AI_BRIEFING_GROQ_PROMPT_CHARS", default=10000)
# Cerebras / OpenRouter can take larger review bodies than Groq.
AI_BRIEFING_CEREBRAS_PROMPT_CHARS = env.int(
    "AI_BRIEFING_CEREBRAS_PROMPT_CHARS", default=36000
)
AI_BRIEFING_OPENROUTER_PROMPT_CHARS = env.int(
    "AI_BRIEFING_OPENROUTER_PROMPT_CHARS", default=28000
)
# When OpenRouter/Cerebras/Ollama/Wigolo return thin/weak/oversized prose, Groq rewrite.
AI_BRIEFING_GROQ_QUALITY_ASSIST = env.bool(
    "AI_BRIEFING_GROQ_QUALITY_ASSIST", default=True
)
AI_BRIEFING_GROQ_ASSIST_MIN_CHARS = env.int(
    "AI_BRIEFING_GROQ_ASSIST_MIN_CHARS", default=900
)
# Mandatory Groq final pass: VN-only polish and/or condense to 2–3 A4 pages.
AI_BRIEFING_GROQ_LANGUAGE_POLISH = env.bool(
    "AI_BRIEFING_GROQ_LANGUAGE_POLISH", default=True
)
AI_BRIEFING_GROQ_FINAL_RETRIES = env.int("AI_BRIEFING_GROQ_FINAL_RETRIES", default=2)
# Body prose band (excl. NGUỒN links) ≈ 2–3 A4 pages Vietnamese.
AI_BRIEFING_BODY_MIN_CHARS = env.int("AI_BRIEFING_BODY_MIN_CHARS", default=4500)
AI_BRIEFING_BODY_TARGET_CHARS = env.int("AI_BRIEFING_BODY_TARGET_CHARS", default=6000)
AI_BRIEFING_BODY_MAX_CHARS = env.int("AI_BRIEFING_BODY_MAX_CHARS", default=8000)
# Legacy alias → body min (older code / env used "quality floor" for long essays).
AI_BRIEFING_QUALITY_FLOOR_CHARS = env.int(
    "AI_BRIEFING_QUALITY_FLOOR_CHARS", default=AI_BRIEFING_BODY_MIN_CHARS
)
# Final review: never accept the "LLM tạm không khả dụng" stub; retry Groq/Ollama.
AI_BRIEFING_LLM_RETRY_ROUNDS = env.int("AI_BRIEFING_LLM_RETRY_ROUNDS", default=3)
GROQ_BRIEFING_KEY_WAIT_SEC = env.float("GROQ_BRIEFING_KEY_WAIT_SEC", default=90)
GROQ_BRIEFING_TIMEOUT_SEC = env.float("GROQ_BRIEFING_TIMEOUT_SEC", default=120)
# Optional tiny Groq call to expand search queries before Wigolo gather.
AI_BRIEFING_GROQ_PLAN = env.bool("AI_BRIEFING_GROQ_PLAN", default=False)
# Groq semantic pre-check for long keyword briefing prompts (briefing key pool).
AI_BRIEFING_KEYWORD_INTENT = env.bool("AI_BRIEFING_KEYWORD_INTENT", default=True)
# Push briefing sources into Open Notebook (Notebook AI sidecar).
NOTEBOOK_INTERNAL_URL = env("NOTEBOOK_INTERNAL_URL", default="http://notebook-gateway:80")
NOTEBOOK_PUBLIC_URL = env(
    "NOTEBOOK_PUBLIC_URL", default="http://127.0.0.1:3000"
)
NOTEBOOK_EXPORT_TIMEOUT_SEC = env.float("NOTEBOOK_EXPORT_TIMEOUT_SEC", default=90)
# Notebook SPA model router: probe cerebras-proxy + pools (no heavy completions).
NOTEBOOK_CEREBRAS_PROXY_HEALTH_URL = env(
    "NOTEBOOK_CEREBRAS_PROXY_HEALTH_URL",
    default="http://cerebras-proxy:8088/health",
)
NOTEBOOK_MODEL_HEALTH_CACHE_TTL = env.int("NOTEBOOK_MODEL_HEALTH_CACHE_TTL", default=45)
NOTEBOOK_MODEL_UNHEALTHY_TTL = env.int("NOTEBOOK_MODEL_UNHEALTHY_TTL", default=60)
# Notebook AI crawl-body cache (Redis DB 2) — avoid re-crawl per question.
NOTEBOOK_CRAWL_CACHE_ENABLED = env.bool("NOTEBOOK_CRAWL_CACHE_ENABLED", default=True)
NOTEBOOK_CRAWL_CACHE_TTL_SEC = env.int("NOTEBOOK_CRAWL_CACHE_TTL_SEC", default=10800)  # 3h
NOTEBOOK_CRAWL_CACHE_MAX_CHARS = env.int("NOTEBOOK_CRAWL_CACHE_MAX_CHARS", default=80000)
NOTEBOOK_CRAWL_CACHE_MAX_KEYS = env.int("NOTEBOOK_CRAWL_CACHE_MAX_KEYS", default=64)
# Optional override; default = REDIS_URL with DB /2 (broker=0, results=1).
NOTEBOOK_CRAWL_CACHE_REDIS_URL = env("NOTEBOOK_CRAWL_CACHE_REDIS_URL", default="")
NOTEBOOK_DIGEST_TIMEOUT_SEC = env.float("NOTEBOOK_DIGEST_TIMEOUT_SEC", default=12)
WIGOLO_BRIEFING_MIN_DB_HITS = env.int("WIGOLO_BRIEFING_MIN_DB_HITS", default=3)
# Open-web leak enrichment (Jina Reader) — does not affect Wire / GitHub Scanner.
WEB_READER_ENABLED = env.bool("WEB_READER_ENABLED", default=True)
WEB_READER_BACKEND = env("WEB_READER_BACKEND", default="jina")
WEB_READER_MAX_BYTES = env.int("WEB_READER_MAX_BYTES", default=200000)
WEB_READER_TIMEOUT = env.float("WEB_READER_TIMEOUT", default=20.0)
SEARX_LEAK_ENRICH = env.bool("SEARX_LEAK_ENRICH", default=True)
SEARX_LEAK_ENRICH_SYNC = env.bool("SEARX_LEAK_ENRICH_SYNC", default=False)
SEARX_LEAK_ENRICH_BUDGET = env.int("SEARX_LEAK_ENRICH_BUDGET", default=16)
SEARX_QUERY_PACKS = env.bool("SEARX_QUERY_PACKS", default=True)
SEARX_QUERY_PACK_SIZE = env.int("SEARX_QUERY_PACK_SIZE", default=4)
# Prefer fresher open-web hits (Searx time_range: day|week|month|year|"").
SEARX_TIME_RANGE = env("SEARX_TIME_RANGE", default="month")
# Automatic military/defence PDF discovery (Google-biased Searx dorks).
# Off by default — PDF dork/scan + document title AI burn RAM/CPU and Groq quota.
DOCUMENT_SCAN_ENABLED = env.bool("DOCUMENT_SCAN_ENABLED", default=False)
DOCUMENT_SCAN_MAX_AGE_DAYS = env.int("DOCUMENT_SCAN_MAX_AGE_DAYS", default=30)
DOCUMENT_SCAN_MIN_IMPORTANCE = env.int("DOCUMENT_SCAN_MIN_IMPORTANCE", default=40)
DOCUMENT_SCAN_LIMIT_PER_KEYWORD = env.int("DOCUMENT_SCAN_LIMIT_PER_KEYWORD", default=12)
# Align with Google advanced search "Past month" (tbs=qdr:m / Searx time_range=month).
DOCUMENT_SCAN_TIME_RANGE = env("DOCUMENT_SCAN_TIME_RANGE", default="month")
# Google first when healthy (month filter). Bing/Yandex lack time filters.
DOCUMENT_SCAN_ENGINES = env(
    "DOCUMENT_SCAN_ENGINES",
    default="google,bing,yandex",
)
# Keep low — concurrent Searx/Google queries trigger blocks quickly.
DOCUMENT_SCAN_PARALLELISM = env.int("DOCUMENT_SCAN_PARALLELISM", default=1)
# Align with 30m beat so most keywords stay eligible each cycle.
DOCUMENT_SCAN_KEYWORD_COOLDOWN_MINUTES = env.int(
    "DOCUMENT_SCAN_KEYWORD_COOLDOWN_MINUTES", default=25
)
DOCUMENT_SCAN_SECONDARY_RANGE = env.bool("DOCUMENT_SCAN_SECONDARY_RANGE", default=True)
# Minimum seconds between Searx calls inside one sweep (shared gate).
DOCUMENT_SCAN_QUERY_DELAY_SEC = env.float("DOCUMENT_SCAN_QUERY_DELAY_SEC", default=2.0)
DOCUMENT_SCAN_LOCK_TTL_SEC = env.int("DOCUMENT_SCAN_LOCK_TTL_SEC", default=1800)
# Matches Celery beat interval for UI countdown ("chờ lượt quét mới").
DOCUMENT_SCAN_INTERVAL_SEC = env.int("DOCUMENT_SCAN_INTERVAL_SEC", default=1800)
# Cap keywords / early-stop to avoid Celery congestion on empty tails.
DOCUMENT_SCAN_MAX_KEYWORDS_PER_RUN = env.int(
    "DOCUMENT_SCAN_MAX_KEYWORDS_PER_RUN", default=22
)
DOCUMENT_SCAN_TARGET_CREATED = env.int("DOCUMENT_SCAN_TARGET_CREATED", default=10)
# Keep the same 1-month gate even when Google month-filter is unavailable.
DOCUMENT_SCAN_FALLBACK_MAX_AGE_DAYS = env.int(
    "DOCUMENT_SCAN_FALLBACK_MAX_AGE_DAYS", default=31
)
# Chromium Google dorking (SERP links only — never downloads PDF bodies).
# Prefer this over Searx google to reduce CSE captcha/suspend noise.
DOCUMENT_SCAN_GOOGLE_BROWSER = env.bool("DOCUMENT_SCAN_GOOGLE_BROWSER", default=True)
DOCUMENT_SCAN_GOOGLE_BROWSER_DELAY_SEC = env.float(
    "DOCUMENT_SCAN_GOOGLE_BROWSER_DELAY_SEC", default=12.0
)
DOCUMENT_SCAN_GOOGLE_BROWSER_TIMEOUT_MS = env.int(
    "DOCUMENT_SCAN_GOOGLE_BROWSER_TIMEOUT_MS", default=25000
)
DOCUMENT_SCAN_GOOGLE_BROWSER_CAPTCHA_TTL_SEC = env.int(
    "DOCUMENT_SCAN_GOOGLE_BROWSER_CAPTCHA_TTL_SEC", default=2700
)
DOCUMENT_SCAN_GOOGLE_BROWSER_PROFILE = env(
    "DOCUMENT_SCAN_GOOGLE_BROWSER_PROFILE",
    default="/data/google-browser-profile",
)
DOCUMENT_SCAN_GOOGLE_BROWSER_HEADLESS = env.bool(
    "DOCUMENT_SCAN_GOOGLE_BROWSER_HEADLESS", default=True
)
DOCUMENT_SCAN_GOOGLE_BROWSER_EXECUTABLE = env(
    "DOCUMENT_SCAN_GOOGLE_BROWSER_EXECUTABLE", default=""
)
# Fewer keywords per sweep when Chromium pacing (~12s/query) is active.
DOCUMENT_SCAN_GOOGLE_BROWSER_MAX_KEYWORDS = env.int(
    "DOCUMENT_SCAN_GOOGLE_BROWSER_MAX_KEYWORDS", default=12
)
DOCUMENT_SCAN_BING_BROWSER_FRESHNESS = env.bool(
    "DOCUMENT_SCAN_BING_BROWSER_FRESHNESS", default=False
)
# Exa semantic search (fallback open-web channel when keyed — prefer Searx/X/Reddit).
EXA_API_KEY = env("EXA_API_KEY", default="")
EXA_RECENCY_DAYS = env.int("EXA_RECENCY_DAYS", default=90)
# auto|fast|deep|… — keep auto; deep burns credits heavily.
EXA_SEARCH_TYPE = env("EXA_SEARCH_TYPE", default="auto")
# NL queries per OSINT/leak keyword (1 = frugal; raise for recall).
EXA_QUERY_COUNT = env.int("EXA_QUERY_COUNT", default=1)
EXA_HIGHLIGHTS = env.bool("EXA_HIGHLIGHTS", default=True)
# Guide: prefer highlights alone; text is opt-in (antipattern to combine by default).
EXA_INCLUDE_TEXT = env.bool("EXA_INCLUDE_TEXT", default=False)
EXA_TEXT_MAX_CHARS = env.int("EXA_TEXT_MAX_CHARS", default=2000)
# Optional highlights object {query} instead of highlights:true
EXA_HIGHLIGHTS_GUIDE = env.bool("EXA_HIGHLIGHTS_GUIDE", default=False)
# Opt-in includeText for leak hunts (default on — anchors brand/keyword).
EXA_REQUIRE_PHRASE = env.bool("EXA_REQUIRE_PHRASE", default=True)
# contents.maxAgeHours: omit empty; 24 / 1 / 0 / -1 per Exa livecrawl docs.
EXA_MAX_AGE_HOURS = env("EXA_MAX_AGE_HOURS", default="")
EXA_TIMEOUT = env.float("EXA_TIMEOUT", default=35.0)
EXA_CATEGORY = env("EXA_CATEGORY", default="")  # optional: news (not company+exclude)
EXA_EXCLUDE_DOMAINS = env(
    "EXA_EXCLUDE_DOMAINS",
    default="github.com,www.github.com,gist.github.com",
)
EXA_INCLUDE_DOMAINS = env("EXA_INCLUDE_DOMAINS", default="")
# OSINT ad-hoc: fallback = Exa only if Searx/X/Reddit kept < MIN_HITS (or use_exa=true).
EXA_OSINT_MODE = env("EXA_OSINT_MODE", default="fallback")  # fallback|always|off
EXA_OSINT_MIN_HITS = env.int("EXA_OSINT_MIN_HITS", default=5)
# Watch Rule leak sweeps: same gating.
EXA_LEAK_MODE = env("EXA_LEAK_MODE", default="fallback")  # fallback|always|off
EXA_LEAK_MIN_HITS = env.int("EXA_LEAK_MIN_HITS", default=5)
# Exa → The Wire (Threat ingest alongside RSS / Searx site discovery).
EXA_WIRE_ENABLED = env.bool("EXA_WIRE_ENABLED", default=False)
EXA_WIRE_MAX_AGE_DAYS = env.int("EXA_WIRE_MAX_AGE_DAYS", default=30)
# Cap results per beat run (lower = fewer Exa calls / credits).
EXA_WIRE_LIMIT = env.int("EXA_WIRE_LIMIT", default=8)
EXA_WIRE_LIMIT_PER_DOMAIN = env.int("EXA_WIRE_LIMIT_PER_DOMAIN", default=2)
# Optional pipe-separated NL queries (empty = built-in CTI pack).
EXA_WIRE_QUERIES = env("EXA_WIRE_QUERIES", default="")
# Max built-in/custom wire NL queries per run (each is an API call).
EXA_WIRE_QUERY_COUNT = env.int("EXA_WIRE_QUERY_COUNT", default=2)
# X/Twitter cookie search (secondary account only — never commit real values).
X_TWITTER_ENABLED = env.bool("X_TWITTER_ENABLED", default=True)
X_AUTH_TOKEN = env("X_AUTH_TOKEN", default="")
X_CT0 = env("X_CT0", default="")
# GraphQL query ids (rotate when X returns 404 — see twikit/xkit release notes).
X_SEARCH_QUERY_ID = env("X_SEARCH_QUERY_ID", default="R0u1RWRf748KzyGBXvOYRA")
X_TWEET_QUERY_ID = env("X_TWEET_QUERY_ID", default="Xl5pC_lBk_gcO2ItU39DQw")
# Curated X CTI accounts → The Wire (requires X cookies).
X_WIRE_ENABLED = env.bool("X_WIRE_ENABLED", default=False)
X_WIRE_MAX_AGE_DAYS = env.int("X_WIRE_MAX_AGE_DAYS", default=7)
X_WIRE_LIMIT_PER_ACCOUNT = env.int("X_WIRE_LIMIT_PER_ACCOUNT", default=8)
# Comma/pipe-separated handles (empty = built-in CTI pack). Append later without code.
X_WIRE_ACCOUNTS = env("X_WIRE_ACCOUNTS", default="")
# Pause between account fetches to reduce GraphQL rate pressure.
X_WIRE_PAUSE_MS = env.int("X_WIRE_PAUSE_MS", default=400)
# Reddit enrich (public JSON; optional cookie if rate-limited).
REDDIT_ENRICH_ENABLED = env.bool("REDDIT_ENRICH_ENABLED", default=True)
REDDIT_SEARCH_ENABLED = env.bool("REDDIT_SEARCH_ENABLED", default=True)
# Reddit search: relevance+all for recall; local phrase filter + published sort.
REDDIT_SEARCH_SORT = env("REDDIT_SEARCH_SORT", default="relevance")
REDDIT_SEARCH_TIME = env("REDDIT_SEARCH_TIME", default="all")
REDDIT_COOKIE = env("REDDIT_COOKIE", default="")
REDDIT_USER_AGENT = env(
    "REDDIT_USER_AGENT",
    default=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
)
PASTE_ENRICH_ENABLED = env.bool("PASTE_ENRICH_ENABLED", default=True)
PASTE_EXTRA_HOSTS = env("PASTE_EXTRA_HOSTS", default="")
GITHUB_TOKEN = env("GITHUB_TOKEN", default="")
GITHUB_MAX_FILE_BYTES = env.int("GITHUB_MAX_FILE_BYTES", default=512000)
# Prefer fewer content GETs: text_matches cover most keyword hits; fetch for secrets only.
GITHUB_CONTENT_FETCH_LIMIT = env.int("GITHUB_CONTENT_FETCH_LIMIT", default=120)
# Small batches so the UI can show repos/files as soon as each page is persisted.
GITHUB_STREAM_BATCH_SIZE = env.int("GITHUB_STREAM_BATCH_SIZE", default=3)
GITHUB_SCAN_STALE_MINUTES = env.int("GITHUB_SCAN_STALE_MINUTES", default=20)
# Cap below GitHub's practical search budget to reduce rate-limit hits.
GITHUB_SCAN_MAX_RESULTS = env.int("GITHUB_SCAN_MAX_RESULTS", default=1500)

# last30days-skill (vendored): multi-source topic research (Reddit/X/Polymarket + Wigolo web).
# Upstream: https://github.com/mvanhorn/last30days-skill (MIT)
# GitHub / Hacker News omitted from defaults (noisy / out of product scope).
LAST30DAYS_ENABLED = env.bool("LAST30DAYS_ENABLED", default=True)
LAST30DAYS_DEFAULT_DAYS = env.int("LAST30DAYS_DEFAULT_DAYS", default=30)
# Hard max age for findings (publish/discovery). 0 = use each research's lookback_days.
LAST30DAYS_MAX_AGE_DAYS = env.int("LAST30DAYS_MAX_AGE_DAYS", default=0)
LAST30DAYS_MAX_RESULTS = env.int("LAST30DAYS_MAX_RESULTS", default=60)
LAST30DAYS_TIMEOUT_SEC = env.int("LAST30DAYS_TIMEOUT_SEC", default=300)
LAST30DAYS_STALE_MINUTES = env.int("LAST30DAYS_STALE_MINUTES", default=25)
LAST30DAYS_SOURCES = env(
    "LAST30DAYS_SOURCES",
    default="reddit,x,polymarket",
)
LAST30DAYS_GROQ_EXPAND = env.bool("LAST30DAYS_GROQ_EXPAND", default=True)
LAST30DAYS_GROQ_TIMEOUT_SEC = env.float("LAST30DAYS_GROQ_TIMEOUT_SEC", default=20.0)
LAST30DAYS_TRANSLATE_ENABLED = env.bool("LAST30DAYS_TRANSLATE_ENABLED", default=True)
# Findings-grounded multi-dimensional Vietnamese brief after each research run.
LAST30DAYS_BRIEF_ENABLED = env.bool("LAST30DAYS_BRIEF_ENABLED", default=True)
LAST30DAYS_BRIEF_MAX_FINDINGS = env.int("LAST30DAYS_BRIEF_MAX_FINDINGS", default=80)
LAST30DAYS_BRIEF_SNIPPET_CHARS = env.int("LAST30DAYS_BRIEF_SNIPPET_CHARS", default=700)
LAST30DAYS_BRIEF_EVIDENCE_CHARS = env.int(
    "LAST30DAYS_BRIEF_EVIDENCE_CHARS", default=36000
)
LAST30DAYS_BRIEF_MAX_TOKENS = env.int("LAST30DAYS_BRIEF_MAX_TOKENS", default=4500)
LAST30DAYS_BRIEF_MIN_CHARS = env.int("LAST30DAYS_BRIEF_MIN_CHARS", default=900)
# auto|brave|exa|serper|parallel|wigolo|keyless|none — empty → wigolo if up else exa/none
LAST30DAYS_WEB_BACKEND = env("LAST30DAYS_WEB_BACKEND", default="")

# Zone-H / defacement archive → The Wire.
# Default provider=haxor (haxor.id) bypasses zone-h.org captcha from cloud IPs.
# For zone-h.org directly: ZONEH_PROVIDER=zoneh + PHPSESSID + ZHE cookies
# (browser Cookie-Editor after solving captcha once — inspired by BAUZACE7/Zone-H).
ZONEH_ENABLED = env.bool("ZONEH_ENABLED", default=True)
ZONEH_PROVIDER = env("ZONEH_PROVIDER", default="haxor")  # haxor | zoneh
ZONEH_BASE_URL = env("ZONEH_BASE_URL", default="")  # optional override
ZONEH_PAGES = env.int("ZONEH_PAGES", default=2)
ZONEH_INCLUDE_SPECIAL = env.bool("ZONEH_INCLUDE_SPECIAL", default=True)
ZONEH_TIMEOUT = env.float("ZONEH_TIMEOUT", default=30.0)
ZONEH_PHPSESSID = env("ZONEH_PHPSESSID", default="")
ZONEH_ZHE = env("ZONEH_ZHE", default="")

# Clearnet claim / forum-status → The Wire (no forum cookies or VNC).
FORUM_AI_ENRICH = env.bool("FORUM_AI_ENRICH", default=True)

# Fail closed when running production-like (DEBUG=False) with placeholder secrets.
from apps.core.security_checks import assert_secure_settings  # noqa: E402

assert_secure_settings()
