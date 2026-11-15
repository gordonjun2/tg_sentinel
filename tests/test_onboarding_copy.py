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
    assert "sisc.club" in INTRO_MARKDOWN
    assert "Super-Individual Secret Club" in INTRO_MARKDOWN
    assert "sharp minds" in INTRO_MARKDOWN
    assert "circle intentional" in INTRO_MARKDOWN
    assert "won't take long" in INTRO_MARKDOWN
    assert not INTRO_MARKDOWN.lstrip().startswith("# ")
    assert "What we stand for" not in INTRO_MARKDOWN
    assert "## Serendip" not in INTRO_MARKDOWN
    assert "Mini App" not in INTRO_MARKDOWN
    assert "SISC access first" not in INTRO_MARKDOWN


def test_serendip_pitch_has_product_without_gate():
    assert "# Serendip" in SERENDIP_PITCH_MARKDOWN
    assert "Mini App" in SERENDIP_PITCH_MARKDOWN
    assert "SISC access" not in SERENDIP_PITCH_MARKDOWN
    assert "found quietly" in SERENDIP_PITCH_MARKDOWN


def test_public_status_copy_omits_serendip_cta():
    assert "Serendip" not in survey_complete_markdown()
    assert "Serendip" not in approved_invite_markdown("https://t.me/+x")
    assert "Serendip" not in member_followup_markdown(has_group_link=True)
    assert "Serendip" not in member_followup_markdown(has_group_link=False)
    assert "Serendip" not in pending_approval_markdown()
    assert "Serendip" not in rejection_markdown("Nope")


def test_serendip_command_copy():
    gate = serendip_gate_markdown()
    assert "SISC" in gate
    assert "/start" in gate
    assert "/serendip" in gate
    followup = serendip_member_followup_markdown()
    assert "Mini App" in followup
    assert "##" not in followup


def test_survey_question_uses_one_based_of_total():
    text = survey_question_markdown(0, 3, "Why do you want to join?")
    assert "1 of 3" in text
    assert "**Why do you want to join?**" in text
    assert "*Reply with your answer as text.*" in text


def test_survey_question_omits_text_hint_for_button_questions():
    text = survey_question_markdown(
        5, 8, "How do you know about SISC?", text_reply_hint=False
    )
    assert "**How do you know about SISC?**" in text
    assert "*Choose an option below.*" in text
    assert "Reply with your answer as text." not in text


def test_rejection_includes_reason():
    text = rejection_markdown("Not a fit right now")
    assert "Not a fit right now" in text
    assert "Request not approved" in text
    assert "**Reason:**" in text


def test_survey_complete_and_pending_and_member_followup():
    complete = survey_complete_markdown()
    assert "Application received" in complete
    assert "*Thanks for completing the questions.*" in complete
    assert "Pending review" in pending_approval_markdown()
    with_link = member_followup_markdown(has_group_link=True)
    without_link = member_followup_markdown(has_group_link=False)
    assert "You are in" in with_link
    assert "Open the SISC group when you are ready." in with_link
    assert "You are in" in without_link
    assert "already a member" in without_link


def test_approved_invite_includes_link():
    link = "https://t.me/+example"
    text = approved_invite_markdown(link)
    assert link in text
    assert "You are approved" in text
    assert f"**{link}**" in text
