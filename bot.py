import logging
import csv
import json
import random
import sys
from io import StringIO
from typing import Optional
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeAllPrivateChats,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    TypeHandler,
    filters,
)
from telegram.constants import ParseMode
from config import (
    BOT_TOKEN,
    ADMIN_GROUP_ID,
    TARGET_GROUP_ID,
    TARGET_SUB_GROUP_ID,
    SURVEY_QUESTIONS,
    GOOGLE_DRIVE_MAIN_FOLDER_ID,
    GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID,
    GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID,
    TELEGRAM_API_KEY,
    TELEGRAM_HASH,
    MAX_AUDIO_FILE_SIZE,
    MINI_APP_URL,
)
from database import db, UserState, UserData
import telegram
import pytz
from datetime import datetime, timezone
import asyncio
from upload_to_google_drive import GoogleDriveUploader
from audio_transcribe import AudioTranscriber
import os
import time
from pyrogram import Client
from user_sweep import user_sweep_loop
from ai_enrichment import process_enrichment, buffer, admin_buffer
from rich_messages import (
    STREAM_CHUNK_DELAY_SECONDS,
    stream_rich_message,
    stream_rich_then_animation,
    warm_onboarding_animations,
)
from onboarding_copy import (
    INTRO_MARKDOWN,
    SERENDIP_PITCH_MARKDOWN,
    approved_invite_markdown,
    approved_resume_markdown,
    member_start_markdown,
    pending_approval_markdown,
    rejection_markdown,
    serendip_gate_markdown,
    serendip_member_followup_markdown,
    survey_buttons_only_prefix,
    survey_complete_markdown,
    survey_in_progress_markdown,
    survey_question_markdown,
    survey_text_only_prefix,
)
from onboarding_media import (
    SERENDIP_WELCOME_ANIM,
    SISC_WELCOME_ANIM,
    require_onboarding_gifs,
)
from serendip_update_forwarder import (
    attach_forward_client,
    close_forward_client,
    forward_update_to_serendip,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

APPLY_JOIN_CALLBACK = "apply_join"


def sisc_source_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 SISC Website", callback_data="SISC Website"),
                InlineKeyboardButton("📰 Newsletter", callback_data="Newsletter"),
            ],
            [
                InlineKeyboardButton(
                    "📍 Physical Event", callback_data="Physical Event"
                ),
                InlineKeyboardButton("🤝 Referral", callback_data="Referral"),
            ],
        ]
    )


def admin_approval_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve (Main)", callback_data=f"approve_main_{user_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Approve (Sub)", callback_data=f"approve_sub_{user_id}"
                )
            ],
            [InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")],
        ]
    )


# Initialize transcriber
transcriber = AudioTranscriber()


def resolve_start_branch(*, is_member: bool, state: UserState) -> str:
    if is_member:
        return "member"
    if state == UserState.APPROVED:
        return "approved_invite"
    if state in (UserState.PENDING_APPROVAL, UserState.PENDING_REJECTION):
        return "pending"
    if state == UserState.IN_SURVEY:
        return "in_survey"
    return "apply"


def resolve_serendip_branch(*, is_member: bool) -> str:
    if is_member:
        return "member"
    return "gate"


def member_start_owns_reply(*, deep_link: bool) -> bool:
    """Whether the gate bot answers a member /start itself.

    The bots share one token: tg_sentinel polls and forwards every update to
    Serendip. A deep-link /start is therefore answered by Serendip's main
    menu, which already greets members and carries the group link — the gate
    bot stays silent there. Plain /start is only handled here, so it is the
    only path that returns the group link.
    """
    return not deep_link


def begin_apply_survey(user_data: UserData) -> Optional[UserData]:
    """Start the join survey if the user is eligible for Apply.

    Eligible when resolve_start_branch(is_member=False, state=...) == "apply"
    (IDLE / REJECTED). Mutates and returns user_data on success; returns None
    when already pending, approved, or in_survey so callers do not wipe state.
    """
    if resolve_start_branch(is_member=False, state=user_data.state) != "apply":
        return None
    user_data.state = UserState.IN_SURVEY
    user_data.current_question = 0
    user_data.answers = {}
    return user_data


def apply_ineligible_status_markdown(state: UserState) -> str:
    branch = resolve_start_branch(is_member=False, state=state)
    if branch == "pending":
        return pending_approval_markdown()
    if branch == "approved_invite":
        return approved_resume_markdown()
    if branch == "in_survey":
        return survey_in_progress_markdown()
    raise ValueError(
        f"apply_ineligible_status_markdown called for eligible branch={branch!r} "
        f"state={state!r}"
    )


# [Pyrogram] get_peer_type monkey patch is applied by user_sweep on import.


async def revoke_and_create_invite_link(
    bot, user_data: UserData, target_group_id: int = TARGET_GROUP_ID
) -> str:
    """Revoke user's previous invite links and create a new one."""
    try:
        # Revoke user's previous invite links
        # (links that belong to the other group fail revocation and are skipped)
        for link_id in user_data.invite_links:
            try:
                await bot.revoke_chat_invite_link(target_group_id, link_id)
            except telegram.error.BadRequest:
                # Skip if link is already revoked or invalid
                continue

        # Create new invite link with member limit of 1
        new_link = await bot.create_chat_invite_link(
            chat_id=target_group_id, member_limit=1
        )

        # Update user's invite links
        user_data.invite_links = [new_link.invite_link]
        db.update_user(user_data)

        return new_link.invite_link
    except telegram.error.BadRequest as e:
        if "rights to manage chat invite link" in str(e).lower():
            raise telegram.error.BadRequest(
                "Bot needs admin rights to manage invite links"
            )
        raise e


TARGET_GROUP_IDS = (TARGET_GROUP_ID, TARGET_SUB_GROUP_ID)

def _record_group_member(user, chat_id: int) -> None:
    """Realtime join: insert the member now (unwelcomed). The user sweep sends
    the welcome and also catches anyone these events miss."""
    db.upsert_group_member(
        chat_id,
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        is_bot=user.is_bot,
    )


async def announce_new_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id not in TARGET_GROUP_IDS:
        return
    if not update.message or not update.message.new_chat_members:
        return

    bot_id = context.bot.id
    for member in update.message.new_chat_members:
        if member.id != bot_id:
            _record_group_member(member, update.effective_chat.id)


async def handle_chat_member_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id not in TARGET_GROUP_IDS:
        return
    if not update.chat_member:
        return

    new_status = update.chat_member.new_chat_member.status
    old_status = update.chat_member.old_chat_member.status
    user = update.chat_member.new_chat_member.user
    if user.id == context.bot.id:
        return

    joining_statuses = {"member", "administrator", "owner"}
    if new_status not in joining_statuses:
        if old_status in joining_statuses:
            db.mark_group_member_left(
                update.effective_chat.id, user.id, status=new_status
            )
        return
    if old_status in joining_statuses:
        return

    _record_group_member(user, update.effective_chat.id)


PINNED_IDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinned_ids.json")


