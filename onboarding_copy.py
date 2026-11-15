INTRO_MARKDOWN = """# Welcome

## SISC
The Super-Individual Secret Club is a tight-knit community for curious minds who create the future with AI. Founders, investors, operators, researchers, and artists gather here to stay ahead and collaborate with intention.

**What we stand for**
- **Urgency** — stay AI-ready as the space moves fast
- **A safe space** — explore hard topics privately
- **Serendipity** — meet brilliant minds and prototype the future

[Learn more about SISC](https://www.sisc.club/)
"""

SERENDIP_PITCH_MARKDOWN = """# Serendip

## Serendip
Serendip is a Telegram Mini App that helps the right people find each other. Share your personality, goals, and intentions, and AI surfaces meaningful matches for you.

**To use Serendip, you need SISC access first.**
"""


def survey_question_markdown(
    index: int, total: int, question: str, text_reply_hint: bool = True
) -> str:
    hint = (
        "_Reply with your answer as text._"
        if text_reply_hint
        else "_Choose an option below._"
    )
    return f"## Question {index + 1} of {total}\n\n{question}\n\n{hint}"


def survey_complete_markdown() -> str:
    return (
        "## Application received\n\n"
        "Thanks for completing the questions.\n\n"
        "**What happens next**\n"
        "- Admins review your answers\n"
        "- You get a DM when approved or rejected\n"
        "- After you join SISC, send /start again if you need the group link"
    )


def pending_approval_markdown() -> str:
    return (
        "## Pending review\n\n"
        "Your join request is waiting for admin review.\n\n"
        "You will get a DM when there is an update."
    )


def approved_invite_markdown(invite_link: str) -> str:
    return (
        "## You are approved\n\n"
        "Welcome to SISC. Use this one-time invite to join the group:\n\n"
        f"{invite_link}\n\n"
        "After you join, send /start again if you need help finding the group."
    )


def rejection_markdown(reason: str) -> str:
    return (
        "## Request not approved\n\n"
        f"**Reason:** {reason}\n\n"
        "You can send /start again later if you want to re-apply."
    )


def member_followup_markdown() -> str:
    return (
        "## You are in\n\n"
        "Jump back to the SISC group below."
    )


def serendip_gate_markdown() -> str:
    return (
        "## SISC access required\n\n"
        "Serendip is for SISC members.\n\n"
        "Send /start to apply or check your join status."
    )


def serendip_member_followup_markdown() -> str:
    return (
        "## Launch Serendip\n\n"
        "Open the Mini App below, or jump back to the SISC group."
    )
