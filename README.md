# Telegram Group Access Bot

A Telegram bot that manages group access through a survey and admin approval workflow.

## Features

- Survey-based group access request
- Admin approval workflow
- Automated group invitation management
- Pending request tracking

## Setup Instructions

1. Create a new bot using [@BotFather](https://t.me/botfather) on Telegram
2. Get your bot token from BotFather
3. Create a `.env` file with the following variables:
   ```
   BOT_TOKEN=your_bot_token_here
   ADMIN_GROUP_ID=your_admin_group_id
   TARGET_GROUP_ID=your_target_group_id
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Run the bot:
   ```bash
   python bot.py
   ```

## Usage

1. Users start the bot with `/start`
2. Bot guides users through a survey
3. Admins receive join requests in the admin group
4. Admins can approve/reject requests
5. Users receive the outcome and group invitation if approved

## Project Structure

- `bot.py`: Main bot implementation
- `survey.py`: Survey questions and logic
- `database.py`: User state and request tracking
- `config.py`: Configuration management