def _read_pinned_ids() -> list:
    try:
        with open(PINNED_IDS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_pinned_ids(ids: list) -> None:
    with open(PINNED_IDS_FILE, "w") as f:
        json.dump(ids[:10], f)


async def handle_pinned_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != TARGET_GROUP_ID:
        return
    if not update.message or not update.message.pinned_message:
        return

    pinned_id = update.message.pinned_message.message_id
    ids = _read_pinned_ids()
    if pinned_id in ids:
        return

    ids.insert(0, pinned_id)
    _write_pinned_ids(ids)
    logger.info(f"Tracked pinned message: {pinned_id}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    # Only allow in private chats
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    username = update.effective_user.username

    # Get or create user
    user_data = db.get_user(user_id)
    if not user_data:
        user_data = db.create_user(user_id, username)

    is_member = False
    group_link = None
    try:
        member = await context.bot.get_chat_member(TARGET_GROUP_ID, user_id)
        if member.status not in ["left", "kicked", "banned"]:
            is_member = True
            chat = await context.bot.get_chat(TARGET_GROUP_ID)
            if chat.username:
                group_link = f"https://t.me/{chat.username}"
            else:
                group_link = chat.invite_link
    except Exception:
        # If we can't get member info, continue with the flow
        pass

    branch = resolve_start_branch(is_member=is_member, state=user_data.state)

    if branch == "member":
        if not member_start_owns_reply(deep_link=bool(context.args)):
            return
        member_rows = []
        if group_link:
            member_rows.append(
                [InlineKeyboardButton("💬 Open SISC group", url=group_link)]
            )
        member_markup = (
            InlineKeyboardMarkup(member_rows) if member_rows else None
        )
        await stream_rich_then_animation(
            BOT_TOKEN,
            user_id,
            member_start_markdown(has_group_link=bool(group_link)),
            SISC_WELCOME_ANIM,
            "sisc_welcome",
            member_markup.to_dict() if member_markup else None,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
        return

    if branch == "apply":
        apply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✨ Apply to join SISC", callback_data=APPLY_JOIN_CALLBACK
                    )
                ]
            ]
        )
        await stream_rich_then_animation(
            BOT_TOKEN,
            user_id,
            INTRO_MARKDOWN,
            SISC_WELCOME_ANIM,
            "sisc_welcome",
            apply_markup.to_dict(),
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
    else:
        await stream_rich_then_animation(
            BOT_TOKEN,
            user_id,
            INTRO_MARKDOWN,
            SISC_WELCOME_ANIM,
            "sisc_welcome",
            None,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )

    if branch == "approved_invite":
        try:
            invite_link = await revoke_and_create_invite_link(context.bot, user_data)
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                approved_invite_markdown(invite_link),
                None,
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )
        except telegram.error.BadRequest as e:
            if "rights to manage chat invite link" in str(e).lower():
                await update.message.reply_text(
                    "You were already approved! Please wait while I notify the admins to help you join."
                )
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"⚠️ Cannot create invite link for approved user {username or user_id} because bot "
                    f"needs admin rights in the target group. Please make the bot an admin or help the user join manually.",
                )
            else:
                raise e
        return

    if branch == "pending":
        await stream_rich_message(
            BOT_TOKEN,
            user_id,
            pending_approval_markdown(),
            None,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
        return

    if branch == "in_survey":
        question_index = user_data.current_question
        question_text = SURVEY_QUESTIONS[question_index]
        reply_markup = None
        if question_text == "How do you know about SISC?":
            reply_markup = sisc_source_keyboard().to_dict()
        await stream_rich_message(
            BOT_TOKEN,
            user_id,
            survey_question_markdown(
                question_index,
                len(SURVEY_QUESTIONS),
                question_text,
                text_reply_hint=reply_markup is None,
            ),
            reply_markup,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
        return

    # apply: intro already sent with Apply keyboard; do not set IN_SURVEY here


async def serendip_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Unlisted dogfood entry: Serendip pitch + Launch for SISC members."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    username = update.effective_user.username

    user_data = db.get_user(user_id)
    if not user_data:
        user_data = db.create_user(user_id, username)

    is_member = False
    group_link = None
    try:
        member = await context.bot.get_chat_member(TARGET_GROUP_ID, user_id)
        if member.status not in ["left", "kicked", "banned"]:
            is_member = True
            chat = await context.bot.get_chat(TARGET_GROUP_ID)
            if chat.username:
                group_link = f"https://t.me/{chat.username}"
            else:
                group_link = chat.invite_link
    except Exception:
        pass

    branch = resolve_serendip_branch(is_member=is_member)

    if branch == "member":
        rows = [
            [
                InlineKeyboardButton(
                    "🚀 Launch Serendip", web_app=WebAppInfo(url=MINI_APP_URL)
                )
            ]
        ]
        if group_link:
            rows.append(
                [InlineKeyboardButton("💬 Open SISC group", url=group_link)]
            )
        await stream_rich_then_animation(
            BOT_TOKEN,
            user_id,
            SERENDIP_PITCH_MARKDOWN + "\n\n" + serendip_member_followup_markdown(),
            SERENDIP_WELCOME_ANIM,
            "serendip_welcome",
            InlineKeyboardMarkup(rows).to_dict(),
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
        return

    await stream_rich_then_animation(
        BOT_TOKEN,
        user_id,
        SERENDIP_PITCH_MARKDOWN + "\n\n" + serendip_gate_markdown(),
        SERENDIP_WELCOME_ANIM,
        "serendip_welcome",
        None,
        STREAM_CHUNK_DELAY_SECONDS,
        None,
    )


async def handle_apply_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data != APPLY_JOIN_CALLBACK:
        return
    user_id = update.effective_user.id
    username = update.effective_user.username
    user_data = db.get_user(user_id) or db.create_user(user_id, username)
    started = begin_apply_survey(user_data)
    if started is None:
        await stream_rich_message(
            BOT_TOKEN,
            user_id,
            apply_ineligible_status_markdown(user_data.state),
            None,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
        return
    db.update_user(started)
    await stream_rich_message(
        BOT_TOKEN,
        user_id,
        survey_question_markdown(0, len(SURVEY_QUESTIONS), SURVEY_QUESTIONS[0]),
        None,
        STREAM_CHUNK_DELAY_SECONDS,
        None,
    )


async def handle_survey_response(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle survey responses."""
    user_id = update.effective_user.id
    user_data = db.get_user(user_id)

    if not user_data or user_data.state != UserState.IN_SURVEY:
        return

    sisc_source_question = "How do you know about SISC?"
    is_sisc_source = SURVEY_QUESTIONS[user_data.current_question] == sisc_source_question

    # Check if the message is text
    if not update.message.text:
        if is_sisc_source:
            reply_markup = sisc_source_keyboard()
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                survey_buttons_only_prefix()
                + survey_question_markdown(
                    user_data.current_question,
                    len(SURVEY_QUESTIONS),
                    SURVEY_QUESTIONS[user_data.current_question],
                    text_reply_hint=False,
                ),
                reply_markup.to_dict(),
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )
        else:
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                survey_text_only_prefix()
                + survey_question_markdown(
                    user_data.current_question,
                    len(SURVEY_QUESTIONS),
                    SURVEY_QUESTIONS[user_data.current_question],
                ),
                None,
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )
        return

    # Check if current question is "How do you know about SISC?" - user should use buttons
    if is_sisc_source:
        reply_markup = sisc_source_keyboard()

        await stream_rich_message(
            BOT_TOKEN,
            user_id,
            survey_buttons_only_prefix()
            + survey_question_markdown(
                user_data.current_question,
                len(SURVEY_QUESTIONS),
                SURVEY_QUESTIONS[user_data.current_question],
                text_reply_hint=False,
            ),
            reply_markup.to_dict(),
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
        return

    # Save the answer
    current_question = SURVEY_QUESTIONS[user_data.current_question]
    user_data.answers[current_question] = update.message.text
    user_data.current_question += 1

    # Check if survey is complete
    if user_data.current_question >= len(SURVEY_QUESTIONS):
        user_data.state = UserState.PENDING_APPROVAL
        db.update_user(user_data)

        # Notify user
        await stream_rich_message(
            BOT_TOKEN,
            user_id,
            survey_complete_markdown(),
            None,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )

        # Notify admin group
        # Build survey answers, skipping Question 7 if not relevant
        answers_text = []
        for q in SURVEY_QUESTIONS:
            # Skip "Who is your referral?" if user didn't select "Referral"
            if q == "Who is your referral?":
                sisc_question = "How do you know about SISC?"
                if user_data.answers.get(sisc_question) != "Referral":
                    continue
            answers_text.append(f"{q}: {user_data.answers.get(q, '')}")

        survey_answers = "\n".join(answers_text)

        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=(
                f"New join request from {user_data.username or user_id}:\n\n"
                f"{survey_answers}"
            ),
            reply_markup=admin_approval_keyboard(user_id),
        )
    else:
        # Check if the next question is "How do you know about SISC?"
        if (
            SURVEY_QUESTIONS[user_data.current_question]
            == "How do you know about SISC?"
        ):
            reply_markup = sisc_source_keyboard()

            db.update_user(user_data)
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                survey_question_markdown(
                    user_data.current_question,
                    len(SURVEY_QUESTIONS),
                    SURVEY_QUESTIONS[user_data.current_question],
                    text_reply_hint=False,
                ),
                reply_markup.to_dict(),
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )
        else:
            # Ask next question normally
            db.update_user(user_data)
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                survey_question_markdown(
                    user_data.current_question,
                    len(SURVEY_QUESTIONS),
                    SURVEY_QUESTIONS[user_data.current_question],
                ),
                None,
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )


async def handle_survey_option_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle survey option selection from inline keyboard."""
    query = update.callback_query
    await query.answer()

    # Remove buttons from the original message
    await query.edit_message_reply_markup(reply_markup=None)

    user_id = update.effective_user.id
    user_data = db.get_user(user_id)

    if not user_data or user_data.state != UserState.IN_SURVEY:
        return

    # Get the selected option
    selected_option = query.data

    # Find the question index for "How do you know about SISC?"
    sisc_question_index = 5  # Index 5 in SURVEY_QUESTIONS

    # Save the selected option as the answer to "How do you know about SISC?"
    user_data.answers[SURVEY_QUESTIONS[sisc_question_index]] = selected_option
    user_data.current_question += 1

    # Check if "Referral" was selected
    if selected_option == "Referral":
        # Show confirmation and ask the referral question (without back button)
        db.update_user(user_data)
        await stream_rich_message(
            BOT_TOKEN,
            user_id,
            "*You selected:* ==Referral==\n\n"
            + survey_question_markdown(
                user_data.current_question,
                len(SURVEY_QUESTIONS),
                SURVEY_QUESTIONS[user_data.current_question],
            ),
            None,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )
    else:
        await stream_rich_message(
            BOT_TOKEN,
            user_id,
            f"*You selected:* =={selected_option}==",
            None,
            STREAM_CHUNK_DELAY_SECONDS,
            None,
        )

        # Move to the question after "Who is your referral?"
        user_data.current_question += 1
        db.update_user(user_data)

        # Check if survey is complete
        if user_data.current_question >= len(SURVEY_QUESTIONS):
            user_data.state = UserState.PENDING_APPROVAL
            db.update_user(user_data)

            # Send completion message
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                survey_complete_markdown(),
                None,
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )

            # Notify admin group
            # Build survey answers, skipping Question 7 if not relevant
            answers_text = []
            for q in SURVEY_QUESTIONS:
                # Skip "Who is your referral?" if user didn't select "Referral"
                if q == "Who is your referral?":
                    sisc_question = "How do you know about SISC?"
                    if user_data.answers.get(sisc_question) != "Referral":
                        continue
                answers_text.append(f"{q}: {user_data.answers.get(q, '')}")

            survey_answers = "\n".join(answers_text)

            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"New join request from {user_data.username or user_id}:\n\n"
                    f"{survey_answers}"
                ),
                reply_markup=admin_approval_keyboard(user_id),
            )


async def handle_admin_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle admin's approval/rejection."""
    query = update.callback_query
    await query.answer()

    # Extract decision and user_id from callback data
    # (approve_main_<id>, approve_sub_<id> or reject_<id>)
    action, user_id = query.data.rsplit("_", 1)
    user_id = int(user_id)
    user_data = db.get_user(user_id)

    if not user_data:
        await query.edit_message_text("User not found in database.")
        return

    # Target group depends on which approve button was pressed
    if action == "approve_sub":
        target_group_id = TARGET_SUB_GROUP_ID
        target_label = "sub group"
    else:
        target_group_id = TARGET_GROUP_ID
        target_label = "main group"

    # Check if user is already in the target group
    try:
        member = await context.bot.get_chat_member(target_group_id, user_id)
        if member.status not in ["left", "kicked", "banned"]:
            await query.edit_message_text(
                "User is already a member of the target group."
            )
            return
    except Exception:
        # If we can't get member info, continue with the flow
        pass

    # If user is approved but hasn't joined yet, resend the invite
    if user_data.state == UserState.APPROVED:
        try:
            # Revoke old links and generate new one
            invite_link = await revoke_and_create_invite_link(
                context.bot, user_data, target_group_id
            )

            # Notify user with new link
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                approved_invite_markdown(invite_link),
                None,
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )
            await query.edit_message_text(
                f"User {user_data.username or user_id} was already approved. Sent new invite link."
            )
        except telegram.error.BadRequest as e:
            if "rights to manage chat invite link" in str(e).lower():
                # Send error as new message instead of editing
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text="⚠️ Cannot create invite link because bot needs admin rights in the target group. "
                    "Please make the bot an admin first before approving requests.",
                )
            else:
                raise e
        return

    if (
        user_data.state != UserState.PENDING_APPROVAL
        and user_data.state != UserState.PENDING_REJECTION
    ):
        await query.edit_message_text("This request is no longer valid.")
        return

    if action in ("approve_main", "approve_sub"):
        # If user was in pending rejection, clean up the rejection message
        if (
            user_data.state == UserState.PENDING_REJECTION
            and user_data.rejection_message_id
        ):
            try:
                await context.bot.delete_message(
                    chat_id=ADMIN_GROUP_ID, message_id=user_data.rejection_message_id
                )
            except telegram.error.BadRequest:
                # Message might be already deleted, ignore
                pass
            user_data.rejection_message_id = None

        try:
            # Revoke old links and generate new one
            invite_link = await revoke_and_create_invite_link(
                context.bot, user_data, target_group_id
            )

            # If invite link creation successful, proceed with approval
            user_data.state = UserState.APPROVED
            user_data.approval_group = "sub" if action == "approve_sub" else "main"
            db.update_user(user_data)

            # Notify user
            await stream_rich_message(
                BOT_TOKEN,
                user_id,
                approved_invite_markdown(invite_link),
                None,
                STREAM_CHUNK_DELAY_SECONDS,
                None,
            )

            # Remove inline keyboard and update message text
            await query.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"Request from {user_data.username or user_id} has been approved "
                    f"for the {target_label}."
                ),
            )

            # Export and upload data
            try:
                # Export data
                _, _, csv_path = await export_data()
                if csv_path:
                    # Start upload in background
                    asyncio.create_task(upload_to_drive(context.bot, csv_path))
            except Exception as e:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"❌ Error exporting/uploading data: {str(e)}",
                )

        except telegram.error.BadRequest as e:
            if "rights to manage chat invite link" in str(e).lower():
                # Send error as new message instead of editing
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text="⚠️ Cannot approve request because bot needs admin rights in the target group. "
                    "Please make the bot an admin first before approving requests.",
                )
                # Answer the callback query to remove loading state
                await query.answer("Cannot approve - bot needs admin rights")
            else:
                raise e

    elif action == "reject":
        # If already in pending rejection state, ignore
        if user_data.state == UserState.PENDING_REJECTION:
            return

        # Send message asking for rejection reason
        reason_msg = await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Please reply to this message with the reason for rejecting {user_data.username or user_id}'s request.",
        )

        # Store both the reason request message ID and the original message ID
        user_data.state = UserState.PENDING_REJECTION
        user_data.rejection_message_id = reason_msg.message_id
        # Store the original message ID in the answers dict temporarily
        user_data.answers["original_message_id"] = query.message.message_id
        db.update_user(user_data)


