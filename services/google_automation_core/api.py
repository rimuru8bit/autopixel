"""Public API for Google One automation service."""

import logging
from typing import Optional

from services.device_simulator import DeviceProfile
from services.google_automation_core.driver_factory import build_driver
from services.google_automation_core.errors import GoogleAutomationError
from services.google_automation_core.login_flow import gmail_login, submit_totp_code
from services.google_automation_core.offer_scanner import navigate_google_one

logger = logging.getLogger(__name__)


def start_login(email: str, password: str, device: DeviceProfile) -> tuple:
    """Start login process and return (driver, status)."""
    logger.info("Starting WebDriver for session %s", device.session_id)
    driver = build_driver(device)

    try:
        status = gmail_login(driver, email, password)
        if status == "failed":
            driver.quit()
            raise GoogleAutomationError("Login failed - please check your credentials.")
        return driver, status
    except GoogleAutomationError:
        driver.quit()
        raise
    except Exception:
        driver.quit()
        raise


def submit_2fa_code(driver, code: str) -> bool:
    """Submit TOTP code on a driver that is on the 2FA challenge page."""
    return submit_totp_code(driver, code)


def start_with_cookies(cookies_json: str, device: DeviceProfile):
    """Start a driver and inject cookies for authenticated Google session."""
    logger.info("Starting cookie-based session for %s", device.session_id)
    driver = build_driver(device)

    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list) or not cookies:
            raise GoogleAutomationError("GOOGLE_COOKIES_JSON must be a non-empty JSON list.")

        # Open Google domain before adding cookies.
        driver.get("https://accounts.google.com/")

        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            cleaned = {
                "name": cookie.get("name"),
                "value": cookie.get("value"),
                "domain": cookie.get("domain", ".google.com"),
                "path": cookie.get("path", "/"),
                "secure": bool(cookie.get("secure", True)),
                "httpOnly": bool(cookie.get("httpOnly", False)),
            }
            expiry = cookie.get("expiry")
            if isinstance(expiry, (int, float)):
                cleaned["expiry"] = int(expiry)
            same_site = cookie.get("sameSite")
            if same_site in {"Strict", "Lax", "None"}:
                cleaned["sameSite"] = same_site

            if cleaned["name"] and cleaned["value"]:
                try:
                    driver.add_cookie(cleaned)
                except Exception:
                    continue

        driver.get("https://one.google.com/")
        return driver
    except GoogleAutomationError:
        driver.quit()
        raise
    except Exception as exc:
        driver.quit()
        raise GoogleAutomationError(f"Cookie session init failed: {exc}") from exc


def check_offer_with_driver(driver) -> Optional[str]:
    """Navigate to Google One and find the Gemini Pro offer link."""
    return navigate_google_one(driver)


def close_driver(driver) -> None:
    """Safely close WebDriver instance."""
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
