import logging
import csv
from io import StringIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)
from telegram.constants import ParseMode
from config import (BOT_TOKEN, ADMIN_GROUP_ID, TARGET_GROUP_ID,
                    SURVEY_QUESTIONS, GOOGLE_DRIVE_MAIN_FOLDER_ID,
                    GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID,
                    GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID,
                    TELEGRAM_API_KEY, TELEGRAM_HASH, MAX_AUDIO_FILE_SIZE)
from database import db, UserState, UserData
import telegram
import pytz
from datetime import datetime, timezone
import asyncio
from upload_to_google_drive import GoogleDriveUploader
from audio_transcribe import AudioTranscriber
import os
import time
from pyrogram import Client, utils

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize transcriber
transcriber = AudioTranscriber()


# [Pyrogram] Monkey Patch
def get_peer_type(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"


utils.get_peer_type = get_peer_type


async def revoke_and_create_invite_link(bot, user_data: UserData) -> str:
    """Revoke user's previous invite links and create a new one."""
    try:
        # Revoke user's previous invite links
        for link_id in user_data.invite_links:
            try:
                await bot.revoke_chat_invite_link(TARGET_GROUP_ID, link_id)
            except telegram.error.BadRequest:
                # Skip if link is already revoked or invalid
                continue

        # Create new invite link with member limit of 1
        new_link = await bot.create_chat_invite_link(chat_id=TARGET_GROUP_ID,
                                                     member_limit=1)

        # Update user's invite links
        user_data.invite_links = [new_link.invite_link]
        db.update_user(user_data)

        return new_link.invite_link
    except telegram.error.BadRequest as e:
        if "rights to manage chat invite link" in str(e).lower():
            raise telegram.error.BadRequest(
                "Bot needs admin rights to manage invite links")
        raise e


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

    # Check if user is already in target group
    try:
        member = await context.bot.get_chat_member(TARGET_GROUP_ID, user_id)
        if member.status not in ['left', 'kicked', 'banned']:
            # Get chat info to get the link
            chat = await context.bot.get_chat(TARGET_GROUP_ID)
            if chat.username:  # Public group with username
                group_link = f"https://t.me/{chat.username}"
            else:  # Private group
                group_link = chat.invite_link  # This is the permanent invite link of the group, not a temporary one

            await update.message.reply_text(
                f"You are already a member of the group! Click here to open the chat: {group_link}"
            )
            return
    except Exception:
        # If we can't get member info, continue with the flow
        pass

    if user_data.state == UserState.APPROVED:
        try:
            # Revoke old links and generate new one
            invite_link = await revoke_and_create_invite_link(
                context.bot, user_data)
            await update.message.reply_text(
                f"You were already approved! Here's a new invite link to join: {invite_link}"
            )
        except telegram.error.BadRequest as e:
            if "rights to manage chat invite link" in str(e).lower():
                await update.message.reply_text(
                    "You were already approved! Please wait while I notify the admins to help you join."
                )
                # Notify admins about the missing permission and the pending user
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=
                    f"⚠️ Cannot create invite link for approved user {username or user_id} because bot "
                    f"needs admin rights in the target group. Please make the bot an admin or help the user join manually."
                )
            else:
                raise e
        return

    if user_data.state == UserState.PENDING_APPROVAL:
        await update.message.reply_text(
            "Your request is pending approval. Please wait for admin review.")
        return

    # Start the survey
    user_data.state = UserState.IN_SURVEY
    user_data.current_question = 0
    user_data.answers = {}
    db.update_user(user_data)

    await update.message.reply_text(
        "👋 Welcome to the *Super-Individual Secret Club*!\n"
        "🎯We're a space where sharp minds gather to stay AI-ready, challenge boundaries, and explore bold ideas shaping the future.\n\n"
        "To keep this circle intentional, we ask a few quick questions before letting you in.\n"
        "🧠 It won't take long — just helps us make sure the right people are in the room.\n"
        "👇 Let's begin:\n\n"
        f"Question 1: {SURVEY_QUESTIONS[0]}",
        parse_mode=ParseMode.MARKDOWN)