async def handle_rejection_reason(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the rejection reason reply from admin."""
    # Only process messages in admin group that are replies
    if (
        update.effective_chat.id != ADMIN_GROUP_ID
        or not update.message.reply_to_message
    ):
        return

    # Get the replied-to message ID
    replied_msg_id = update.message.reply_to_message.message_id

    # Find user with this rejection_message_id
    all_users = db.get_all_users()
    user_data = next(
        (
            user
            for user in all_users
            if user.state == UserState.PENDING_REJECTION
            and user.rejection_message_id == replied_msg_id
        ),
        None,
    )

    if not user_data:
        return

    rejection_reason = update.message.text.strip()
    if not rejection_reason:
        await update.message.reply_text("Please provide a valid rejection reason.")
        return

    # Get the original message ID from answers
    original_message_id = user_data.answers.pop("original_message_id", None)

    # Update user state
    user_data.state = UserState.REJECTED
    user_data.rejection_message_id = None
    db.update_user(user_data)

    # Notify user with reason
    await stream_rich_message(
        BOT_TOKEN,
        user_data.user_id,
        rejection_markdown(rejection_reason),
        None,
        STREAM_CHUNK_DELAY_SECONDS,
        None,
    )

    # Remove inline keyboard from original message if we have its ID
    if original_message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=ADMIN_GROUP_ID,
                message_id=original_message_id,
                reply_markup=None,
            )
        except telegram.error.BadRequest:
            pass

    # Delete the rejection reason request message and the admin's reply
    try:
        await update.message.delete()
        await update.message.reply_to_message.delete()
    except telegram.error.BadRequest:
        pass

    # Send confirmation to admin group
    await context.bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=f"✅ Rejection completed for {user_data.username or user_data.user_id}\nReason: {rejection_reason}",
    )

    # Export and upload data
    try:
        # Export data
        _, _, csv_path = await export_data()
        if csv_path:
            # Start upload in background
            asyncio.create_task(upload_to_drive(context.bot, csv_path))
    except Exception as e:
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID, text=f"❌ Error exporting/uploading data: {str(e)}"
        )


async def export_data(
    update: Update = None, context: ContextTypes.DEFAULT_TYPE = None
) -> tuple[str, str, str]:
    """Export all user data to CSV. Returns tuple of (csv_data, filename, csv_path)."""
    # Only allow command usage in admin group
    if update and update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group."
        )
        return None, None, None

    # Set timezone to Singapore (GMT+8)
    sg_tz = pytz.timezone("Asia/Singapore")

    # Create CSV in memory
    output = StringIO()
    csv_writer = csv.writer(output)

    # Write header
    headers = ["User ID", "Username", "State", "Join Date (GMT+8)"]
    headers.extend(SURVEY_QUESTIONS)  # Add each survey question as a column
    csv_writer.writerow(headers)

    # Get all users from database
    all_users = db.get_all_users()

    # Write data rows
    for user in all_users:
        # Convert UTC time to Singapore time
        sg_time = user.join_datetime.replace(tzinfo=pytz.UTC).astimezone(sg_tz)

        row = [
            user.user_id,
            user.username or "None",
            user.state.value,
            sg_time.strftime(
                "%Y-%m-%d %H:%M:%S GMT+8"
            ),  # Format datetime in Singapore timezone
        ]
        # Add answers in the same order as questions
        for question in SURVEY_QUESTIONS:
            # Skip "Who is your referral?" if user didn't select "Referral"
            if question == "Who is your referral?":
                sisc_question = "How do you know about SISC?"
                if user.answers.get(sisc_question) != "Referral":
                    continue
            row.append(user.answers.get(question, ""))

        csv_writer.writerow(row)

    # Set csv file name
    filename = "sisc_user_data.csv"
    csv_path = filename  # Save in root directory

    # Get CSV data and save to file
    csv_data = output.getvalue()
    with open(csv_path, "w") as f:
        f.write(csv_data)

    # If called as command, send the file
    if update and context:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=csv_data.encode(),
            filename=filename,
            caption="Here is the exported user data.",
        )

    output.close()
    return csv_data, filename, csv_path


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of available commands based on chat type."""
    chat_type = update.effective_chat.type

    if chat_type == "private":
        help_text = """
*Available Commands*

/start - Open SISC intro
• Apply to join SISC (survey starts after you tap Apply)
• Check pending / rejected status
• Members: Open SISC group

/help - Show this help message with command list

*How to Join*
1. Use /start to see the intro
2. Tap *Apply to join SISC* to start the survey
3. Answer all survey questions
4. Wait for admin approval
5. Once approved, you'll receive an invite link
6. After you join the group, /start again for the group link

Note: Each invite link can only be used once and expires after use.
"""
    elif update.effective_chat.id == ADMIN_GROUP_ID:
        help_text = """
*Admin Commands*

*User Management Commands:*
/start - Start the join process (in private chat)
/help - Show this help message
/export - Export user data to CSV
/stats - Show current statistics
/reset\\_pending\\_approval - Clear all pending approval records

*AI Enrichment Commands:*
/enable\\_ai\\_insights - Enable AI enrichment in target group
/disable\\_ai\\_insights - Disable AI enrichment in target group
/enable\\_admin\\_ai\\_insights - Enable AI enrichment in admin group
/disable\\_admin\\_ai\\_insights - Disable AI enrichment in admin group
/insights\\_status - Check enrichment status for all groups
/enable\\_ai\\_context\\_window - Enable context window trigger \\(non\\-URL\\)
/disable\\_ai\\_context\\_window - Disable context window trigger \\(URL\\-only mode\\)

*Audio Processing Commands:*
/transcribe\\_audio - Start audio transcription
/check\\_transcription\\_status - Check transcription progress

*Audio Processing Features:*
• Supports audio files, voice messages, and documents
• Transcribes speech to text
• Generates discussion insights
• Uploads results to Google Drive
• Shows progress and time elapsed
• Automatic file cleanup after processing

*Important Notes:*
• Only one transcription can run at a time
• Results are uploaded to Google Drive
• Admin commands work only in this group
• User management via inline buttons
"""
    else:
        await update.message.reply_text(
            "This command can only be used in private chat or the admin group."
        )
        return

    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current statistics."""
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group."
        )
        return

    # Get all users
    all_users = db.get_all_users()

    # Calculate stats
    total_users = len(all_users)
    pending_requests = sum(
        1
        for user in all_users
        if user.state == UserState.IN_SURVEY or user.state == UserState.PENDING_APPROVAL
    )
    approved_users = sum(1 for user in all_users if user.state == UserState.APPROVED)
    rejected_users = sum(1 for user in all_users if user.state == UserState.REJECTED)

    stats_text = f"""
*Bot Statistics*

Total Users: {total_users}
Pending Requests: {pending_requests}
Approved Users: {approved_users}
Rejected Users: {rejected_users}
"""

    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)


async def reset_pending_approval_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Delete all users with PENDING_APPROVAL state."""
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group."
        )
        return

    # Delete pending approval users
    deleted_count = db.delete_pending_approval_users()

    if deleted_count > 0:
        await update.message.reply_text(
            f"✅ Successfully cleared {deleted_count} pending approval record(s)."
        )
    else:
        await update.message.reply_text("No pending approval records found to clear.")


