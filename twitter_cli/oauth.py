"""OAuth authentication for Twitter/X.

Supports:
1. OAuth 1.0a (User Context) - For posting, DMs, engagement
2. OAuth 2.0 with PKCE (User Context) - Modern flow with refresh tokens
3. OAuth 2.0 App-Only (Client Credentials) - Read-only public data

Token storage: ~/.twitter-lyr/tokens.json
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import (  # noqa: F401 (used in # type: comments)  # noqa: F401 (used in # type: comments)
    Any,
    Optional,
)

import requests
from requests_oauthlib import OAuth1Session

logger = logging.getLogger(__name__)

# Twitter OAuth endpoints
OAUTH1_REQUEST_TOKEN_URL = "https://api.twitter.com/oauth/request_token"
OAUTH1_AUTHORIZE_URL = "https://api.twitter.com/oauth/authorize"
OAUTH1_ACCESS_TOKEN_URL = "https://api.twitter.com/oauth/access_token"

OAUTH2_AUTHORIZE_URL = "https://twitter.com/i/oauth2/authorize"
OAUTH2_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"

# Token storage
TOKEN_FILE = Path.home() / ".twitter-lyr" / "tokens.json"

# Client ID/Secret for OAuth2 - these should be configured by user
# Twitter Developer Portal: https://developer.twitter.com/en/portal/projects
DEFAULT_OAUTH2_CLIENT_ID = os.environ.get("TWITTER_OAUTH2_CLIENT_ID", "")
DEFAULT_OAUTH2_CLIENT_SECRET = os.environ.get("TWITTER_OAUTH2_CLIENT_SECRET", "")

# OAuth1 Consumer Key/Secret
DEFAULT_OAUTH1_CONSUMER_KEY = os.environ.get("TWITTER_OAUTH1_CONSUMER_KEY", "")
DEFAULT_OAUTH1_CONSUMER_SECRET = os.environ.get("TWITTER_OAUTH1_CONSUMER_SECRET", "")


@dataclass
class OAuth1Tokens:
    """OAuth 1.0a tokens."""

    oauth_token: str
    oauth_token_secret: str
    screen_name: str = ""
    user_id: str = ""
    created_at: float = 0


@dataclass
class OAuth2Tokens:
    """OAuth 2.0 tokens with PKCE."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 7200  # 2 hours
    scope: str = ""
    created_at: float = 0


