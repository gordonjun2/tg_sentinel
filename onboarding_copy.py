INTRO_MARKDOWN = """# SISC

👋 Welcome to the **Super-Individual Secret Club**!

*We're a space where sharp minds gather to stay AI-ready, challenge boundaries, and explore bold ideas shaping the future.*

To keep this circle intentional, we ask a few quick questions before letting you in.

🧠 It won't take long — just helps us make sure the right people are in the room.

🌐 [Learn more about us](https://www.sisc.club/)
"""

SERENDIP_PITCH_MARKDOWN = """# Serendip

*A Telegram Mini App that helps the right people find each other.*

Share your personality, goals, and intentions, and AI surfaces meaningful matches for you.

**To use Serendip, you need SISC access first.**
"""


def survey_question_markdown(
    index: int, total: int, question: str, text_reply_hint: bool = True
) -> str:
    hint = (
        "*Reply with your answer as text.*"
        if text_reply_hint
        else "*Choose an option below.*"
    )
    return (
        f"## Question {index + 1} of {total}\n\n"
        f"**{question}**\n\n"
        f"{hint}"
    )


def survey_complete_markdown() -> str:
    return (
        "## Application received\n\n"
        "*Thanks for completing the questions.*\n\n"
        "**What happens next**\n"
        "- Admins review your answers\n"
        "- You get a DM when approved or rejected\n"
        "- After you join SISC, send /start again if you need the group link"
    )


def pending_approval_markdown() -> str:
    return (
        "## Pending review\n\n"
        "*Your join request is waiting for admin review.*\n\n"
        "You will get a DM when there is an update."
    )


def approved_invite_markdown(invite_link: str) -> str:
    return (
        "## You are approved\n\n"
        "Welcome to SISC. Use this one-time invite to join the group:\n\n"
        f"**{invite_link}**\n\n"
        "*After you join, send /start again if you need help finding the group.*"
    )


def rejection_markdown(reason: str) -> str:
    return (
        "## Request not approved\n\n"
        f"**Reason:** {reason}\n\n"
        "*You can send /start again later if you want to re-apply.*"
    )


def member_followup_markdown(*, has_group_link: bool) -> str:
    if has_group_link:
        return (
            "## You are in\n\n"
            "*Open the SISC group when you are ready.*"
        )
    return (
        "## You are in\n\n"
        "*You are already a member of the SISC group.*"
    )


def serendip_gate_markdown() -> str:
    return (
        "## SISC access required\n\n"
        "*Serendip is for SISC members.*\n\n"
        "Send /start to apply or check your join status."
    )


def serendip_member_followup_markdown() -> str:
    return (
        "## Launch Serendip\n\n"
        "*Open the Mini App below, or jump back to the SISC group.*"
    )


def approved_resume_markdown() -> str:
    return (
        "## You are approved\n\n"
        "*Send /start to get your one-time invite link.*"
    )


def survey_in_progress_markdown() -> str:
    return (
        "## Application in progress\n\n"
        "*Send /start to continue where you left off.*"
    )


def survey_buttons_only_prefix() -> str:
    return "**Please select an option from the buttons below.**\n\n"


def survey_text_only_prefix() -> str:
    return (
        "**Please send your answer as text only.**\n"
        "*Images, audio, or other media are not accepted.*\n\n"
    )