async def invite_main_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Invite a sub group member to the main group via a one-time invite link.

    The user stays in the sub group; they just receive a fresh main-group
    invite link (member_limit=1) in a private message.
    """
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group."
        )
        return

    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "Usage: /invite_main @username or /invite_main <user_id>"
        )
        return
    identifier = parts[1].strip().lstrip("@")

    # Resolve the user: numeric ID directly, otherwise look up username in DB
    user_id = None
    if identifier.isdigit():
        user_id = int(identifier)
    else:
        for user in db.get_all_users():
            if (
                user.username
                and user.username.lstrip("@").lower() == identifier.lower()
            ):
                user_id = user.user_id
                break
        if user_id is None:
            await update.message.reply_text(
                f"❌ Couldn't resolve @{identifier}. Use their numeric Telegram "
                "user ID, or have them /start the bot once so they are in the "
                "database."
            )
            return

    # Skip if the user is already in the main group
    try:
        main_member = await context.bot.get_chat_member(TARGET_GROUP_ID, user_id)
        if main_member.status in ("member", "administrator", "creator"):
            await update.message.reply_text(
                f"ℹ️ User {main_member.user.username or user_id} is already a "
                "member of the main group — no invite sent."
            )
            return
    except telegram.error.BadRequest:
        pass  # not in the main group, continue

    # Verify the user is actually a member of the sub group
    try:
        member = await context.bot.get_chat_member(TARGET_SUB_GROUP_ID, user_id)
        if member.status not in ("member", "administrator", "creator"):
            await update.message.reply_text(
                f"❌ User {member.user.username or user_id} is not a member of "
                f"the sub group (status: {member.status})."
            )
            return
        username = member.user.username
    except telegram.error.BadRequest:
        await update.message.reply_text(
            f"❌ Couldn't find user {identifier} in the sub group."
        )
        return

    # Reuse the existing DB record if present; otherwise create one
    user_data = db.get_user(user_id)
    if user_data is None:
        user_data = db.create_user(user_id, username)

    user_data.username = username or user_data.username
    user_data.state = UserState.APPROVED
    user_data.approval_group = "main"

    try:
        # Revoke previous links and create a new one-time link for the main group
        # (also persists invite_links + state via update_user)
        invite_link = await revoke_and_create_invite_link(
            context.bot, user_data, TARGET_GROUP_ID
        )
    except telegram.error.BadRequest as e:
        if "rights to manage chat invite link" in str(e).lower():
            await update.message.reply_text(
                "⚠️ Cannot create invite link because bot needs admin rights "
                "in the main group."
            )
            return
        raise e

    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "You've been invited to join the main group! "
                f"Click here to join: {invite_link}"
            ),
        )
        await update.message.reply_text(
            f"✅ Main group invite sent to {user_data.username or user_id} "
            "(one-time link)."
        )
    except telegram.error.Forbidden:
        await update.message.reply_text(
            f"⚠️ Invite link created but couldn't message the user "
            f"(they need to /start the bot first). Link for {user_data.username or user_id}: "
            f"{invite_link}"
        )


async def upload_to_drive(bot, csv_path: str) -> None:
    """Upload CSV file to Google Drive asynchronously."""
    try:
        # Create uploader instance
        uploader = GoogleDriveUploader()

        # Configure folder ID - you should set this in your config.py
        folder_id = GOOGLE_DRIVE_MAIN_FOLDER_ID

        # Run the upload in a thread pool to not block
        loop = asyncio.get_running_loop()
        upload_result = await loop.run_in_executor(
            None, lambda: uploader.upload_file(csv_path, folder_id)
        )

        if upload_result:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"✅ Successfully uploaded {csv_path} to Google Drive\nLink: {upload_result.get('webViewLink')}",
            )
        else:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"❌ Failed to upload {csv_path} to Google Drive",
            )

    except Exception as e:
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID, text=f"❌ Error uploading to Google Drive: {str(e)}"
        )


async def transcribe_audio_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start the audio transcription process by requesting an audio file."""
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group."
        )
        return

    # Check if there's already an active transcription
    active_transcription = db.get_active_transcription()
    if active_transcription:
        # Calculate time elapsed
        elapsed_time = datetime.now(timezone.utc) - active_transcription.start_time
        elapsed_minutes = elapsed_time.total_seconds() / 60

        await update.message.reply_text(
            f"❌ Another transcription is already in progress:\n"
            f"🎵 File: {os.path.basename(active_transcription.file_path)}\n"
            f"⏱️ Time elapsed: {elapsed_minutes:.1f} minutes\n\n"
            "Please wait for it to complete or check status with /check_transcription_status"
        )
        return

    # Send message requesting audio file
    message = await update.message.reply_text(
        "Please reply to this message with the audio file you want to transcribe."
    )

    # Store the message ID in user_data for reference
    context.user_data["transcribe_request_id"] = message.message_id


