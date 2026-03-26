"""Offer detection and 2FA-related handlers."""

import asyncio
import json
import logging
import random
import time

import config

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from core.runtime_state import (
    CHECK_OFFER_COOLDOWN,
    CHROME_SEMAPHORE,
    LAST_CHECK_TIME,
)
from core.session_manager import (
    SESSION_STORE,
    get_session,
    secure_wipe,
)
from services.device_simulator import create_device_profile
from services.google_automation import (
    GoogleAutomationError,
    check_offer_with_driver,
    close_driver,
    start_login,
    start_with_cookies,
    submit_2fa_code,
)

from handlers.states import AWAIT_2FA_CODE, AWAIT_COOKIE_JSON
from handlers.ui import main_menu_keyboard, quick_actions_inline_keyboard

logger = logging.getLogger(__name__)


async def _delete_message_later(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a message after scheduled delay."""
    job = context.job
    if not job:
        return
    data = job.data or {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if chat_id and message_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass


async def _report_offer(update_or_chat_id, context, session, offer_link) -> None:
    """Send the offer result message."""
    chat_id = (
        update_or_chat_id
        if isinstance(update_or_chat_id, int)
        else update_or_chat_id.effective_chat.id
    )
    if offer_link:
        session["offer_link"] = offer_link
        text = (
            "🎉 <b>Gemini Pro Offer Found!</b>\n\n"
            "Use the link below to activate your 12-month free Gemini Pro:\n\n"
            f"🔗 {offer_link}\n\n"
            "You can run /get_link anytime to retrieve this link again."
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=quick_actions_inline_keyboard(),
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎉 Gemini Pro Offer Found!\n\n"
                    f"🔗 {offer_link}\n\n"
                    "You can run /get_link anytime to retrieve this link again."
                ),
                reply_markup=main_menu_keyboard(),
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "😔 No active Gemini Pro offer was detected on your Google One "
                "account at this time.\n\n"
                "The offer may not be available for your account region or may "
                "have already been activated. You can try again later."
            ),
            reply_markup=quick_actions_inline_keyboard(),
        )


async def check_offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Run Google One automation and report the result."""
    max_offer_attempts = 3
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    session_cookie_buf = session.get("cookies_json")
    session_cookie_json = (
        bytes(session_cookie_buf).decode("utf-8")
        if isinstance(session_cookie_buf, bytearray) and session_cookie_buf
        else ""
    )
    env_cookie_json = config.GOOGLE_COOKIES_JSON.strip()
    active_cookie_json = session_cookie_json or env_cookie_json

    use_cookie_mode = bool(active_cookie_json)
    has_credentials = bool(session.get("email") and session.get("password"))

    if not has_credentials and not use_cookie_mode:
        await update.message.reply_text(
            "🍪 No cookies found. Please send GOOGLE_COOKIES_JSON now as a single message.\n"
            "You can /cancel anytime. Your cookie message will be auto-deleted after 1 minute.",
            reply_markup=main_menu_keyboard(),
        )
        return AWAIT_COOKIE_JSON

    last_check = LAST_CHECK_TIME.get(chat_id, 0)
    elapsed = time.time() - last_check
    if elapsed < CHECK_OFFER_COOLDOWN:
        remaining = int(CHECK_OFFER_COOLDOWN - elapsed)
        mins, secs = divmod(remaining, 60)
        await update.message.reply_text(f"⏳ Please wait {mins}m {secs}s before checking again.")
        return ConversationHandler.END
    LAST_CHECK_TIME[chat_id] = time.time()

    if CHROME_SEMAPHORE.locked():
        await update.message.reply_text(
            "🔄 The system is currently at maximum capacity. Please try again in a minute.",
            reply_markup=main_menu_keyboard(),
        )
        LAST_CHECK_TIME.pop(chat_id, None)
        return ConversationHandler.END

    await update.message.reply_text(
        "⏳ Starting secure check...\n"
        + (
            "Launching Pixel 10 Pro simulation and restoring cookie session.\n"
            if use_cookie_mode
            else "Launching Pixel 10 Pro simulation and signing in.\n"
        )
        + "This usually takes up to 60 seconds."
    )

    offer_link = None
    try:
        async with CHROME_SEMAPHORE:
            email_str = bytes(session["email"]).decode("utf-8") if has_credentials else ""
            pw_str = bytes(session["password"]).decode("utf-8") if has_credentials else ""

            for attempt in range(1, max_offer_attempts + 1):
                device = create_device_profile()
                session["device"] = device

                if attempt > 1:
                    await update.message.reply_text(
                        f"🔄 Retry {attempt}/{max_offer_attempts}: "
                        "creating a fresh device profile and trying again."
                    )

                driver = None
                try:
                    if use_cookie_mode:
                        driver = await asyncio.to_thread(start_with_cookies, active_cookie_json, device)
                        await update.message.reply_text(
                            f"✅ Cookie session restored ({attempt}/{max_offer_attempts}).\n"
                            "Checking Gemini Pro offer now..."
                        )
                        offer_link = await asyncio.to_thread(check_offer_with_driver, driver)
                    else:
                        driver, status = await asyncio.to_thread(start_login, email_str, pw_str, device)

                        if status == "needs_totp":
                            totp_secret_buf = session.get("totp_secret")
                            if isinstance(totp_secret_buf, bytearray) and totp_secret_buf:
                                try:
                                    import pyotp

                                    totp_secret = bytes(totp_secret_buf).decode("utf-8")
                                    code = pyotp.TOTP(totp_secret).now()
                                    logger.info(
                                        "Auto-generated TOTP code for chat %s (attempt %d)",
                                        chat_id,
                                        attempt,
                                    )
                                    accepted = await asyncio.to_thread(submit_2fa_code, driver, code)
                                    if not accepted:
                                        close_driver(driver)
                                        driver = None
                                        await update.message.reply_text(
                                            "❌ Auto-generated TOTP code was rejected. "
                                            "Please check your TOTP secret key.",
                                            reply_markup=main_menu_keyboard(),
                                        )
                                        return ConversationHandler.END

                                    await update.message.reply_text(
                                        f"✅ Login successful ({attempt}/{max_offer_attempts}).\n"
                                        "Checking Gemini Pro offer now..."
                                    )
                                    offer_link = await asyncio.to_thread(check_offer_with_driver, driver)
                                except Exception as exc:
                                    logger.warning("Auto-TOTP failed: %s", exc)
                                    close_driver(driver)
                                    driver = None
                                    await update.message.reply_text(
                                        f"❌ Auto-TOTP error: {exc}\n"
                                        "Please check your TOTP secret key.",
                                        reply_markup=main_menu_keyboard(),
                                    )
                                    return ConversationHandler.END
                            else:
                                session["_driver"] = driver
                                await update.message.reply_text(
                                    "🔐 *Two-Factor Authentication Required*\n\n"
                                    "Please enter your 6-digit authenticator code.",
                                    parse_mode="Markdown",
                                )
                                return AWAIT_2FA_CODE
                        else:
                            await update.message.reply_text(
                                f"✅ Login successful ({attempt}/{max_offer_attempts}).\n"
                                "Checking Gemini Pro offer now..."
                            )
                            offer_link = await asyncio.to_thread(check_offer_with_driver, driver)
                finally:
                    if driver:
                        close_driver(driver)

                if offer_link:
                    logger.info(
                        "Offer found on attempt %d for chat %s: %s",
                        attempt,
                        chat_id,
                        offer_link,
                    )
                    break

                logger.info(
                    "No offer found on attempt %d/%d for chat %s",
                    attempt,
                    max_offer_attempts,
                    chat_id,
                )

                if attempt < max_offer_attempts:
                    delay = random.randint(15, 30)
                    await update.message.reply_text(
                        f"⏳ Offer not found yet. Retrying in {delay} seconds..."
                    )
                    await asyncio.sleep(delay)
                    await update.message.reply_text(
                        f"🔄 Starting retry {attempt + 1}/{max_offer_attempts}: "
                        "building a new device profile and signing in."
                    )

    except GoogleAutomationError as exc:
        await update.message.reply_text(
            f"❌ <b>Automation Error:</b> {exc}",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END
    except Exception as exc:
        logger.exception("Unexpected error in check_offer for chat %s", chat_id)
        await update.message.reply_text(f"❌ An unexpected error occurred: {exc}")
        return ConversationHandler.END
    finally:
        pw = session.get("password")
        if isinstance(pw, bytearray):
            secure_wipe(pw)
        session.pop("password", None)

        totp = session.get("totp_secret")
        if isinstance(totp, bytearray):
            secure_wipe(totp)
        session.pop("totp_secret", None)

    if not offer_link:
        await update.message.reply_text(
            f"❌ No Gemini Pro offer found after {max_offer_attempts} attempts.\n\n"
            "Possible reasons:\n"
            "• Your account region is not eligible\n"
            "• An active Gemini subscription already exists\n"
            "• Family group eligibility has already been used\n"
            "• New-account risk controls are in effect",
            reply_markup=quick_actions_inline_keyboard(),
        )
        return ConversationHandler.END

    await _report_offer(update, context, session, offer_link)
    return ConversationHandler.END


async def handle_cookie_json(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture cookie JSON from chat, schedule message deletion, then run check_offer."""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    raw = (update.message.text or "").strip()

    # Auto-delete cookie message after 1 minute.
    if context.job_queue and update.message:
        context.job_queue.run_once(
            _delete_message_later,
            when=60,
            data={"chat_id": chat_id, "message_id": update.message.message_id},
            name=f"delete-cookie-msg:{chat_id}:{update.message.message_id}",
        )

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("Cookie JSON must be a non-empty JSON array")
    except Exception:
        await update.message.reply_text(
            "⚠️ Invalid cookie JSON. Send a non-empty JSON array of cookies or /cancel.",
            reply_markup=main_menu_keyboard(),
        )
        return AWAIT_COOKIE_JSON

    session["cookies_json"] = bytearray(raw.encode("utf-8"))

    await update.message.reply_text(
        "✅ Cookies received. Running /check_offer now...",
        reply_markup=main_menu_keyboard(),
    )
    return await check_offer(update, context)


async def handle_2fa_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the TOTP code submitted by the user during 2FA."""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    code = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    driver = session.pop("_driver", None)
    if not driver:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Session expired. Please run /check\\_offer again.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    if not code.isdigit() or len(code) != 6:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Invalid code. Please enter a 6-digit number.",
            reply_markup=main_menu_keyboard(),
        )
        session["_driver"] = driver
        return AWAIT_2FA_CODE

    await context.bot.send_message(chat_id=chat_id, text="🔄 Verifying code…")

    try:
        async with CHROME_SEMAPHORE:
            accepted = await asyncio.to_thread(submit_2fa_code, driver, code)

            if not accepted:
                close_driver(driver)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Code rejected. Please run /check\\_offer again.",
                    reply_markup=main_menu_keyboard(),
                )
                return ConversationHandler.END

            try:
                offer_link = await asyncio.to_thread(check_offer_with_driver, driver)
            finally:
                close_driver(driver)

    except Exception as exc:
        logger.exception("Error in 2FA for chat %s", chat_id)
        close_driver(driver)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {exc}")
        return ConversationHandler.END
    finally:
        pw = session.get("password")
        if isinstance(pw, bytearray):
            secure_wipe(pw)
        session.pop("password", None)

        totp = session.get("totp_secret")
        if isinstance(totp, bytearray):
            secure_wipe(totp)
        session.pop("totp_secret", None)

        cookies_buf = session.get("cookies_json")
        if isinstance(cookies_buf, bytearray):
            secure_wipe(cookies_buf)
        session.pop("cookies_json", None)

    await _report_offer(chat_id, context, session, offer_link)
    return ConversationHandler.END


async def cancel_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel offer-related input (2FA/cookies) and close the driver."""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    driver = session.pop("_driver", None)
    close_driver(driver)
    await update.message.reply_text(
        "❌ Input cancelled.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def offer_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle conversation timeout by cleaning up a pending 2FA driver."""
    if update and update.effective_chat:
        chat_id = update.effective_chat.id
        session = SESSION_STORE.get(chat_id, {})
        driver = session.pop("_driver", None)
        close_driver(driver)
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏰ 2FA verification timed out. Please run /check_offer again.",
            reply_markup=main_menu_keyboard(),
        )
    return ConversationHandler.END
