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

GOOGLE_DRIVE_MAIN_FOLDER_ID = os.getenv('GOOGLE_DRIVE_MAIN_FOLDER_ID')
if not GOOGLE_DRIVE_MAIN_FOLDER_ID:
    raise ValueError(
        "GOOGLE_DRIVE_MAIN_FOLDER_ID not found in environment variables")

GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID = os.getenv(
    'GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID')
if not GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID:
    raise ValueError(
        "GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID not found in environment variables"
    )

GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID = os.getenv(
    'GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID')
if not GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID:
    raise ValueError(
        "GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID not found in environment variables"
    )

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

TELEGRAM_API_KEY = os.getenv('TELEGRAM_API_KEY')
if not TELEGRAM_API_KEY:
    raise ValueError("TELEGRAM_API_KEY not found in environment variables")

TELEGRAM_HASH = os.getenv('TELEGRAM_HASH')
if not TELEGRAM_HASH:
    raise ValueError("TELEGRAM_HASH not found in environment variables")

# Survey questions
SURVEY_QUESTIONS = [
    "What's your name?", "Which company are you currently with?",
    "What is your current role or job title?",
    "Please share your LinkedIn profile.",
    "What's one AI tool you use regularly, and why do you find it valuable?",
    "How do you know about SISC?",
    "Who is your referral?"
]

WELCOME_BATCH_INTERVAL_MINUTES = 5

