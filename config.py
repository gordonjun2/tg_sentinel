import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment variables")

# Group IDs
ADMIN_GROUP_ID = os.getenv('ADMIN_GROUP_ID')
if not ADMIN_GROUP_ID:
    raise ValueError("ADMIN_GROUP_ID not found in environment variables")
try:
    ADMIN_GROUP_ID = int(ADMIN_GROUP_ID)
except ValueError:
    raise ValueError("ADMIN_GROUP_ID must be a valid integer")

TARGET_GROUP_ID = os.getenv('TARGET_GROUP_ID')
if not TARGET_GROUP_ID:
    raise ValueError("TARGET_GROUP_ID not found in environment variables")
try:
    TARGET_GROUP_ID = int(TARGET_GROUP_ID)
except ValueError:
    raise ValueError("TARGET_GROUP_ID must be a valid integer")

# Survey questions
SURVEY_QUESTIONS = [
    "What's your name?", "What's your age?",
    "Why do you want to join this group?", "How did you hear about us?",
    "What do you hope to contribute to the community?"
]