async def handle_survey_response(update: Update,
                                 context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle survey responses."""
    user_id = update.effective_user.id
    user_data = db.get_user(user_id)

    if not user_data or user_data.state != UserState.IN_SURVEY:
        return

    # Check if the message is text
    if not update.message.text:
        await update.message.reply_text(
            "Please send your answer as text only. Images, audio, or other media are not accepted.\n\n"
            f"Question {user_data.current_question + 1}: {SURVEY_QUESTIONS[user_data.current_question]}"
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
        await update.message.reply_text(
            "Thank you for completing the survey! Your request has been sent to the admins for review."
        )

        # Notify admin group
        survey_answers = "\n".join(f"{q}: {user_data.answers[q]}"
                                   for q in SURVEY_QUESTIONS)

        keyboard = [[
            InlineKeyboardButton("Approve",
                                 callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject_{user_id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=(f"New join request from {user_data.username or user_id}:\n\n"
                  f"{survey_answers}"),
            reply_markup=reply_markup)
    else:
        # Ask next question
        db.update_user(user_data)
        await update.message.reply_text(
            f"Question {user_data.current_question + 1}: "
            f"{SURVEY_QUESTIONS[user_data.current_question]}")


async def handle_admin_decision(update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin's approval/rejection."""
    query = update.callback_query
    await query.answer()

    # Extract decision and user_id from callback data
    action, user_id = query.data.split('_')
    user_id = int(user_id)
    user_data = db.get_user(user_id)

    if not user_data:
        await query.edit_message_text("User not found in database.")
        return

    # Check if user is already in target group
    try:
        member = await context.bot.get_chat_member(TARGET_GROUP_ID, user_id)
        if member.status not in ['left', 'kicked', 'banned']:
            await query.edit_message_text(
                "User is already a member of the target group.")
            return
    except Exception:
        # If we can't get member info, continue with the flow
        pass

    # If user is approved but hasn't joined yet, resend the invite
    if user_data.state == UserState.APPROVED:
        try:
            # Revoke old links and generate new one
            invite_link = await revoke_and_create_invite_link(
                context.bot, user_data)

            # Notify user with new link
            await context.bot.send_message(
                chat_id=user_id,
                text=
                f"Here's a new invite link to join the group: {invite_link}")
            await query.edit_message_text(
                f"User {user_data.username or user_id} was already approved. Sent new invite link."
            )
        except telegram.error.BadRequest as e:
            if "rights to manage chat invite link" in str(e).lower():
                # Send error as new message instead of editing
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=
                    "⚠️ Cannot create invite link because bot needs admin rights in the target group. "
                    "Please make the bot an admin first before approving requests."
                )
            else:
                raise e
        return

    if user_data.state != UserState.PENDING_APPROVAL and user_data.state != UserState.PENDING_REJECTION:
        await query.edit_message_text("This request is no longer valid.")
        return

    if action == "approve":
        # If user was in pending rejection, clean up the rejection message
        if user_data.state == UserState.PENDING_REJECTION and user_data.rejection_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=ADMIN_GROUP_ID,
                    message_id=user_data.rejection_message_id)
            except telegram.error.BadRequest:
                # Message might be already deleted, ignore
                pass
            user_data.rejection_message_id = None

        try:
            # Revoke old links and generate new one
            invite_link = await revoke_and_create_invite_link(
                context.bot, user_data)

            # If invite link creation successful, proceed with approval
            user_data.state = UserState.APPROVED
            db.update_user(user_data)

            # Notify user
            await context.bot.send_message(
                chat_id=user_id,
                text=
                f"Your join request has been approved! Click here to join: {invite_link}"
            )

            # Remove inline keyboard and update message text
            await query.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=
                f"Request from {user_data.username or user_id} has been approved."
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
                    text=f"❌ Error exporting/uploading data: {str(e)}")

        except telegram.error.BadRequest as e:
            if "rights to manage chat invite link" in str(e).lower():
                # Send error as new message instead of editing
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=
                    "⚠️ Cannot approve request because bot needs admin rights in the target group. "
                    "Please make the bot an admin first before approving requests."
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
            text=
            f"Please reply to this message with the reason for rejecting {user_data.username or user_id}'s request."
        )

        # Store both the reason request message ID and the original message ID
        user_data.state = UserState.PENDING_REJECTION
        user_data.rejection_message_id = reason_msg.message_id
        # Store the original message ID in the answers dict temporarily
        user_data.answers['original_message_id'] = query.message.message_id
        db.update_user(user_data)


