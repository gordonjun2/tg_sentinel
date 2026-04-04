import asyncio
from telegram import Bot, Poll
from config import BOT_TOKEN, TARGET_GROUP_ID


async def test_bot():
    """Test function to send a message and poll to admin group."""
    try:
        # Initialize bot
        bot = Bot(BOT_TOKEN)

        # # Send a test message
        # message = await bot.send_message(
        #     chat_id=TARGET_GROUP_ID,
        #     text=
        #     "Thanks for joining our last Super-Individual Secret Club session — your energy made it 🔥🧠\n\n",
        #     parse_mode="Markdown")

        # Send a test poll
        poll = await bot.send_poll(
            chat_id=TARGET_GROUP_ID,
            question=
            "Hi Super-Individuals!\nWhich topic would you like to explore in our next session? (multi-select)",
            options=[
                "🫧 AI & OBE (Out-of-Body Experience)",
                "💋 AI & Beauty", 
                "🧸 AI & Toy",
                "Others (please let us know)"
            ],
            is_anonymous=False,  # Make poll non-anonymous
            allows_multiple_answers=True)

        print("✅ Successfully sent test message and poll!")
        # print(f"Message ID: {message.message_id}")
        print(f"Poll ID: {poll.message_id}")

    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
    finally:
        # Close the bot connection
        await bot.close()


if __name__ == "__main__":
    # Run the test function
    asyncio.run(test_bot())
