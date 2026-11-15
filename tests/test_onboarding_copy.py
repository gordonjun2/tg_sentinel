from onboarding_copy import (
    INTRO_MARKDOWN,
    SERENDIP_PITCH_MARKDOWN,
    approved_invite_markdown,
    member_followup_markdown,
    pending_approval_markdown,
    rejection_markdown,
    serendip_gate_markdown,
    serendip_member_followup_markdown,
    survey_complete_markdown,
    survey_question_markdown,
)


def test_intro_is_sisc_only_no_serendip_product():
    assert "SISC" in INTRO_MARKDOWN
    assert "sisc.club" in INTRO_MARKDOWN
    assert "## Serendip" not in INTRO_MARKDOWN
    assert "Mini App" not in INTRO_MARKDOWN
    assert "SISC access first" not in INTRO_MARKDOWN


def test_serendip_pitch_has_product_and_gate():
    assert "## Serendip" in SERENDIP_PITCH_MARKDOWN
    assert "Mini App" in SERENDIP_PITCH_MARKDOWN
    assert "SISC" in SERENDIP_PITCH_MARKDOWN


def test_public_status_copy_omits_serendip_cta():
    assert "Serendip" not in survey_complete_markdown()
    assert "Serendip" not in approved_invite_markdown("https://t.me/+x")
    assert "Serendip" not in member_followup_markdown()
    assert "Serendip" not in pending_approval_markdown()
    assert "Serendip" not in rejection_markdown("Nope")


def test_serendip_command_copy():
    assert "join SISC" in serendip_gate_markdown().lower() or "SISC" in serendip_gate_markdown()
    assert "/start" in serendip_gate_markdown()
    assert "Serendip" in serendip_member_followup_markdown()


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
