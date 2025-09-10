from __future__ import annotations

import os
from pydantic import BaseModel
from dotenv import load_dotenv


# Load .env early so os.getenv() sees local overrides in dev
_ = load_dotenv(os.getenv("ENV_FILE", ".env"), override=False)


class Settings(BaseModel):
    env: str = os.getenv("ENV", "development")
    # Keep offline mode available for local/dev. Set to false in production.
    offline_mode: bool = os.getenv("OFFLINE_MODE", "true").lower() == "true"
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/app.db")

    # External APIs
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    yoto_client_id: str | None = os.getenv("YOTO_CLIENT_ID")
    yoto_client_secret: str | None = os.getenv("YOTO_CLIENT_SECRET")
    yoto_redirect_uri: str | None = os.getenv("YOTO_REDIRECT_URI")
    # ElevenLabs key is not needed when using Yoto Labs API
    elevenlabs_api_key: str | None = os.getenv("ELEVENLABS_API_KEY")

    # OAuth and API endpoints
    # Token endpoint base (OAuth token/refresh). Default to login host for PKCE/public clients.
    yoto_oauth_base: str = os.getenv("YOTO_OAUTH_BASE", "https://login.yotoplay.com")
    # Authorization endpoint base (browser redirect)
    yoto_login_base: str = os.getenv("YOTO_LOGIN_BASE", "https://login.yotoplay.com")
    # OAuth audience parameter (as per Yoto docs)
    yoto_audience: str = os.getenv("YOTO_AUDIENCE", "https://api.yotoplay.com")
    yoto_content_base: str = os.getenv("YOTO_CONTENT_BASE", "https://api.yotoplay.com")
    yoto_labs_base: str = os.getenv("YOTO_LABS_BASE", "https://labs.api.yotoplay.com")

    # App
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000")
    api_user_agent: str = os.getenv(
        "API_USER_AGENT", "yoto-today-in-history/0.1 (contact@example.com)"
    )
    log_level: str = os.getenv("LOG_LEVEL", "info")
    session_secret: str = os.getenv("SESSION_SECRET", "dev-secret-change-me")


settings = Settings()
