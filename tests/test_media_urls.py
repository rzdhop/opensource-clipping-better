"""Signed media URLs: the pure sign/verify/mint layer.

Context. A `<video src>` and an `<a href download>` are requests the BROWSER
makes, not fetch() calls, so they cannot carry the Authorization header every
/api/outputs/ route requires. That is why, after a successful 7-clip render, the
player showed nothing, the Download button saved a `.json` (the 401 body, whose
application/json type made the browser rewrite the extension) and a pasted URL
said the file was not available.

These tests cover the helpers only -- no FastAPI, no TestClient -- so they run in
the pytest-only CI environment. The dependency wiring that consumes them is
tested in tests/test_auth_token.py, where the negative battery lives.
"""

import re

import pytest

from web.api import auth

TOKEN = "test-token-not-a-real-one"
JOB = "2773bd83c7b6"
CLIP = "highlight_rank_1_ready.mp4"
NOW = 1_700_000_000.0


def mint(job=JOB, filename=CLIP, *, now=NOW, ttl=None):
    return auth.media_url(job, filename, token=TOKEN, now=now, ttl=ttl)


def parts(url):
    """``(exp, sig)`` out of a minted URL."""
    exp = int(re.search(r"[?&]exp=(\d+)", url).group(1))
    sig = re.search(r"[?&]sig=([0-9a-f]+)", url).group(1)
    return exp, sig


def check(url, job=JOB, filename=CLIP, *, now=NOW):
    exp, sig = parts(url)
    return auth.media_signature_is_valid(job, filename, exp, sig, token=TOKEN, now=now)


# ------------------------------------------------------------------ round trip

def test_a_minted_url_verifies():
    assert check(mint()) is True


def test_a_minted_url_points_at_the_file_it_signs():
    url = mint()
    assert url.startswith(f"/api/outputs/{JOB}/{CLIP}?")


def test_the_token_is_not_in_the_url():
    """The whole point of the design: the credential still never travels in a
    URL. What travels is an HMAC over one file, keyed by a DERIVED value."""
    url = mint()
    assert TOKEN not in url
    assert auth._media_key(TOKEN).hex() not in url


# ------------------------------------------------------------------- rejection

def test_a_signature_for_one_clip_does_not_open_another():
    """The failure this prevents: one leaked URL becoming a key to the whole
    output directory."""
    exp, sig = parts(mint(filename="highlight_rank_1_ready.mp4"))
    assert auth.media_signature_is_valid(
        JOB, "highlight_rank_2_ready.mp4", exp, sig, token=TOKEN, now=NOW
    ) is False


def test_a_signature_for_one_job_does_not_open_another():
    exp, sig = parts(mint(job="aaaaaaaaaaaa"))
    assert auth.media_signature_is_valid(
        "bbbbbbbbbbbb", CLIP, exp, sig, token=TOKEN, now=NOW
    ) is False


def test_an_expired_signature_is_refused():
    exp, sig = parts(mint())
    assert auth.media_signature_is_valid(
        JOB, CLIP, exp, sig, token=TOKEN, now=exp + 1
    ) is False


def test_expiry_is_exclusive_at_the_boundary():
    """exp is the moment it stops working, not the last moment it works."""
    exp, sig = parts(mint())
    assert auth.media_signature_is_valid(
        JOB, CLIP, exp, sig, token=TOKEN, now=exp - 1
    ) is True
    assert auth.media_signature_is_valid(
        JOB, CLIP, exp, sig, token=TOKEN, now=exp
    ) is False


def test_a_tampered_expiry_does_not_validate_with_the_original_signature():
    """Extending the life of a URL by editing the query string must fail."""
    exp, sig = parts(mint())
    assert auth.media_signature_is_valid(
        JOB, CLIP, exp + 86400, sig, token=TOKEN, now=NOW
    ) is False


