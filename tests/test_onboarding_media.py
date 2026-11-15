from onboarding_media import (
    SERENDIP_WELCOME_GIF,
    SISC_HERO_GIF,
    require_onboarding_gifs,
)


def test_onboarding_gifs_exist():
    require_onboarding_gifs()
    assert SISC_HERO_GIF.is_file()
    assert SERENDIP_WELCOME_GIF.is_file()
    assert SISC_HERO_GIF.stat().st_size > 0
    assert SERENDIP_WELCOME_GIF.stat().st_size > 0
