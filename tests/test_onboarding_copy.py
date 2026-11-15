from onboarding_copy import (
    INTRO_MARKDOWN,
    approved_invite_markdown,
    member_followup_markdown,
    pending_approval_markdown,
    rejection_markdown,
    survey_complete_markdown,
    survey_question_markdown,
)


def test_intro_contains_serendip_sisc_access_and_club_link():
    assert "Serendip" in INTRO_MARKDOWN
    assert "SISC access" in INTRO_MARKDOWN
    assert "sisc.club" in INTRO_MARKDOWN


def test_survey_question_uses_one_based_of_total():
    text = survey_question_markdown(0, 3, "Why do you want to join?")
    assert "1 of 3" in text
    assert "Why do you want to join?" in text
    assert "Reply with your answer as text." in text


def test_survey_question_omits_text_hint_for_button_questions():
    text = survey_question_markdown(
        5, 8, "How do you know about SISC?", text_reply_hint=False
    )
    assert "How do you know about SISC?" in text
    assert "Choose an option below." in text
    assert "Reply with your answer as text." not in text


def test_rejection_includes_reason():
    text = rejection_markdown("Not a fit right now")
    assert "Not a fit right now" in text
    assert "Request not approved" in text


def test_survey_complete_and_pending_and_member_followup():
    assert "Application received" in survey_complete_markdown()
    assert "Pending review" in pending_approval_markdown()
    assert "You are in" in member_followup_markdown()


def test_approved_invite_includes_link():
    link = "https://t.me/+example"
    text = approved_invite_markdown(link)
    assert link in text
    assert "You are approved" in text
