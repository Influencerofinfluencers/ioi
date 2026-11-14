"""All settings come from environment variables (or a local .env file)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


# --- niche profile (config/niches/<NICHE>.json): one pipeline, any niche ---
import json as _json

NICHE = env("NICHE", "crypto")
_niche_file = ROOT / "config" / "niches" / f"{NICHE}.json"
PROFILE = _json.loads(_niche_file.read_text(encoding="utf-8")) if _niche_file.exists() else {}

# --- storage ---
DATABASE_URL = env("DATABASE_URL", f"sqlite:///{ROOT / 'ioi.db'}")
ACCOUNTS_CSV = Path(env("ACCOUNTS_CSV", str(ROOT / PROFILE.get("accounts_csv", "config/accounts.csv"))))
RETENTION_DAYS = env_int("RETENTION_DAYS", 90)  # posts older than this are deleted (privacy + free tier)
PUBLIC_DIR = Path(env("PUBLIC_DIR", str(ROOT / "public")))
SITE_URL = env("SITE_URL", "https://example.github.io/ioi").rstrip("/")
BRAND = env("BRAND", PROFILE.get("brand", "IOI - Influencer of Influencers"))

# --- sources ---
X_BEARER_TOKEN = env("X_BEARER_TOKEN")
X_API_BASE = env("X_API_BASE", "https://api.x.com/2")
X_MAX_RESULTS = env_int("X_MAX_RESULTS", 20)  # per account per run (min 5)
X_MAX_READS_PER_DAY = env_int("X_MAX_READS_PER_DAY", 1000)  # own budget guard, on top of X's spend cap

YOUTUBE_API_KEY = env("YOUTUBE_API_KEY")

TELEGRAM_API_ID = env("TELEGRAM_API_ID")
TELEGRAM_API_HASH = env("TELEGRAM_API_HASH")
TELEGRAM_SESSION = env("TELEGRAM_SESSION")  # StringSession, see README

IG_USER_ID = env("IG_USER_ID")  # your own Instagram business account id
IG_ACCESS_TOKEN = env("IG_ACCESS_TOKEN")
GRAPH_VERSION = env("GRAPH_VERSION", "v25.0")

# --- AI ---
# LLM_PROVIDER: "anthropic" (Claude) or "openai" (any OpenAI-compatible endpoint:
# OpenRouter, Groq, Together, a self-hosted vLLM/Ollama server, ...)
LLM_PROVIDER = env("LLM_PROVIDER", "anthropic")
# per step: e.g. open-source for bulk labelling, Claude for writing
LABEL_PROVIDER = env("LABEL_PROVIDER", LLM_PROVIDER)
WRITE_PROVIDER = env("WRITE_PROVIDER", LLM_PROVIDER)
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
LABEL_MODEL = env("LABEL_MODEL", "claude-haiku-5-5")  # cheap bulk work
WRITE_MODEL = env("WRITE_MODEL", "claude-sonnet-5-5")  # digests and reports
OPENAI_BASE_URL = env("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
OPENAI_API_KEY = env("OPENAI_API_KEY")
# open-source backups, tried in order when the primary model fails (needs OPENAI_API_KEY, e.g. OpenRouter)
_FALLBACK_DEFAULT = ("nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free,nvidia/nemotron-3.5-super-vl:free,"
                     "meta-llama/llama-3.3-70b-instruct,deepseek/deepseek-chat")  # free open-source first, then paid (key capped at $5)
LLM_FALLBACK_MODELS = [m.strip() for m in (env("LLM_FALLBACK_MODELS") or _FALLBACK_DEFAULT).split(",") if m.strip()]
DIGEST_LANGUAGE = env("DIGEST_LANGUAGE", PROFILE.get("language", "English"))

# --- distribution ---
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_STATUS_CHAT_ID = env("TELEGRAM_STATUS_CHAT_ID")  # your own chat: status + errors
TELEGRAM_PUBLIC_CHAT_ID = env("TELEGRAM_PUBLIC_CHAT_ID")  # free channel: daily digest
TELEGRAM_PRO_CHAT_ID = env("TELEGRAM_PRO_CHAT_ID")  # paid channel: realtime alerts

# --- selling + growth (all optional; empty = that button/channel is hidden or skipped) ---
PRO_CHECKOUT_URL = env("PRO_CHECKOUT_URL")  # Whop checkout link for IOI Pro
PRO_PRICE = env("PRO_PRICE", "€25")
FREE_CHANNEL_URL = env("FREE_CHANNEL_URL")  # public t.me link of the free channel
NEWSLETTER_URL = env("NEWSLETTER_URL")  # beehiiv subscribe page (later)
CONTACT_URL = env("CONTACT_URL")  # where "Request access" (Data API) goes
X_PROFILE_URL = env("X_PROFILE_URL")
BLUESKY_PROFILE_URL = env("BLUESKY_PROFILE_URL")
TEASER_DELAY_HOURS = env_int("TEASER_DELAY_HOURS", 4)  # free channel sees Pro alerts this much later
TEASERS_PER_DAY = env_int("TEASERS_PER_DAY", 3)

# --- Whop (IOI Pro access) - the key is read-only: members + member e-mails ---
WHOP_API_KEY = env("WHOP_API_KEY")
WHOP_API_BASE = env("WHOP_API_BASE", "https://api.whop.com/api/v1")
WHOP_API_VERSION = env("WHOP_API_VERSION", "2026-11-12")
WHOP_COMPANY_ID = env("WHOP_COMPANY_ID")
WHOP_PRODUCT_ID = env("WHOP_PRODUCT_ID")

# --- newswire (fast primary sources) ---
NEWS_ENABLED = env("NEWS_ENABLED", "1") not in ("0", "false", "no")
CRYPTOPANIC_TOKEN = env("CRYPTOPANIC_TOKEN")
SEC_CONTACT_EMAIL = env("SEC_CONTACT_EMAIL")
KIT_API_KEY = env("KIT_API_KEY")  # Kit (free Newsletter plan) V4 API key - e-mail edition of the daily digest
KIT_TEMPLATE_ID = env("KIT_TEMPLATE_ID")  # SEC EDGAR requires a contact e-mail in the User-Agent  # optional free token from cryptopanic.com/developers/api

# --- automatic social posts (highlights) ---
SOCIAL_ENABLED = env("SOCIAL_ENABLED", "1") not in ("0", "false", "no", "")
SOCIAL_MAX_PER_DAY = env_int("SOCIAL_MAX_PER_DAY", 3)
X_API_KEY = env("X_API_KEY")  # OAuth 1.0a "consumer" key of the X app
X_API_SECRET = env("X_API_SECRET")
X_ACCESS_TOKEN = env("X_ACCESS_TOKEN")  # access token of the @IOI account (read+write)
X_ACCESS_SECRET = env("X_ACCESS_SECRET")
BLUESKY_HANDLE = env("BLUESKY_HANDLE")
BLUESKY_APP_PASSWORD = env("BLUESKY_APP_PASSWORD")  # app password, never the main password

# --- analysis ---
ALERT_WINDOW_HOURS = env_int("ALERT_WINDOW_HOURS", 6)
ALERT_MIN_ACCOUNTS = env_int("ALERT_MIN_ACCOUNTS", 4)
ECHO_WINDOW_HOURS = env_int("ECHO_WINDOW_HOURS", 24)

DISCLAIMER = PROFILE.get(
    "disclaimer",
    "Attention data, not financial advice. IOI summarises what influential accounts "
    "say; it does not recommend buying or selling anything. Always do your own research.",
)
AUDIENCE = PROFILE.get("audience", "crypto readers")
CATEGORIES = PROFILE.get("categories", ["opinion", "promo_spam", "other"])
TOPIC_RULES = PROFILE.get("topic_rules", "Short UPPER_SNAKE topic names. Merge synonyms.")
VOICE = PROFILE.get("voice", "a smart friend explaining things in plain language. No hype.")
CONTENT_RULES = PROFILE.get("rules", "Never give advice. Describe what influential accounts say.")