async def handle_rejection_reason(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the rejection reason reply from admin."""
    # Only process messages in admin group that are replies
    if update.effective_chat.id != ADMIN_GROUP_ID or not update.message.reply_to_message:
        return

    # Get the replied-to message ID
    replied_msg_id = update.message.reply_to_message.message_id

    # Find user with this rejection_message_id
    all_users = db.get_all_users()
    user_data = next(
        (user
         for user in all_users if user.state == UserState.PENDING_REJECTION
         and user.rejection_message_id == replied_msg_id), None)

    if not user_data:
        return

    rejection_reason = update.message.text.strip()
    if not rejection_reason:
        await update.message.reply_text(
            "Please provide a valid rejection reason.")
        return

    # Get the original message ID from answers
    original_message_id = user_data.answers.pop('original_message_id', None)

    # Update user state
    user_data.state = UserState.REJECTED
    user_data.rejection_message_id = None
    db.update_user(user_data)

    # Notify user with reason
    await context.bot.send_message(
        chat_id=user_data.user_id,
        text=
        f"Sorry, your join request has been rejected.\nReason: {rejection_reason}"
    )

    # Remove inline keyboard from original message if we have its ID
    if original_message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=ADMIN_GROUP_ID,
                message_id=original_message_id,
                reply_markup=None)
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
        text=
        f"✅ Rejection completed for {user_data.username or user_data.user_id}\nReason: {rejection_reason}"
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
            text=f"❌ Error exporting/uploading data: {str(e)}")


async def export_data(
        update: Update = None,
        context: ContextTypes.DEFAULT_TYPE = None) -> tuple[str, str, str]:
    """Export all user data to CSV. Returns tuple of (csv_data, filename, csv_path)."""
    # Only allow command usage in admin group
    if update and update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group.")
        return None, None, None

    # Set timezone to Singapore (GMT+8)
    sg_tz = pytz.timezone('Asia/Singapore')

    # Create CSV in memory
    output = StringIO()
    csv_writer = csv.writer(output)

    # Write header
    headers = ['User ID', 'Username', 'State', 'Join Date (GMT+8)']
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
            user.username or 'None',
            user.state.value,
            sg_time.strftime('%Y-%m-%d %H:%M:%S GMT+8'
                             )  # Format datetime in Singapore timezone
        ]
        # Add answers in the same order as questions
        for question in SURVEY_QUESTIONS:
            row.append(user.answers.get(question, ''))

        csv_writer.writerow(row)

    # Set csv file name
    filename = 'sisc_user_data.csv'
    csv_path = filename  # Save in root directory

    # Get CSV data and save to file
    csv_data = output.getvalue()
    with open(csv_path, 'w') as f:
        f.write(csv_data)

    # If called as command, send the file
    if update and context:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=csv_data.encode(),
            filename=filename,
            caption='Here is the exported user data.')

    output.close()
    return csv_data, filename, csv_path


async def help_command(update: Update,
                       context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of available commands based on chat type."""
    chat_type = update.effective_chat.type

    if chat_type == "private":
        help_text = """
*Available Commands*

/start - Start the join process
• Complete the join survey
• Get your invite link (if approved)
• Check your request status

/help - Show this help message with command list

*How to Join*
1. Use /start to begin the process
2. Answer all survey questions
3. Wait for admin approval
4. Once approved, you'll receive an invite link

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


async def stats_command(update: Update,
                        context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current statistics."""
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group.")
        return

    # Get all users
    all_users = db.get_all_users()

    # Calculate stats
    total_users = len(all_users)
    pending_requests = sum(1 for user in all_users
                           if user.state == UserState.IN_SURVEY
                           or user.state == UserState.PENDING_APPROVAL)
    approved_users = sum(1 for user in all_users
                         if user.state == UserState.APPROVED)
    rejected_users = sum(1 for user in all_users
                         if user.state == UserState.REJECTED)

    stats_text = f"""
*Bot Statistics*

Total Users: {total_users}
Pending Requests: {pending_requests}
Approved Users: {approved_users}
Rejected Users: {rejected_users}
"""

    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)


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
            None, lambda: uploader.upload_file(csv_path, folder_id))

        if upload_result:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=
                f"✅ Successfully uploaded {csv_path} to Google Drive\nLink: {upload_result.get('webViewLink')}"
            )
        else:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"❌ Failed to upload {csv_path} to Google Drive")

    except Exception as e:
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"❌ Error uploading to Google Drive: {str(e)}")


