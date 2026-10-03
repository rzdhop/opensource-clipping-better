"""Phase 6 stage 12: the clip controls (preview, state/route badges, Animate /
Keep still / Re-animate-with-note, the native-audio toggle) must render only
at tier >= 2 -- a tier-1 story's episode page must look and behave exactly
as it did before clips existed (plan 11's phase-6 brief) -- and a disabled
clip control must show its reason as visible text, never only inside a
``title`` attribute (the F8 pattern, phase 5 stage 13b, carried forward: a
``title`` shows nothing on a phone).

Text contracts over StoryboardPane.jsx, like the rest of the dashboard test
suite (DEC-012: stdlib + pytest only, no JS runner -- the dashboard tests in
this repo read JSX source as text).
"""

import re
from pathlib import Path

PANE = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "pages" / "story" / "episode"
STORYBOARD = PANE / "StoryboardPane.jsx"


def _src():
    return STORYBOARD.read_text(encoding="utf-8")


def test_the_clip_block_exists_and_is_gated_on_tier_2_or_more():
    """A tier-1 episode must render no clip block at all: the component that
    draws it is only ever reached behind a `tier >= 2` check."""
    src = _src()
    assert "function ShotClipBlock" in src, "the clip block component is missing"
    assert re.search(r"tier >= 2[\s\S]{0,200}<ShotClipBlock", src), (
        "ShotClipBlock must be rendered behind a `tier >= 2` guard"
    )


def test_a_blocked_clip_regenerate_shows_its_reason_as_text_not_only_a_title():
    """`clip.blocked` / the continue sentence must reach a plain text node
    (e.g. a form-hint paragraph), not only a `title=` attribute."""
    src = _src()
    assert "clip.blocked" in src, "the clip block must read the server's own refusal sentence (clip.blocked)"
    assert re.search(r"\{regenerateReason\}", src), (
        "the clip block's blocking reason must be rendered as visible text (e.g. {regenerateReason} in a "
        "paragraph), not only inside a title attribute -- a title shows nothing on a phone"
    )


def test_a_failed_or_stale_clip_shows_its_own_reason_as_text():
    """Browser-check finding F1: `clip.reason` (the provider's own failure
    text, e.g. "HTTP 500 from fal…") was never rendered anywhere -- not even
    a title -- for a failed or stale clip. It must now reach a plain text
    node, keyed off the clip's own state (not only the regenerate control's
    separate blocked reason)."""
    src = _src()
    assert "clip.reason" in src, "the clip block must read the server's own failure reason (clip.reason)"
    assert re.search(r"\{clipStatusReason\}", src), (
        "the clip's own status reason (clip.reason for failed/stale, or the continue sentence) must be rendered "
        "as visible text under the badges, not only inside a title attribute"
    )
    assert re.search(r"state === 'failed'[\s\S]{0,40}state === 'stale'[\s\S]{0,20}clip\.reason", src) or \
        re.search(r"state === 'stale'[\s\S]{0,40}state === 'failed'[\s\S]{0,20}clip\.reason", src), (
        "clip.reason must be shown specifically when the clip's state is failed or stale"
    )


def test_the_storyboards_own_keep_still_checkbox_is_hidden_at_tier_2_or_more():
    """Browser-check finding F3: a tier >= 2 shot card used to show TWO
    keep-still controls -- the storyboard's own (`saveKeepStill`, which
    clears the storyboard approval) and the clip block's new one (an
    assets.json override, which does not). The storyboard's own checkbox
    must now be hidden behind `tier < 2`; at tier 1 it is unconditional, as
    it always was (ShotCard has no `ShotClipBlock` there at all)."""
    src = _src()
    match = re.search(r"checked=\{shot\.keep_still\}", src)
    assert match, "the storyboard's own Keep still checkbox (shot.keep_still) is missing"
    before = src[max(0, match.start() - 200):match.start()]
    assert "tier < 2" in before, (
        "the storyboard's own Keep still checkbox must be gated behind `tier < 2`, "
        "now that the clip block offers its own at tier >= 2"
    )


def test_the_clip_pin_buttons_reflect_the_effective_state():
    """Browser-check finding F3: Animate/Keep still must show the EFFECTIVE
    flag (`clip.flags`, which already folds the storyboard's own) as
    pressed -- `aria-pressed` plus a visible mark -- and toggle it off (null)
    on a second press, rather than always pinning `true`."""
    src = _src()
    assert "aria-pressed={Boolean(clip.flags.animate)}" in src
    assert "aria-pressed={Boolean(clip.flags.keep_still)}" in src
    assert "animate: null" in src and "keep_still: null" in src, (
        "a second press must be able to clear the override (send null), not just set it"
    )


def test_the_assets_header_never_shows_a_bare_unknown_route_chip():
    """Browser-check finding F4: `estimate.images.route_class` is null once
    every shot already has its image (nothing left to route) -- RouteChip's
    own fallback then rendered a bare "unknown" chip. It must now be gated
    on the route_class being present."""
    src = _src()
    assert re.search(r"estimate\.images\.route_class &&\s*\(?\s*<RouteChip", src), (
        "the Assets header's images RouteChip must be gated on estimate.images.route_class"
    )


def test_the_assets_header_shows_the_clip_estimate_separately_with_its_own_readiness():
    """Browser-check finding F4: the clip count used to be folded into the
    same "est. $x" chip as the images/voices total, which does not include
    the clips whenever `video.ready` is false (allow_paid off, over a cap,
    ...) -- reading as if the clips were free. The video part must get its
    own chip that says so."""
    src = _src()
    assert "estimate.video.count" in src and "not now" in src, (
        "the clip chip must distinguish video.ready (priced) from not-ready (\"est $x, not now\")"
    )
    assert re.search(r"\{estimate\.video\.ready\s*\n\s*\?", src), (
        "the clip chip's wording must branch on estimate.video.ready"
    )


def test_the_image_offer_banner_exists_with_a_confirmation_and_is_not_tier_gated():
    """A-087's sticky image-link offer applies at ANY tier (image
    generation exists from tier 1), unlike the video offer and every clip
    control which are tier >= 2 only -- so, unlike `ShotClipBlock`, its
    render call site must NOT sit behind the `tier >= 2` guard. Its switch
    must be confirmed first (the kit's confirm dialog, DEC-253), the same
    pattern as the video offer's own."""
    src = _src()
    assert "function ImageOfferBanner" in src, "the image offer banner is missing"
    assert re.search(r"function ImageOfferBanner[\s\S]*?await confirm\(\{", src), (
        "the image offer's switch must be confirmed before it is sent, like the video offer's"
    )
    assert re.search(r"<ImageOfferBanner\b", src), "ImageOfferBanner must be rendered on the page"
    assert not re.search(r"tier >= 2[\s\S]{0,300}<ImageOfferBanner", src), (
        "ImageOfferBanner must not be gated behind the clip controls' tier >= 2 check -- "
        "the image link offer applies at tier 1 too"
    )
