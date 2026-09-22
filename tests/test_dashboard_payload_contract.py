"""The dashboard must only send job fields the backend actually declares.

``JobCreateRequest`` is default-strict and ``routes/jobs.py`` turns it into the
job payload with ``model_dump()``, so a key the model does not declare is
dropped there and the control that sent it silently does nothing. That has
happened twice on the backend side, and once on this side: the dashboard shipped
a "YouTube Subs" toggle sending ``use_dlp_subs`` long after the flag had been
purged from the CLI, so the switch did nothing at all.

``tests/test_web_job_fields.py`` guards the backend half of that contract. This
file guards the front-end half, plus the promise that Clone & Rerun reproduces a
job rather than resetting half of it.

No npm: a JS test runner would be a new dependency and would not run in CI,
which installs pytest and nothing else (DEC-012). The page keeps its payload in
one object literal with one key per line precisely so it can be read as text.
"""

import ast
import pathlib
import re

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
NEW_JOB = PROJECT_ROOT / "web/dashboard/src/pages/NewJob.jsx"
MODELS = PROJECT_ROOT / "web/api/models.py"
DASHBOARD_SRC = PROJECT_ROOT / "web/dashboard/src"

# Fields the page sends that belong to the source/reuse block rather than to
# `jobFields`; they are spread in conditionally because an absent key and an
# empty one mean different things to the backend (DEC-015).
SOURCE_FIELDS = {
    "upload_filename",
    "transcript_filename",
    "transcript_offset",
    "source_url",
    "reuse_job_id",
}


def _job_fields() -> set[str]:
    """Keys from the page's ``jobFields`` object literal."""
    src = NEW_JOB.read_text(encoding="utf-8")
    match = re.search(r"const jobFields = \{(.*?)\n      \}", src, re.DOTALL)
    assert match, "jobFields object literal not found in NewJob.jsx"

    # `clips,` (shorthand) and `font_style: fontStyle,` both yield the key.
    return set(re.findall(r"^\s{8}([a-z0-9_]+)[,:]", match.group(1), re.MULTILINE))


def _declared_fields() -> set[str]:
    """``JobCreateRequest`` field names, read without importing pydantic."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JobCreateRequest":
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError("JobCreateRequest not found in models.py")


def _restored_fields() -> set[str]:
    """Keys Clone & Rerun reads back off the stored job config."""
    src = NEW_JOB.read_text(encoding="utf-8")
    return set(re.findall(r"config\.([a-z0-9_]+) !== undefined", src))


# --------------------------------------------------------------- non-vacuity

def test_the_readers_see_something():
    """A broken regex would make every assertion below pass for free."""
    assert len(_job_fields()) > 15
    assert len(_declared_fields()) > 30
    assert len(_restored_fields()) > 15


# ------------------------------------------------------------- the contract

def test_every_field_the_page_sends_is_declared():
    undeclared = _job_fields() - _declared_fields()

    assert undeclared == set(), (
        "NewJob.jsx sends these keys but JobCreateRequest does not declare them, "
        f"so Pydantic drops them and the controls do nothing: {sorted(undeclared)}"
    )


def test_clone_and_rerun_restores_everything_the_page_sends():
    """Otherwise cloning a job quietly resets settings to their defaults."""
    # load_gemini_json is deliberately forced on when cloning, to reuse the
    # cached analysis, so it is not read back from the config.
    expected = _job_fields() - {"load_gemini_json"}
    missing = expected - _restored_fields()

    assert missing == set(), (
        "these fields are sent but not restored by Clone & Rerun, so a cloned "
        f"job silently loses them: {sorted(missing)}"
    )


def test_source_fields_are_not_duplicated_in_job_fields():
    """They are spread conditionally, because 'absent' and 'empty' differ."""
    assert _job_fields() & SOURCE_FIELDS == set()


# ----------------------------------------------------------- the dead toggle

@pytest.mark.parametrize("dead", ["use_dlp_subs", "useDlpSubs"])
def test_the_purged_youtube_subs_toggle_is_gone(dead):
    """It was removed from the CLI but kept sending from the dashboard, where
    it did nothing whatsoever."""
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in DASHBOARD_SRC.rglob("*.jsx")
        if dead in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"{dead} still referenced in {offenders}"


# ------------------------------------------------- the Settings page's half

SETTINGS_PAGE = PROJECT_ROOT / "web/dashboard/src/pages/Settings.jsx"
SETTINGS_STORE = PROJECT_ROOT / "web/api/settings_store.py"

# Read as text, never imported: importing web/api needs pydantic, which CI does
# not install, and an importorskip on a drift guard means it never runs in the
# one place that checks every push (DEC-012).


def _declared_settings_fields():
    """The `<name>_api_key` / `hf_token` fields SettingsRequest declares."""
    text = MODELS.read_text(encoding="utf-8")
    body = text[text.index("class SettingsRequest"):]
    body = body[: body.index("\nclass ")] if "\nclass " in body else body
    return set(re.findall(r"^\s{4}(\w+):\s", body, flags=re.M))


def _persisted_secret_env_names():
    text = SETTINGS_STORE.read_text(encoding="utf-8")
    block = text[text.index("SECRET_KEYS = frozenset({"):]
    block = block[: block.index("})")]
    return set(re.findall(r'"(\w+)"', block))


def test_every_secret_the_backend_accepts_has_a_field_on_the_settings_page():
    """The fourth instance of one pattern, and the guard that ends it.

    A field the backend declares with no control in the deployed UI has now
    bitten this project four times: the AI provider select that could not reach
    `chain`, `platform` (DEC-051), `nvidia_model`, and the four chain provider
    keys. The last one is why a real job had a single point of failure — Groq is
    the first and fastest link in the default chain, the backend has accepted
    `groq_api_key` since the chain landed, and there was no box to type it into.

    Scoped to secrets that are BOTH declared by SettingsRequest and persisted,
    which is exactly the set a user can be expected to enter here.
    """
    page = SETTINGS_PAGE.read_text(encoding="utf-8")
    declared = _declared_settings_fields()
    persisted = _persisted_secret_env_names()

    missing = []
    for field in sorted(declared):
        if field.upper() not in persisted:
            continue
        # The page must both send it and report whether it is already set,
        # or the control is there and tells the user nothing.
        if f"payload.{field} =" not in page:
            missing.append(f"{field} (no input sends it)")
        elif f"{field}_set" not in page:
            missing.append(f"{field} (no SetBadge reads {field}_set)")

    assert not missing, (
        "The backend accepts these but the Settings page cannot set them:\n  "
        + "\n  ".join(missing)
    )


def test_the_settings_page_only_sends_fields_the_backend_declares():
    """The reverse direction: a key the model does not declare is dropped."""
    page = SETTINGS_PAGE.read_text(encoding="utf-8")
    declared = _declared_settings_fields()
    sent = set(re.findall(r"payload\.(\w+)\s*=", page))
    unknown = sorted(sent - declared)
    assert not unknown, f"Settings.jsx sends fields SettingsRequest does not declare: {unknown}"