WELCOME_MESSAGES = [
    "🚀 Welcome to SISC, {names}!\n\nWe're glad to have you here. Drop a quick intro — tell us your name, what you do, and one AI tool you're loving right now!",
    "👋 Hey {names}, welcome to SISC!\n\nWe'd love to get to know you — share a brief intro about yourself and what brings you here!",
    "🎉 {names} just landed in SISC!\n\nTime for the fun part — introduce yourself! Name, role, and your current favorite AI hack.",
    "🔥 {names} joined the club!\n\nSay hi and tell us about yourself — what you're working on and what AI topics excite you most.",
    "✨ Welcome aboard, {names}!\n\nThe community would love to hear from you. Drop your intro: who you are, what you do, and why AI interests you!",
    "🌟 {names} is now part of SISC!\n\nDon't be shy — introduce yourself! Share your background and what you're hoping to learn here.",
    "🙌 Great to have you, {names}!\n\nGive us a quick intro — your name, what you're building, and one thing you'd love to learn about AI.",
    "🎯 {names} just entered the chat!\n\nWe'd love to know more about you. Share a short intro and let the conversations begin!",
    "💡 Welcome to the community, {names}!\n\nTell us about yourself — your background, current projects, and your go-to AI tool.",
    "🚀 {names} has arrived!\n\nJump in and introduce yourself! We're all friends here. Share what you do and what excites you about AI.",
    "🎓 Welcome to SISC, {names}!\n\nWe'd love to hear your story — drop a quick intro about who you are and what you're passionate about.",
    "⚡ {names} is here!\n\nTime to break the ice — tell us your name, your role, and the AI tool you can't live without.",
    "🌊 Welcome in, {names}!\n\nThe floor is yours! Introduce yourself and share what brought you to this community.",
    "🏆 {names} just leveled up by joining SISC!\n\nShare a quick intro — who you are, what you do, and your hottest AI take.",
    "👋 Welcome, {names}!\n\nWe're excited to meet you. Drop your intro and let us know what AI topics you're most curious about.",
    "🎭 {names} has entered the stage!\n\nIntroduce yourself to the crew — your background, interests, and what you hope to get out of SISC.",
    "🔮 {names} has joined SISC!\n\nSay hello and tell us about yourself! What are you working on and what AI trends are you following?",
    "🤝 Welcome to the network, {names}!\n\nShare a brief intro so we can all connect — your name, role, and what excites you in the AI space.",
    "📣 Everyone welcome {names}!\n\nWe'd love to hear from you — introduce yourself and share one interesting thing about your AI journey.",
    "🎊 {names} is now officially in SISC!\n\nDon't hold back — tell us who you are, what you do, and what you'd love to explore with AI.",
    "🦁 Welcome to the pride, {names}!\n\nRoar your intro — tell us who you are, what drives you, and how AI fits into your world!",
    "🎪 Step right up, {names}!\n\nThe spotlight is on you. Introduce yourself and share your most creative use of AI so far!",
    "🏠 {names} found home in SISC!\n\nMake yourself known — drop an intro with your name, your craft, and your boldest AI prediction.",
    "🌱 Welcome {names}!\n\nPlant your roots here. Introduce yourself and tell us what you're growing in the AI space!",
    "🎸 {names} has joined the jam!\n\nGrab the mic and introduce yourself — your vibe, your work, and the AI tool that rocks your world.",
    "🧠 Welcome to the think tank, {names}!\n\nShare your brain with us — intro yourself and tell us your most mind-blowing AI discovery.",
    "🪄 {names} just unlocked SISC!\n\nCast your intro spell — who are you, what do you do, and what AI magic are you conjuring?",
    "📊 Welcome, {names}!\n\nLet's see your data — introduce yourself and share one AI insight that changed how you think!",
    "🛸 {names} has teleported into SISC!\n\nBeam down your intro — tell us where you're from, what you do, and your AI mission!",
    "🎨 Welcome to the canvas, {names}!\n\nPaint us a picture of who you are and how you're using AI creatively in your work or life!",
    "🗺️ {names} has discovered SISC!\n\nChart your course — introduce yourself and share the AI frontier you're most excited to explore!",
    "🧲 Welcome, {names}!\n\nYou've been drawn here for a reason. Introduce yourself and tell us what pulls you toward AI!",
    "💬 {names} is now in the conversation!\n\nDon't just lurk — introduce yourself! Share your background and your take on where AI is heading.",
    "🔑 {names} unlocked access to SISC!\n\nThe key to this community is connection. Introduce yourself and tell us what you'd love to unlock with AI!",
    "🧭 Welcome to SISC, {names}!\n\nNavigate your way in — drop an intro and tell us what direction you see AI taking your career!",
    "🎬 {names}, you're on!\n\nLights, camera, intro! Tell us who you are and what AI storyline you're most excited about right now.",
    "🧪 Welcome to the lab, {names}!\n\nTime to experiment — introduce yourself and share the AI experiment you're most proud of!",
    "🦾 {names} just powered up in SISC!\n\nShow us your strength — drop an intro and tell us about the AI superpower you want to develop!",
    "🧩 {names} found the missing piece!\n\nYou fit right in. Introduce yourself and share how AI connects to what you do every day!",
    "🌋 {names} erupted into SISC!\n\nMake some noise — introduce yourself and share the AI topic that ignites your passion!",
    "🎯 {names}, welcome to the target!\n\nBullseye your intro — who are you, what's your focus, and what AI goal are you aiming for?",
    "🌍 Welcome to the SISC universe, {names}!\n\nIntroduce yourself and tell us — if you could solve one problem with AI, what would it be?",
    "🧬 {names} has been decoded!\n\nShare your DNA — intro yourself with your background, skills, and what AI evolution you're most excited about!",
    "🎪 Welcome to the show, {names}!\n\nStep into the ring and introduce yourself. What's your AI act? Tell us what you're working on!",
    "🔧 {names} just tuned in to SISC!\n\nGet your tools ready — introduce yourself and share the AI workflow you've been tweaking lately!",
    "🌌 {names} entered the SISC orbit!\n\nGround control says: introduce yourself! Tell us your mission and what AI galaxy you're exploring.",
    "🧊 {names}, welcome to SISC!\n\nBreak the ice — tell us who you are, what you do, and your coolest AI discovery this year!",
    "🎵 {names} is now on the SISC playlist!\n\nHit play on your intro — your name, your rhythm (what you do), and the AI beat you're vibing with!",
    "🏅 Welcome to the league, {names}!\n\nStep up and introduce yourself — your skills, your goals, and how AI is changing your game!",
    " {names} crossed into SISC territory!\n\nBridge the gap — drop your intro and share how you're building the future with AI!",
]

# File size limits (in bytes)
MAX_AUDIO_FILE_SIZE = 1024 * 1024 * 1024