async def check_transcription_status_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Check the status of any ongoing transcription."""
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group."
        )
        return

    # Get active transcription
    active_transcription = db.get_active_transcription()

    if not active_transcription:
        await update.message.reply_text(
            "No audio is being transcribed or processed currently."
        )
        return

    # Calculate time elapsed
    elapsed_time = datetime.now(timezone.utc) - active_transcription.start_time
    elapsed_minutes = elapsed_time.total_seconds() / 60

    # Format status message based on state
    if active_transcription.is_fully_completed:
        await update.message.reply_text(
            "No audio is being transcribed or processed currently."
        )
        return
    elif not active_transcription.is_completed:
        status_msg = (
            f"🎵 Transcribing file: {os.path.basename(active_transcription.file_path)}\n"
            f"Progress: {active_transcription.percentage:.1f}%\n"
            f"Time elapsed: {elapsed_minutes:.1f} minutes"
        )
    elif active_transcription.is_extracting_insights:
        status_msg = (
            f"✅ Transcription completed for: {os.path.basename(active_transcription.file_path)}\n"
            f"🔄 Now extracting discussion insights...\n"
            f"Time elapsed: {elapsed_minutes:.1f} minutes"
        )
    else:
        status_msg = (
            f"✅ Transcription completed for: {os.path.basename(active_transcription.file_path)}\n"
            f"⏳ Preparing to extract discussion insights...\n"
            f"Time elapsed: {elapsed_minutes:.1f} minutes"
        )

    await update.message.reply_text(status_msg)


TRANSCRIBE_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "audio_transcribe.py"
)


async def _transcribe_in_subprocess(file_path: str, progress_callback=None) -> None:
    """Run whisperx in a child process so all model/torch memory is returned
    to the OS when it exits (prevents RSS creep in the long-lived bot)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        TRANSCRIBE_SCRIPT,
        "--transcribe",
        file_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async for line in proc.stdout:
        text = line.decode(errors="replace").strip()
        if text.startswith("PROGRESS:") and progress_callback:
            try:
                _, done, total = text.split(":")
                progress_callback(int(done), int(total))
            except ValueError:
                pass
    stderr_output = await proc.stderr.read()
    return_code = await proc.wait()
    if return_code != 0:
        raise RuntimeError(
            "transcription subprocess failed with code "
            f"{return_code}: {stderr_output.decode(errors='replace')[-500:]}"
        )