async def transcribe_audio_command(update: Update,
                                   context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the audio transcription process by requesting an audio file."""
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group.")
        return

    # Check if there's already an active transcription
    active_transcription = db.get_active_transcription()
    if active_transcription:
        # Calculate time elapsed
        elapsed_time = datetime.now(
            timezone.utc) - active_transcription.start_time
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
    context.user_data['transcribe_request_id'] = message.message_id


async def check_transcription_status_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check the status of any ongoing transcription."""
    # Only allow in admin group
    if update.effective_chat.id != ADMIN_GROUP_ID:
        await update.message.reply_text(
            "This command can only be used in the admin group.")
        return

    # Get active transcription
    active_transcription = db.get_active_transcription()

    if not active_transcription:
        await update.message.reply_text(
            "No audio is being transcribed or processed currently.")
        return

    # Calculate time elapsed
    elapsed_time = datetime.now(timezone.utc) - active_transcription.start_time
    elapsed_minutes = elapsed_time.total_seconds() / 60

    # Format status message based on state
    if active_transcription.is_fully_completed:
        await update.message.reply_text(
            "No audio is being transcribed or processed currently.")
        return
    elif not active_transcription.is_completed:
        status_msg = (
            f"🎵 Transcribing file: {os.path.basename(active_transcription.file_path)}\n"
            f"Progress: {active_transcription.percentage:.1f}%\n"
            f"Time elapsed: {elapsed_minutes:.1f} minutes")
    elif active_transcription.is_extracting_insights:
        status_msg = (
            f"✅ Transcription completed for: {os.path.basename(active_transcription.file_path)}\n"
            f"🔄 Now extracting discussion insights...\n"
            f"Time elapsed: {elapsed_minutes:.1f} minutes")
    else:
        status_msg = (
            f"✅ Transcription completed for: {os.path.basename(active_transcription.file_path)}\n"
            f"⏳ Preparing to extract discussion insights...\n"
            f"Time elapsed: {elapsed_minutes:.1f} minutes")

    await update.message.reply_text(status_msg)


async def process_transcription(bot, chat_id, file_path: str,
                                base_filename: str, processing_msg) -> None:
    """Process transcription in background."""
    try:
        # Start tracking transcription
        db.start_transcription(file_path)

        # Create a progress callback
        def progress_callback(current: int, total: int):
            percentage = (current / total) * 100
            db.update_transcription_progress(file_path, percentage)

        # Process the file with progress tracking - run in executor to not block
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: transcriber.transcribe(
                file_path, progress_callback=progress_callback))

        # Mark transcription as complete but keep tracking for insights
        db.complete_transcription(file_path)

        # Define transcription output path
        transcription_file = f"./transcriptions/{base_filename}_transcription.txt"

        try:
            # Create uploader instance
            uploader = GoogleDriveUploader()

            # Upload to Google Drive using event loop
            upload_result = await loop.run_in_executor(
                None, lambda: uploader.upload_file(
                    transcription_file, GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID))

            if upload_result:
                await bot.send_message(
                    chat_id=chat_id,
                    text=
                    f"✅ Transcription completed and uploaded to Google Drive!\nLink: {upload_result.get('webViewLink')}\n\n🔄 Now extracting discussion insights..."
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="❌ Failed to upload transcription to Google Drive")

            # Start insight extraction
            db.start_insight_extraction(file_path)

            # Generate insights - run in executor
            try:
                await loop.run_in_executor(
                    None, lambda: transcriber.extract_discussion_insight(
                        transcription_file))

                # Get the base filename from transcription file path
                transcription_base = os.path.splitext(
                    os.path.basename(transcription_file))[0]
                if transcription_base.endswith('_transcription'):
                    base_filename = transcription_base[:
                                                       -14]  # remove '_transcription'

                docx_path = f"./discussion_insights/{base_filename}_insights.docx"

                # Upload file to Google Drive if it exists
                upload_results = []
                if os.path.exists(docx_path):
                    try:
                        result = await loop.run_in_executor(
                            None, lambda: uploader.upload_file(
                                docx_path,
                                GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID))
                        if result:
                            upload_results.append((os.path.basename(docx_path),
                                                   result.get('webViewLink')))
                    except Exception as e:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=
                            f"❌ Failed to upload {os.path.basename(docx_path)}: {str(e)}"
                        )

                # Send success message with links if any uploads succeeded
                if upload_results:
                    links_text = "\n".join(
                        [f"• {name}: {link}" for name, link in upload_results])
                    await bot.send_message(
                        chat_id=chat_id,
                        text=
                        f"📊 Discussion insights generated and uploaded:\n{links_text}"
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=
                        "❌ Failed to generate or upload discussion insights")

            except Exception as e:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Error generating discussion insights: {str(e)}")
                # Mark transcription as failed and cleanup
                db.complete_transcription(file_path, error=str(e))
                db.complete_insight_extraction(file_path)
                raise e

        except Exception as e:
            await bot.send_message(
                chat_id=chat_id,
                text=
                f"✅ Transcription completed but failed to upload to Google Drive: {str(e)}"
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
            await processing_msg.edit_text(
                f"❌ Error during transcription: {str(e)}")
        except Exception:
            # If editing fails, try to send a new message
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error during transcription: {str(e)}")
        raise e
    finally:
        # Always ensure we complete insight extraction status
        db.complete_insight_extraction(file_path)


async def download_large_file(
    message_id: int,
    chat_id: int,
    file_path: str,
    progress_callback=None
) -> bool:
    """Download large file using Pyrogram asynchronously, with throttled progress updates."""

    # Get main running loop
    try:
        main_loop = asyncio.get_running_loop()
    except RuntimeError:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)

    try:
        async with Client(
            "audio_downloader_temp",
            api_id=TELEGRAM_API_KEY,
            api_hash=TELEGRAM_HASH,
            bot_token=BOT_TOKEN
        ) as client:

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
                if progress_callback and (now - last_update_time >= min_interval or current == total):
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
                message=message,
                file_name=file_path,
                progress=progress
            )

            return bool(result)

    except Exception as e:
        logger.error(f"Failed to download file using Pyrogram: {e}")
        return False


