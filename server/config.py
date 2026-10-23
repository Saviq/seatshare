import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def default_base_url(cookie_secure: bool) -> str:
    # Matches `hostname -f`: HTTPS behind a TLS proxy, otherwise the dev server port.
    host = socket.getfqdn()
    return f"https://{host}" if cookie_secure else f"http://{host}:8080"


@dataclass(frozen=True)
class Settings:
    github_client_id: str
    github_client_secret: str
    session_secret: str
    base_url: str
    db_path: Path = Path("/data/app.db")
    environment: str = "production"
    cookie_secure: bool = True
    mutation_limit: int = 30

    def __post_init__(self) -> None:
        if not self.github_client_id or not self.github_client_secret:
            raise ValueError("GitHub OAuth client ID and secret are required")
        if len(self.session_secret) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 characters")
        if self.environment not in {"production", "development", "test"}:
            raise ValueError("APP_ENV must be production, development, or test")
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.netloc
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("BASE_URL must be an http(s) origin without a path")
        if self.environment == "production" and (url.scheme != "https" or not self.cookie_secure):
            raise ValueError("Production requires HTTPS and secure cookies")
        if self.mutation_limit < 1:
            raise ValueError("Mutation limit must be positive")

    @classmethod
    def from_env(cls) -> "Settings":
        secure = os.getenv("COOKIE_SECURE", "true").lower()
        if secure not in {"true", "false"}:
            raise ValueError("COOKIE_SECURE must be true or false")
        return cls(
            github_client_id=os.environ["GITHUB_CLIENT_ID"],
            github_client_secret=os.environ["GITHUB_CLIENT_SECRET"],
            session_secret=os.environ["SESSION_SECRET"],
            base_url=(os.getenv("BASE_URL") or default_base_url(secure == "true")).rstrip("/"),
            db_path=Path(os.getenv("DB_PATH", "/data/app.db")),
            environment=os.getenv("APP_ENV", "production"),
            cookie_secure=secure == "true",
        )
