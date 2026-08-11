"""
Twitter/X-specific ObscuraCookieManager integration.
"""

from __future__ import annotations

import logging
from pathlib import Path

from obscura_core import (
    BrowserCookieExtractor,
    CookieValidationResult,
    FileCookieStorage,
    ObscuraCookieManager,
)

logger = logging.getLogger(__name__)

# Required cookies for Twitter/X
TWITTER_REQUIRED_COOKIES = ["auth_token", "ct0"]


class TwitterCookieValidator:
    """Validates Twitter/X cookies by making an API call."""

    def __init__(self):
        self._session = None

    async def validate(self, cookies: dict[str, str]) -> bool:
        """Validate cookies by checking required cookies are present."""
        try:
            # For now, just check that required cookies are present
            # Full API validation can be added later
            required = ["auth_token", "ct0"]
            for cookie in required:
                if cookie not in cookies or not cookies[cookie]:
                    logger.debug(f"Required cookie missing: {cookie}")
                    return False
            return True
        except Exception as e:
            logger.debug(f"Twitter cookie validation failed: {e}")
            return False


class TwitterObscuraManager:
    """Twitter/X-specific wrapper around ObscuraCookieManager."""

    def __init__(self):
        self._manager: ObscuraCookieManager | None = None
        self._validator = TwitterCookieValidator()

    def _get_storage(self) -> FileCookieStorage:
        """Get file-based cookie storage."""
        cookie_path = Path.home() / ".local" / "share" / "twitter-lyr" / "cookies.json"
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        return FileCookieStorage(cookie_path)

    def _get_extractor(self) -> BrowserCookieExtractor:
        """Get browser cookie extractor (prefers Arc/Chrome)."""
        return BrowserCookieExtractor(
            domain="x.com",
            required_cookies=TWITTER_REQUIRED_COOKIES,
            preferred_browsers=["arc", "chrome", "edge", "firefox", "brave"],
        )

    def _get_manager(self) -> ObscuraCookieManager:
        """Get or create the ObscuraCookieManager instance."""
        if self._manager is None:
            self._manager = ObscuraCookieManager(
                storage=self._get_storage(),
                extractor=self._get_extractor(),
                validator=self._validator.validate,
                required_cookies=TWITTER_REQUIRED_COOKIES,
                validation_interval=300,  # 5 minutes
                max_re_extraction_attempts=3,
                re_extraction_cooldown=60,
            )
        return self._manager

    async def get_valid_cookies(self, force_refresh: bool = False) -> CookieValidationResult:
        """Get valid cookies, performing validation and re-extraction as needed."""
        manager = self._get_manager()
        return await manager.get_cookies(force_refresh=force_refresh)

    async def force_re_extraction(self) -> CookieValidationResult:
        """Force re-extraction from browser (call after user logs in)."""
        manager = self._get_manager()
        return await manager.force_re_extraction()

    async def invalidate_and_trigger_relogin(self) -> None:
        """Invalidate auth and trigger re-login flow."""
        manager = self._get_manager()
        await manager.invalidate_and_trigger_relogin()

    def is_cache_valid(self) -> bool:
        """Check if cached cookies are within validation interval."""
        manager = self._get_manager()
        return manager.is_cache_valid()


# Global instance
_twitter_obscura_manager: TwitterObscuraManager | None = None


def get_twitter_obscura_manager() -> TwitterObscuraManager:
    """Get the global Twitter Obscura manager instance."""
    global _twitter_obscura_manager
    if _twitter_obscura_manager is None:
        _twitter_obscura_manager = TwitterObscuraManager()
    return _twitter_obscura_manager


async def get_valid_twitter_cookies(force_refresh: bool = False) -> CookieValidationResult:
    """Get valid Twitter/X cookies using ObscuraCookieManager."""
    manager = get_twitter_obscura_manager()
    return await manager.get_valid_cookies(force_refresh)


async def force_twitter_cookie_refresh() -> CookieValidationResult:
    """Force re-extraction of Twitter/X cookies from browser."""
    manager = get_twitter_obscura_manager()
    return await manager.force_re_extraction()


async def invalidate_twitter_auth() -> None:
    """Invalidate Twitter/X auth and trigger re-login."""
    manager = get_twitter_obscura_manager()
    await manager.invalidate_and_trigger_relogin()