async def process_transcription(
    bot, chat_id, file_path: str, base_filename: str, processing_msg
) -> None:
    """Process transcription in background."""
    try:
        # Start tracking transcription
        db.start_transcription(file_path)

        # Create a progress callback
        def progress_callback(current: int, total: int):
            percentage = (current / total) * 100
            db.update_transcription_progress(file_path, percentage)

        loop = asyncio.get_running_loop()

        # Transcribe in a subprocess — whisper/torch memory is freed on exit
        await _transcribe_in_subprocess(file_path, progress_callback)

        # Mark transcription as complete but keep tracking for insights
        db.complete_transcription(file_path)

        # Define transcription output path
        transcription_file = f"./transcriptions/{base_filename}_transcription.txt"

        try:
            # Create uploader instance
            uploader = GoogleDriveUploader()

            # Upload to Google Drive using event loop
            upload_result = await loop.run_in_executor(
                None,
                lambda: uploader.upload_file(
                    transcription_file, GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID
                ),
            )

            if upload_result:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Transcription completed and uploaded to Google Drive!\nLink: {upload_result.get('webViewLink')}\n\n🔄 Now extracting discussion insights...",
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="❌ Failed to upload transcription to Google Drive",
                )

            # Start insight extraction
            db.start_insight_extraction(file_path)

            # Generate insights - run in executor
            try:
                await loop.run_in_executor(
                    None,
                    lambda: transcriber.extract_discussion_insight(transcription_file),
                )

                # Get the base filename from transcription file path
                transcription_base = os.path.splitext(
                    os.path.basename(transcription_file)
                )[0]
                if transcription_base.endswith("_transcription"):
                    base_filename = transcription_base[:-14]  # remove '_transcription'

                docx_path = f"./discussion_insights/{base_filename}_insights.docx"

                # Upload file to Google Drive if it exists
                upload_results = []
                if os.path.exists(docx_path):
                    try:
                        result = await loop.run_in_executor(
                            None,
                            lambda: uploader.upload_file(
                                docx_path, GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID
                            ),
                        )
                        if result:
                            upload_results.append(
                                (os.path.basename(docx_path), result.get("webViewLink"))
                            )
                    except Exception as e:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"❌ Failed to upload {os.path.basename(docx_path)}: {str(e)}",
                        )

                # Send success message with links if any uploads succeeded
                if upload_results:
                    links_text = "\n".join(
                        [f"• {name}: {link}" for name, link in upload_results]
                    )
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"📊 Discussion insights generated and uploaded:\n{links_text}",
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="❌ Failed to generate or upload discussion insights",
                    )

            except Exception as e:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Error generating discussion insights: {str(e)}",
                )
                # Mark transcription as failed and cleanup
                db.complete_transcription(file_path, error=str(e))
                db.complete_insight_extraction(file_path)
                raise e

        except Exception as e:
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Transcription completed but failed to upload to Google Drive: {str(e)}",
            )
            # Mark transcription as failed and cleanup
            db.complete_transcription(file_path, error=str(e))
            db.complete_insight_extraction(file_path)
            raise e

        # Delete the processing message
        await processing_msg.delete()

    except Exception as e:
        # Mark transcription as failed and cleanup
        db.complete_transcription(file_path, error=str(e))
        db.complete_insight_extraction(file_path)
        # Edit the processing message to show error
        try:
            await processing_msg.edit_text(f"❌ Error during transcription: {str(e)}")
        except Exception:
            # If editing fails, try to send a new message
            await bot.send_message(
                chat_id=chat_id, text=f"❌ Error during transcription: {str(e)}"
            )
        raise e
    finally:
        # Always ensure we complete insight extraction status
        db.complete_insight_extraction(file_path)


_downloader_client = None


async def _get_downloader_client():
    """Shared Pyrogram client for large downloads — created once and reused
    instead of being rebuilt (leaking sockets/sessions) on every download."""
    global _downloader_client
    if _downloader_client is None:
        _downloader_client = Client(
            "audio_downloader_temp",
            api_id=TELEGRAM_API_KEY,
            api_hash=TELEGRAM_HASH,
            bot_token=BOT_TOKEN,
        )
    if not _downloader_client.is_connected:
        await _downloader_client.start()
    return _downloader_client


async def download_large_file(
    message_id: int, chat_id: int, file_path: str, progress_callback=None
) -> bool:
    """Download large file using Pyrogram asynchronously, with throttled progress updates."""

    # Get main running loop
    try:
        main_loop = asyncio.get_running_loop()
    except RuntimeError:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)

    try:
        client = await _get_downloader_client()

        # Get the message
        message = await client.get_messages(chat_id=chat_id, message_ids=message_id)
        if not message:
            logger.error("Could not find message to download")
            return False

        # Throttle variables
        last_update_time = 0
        min_interval = 5.0  # seconds between updates

        # Progress callback wrapper (scheduled in main loop)
        def progress(current, total):
            nonlocal last_update_time
            now = time.time()

            # Only update if min_interval has passed or download is finished
            if progress_callback and (
                now - last_update_time >= min_interval or current == total
            ):
                last_update_time = now
                future = asyncio.run_coroutine_threadsafe(
                    progress_callback(current, total), main_loop
                )
                try:
                    future.result(timeout=1)
                except Exception:
                    pass

        # Download the file
        result = await client.download_media(
            message=message, file_name=file_path, progress=progress
        )

        return bool(result)

    except Exception as e:
        logger.error(f"Failed to download file using Pyrogram: {e}")
        return False


