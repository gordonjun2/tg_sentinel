INTRO_MARKDOWN = """# Welcome

## SISC
The Super-Individual Secret Club is a tight-knit community for curious minds who create the future with AI. Founders, investors, operators, researchers, and artists gather here to stay ahead and collaborate with intention.

**What we stand for**
- **Urgency** — stay AI-ready as the space moves fast
- **A safe space** — explore hard topics privately
- **Serendipity** — meet brilliant minds and prototype the future

[Learn more about SISC](https://www.sisc.club/)

---

## Serendip
Serendip helps you find people who matter inside Telegram, by intent, not by profile.

**To use Serendip, you need SISC access first.**
"""


def survey_question_markdown(index: int, total: int, question: str) -> str:
    # index is 0-based
    return (
        f"## Question {index + 1} of {total}\n\n"
        f"{question}\n\n"
        f"_Reply with your answer as text._"
    )


def survey_complete_markdown() -> str:
    return (
        "## Application received\n\n"
        "Thanks for completing the questions.\n\n"
        "**What happens next**\n"
        "- Admins review your answers\n"
        "- You get a DM when approved or rejected\n"
        "- After you join SISC, use /start again to open Serendip"
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
        "After you join, send /start again to launch Serendip."
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
        "Open Serendip below, or jump back to the SISC group."
    )
