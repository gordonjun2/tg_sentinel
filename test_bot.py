import asyncio
from telegram import Bot, Poll
from config import BOT_TOKEN, ADMIN_GROUP_ID, TARGET_GROUP_ID


async def test_bot():
    """Test function to send a message and poll to admin group."""
    try:
        # Initialize bot
        bot = Bot(BOT_TOKEN)

        # Send a test message
        message = await bot.send_message(
            chat_id=TARGET_GROUP_ID,
            text=
            "Thanks for joining our last Super-Individual Secret Club session — your energy made it 🔥🧠\n\n"
            "Next one's coming up, and we want your vote for the next theme.\n\n"
            "👇 Vote below:",
            parse_mode="Markdown")

        # Send a test poll
        poll = await bot.send_poll(
            chat_id=TARGET_GROUP_ID,
            question=
            "Which topic would you like to explore in our next session?",
            options=[
                "💸 AI & Universal Basic Income (UBI)", "🔮 AI & Divination",
                "🤖 Embodied AI", "Others (please let us know)"
            ],
            is_anonymous=False,  # Make poll non-anonymous
            allows_multiple_answers=False)

        print("✅ Successfully sent test message and poll!")
        print(f"Message ID: {message.message_id}")
        print(f"Poll ID: {poll.message_id}")

    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
    finally:
        # Close the bot connection
        await bot.close()


if __name__ == "__main__":
    # Run the test function
    asyncio.run(test_bot())