async def handle_audio_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle uploaded audio files for transcription."""
    # Only process in admin group and only if it's a reply
    if (
        update.effective_chat.id != ADMIN_GROUP_ID
        or not update.message.reply_to_message
    ):
        return

    # Check if this is a reply to our transcribe request
    request_id = context.user_data.get("transcribe_request_id")
    if not request_id or update.message.reply_to_message.message_id != request_id:
        return

    # Check if there's already an active transcription
    active_transcription = db.get_active_transcription()
    if active_transcription:
        await update.message.reply_text(
            "❌ Another transcription is already in progress. Please wait for it to complete."
        )
        return

    # Check if we have an audio file
    audio_file = None
    file_name = None

    if update.message.audio:
        audio_file = update.message.audio
        file_name = audio_file.file_name
    elif update.message.voice:
        audio_file = update.message.voice
        file_name = f"voice_{update.message.date.strftime('%Y%m%d_%H%M%S')}.ogg"
    elif update.message.document:
        # Check if document mime type is audio
        if (
            update.message.document.mime_type
            and update.message.document.mime_type.startswith("audio/")
        ):
            audio_file = update.message.document
            file_name = audio_file.file_name

    # Show error message for any non-audio reply
    if not audio_file:
        await update.message.reply_text(
            "❌ Please send a valid audio file. Supported formats:\n"
            "• Audio messages (voice)\n"
            "• Audio files (mp3, wav, etc.)\n"
            "• Audio documents\n\n"
            "Reply to my previous message with an audio file to start transcription."
        )
        return

    # Check file size against configured limit
    if audio_file.file_size > MAX_AUDIO_FILE_SIZE:
        size_in_mb = MAX_AUDIO_FILE_SIZE / (1024 * 1024)
        await update.message.reply_text(
            f"❌ Audio file is too large (over {size_in_mb:.0f} MB). Please use a smaller file."
        )
        return

    # Create necessary directories
    os.makedirs("./audios", exist_ok=True)
    os.makedirs("./transcriptions", exist_ok=True)
    os.makedirs("./discussion_insights", exist_ok=True)

    # Get base filename without extension for consistent naming
    base_filename = os.path.splitext(file_name)[0]
    file_path = f"./audios/{file_name}"

    try:
        # Try to get and download the file using Bot API first
        file = await context.bot.get_file(audio_file.file_id)
        await file.download_to_drive(file_path)
    except telegram.error.BadRequest as e:
        if "too big" in str(e).lower():
            # File is too big for Bot API, try Pyrogram
            progress_msg = await update.message.reply_text(
                "📥 File is larger than 20MB. Starting download using alternative method...\n"
                "Progress: 0%"
            )

            # Create progress callback
            async def progress_callback(current: int, total: int):
                try:
                    percentage = (current / total) * 100
                    await progress_msg.edit_text(
                        f"📥 Downloading large file...\nProgress: {percentage:.1f}%"
                    )
                except Exception:
                    # Ignore errors from too many updates
                    pass

            success = await download_large_file(
                message_id=update.message.message_id,
                chat_id=update.effective_chat.id,
                file_path=file_path,
                progress_callback=progress_callback,
            )

            if not success:
                await update.message.reply_text(
                    "❌ Failed to download the large file. Please try again or use a smaller file."
                )
                return

            # Delete progress message on success
            await progress_msg.delete()
        else:
            # Re-raise if it's a different BadRequest error
            raise e

    # Send processing message
    processing_msg = await update.message.reply_text("🎵 Processing audio file...")

    # Start transcription process in background
    asyncio.create_task(
        process_transcription(
            context.bot,
            update.effective_chat.id,
            file_path,
            base_filename,
            processing_msg,
        )
    )

    await update.message.reply_text(
        "🎵 Started transcription in background. You can use /check_transcription_status to check progress."
    )


async def handle_target_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message:
        return
    if not update.message.text:
        return

    message_data = {
        "message_id": update.message.message_id,
        "chat_id": update.effective_chat.id,
        "user_id": update.effective_user.id if update.effective_user else None,
        "username": (update.effective_user.username if update.effective_user else None),
        "first_name": (
            update.effective_user.first_name if update.effective_user else None
        ),
        "text": update.message.text,
        "date": (update.message.date.isoformat() if update.message.date else None),
    }

    asyncio.create_task(process_enrichment(message_data, context.bot))


async def handle_admin_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message:
        return
    if not update.message.text:
        return

    message_data = {
        "message_id": update.message.message_id,
        "chat_id": update.effective_chat.id,
        "user_id": update.effective_user.id if update.effective_user else None,
        "username": (update.effective_user.username if update.effective_user else None),
        "first_name": (
            update.effective_user.first_name if update.effective_user else None
        ),
        "text": update.message.text,
        "date": (update.message.date.isoformat() if update.message.date else None),
    }

    asyncio.create_task(
        process_enrichment(message_data, context.bot, group_type="admin")
    )


async def enable_enrichment_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_enrichment_state(is_enabled=True)
    await update.message.reply_text("AI context enrichment enabled.")


async def disable_enrichment_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_enrichment_state(is_enabled=False)
    await update.message.reply_text("AI context enrichment disabled.")


async def enrichment_status_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    target_state = db.get_enrichment_state()
    admin_state = db.get_admin_enrichment_state()
    ctx_state = db.get_context_window_enrichment_state()
    target_status = "enabled" if target_state["is_enabled"] else "disabled"
    admin_status = "enabled" if admin_state["is_enabled_admin"] else "disabled"
    ctx_status = "enabled" if ctx_state["is_context_window_enabled"] else "disabled"
    await update.message.reply_text(
        f"AI enrichment status:\n"
        f"• Target group: {target_status}\n"
        f"• Admin group: {admin_status}\n"
        f"• Context window trigger: {ctx_status}\n\n"
        f"Last processed message ID (target): {target_state['last_processed_message_id']}"
    )


async def enable_admin_insights_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_admin_enrichment_state(is_enabled=True)
    await update.message.reply_text("AI context enrichment enabled for admin group.")


async def disable_admin_insights_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_admin_enrichment_state(is_enabled=False)
    await update.message.reply_text("AI context enrichment disabled for admin group.")


async def enable_context_enrichment_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_context_window_enrichment_state(is_enabled=True)
    await update.message.reply_text("Context window AI enrichment trigger enabled.")


async def disable_context_enrichment_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_context_window_enrichment_state(is_enabled=False)
    await update.message.reply_text(
        "Context window AI enrichment trigger disabled. Only URL-based enrichment active."
    )


def reset_active_transcriptions():
    """Reset all active transcriptions in the database."""
    try:
        active_transcription = db.get_active_transcription()
        if active_transcription:
            # Just mark the existing transcription as completed with error
            db.complete_transcription(
                active_transcription.file_path,
                error="Application restarted - Previous transcription was incomplete",
            )
            db.complete_insight_extraction(active_transcription.file_path)

            # Log the reset
            logger.info(
                f"Reset incomplete transcription for file: {active_transcription.file_path}"
            )
    except Exception as e:
        logger.error(f"Failed to reset active transcriptions: {str(e)}")


async def _rss_monitor_loop() -> None:
    """Log process RSS hourly so memory growth is visible in the log early."""
    while True:
        await asyncio.sleep(3600)
        try:
            with open("/proc/self/status") as f:
                rss_mb = next(
                    int(line.split()[1]) // 1024
                    for line in f
                    if line.startswith("VmRSS:")
                )
            logger.info(f"[monitor] process RSS: {rss_mb} MB")
        except Exception:
            pass


def main() -> None:
    """Start the bot."""
    require_onboarding_gifs()

    # Reset any active transcriptions from previous runs
    reset_active_transcriptions()

    # Create necessary directories
    os.makedirs("./audios", exist_ok=True)
    os.makedirs("./transcriptions", exist_ok=True)
    os.makedirs("./discussion_insights", exist_ok=True)

    # Create application
    application = Application.builder().token(BOT_TOKEN).build()

    # Set up commands for admin group
    admin_commands = [
        ("help", "Show admin commands and features"),
        ("export", "Export user data to CSV"),
        ("stats", "Show current statistics"),
        ("invite_main", "Invite a sub group member to the main group"),
        ("transcribe_audio", "Transcribe an audio file"),
        ("check_transcription_status", "Check status of ongoing transcription"),
        ("enable_ai_insights", "Enable AI enrichment (target group)"),
        ("disable_ai_insights", "Disable AI enrichment (target group)"),
        ("insights_status", "Check AI enrichment status (all groups)"),
        ("enable_admin_ai_insights", "Enable AI enrichment (admin group)"),
        ("disable_admin_ai_insights", "Disable AI enrichment (admin group)"),
        ("enable_ai_context_window", "Enable context window AI trigger"),
        ("disable_ai_context_window", "Disable context window AI trigger"),
    ]
    # Set up commands for regular users
    user_commands = [
        ("start", "Start the join process or get invite link"),
        ("help", "Show available commands and how to join"),
    ]

    # Register commands with their descriptions
    async def post_init(app: Application) -> None:
        """Post initialization hook to set up commands."""
        # First, delete any existing commands from all scopes to hide command interface
        await app.bot.delete_my_commands()  # Clear default scope
        await app.bot.delete_my_commands(
            scope=BotCommandScopeChat(chat_id=ADMIN_GROUP_ID)
        )  # Clear admin group scope
        await app.bot.delete_my_commands(
            scope=BotCommandScopeAllPrivateChats()
        )  # Clear private chats scope

        # Set admin commands (only visible in admin group)
        await app.bot.set_my_commands(
            [
                BotCommand(command, description)
                for command, description in admin_commands
            ],
            scope=BotCommandScopeChat(chat_id=ADMIN_GROUP_ID),
        )

        # Set user commands (only visible in private chats)
        await app.bot.set_my_commands(
            [
                BotCommand(command, description)
                for command, description in user_commands
            ],
            scope=BotCommandScopeAllPrivateChats(),  # Only show in private chats
        )

        app.create_task(
            user_sweep_loop(_get_downloader_client, app.bot)
        )
        app.create_task(_rss_monitor_loop())
        # Warm animation file_ids so /start and /serendip reuse Telegram CDN
        # instead of re-uploading (buttons + GIF arrive in one fast send).
        app.create_task(
            warm_onboarding_animations(
                BOT_TOKEN,
                ADMIN_GROUP_ID,
                [SISC_WELCOME_ANIM, SERENDIP_WELCOME_ANIM],
                None,
            )
        )
        await attach_forward_client(app)

    async def post_shutdown(app: Application) -> None:
        await close_forward_client(app)

    # Add post init callback
    application.post_init = post_init
    application.post_shutdown = post_shutdown

    # Create command filters
    private_chat_filter = filters.ChatType.PRIVATE
    admin_group_filter = filters.Chat(chat_id=ADMIN_GROUP_ID)

    # Fan-out every update to serendip after local handlers (shared bot token).
    application.add_handler(
        TypeHandler(Update, forward_update_to_serendip),
        group=99,
    )

    # Add handlers with appropriate filters
    application.add_handler(CommandHandler("start", start, filters=private_chat_filter))
    application.add_handler(
        CommandHandler(
            "serendip", serendip_command, filters=private_chat_filter
        )
    )
    application.add_handler(
        CommandHandler(
            "help", help_command, filters=private_chat_filter | admin_group_filter
        )
    )
    application.add_handler(
        CommandHandler("export", export_data, filters=admin_group_filter)
    )
    application.add_handler(
        CommandHandler("stats", stats_command, filters=admin_group_filter)
    )
    application.add_handler(
        CommandHandler("invite_main", invite_main_command, filters=admin_group_filter)
    )
    application.add_handler(
        CommandHandler(
            "reset_pending_approval",
            reset_pending_approval_command,
            filters=admin_group_filter,
        )
    )
    application.add_handler(
        CommandHandler(
            "transcribe_audio", transcribe_audio_command, filters=admin_group_filter
        )
    )
    application.add_handler(
        CommandHandler(
            "check_transcription_status",
            check_transcription_status_command,
            filters=admin_group_filter,
        )
    )
    application.add_handler(
        CommandHandler(
            "enable_ai_insights", enable_enrichment_command, filters=admin_group_filter
        )
    )
    application.add_handler(
        CommandHandler(
            "disable_ai_insights", disable_enrichment_command, filters=admin_group_filter
        )
    )
    application.add_handler(
        CommandHandler(
            "insights_status", enrichment_status_command, filters=admin_group_filter
        )
    )
    application.add_handler(
        CommandHandler(
            "enable_admin_ai_insights",
            enable_admin_insights_command,
            filters=admin_group_filter,
        )
    )
    application.add_handler(
        CommandHandler(
            "disable_admin_ai_insights",
            disable_admin_insights_command,
            filters=admin_group_filter,
        )
    )
    application.add_handler(
        CommandHandler(
            "enable_ai_context_window",
            enable_context_enrichment_command,
            filters=admin_group_filter,
        )
    )
    application.add_handler(
        CommandHandler(
            "disable_ai_context_window",
            disable_context_enrichment_command,
            filters=admin_group_filter,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=TARGET_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
            handle_target_group_message,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=ADMIN_GROUP_ID)
            & filters.TEXT
            & ~filters.COMMAND
            & ~filters.REPLY,
            handle_admin_group_message,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=list(TARGET_GROUP_IDS))
            & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            announce_new_member,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=TARGET_GROUP_ID)
            & filters.StatusUpdate.PINNED_MESSAGE,
            handle_pinned_message,
        )
    )
    application.add_handler(
        ChatMemberHandler(
            handle_chat_member_update, chat_member_types=ChatMemberHandler.CHAT_MEMBER
        )
    )

    # Add message handlers
    application.add_handler(
        CallbackQueryHandler(
            handle_admin_decision, pattern=r"^(approve_main|approve_sub|reject)_"
        )
    )
    # application.add_handler(CallbackQueryHandler(handle_survey_back_navigation, pattern=r'^survey_back$'))
    application.add_handler(
        CallbackQueryHandler(handle_apply_join, pattern=rf"^{APPLY_JOIN_CALLBACK}$")
    )
    application.add_handler(CallbackQueryHandler(handle_survey_option_selection))

    # Handler for rejection reasons - only in admin group and must be a reply
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=ADMIN_GROUP_ID)
            & filters.REPLY
            & filters.TEXT
            & ~filters.COMMAND,
            handle_rejection_reason,
        )
    )

    # Handler for audio transcription replies - catch all types of replies
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=ADMIN_GROUP_ID)
            & filters.REPLY
            & filters.ALL
            & ~filters.COMMAND,
            handle_audio_upload,
        )
    )

    # Handler for survey responses - must be last to not interfere with other handlers
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND, handle_survey_response
        )
    )

    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