@dataclass
class AppOnlyToken:
    """App-only bearer token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = 7200
    created_at: float = 0


@dataclass
class StoredTokens:
    """All stored token types."""

    oauth1: OAuth1Tokens | None = None
    oauth2: OAuth2Tokens | None = None
    app_only: AppOnlyToken | None = None
    active_type: str = "cookie"  # cookie, oauth1, oauth2, app_only


class OAuthManager:
    """Manage OAuth authentication flows."""

    def __init__(
        self,
        oauth1_consumer_key: str = "",
        oauth1_consumer_secret: str = "",
        oauth2_client_id: str = "",
        oauth2_client_secret: str = "",
        redirect_uri: str = "http://localhost:8080/callback",
    ):
        self.oauth1_consumer_key = oauth1_consumer_key or DEFAULT_OAUTH1_CONSUMER_KEY
        self.oauth1_consumer_secret = oauth1_consumer_secret or DEFAULT_OAUTH1_CONSUMER_SECRET
        self.oauth2_client_id = oauth2_client_id or DEFAULT_OAUTH2_CLIENT_ID
        self.oauth2_client_secret = oauth2_client_secret or DEFAULT_OAUTH2_CLIENT_SECRET
        self.redirect_uri = redirect_uri

        # Ensure token directory exists
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

    def load_tokens(self) -> StoredTokens:
        """Load stored tokens from file."""
        if not TOKEN_FILE.exists():
            return StoredTokens()

        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)

            tokens = StoredTokens()
            tokens.active_type = data.get("active_type", "cookie")

            if data.get("oauth1"):
                tokens.oauth1 = OAuth1Tokens(**data["oauth1"])
            if data.get("oauth2"):
                tokens.oauth2 = OAuth2Tokens(**data["oauth2"])
            if data.get("app_only"):
                tokens.app_only = AppOnlyToken(**data["app_only"])

            return tokens
        except Exception as e:
            logger.warning("Failed to load tokens: %s", e)
            return StoredTokens()

    def save_tokens(self, tokens: StoredTokens) -> None:
        """Save tokens to file."""
        data = {
            "active_type": tokens.active_type,
        }  # type: dict[str, Any]
        if tokens.oauth1:
            data["oauth1"] = asdict(tokens.oauth1)
        if tokens.oauth2:
            data["oauth2"] = asdict(tokens.oauth2)
        if tokens.app_only:
            data["app_only"] = asdict(tokens.app_only)

        with open(TOKEN_FILE, "w") as f:
            json.dump(data, f, indent=2)

        logger.info("Saved tokens (active: %s)", tokens.active_type)

    # ── OAuth 1.0a Flow ──────────────────────────────────────────────────

    def oauth1_get_request_token(self) -> tuple[str, str]:
        """Get OAuth1 request token. Returns (oauth_token, oauth_token_secret)."""
        if not self.oauth1_consumer_key or not self.oauth1_consumer_secret:
            raise ValueError(
                "OAuth1 consumer key/secret not configured. Set TWITTER_OAUTH1_CONSUMER_KEY and TWITTER_OAUTH1_CONSUMER_SECRET"
            )

        oauth = OAuth1Session(
            self.oauth1_consumer_key,
            client_secret=self.oauth1_consumer_secret,
            callback_uri=self.redirect_uri,
        )

        resp = oauth.fetch_request_token(OAUTH1_REQUEST_TOKEN_URL)
        return resp["oauth_token"], resp["oauth_token_secret"]

    def oauth1_get_authorize_url(self, oauth_token: str) -> str:
        """Get authorization URL for user to visit."""
        return f"{OAUTH1_AUTHORIZE_URL}?oauth_token={oauth_token}"

    def oauth1_get_access_token(
        self, oauth_token: str, oauth_token_secret: str, oauth_verifier: str
    ) -> OAuth1Tokens:
        """Exchange request token for access token."""
        oauth = OAuth1Session(
            self.oauth1_consumer_key,
            client_secret=self.oauth1_consumer_secret,
            resource_owner_key=oauth_token,
            resource_owner_secret=oauth_token_secret,
            verifier=oauth_verifier,
        )

        resp = oauth.fetch_access_token(OAUTH1_ACCESS_TOKEN_URL)

        return OAuth1Tokens(
            oauth_token=resp["oauth_token"],
            oauth_token_secret=resp["oauth_token_secret"],
            screen_name=resp.get("screen_name", ""),
            user_id=resp.get("user_id", ""),
            created_at=time.time(),
        )

    def oauth1_run_flow(self) -> OAuth1Tokens:
        """Run complete OAuth1 flow interactively."""
        print("🔐 OAuth 1.0a Authentication Flow")
        print("=" * 50)

        # Step 1: Get request token
        print("\n1. Getting request token...")
        oauth_token, oauth_token_secret = self.oauth1_get_request_token()

        # Step 2: Show authorization URL
        auth_url = self.oauth1_get_authorize_url(oauth_token)
        print("\n2. Visit this URL to authorize:")
        print(f"   {auth_url}")
        print("\n   After authorizing, you'll be redirected to a URL like:")
        print(f"   {self.redirect_uri}?oauth_token=...&oauth_verifier=...")
        print("\n   Copy the oauth_verifier parameter from the redirect URL.")

        # Step 3: Get verifier from user
        oauth_verifier = input("\n3. Enter oauth_verifier: ").strip()

        if not oauth_verifier:
            raise ValueError("oauth_verifier is required")

        # Step 4: Exchange for access token
        print("\n4. Exchanging for access token...")
        tokens = self.oauth1_get_access_token(oauth_token, oauth_token_secret, oauth_verifier)

        print("\n✅ OAuth1 authentication successful!")
        print(f"   User: @{tokens.screen_name}")
        print(f"   User ID: {tokens.user_id}")

        return tokens

    # ── OAuth 2.0 with PKCE Flow ─────────────────────────────────────────

    def _generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE code verifier and challenge."""
        code_verifier = secrets.token_urlsafe(32)
        import base64
        import hashlib

        code_challenge = (
            base64
            .urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        return code_verifier, code_challenge

    def oauth2_get_authorize_url(
        self,
        code_challenge: str,
        scope: str = "tweet.read tweet.write users.read offline.access",
        state: str = "",
    ) -> str:
        """Get OAuth2 authorization URL."""
        if not state:
            state = secrets.token_urlsafe(16)

        params = {
            "response_type": "code",
            "client_id": self.oauth2_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{OAUTH2_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def oauth2_exchange_code(self, code: str, code_verifier: str) -> OAuth2Tokens:
        """Exchange authorization code for tokens."""
        if not self.oauth2_client_id or not self.oauth2_client_secret:
            raise ValueError(
                "OAuth2 client ID/secret not configured. Set TWITTER_OAUTH2_CLIENT_ID and TWITTER_OAUTH2_CLIENT_SECRET"
            )

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
            "client_id": self.oauth2_client_id,
        }

        auth = (self.oauth2_client_id, self.oauth2_client_secret)
        resp = requests.post(OAUTH2_TOKEN_URL, data=data, auth=auth, timeout=30)

        if resp.status_code >= 400:
            raise Exception(f"Token exchange failed: {resp.status_code} - {resp.text}")

        token_data = resp.json()

        return OAuth2Tokens(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", ""),
            token_type=token_data.get("token_type", "bearer"),
            expires_in=token_data.get("expires_in", 7200),
            scope=token_data.get("scope", ""),
            created_at=time.time(),
        )

    def oauth2_refresh_token(self, refresh_token: str) -> OAuth2Tokens:
        """Refresh OAuth2 access token."""
        if not self.oauth2_client_id or not self.oauth2_client_secret:
            raise ValueError("OAuth2 client ID/secret not configured")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.oauth2_client_id,
        }

        auth = (self.oauth2_client_id, self.oauth2_client_secret)
        resp = requests.post(OAUTH2_TOKEN_URL, data=data, auth=auth, timeout=30)

        if resp.status_code >= 400:
            raise Exception(f"Token refresh failed: {resp.status_code} - {resp.text}")

        token_data = resp.json()

        return OAuth2Tokens(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", refresh_token),
            token_type=token_data.get("token_type", "bearer"),
            expires_in=token_data.get("expires_in", 7200),
            scope=token_data.get("scope", ""),
            created_at=time.time(),
        )

    def oauth2_run_flow(
        self, scope: str = "tweet.read tweet.write users.read offline.access"
    ) -> OAuth2Tokens:
        """Run complete OAuth2 PKCE flow interactively."""
        if not self.oauth2_client_id or not self.oauth2_client_secret:
            raise ValueError(
                "OAuth2 client ID/secret not configured. Set TWITTER_OAUTH2_CLIENT_ID and TWITTER_OAUTH2_CLIENT_SECRET"
            )

        print("🔐 OAuth 2.0 (PKCE) Authentication Flow")
        print("=" * 50)

        # Step 1: Generate PKCE pair
        print("\n1. Generating PKCE code pair...")
        code_verifier, code_challenge = self._generate_pkce_pair()

        # Step 2: Get authorization URL
        print("\n2. Getting authorization URL...")
        state = secrets.token_urlsafe(16)
        auth_url = self.oauth2_get_authorize_url(code_challenge, scope, state)
        print("\n   Visit this URL to authorize:")
        print(f"   {auth_url}")
        print(f"\n   State parameter: {state}")
        print("\n   After authorizing, you'll be redirected to a URL like:")
        print(f"   {self.redirect_uri}?code=...&state={state}")
        print("\n   Copy the 'code' parameter from the redirect URL.")

        # Step 3: Get code from user
        auth_code = input("\n3. Enter authorization code: ").strip()

        if not auth_code:
            raise ValueError("Authorization code is required")

        # Step 4: Exchange for tokens
        print("\n4. Exchanging code for tokens...")
        tokens = self.oauth2_exchange_code(auth_code, code_verifier)

        print("\n✅ OAuth2 authentication successful!")
        print(f"   Scope: {tokens.scope}")
        print(f"   Expires in: {tokens.expires_in}s")

        return tokens

    # ── App-Only (Client Credentials) Flow ───────────────────────────────

    def app_only_get_token(self) -> AppOnlyToken:
        """Get app-only bearer token using client credentials."""
        if not self.oauth2_client_id or not self.oauth2_client_secret:
            raise ValueError(
                "OAuth2 client ID/secret not configured. Set TWITTER_OAUTH2_CLIENT_ID and TWITTER_OAUTH2_CLIENT_SECRET"
            )

        data = {"grant_type": "client_credentials"}
        auth = (self.oauth2_client_id, self.oauth2_client_secret)

        resp = requests.post(OAUTH2_TOKEN_URL, data=data, auth=auth, timeout=30)

        if resp.status_code >= 400:
            raise Exception(f"App-only token failed: {resp.status_code} - {resp.text}")

        token_data = resp.json()

        return AppOnlyToken(
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "bearer"),
            expires_in=token_data.get("expires_in", 7200),
            created_at=time.time(),
        )

    def app_only_run_flow(self) -> AppOnlyToken:
        """Run app-only flow."""
        print("🔐 App-Only (Client Credentials) Authentication")
        print("=" * 50)
        print("\nGetting app-only bearer token...")

        tokens = self.app_only_get_token()

        print("\n✅ App-only authentication successful!")
        print(f"   Token type: {tokens.token_type}")
        print(f"   Expires in: {tokens.expires_in}s")

        return tokens


def create_oauth_manager(
    oauth1_consumer_key: str = "",
    oauth1_consumer_secret: str = "",
    oauth2_client_id: str = "",
    oauth2_client_secret: str = "",
    redirect_uri: str = "http://localhost:8080/callback",
) -> OAuthManager:
    """Factory function to create OAuthManager."""
    return OAuthManager(
        oauth1_consumer_key=oauth1_consumer_key,
        oauth1_consumer_secret=oauth1_consumer_secret,
        oauth2_client_id=oauth2_client_id,
        oauth2_client_secret=oauth2_client_secret,
        redirect_uri=redirect_uri,
    )
