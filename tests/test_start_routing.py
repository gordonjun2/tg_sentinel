import os

# Config/database import requires a full env; stub before importing bot.
_REQUIRED_ENV = {
    "BOT_TOKEN": "1:tok",
    "ADMIN_GROUP_ID": "-1001",
    "TARGET_GROUP_ID": "-1002",
    "TARGET_SUB_GROUP_ID": "-1003",
    "GOOGLE_DRIVE_MAIN_FOLDER_ID": "x",
    "GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID": "x",
    "GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID": "x",
    "GEMINI_API_KEY": "x",
    "TELEGRAM_API_KEY": "x",
    "TELEGRAM_HASH": "x",
    "FIRECRAWL_API_KEY": "x",
    "MINI_APP_URL": "https://serendip.sisc.club",
}
for _key, _value in _REQUIRED_ENV.items():
    os.environ.setdefault(_key, _value)

from database import UserState
from bot import resolve_start_branch


def test_resolve_start_branch_matrix():
    assert resolve_start_branch(is_member=True, state=UserState.IDLE) == "member"
    assert (
        resolve_start_branch(is_member=False, state=UserState.APPROVED)
        == "approved_invite"
    )
    assert (
        resolve_start_branch(is_member=False, state=UserState.PENDING_APPROVAL)
        == "pending"
    )
    assert (
        resolve_start_branch(is_member=False, state=UserState.PENDING_REJECTION)
        == "pending"
    )
    assert (
        resolve_start_branch(is_member=False, state=UserState.IN_SURVEY) == "in_survey"
    )
    assert resolve_start_branch(is_member=False, state=UserState.IDLE) == "apply"
    assert resolve_start_branch(is_member=False, state=UserState.REJECTED) == "apply"