async def handle_audio_upload(update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle uploaded audio files for transcription."""
    # Only process in admin group and only if it's a reply
    if update.effective_chat.id != ADMIN_GROUP_ID or not update.message.reply_to_message:
        return

    # Check if this is a reply to our transcribe request
    request_id = context.user_data.get('transcribe_request_id')
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
        if update.message.document.mime_type and update.message.document.mime_type.startswith(
                'audio/'):
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
    os.makedirs('./audios', exist_ok=True)
    os.makedirs('./transcriptions', exist_ok=True)
    os.makedirs('./discussion_insights', exist_ok=True)

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
                "Progress: 0%")

            # Create progress callback
            async def progress_callback(current: int, total: int):
                try:
                    percentage = (current / total) * 100
                    await progress_msg.edit_text(
                        f"📥 Downloading large file...\n"
                        f"Progress: {percentage:.1f}%")
                except Exception:
                    # Ignore errors from too many updates
                    pass

            success = await download_large_file(
                message_id=update.message.message_id,
                chat_id=update.effective_chat.id,
                file_path=file_path,
                progress_callback=progress_callback)

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
    processing_msg = await update.message.reply_text(
        "🎵 Processing audio file...")

    # Start transcription process in background
    asyncio.create_task(
        process_transcription(context.bot, update.effective_chat.id, file_path,
                              base_filename, processing_msg))

    await update.message.reply_text(
        "🎵 Started transcription in background. You can use /check_transcription_status to check progress."
    )


def reset_active_transcriptions():
    """Reset all active transcriptions in the database."""
    try:
        active_transcription = db.get_active_transcription()
        if active_transcription:
            # Just mark the existing transcription as completed with error
            db.complete_transcription(
                active_transcription.file_path,
                error=
                "Application restarted - Previous transcription was incomplete"
            )
            db.complete_insight_extraction(active_transcription.file_path)

            # Log the reset
            logger.info(
                f"Reset incomplete transcription for file: {active_transcription.file_path}"
            )
    except Exception as e:
        logger.error(f"Failed to reset active transcriptions: {str(e)}")


def main() -> None:
    """Start the bot."""
    # Reset any active transcriptions from previous runs
    reset_active_transcriptions()

    # Create necessary directories
    os.makedirs('./audios', exist_ok=True)
    os.makedirs('./transcriptions', exist_ok=True)
    os.makedirs('./discussion_insights', exist_ok=True)

    # Create application
    application = Application.builder().token(BOT_TOKEN).build()

    # Set up commands for admin group
    admin_commands = [("help", "Show admin commands and features"),
                      ("export", "Export user data to CSV"),
                      ("stats", "Show current statistics"),
                      ("transcribe_audio", "Transcribe an audio file"),
                      ("check_transcription_status",
                       "Check status of ongoing transcription")]

    # Set up commands for regular users
    user_commands = [("start", "Start the join process or get invite link"),
                     ("help", "Show available commands and how to join")]

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
            scope=BotCommandScopeChat(chat_id=ADMIN_GROUP_ID))

        # Set user commands (only visible in private chats)
        await app.bot.set_my_commands(
            [
                BotCommand(command, description)
                for command, description in user_commands
            ],
            scope=BotCommandScopeAllPrivateChats(
            )  # Only show in private chats
        )

    # Add post init callback
    application.post_init = post_init

    # Create command filters
    private_chat_filter = filters.ChatType.PRIVATE
    admin_group_filter = filters.Chat(chat_id=ADMIN_GROUP_ID)

    # Add handlers with appropriate filters
    application.add_handler(
        CommandHandler("start", start, filters=private_chat_filter))
    application.add_handler(
        CommandHandler("help",
                       help_command,
                       filters=private_chat_filter | admin_group_filter))
    application.add_handler(
        CommandHandler("export", export_data, filters=admin_group_filter))
    application.add_handler(
        CommandHandler("stats", stats_command, filters=admin_group_filter))
    application.add_handler(
        CommandHandler("transcribe_audio",
                       transcribe_audio_command,
                       filters=admin_group_filter))
    application.add_handler(
        CommandHandler("check_transcription_status",
                       check_transcription_status_command,
                       filters=admin_group_filter))

    # Add message handlers
    application.add_handler(CallbackQueryHandler(handle_admin_decision))

    # Handler for rejection reasons - only in admin group and must be a reply
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=ADMIN_GROUP_ID) & filters.REPLY & filters.TEXT
            & ~filters.COMMAND, handle_rejection_reason))

    # Handler for audio transcription replies - catch all types of replies
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=ADMIN_GROUP_ID) & filters.REPLY & filters.ALL
            & ~filters.COMMAND, handle_audio_upload))

    # Handler for survey responses - must be last to not interfere with other handlers
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND,
                       handle_survey_response))

    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
