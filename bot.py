import logging
import csv
from io import StringIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)
from telegram.constants import ParseMode
from config import BOT_TOKEN, ADMIN_GROUP_ID, TARGET_GROUP_ID, SURVEY_QUESTIONS, GOOGLE_DRIVE_FOLDER_ID
from database import db, UserState, UserData
import telegram
import pytz
from datetime import datetime
import asyncio
from upload_to_google_drive import GoogleDriveUploader

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)


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
        if "rights to manage chat invite link" in str(e):
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
            if "rights to manage chat invite link" in str(e):
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
            if "rights to manage chat invite link" in str(e):
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
            if "rights to manage chat invite link" in str(e):
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

/help - Show this help message

*How to Join*
1. Use /start to begin the process
2. Answer all survey questions
3. Wait for admin approval
4. Once approved, you'll receive an invite link

*Note*: Each invite link can only be used once and expires after use.
"""
    elif update.effective_chat.id == ADMIN_GROUP_ID:
        help_text = """
*Available Admin Commands*

/help - Show this help message
/export - Export all user data to CSV file
/stats - Show current statistics (total users, pending requests, etc.)

*Note*: These commands only work in this admin group.

*User Management*
Users can start the bot with /start in private chat to:
• Complete the join survey
• Get their invite link (if approved)
• Check their request status

*Note*: Approval/rejection is done via buttons on join requests.
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
        folder_id = GOOGLE_DRIVE_FOLDER_ID

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


def main() -> None:
    """Start the bot."""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()

    # Set up commands for admin group
    admin_commands = [("help", "Show admin commands and features"),
                      ("export", "Export user data to CSV"),
                      ("stats", "Show current statistics")]

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

    # Add message handlers
    application.add_handler(CallbackQueryHandler(handle_admin_decision))

    # Handler for rejection reasons - only in admin group and must be a reply
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=ADMIN_GROUP_ID) & filters.REPLY & filters.TEXT
            & ~filters.COMMAND, handle_rejection_reason))

    # Handler for survey responses - must be last to not interfere with other handlers
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND,
                       handle_survey_response))

    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
