import os
from datetime import datetime, timezone

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

from database import UserData, UserState
from bot import begin_apply_survey, resolve_start_branch
from onboarding_copy import survey_question_markdown
from config import SURVEY_QUESTIONS


def _user(*, state: UserState, current_question: int, answers: dict) -> UserData:
    return UserData(
        user_id=1,
        username="tester",
        state=state,
        current_question=current_question,
        answers=dict(answers),
        join_datetime=datetime.now(timezone.utc),
        invite_links=[],
    )


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


def test_begin_apply_survey_eligible_idle_resets_and_starts_q1():
    user = _user(state=UserState.IDLE, current_question=3, answers={"0": "old"})
    started = begin_apply_survey(user)
    assert started is not None
    assert started.state == UserState.IN_SURVEY
    assert started.current_question == 0
    assert started.answers == {}
    q1 = survey_question_markdown(0, len(SURVEY_QUESTIONS), SURVEY_QUESTIONS[0])
    assert "Question 1 of" in q1
    assert SURVEY_QUESTIONS[0] in q1


def test_begin_apply_survey_eligible_rejected_resets():
    user = _user(state=UserState.REJECTED, current_question=2, answers={"0": "x"})
    started = begin_apply_survey(user)
    assert started is not None
    assert started.state == UserState.IN_SURVEY
    assert started.current_question == 0
    assert started.answers == {}


def test_begin_apply_survey_pending_does_not_reset():
    answers = {"0": "keep"}
    user = _user(
        state=UserState.PENDING_APPROVAL, current_question=5, answers=answers
    )
    assert begin_apply_survey(user) is None
    assert user.state == UserState.PENDING_APPROVAL
    assert user.current_question == 5
    assert user.answers == {"0": "keep"}


def test_begin_apply_survey_in_survey_does_not_reset():
    answers = {"0": "partial"}
    user = _user(state=UserState.IN_SURVEY, current_question=2, answers=answers)
    assert begin_apply_survey(user) is None
    assert user.state == UserState.IN_SURVEY
    assert user.current_question == 2
    assert user.answers == {"0": "partial"}


def test_begin_apply_survey_approved_does_not_reset():
    answers = {"0": "done"}
    user = _user(state=UserState.APPROVED, current_question=4, answers=answers)
    assert begin_apply_survey(user) is None
    assert user.state == UserState.APPROVED
    assert user.current_question == 4
    assert user.answers == {"0": "done"}
