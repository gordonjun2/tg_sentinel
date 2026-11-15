from onboarding_media import (
    SERENDIP_WELCOME_ANIM,
    SISC_WELCOME_ANIM,
    require_onboarding_gifs,
)


def test_onboarding_gifs_exist():
    require_onboarding_gifs()
    assert SISC_WELCOME_ANIM.is_file()
    assert SERENDIP_WELCOME_ANIM.is_file()
    assert SISC_WELCOME_ANIM.stat().st_size > 0
    assert SERENDIP_WELCOME_ANIM.stat().st_size > 0
    assert SISC_WELCOME_ANIM.name == "sisc-welcome.mp4"
