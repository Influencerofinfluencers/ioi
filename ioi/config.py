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
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
OPENAI_API_KEY = env("OPENAI_API_KEY")
DIGEST_LANGUAGE = env("DIGEST_LANGUAGE", PROFILE.get("language", "English"))

# --- distribution ---
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_STATUS_CHAT_ID = env("TELEGRAM_STATUS_CHAT_ID")  # your own chat: status + errors
TELEGRAM_PUBLIC_CHAT_ID = env("TELEGRAM_PUBLIC_CHAT_ID")  # free channel: daily digest
TELEGRAM_PRO_CHAT_ID = env("TELEGRAM_PRO_CHAT_ID")  # paid channel: realtime alerts

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