def test_another_token_cannot_verify():
    exp, sig = parts(mint())
    assert auth.media_signature_is_valid(
        JOB, CLIP, exp, sig, token="a-different-token", now=NOW
    ) is False


@pytest.mark.parametrize("exp", ["", None, "later", "1e9", "12.5", "0x10", " ", "--1"])
def test_a_non_integer_expiry_is_refused_not_raised(exp):
    """Never allow on error. A malformed exp is a rejection, not an exception
    that some caller might catch into a default of True."""
    _, sig = parts(mint())
    assert auth.media_signature_is_valid(JOB, CLIP, exp, sig, token=TOKEN, now=NOW) is False


@pytest.mark.parametrize("job,filename,sig", [
    ("", CLIP, "abc"),
    (JOB, "", "abc"),
    (JOB, CLIP, ""),
    (None, CLIP, "abc"),
    (JOB, None, "abc"),
    (JOB, CLIP, None),
])
def test_an_empty_component_never_validates(job, filename, sig):
    assert auth.media_signature_is_valid(job, filename, 2 ** 31, sig, token=TOKEN, now=NOW) is False


def test_the_payload_is_length_prefixed_so_triples_cannot_collide():
    """Without length prefixes, ("a|b", "c") and ("a", "b|c") would sign to the
    same bytes, and a signature for one file would open the other."""
    left = auth.sign_media("a|b", "c", 1, token=TOKEN)
    right = auth.sign_media("a", "b|c", 1, token=TOKEN)
    assert left != right


# ------------------------------------------------------------------- bucketing

def test_two_mints_inside_one_bucket_are_byte_identical():
    """The property the player depends on. Handing a <video> a new `src` tears
    down and restarts playback and throws away the cached bytes, and the
    dashboard re-fetches the job on every re-render -- so the URL for a given
    file has to be stable, not freshly stamped."""
    assert mint(now=NOW) == mint(now=NOW + 60)


def test_the_url_changes_once_the_bucket_rolls_over():
    """Otherwise it would never expire at all."""
    ttl = 3600
    bucket = ttl // 2
    early = mint(now=NOW, ttl=ttl)
    later = mint(now=NOW + bucket + bucket, ttl=ttl)
    assert early != later


def test_the_effective_lifetime_stays_between_half_the_ttl_and_the_ttl():
    ttl = 3600
    for offset in range(0, ttl, 137):
        exp = auth.media_expiry(now=NOW + offset, ttl=ttl)
        remaining = exp - (NOW + offset)
        assert ttl / 2 <= remaining <= ttl


# ------------------------------------------------------------------------- ttl

def test_the_default_ttl_is_twelve_hours():
    assert auth.media_url_ttl({}) == 12 * 3600


def test_the_ttl_is_configurable():
    assert auth.media_url_ttl({"MEDIA_URL_TTL": "3600"}) == 3600


@pytest.mark.parametrize("raw", ["", "  ", "abc", "None", "-1", "0", "30"])
def test_a_useless_ttl_falls_back_to_the_default(raw):
    """A TTL shorter than MIN_MEDIA_URL_TTL could expire while the page that
    minted the URL is still loading, which is indistinguishable from the bug
    this feature fixes."""
    assert auth.media_url_ttl({"MEDIA_URL_TTL": raw}) == auth.DEFAULT_MEDIA_URL_TTL


# ------------------------------------------------------------------- filenames

def test_a_filename_needing_escaping_still_round_trips():
    """The signature covers the DECODED filename, so the percent-encoding in the
    URL cannot desync from what was signed. Signing request.url.path instead
    would 401 on exactly these names and on no others."""
    awkward = "highlight rank 1 (final) é.mp4"
    url = auth.media_url(JOB, awkward, token=TOKEN, now=NOW)
    assert " " not in url
    exp, sig = parts(url)
    assert auth.media_signature_is_valid(
        JOB, awkward, exp, sig, token=TOKEN, now=NOW
    ) is True
