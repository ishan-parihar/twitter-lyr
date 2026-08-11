"""
Twitter/X-specific Obscura Daemon plugin integration.
"""

from __future__ import annotations

import logging
import os

from obscura_core import CookieValidationResult, ObscuraPlugin

logger = logging.getLogger(__name__)

# Required cookies for Twitter/X
TWITTER_REQUIRED_COOKIES = ["auth_token", "ct0"]


class TwitterDaemonManager:
    """Twitter/X-specific wrapper around Obscura Daemon plugin."""

    def __init__(self, daemon_url: str = "http://127.0.0.1:9999"):
        self.daemon_url = daemon_url
        self._plugin: ObscuraPlugin | None = None
        self._use_daemon = os.getenv("TWITTER_USE_DAEMON", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    async def _get_plugin(self) -> ObscuraPlugin:
        """Get or create the ObscuraPlugin instance."""
        if self._plugin is None:
            self._plugin = ObscuraPlugin(daemon_url=self.daemon_url)
            await self._plugin.connect()
        return self._plugin

    async def get_valid_cookies(self, force_refresh: bool = False) -> CookieValidationResult:
        """Get valid cookies from daemon cache."""
        if not self._use_daemon:
            logger.debug("Daemon integration disabled, falling back to local ObscuraCookieManager")
            # Import here to avoid circular imports
            from twitter_cli.obscura_integration import get_valid_twitter_cookies

            return await get_valid_twitter_cookies(force_refresh)

        try:
            plugin = await self._get_plugin()

            if force_refresh:
                # Trigger sync to refresh cache
                await plugin.sync_cookies("twitter")

            cookies = await plugin.get_cookies("twitter")

            if cookies is None:
                logger.warning("No cookies found in daemon cache for twitter")
                return CookieValidationResult(
                    valid=False,
                    source="daemon",
                    cookies={},
                    error="No cookies found in daemon cache",
                )

            # Validate required cookies
            for cookie in TWITTER_REQUIRED_COOKIES:
                if cookie not in cookies or not cookies[cookie]:
                    logger.debug(f"Required cookie missing: {cookie}")
                    return CookieValidationResult(
                        valid=False,
                        source="daemon",
                        cookies=cookies,
                        error=f"Required cookie missing: {cookie}",
                    )

            return CookieValidationResult(
                valid=True,
                cookies=cookies,
                source="daemon",
            )
        except Exception as e:
            logger.error(f"Error getting cookies from daemon: {e}")
            # Fall back to local ObscuraCookieManager
            logger.debug("Falling back to local ObscuraCookieManager")
            from twitter_cli.obscura_integration import get_valid_twitter_cookies

            return await get_valid_twitter_cookies(force_refresh)

    async def close(self) -> None:
        """Close the plugin connection."""
        if self._plugin:
            await self._plugin.close()
            self._plugin = None


# Global instance
_twitter_daemon_manager: TwitterDaemonManager | None = None


def get_twitter_daemon_manager(daemon_url: str = "http://127.0.0.1:9999") -> TwitterDaemonManager:
    """Get the global Twitter Daemon manager instance."""
    global _twitter_daemon_manager
    if _twitter_daemon_manager is None:
        _twitter_daemon_manager = TwitterDaemonManager(daemon_url=daemon_url)
    return _twitter_daemon_manager


async def get_valid_twitter_cookies_from_daemon(
    force_refresh: bool = False,
) -> CookieValidationResult:
    """Get valid Twitter/X cookies using Obscura Daemon plugin."""
    manager = get_twitter_daemon_manager()
    return await manager.get_valid_cookies(force_refresh)
