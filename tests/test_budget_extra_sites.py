"""Plan 23 stage A2: every budget check honours today's extra.

``budget.check(day_extra=)`` (stage A1) is only worth what its call sites pass.
A site that forgets it still refuses, so the failure is safe -- but it
refuses a call the user just allowed. The first test names every call site;
the rest run the three that show numbers (the link summary, the assets caps,
the voice estimate) with and without today's extra.
"""

import ast
import pathlib
from types import SimpleNamespace

import pytest

from clipping.aistory import voices
from clipping.aistory.steps import assets
from clipping.providers import budget as budget_mod
from clipping.providers import gating
from clipping.providers import generation as gen
from clipping.providers.generation import GenRequest, Link

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUDGET_MODULE = ROOT / "clipping" / "providers" / "budget.py"
ALIASES = {"budget_mod", "budget"}


@pytest.fixture(autouse=True)
def spend_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    budget_mod.reset()
    yield budget_mod.default_spend()
    budget_mod.reset()


# ------------------------------------------------------------------ call sites

def _sources():
    for top in ("clipping", "web"):
        for path in sorted((ROOT / top).rglob("*.py")):
            if path != BUDGET_MODULE:
                yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_every_budget_check_call_site_passes_day_extra():
    sites, missing = [], []
    for path, tree in _sources():
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "budget":
                # `from ...budget import check` would hide a call from the scan below.
                assert "check" not in {a.name for a in node.names}, f"{rel}:{node.lineno} imports check by name"
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "check" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ALIASES):
                continue
            sites.append(f"{rel}:{node.lineno}")
            if "day_extra" not in {kw.arg for kw in node.keywords}:
                missing.append(f"{rel}:{node.lineno}")
    assert not missing, f"budget check calls without day_extra=: {missing}"
    # The five known sites (gating x2, voices x2, assets x1); a rename that hides one fails here.
    assert len(sites) >= 5, sites


# ---------------------------------------------------------------- link_summary

FAL = Link("fal", "flux-schnell")


def _summary(cap_usd):
    request = GenRequest(kind=gen.IMAGE, prompt="a fruit", width=1024, height=1024)
    budget_obj = budget_mod.Budget(True, 100.0, cap_usd, 100.0, "free")
    return gating.link_summary(gen.IMAGE, FAL, {"FAL_KEY": "k"}, budget_obj, request, adapters={})


def test_link_summary_allows_with_today_extra(spend_file):
    est = _summary(100.0)["est_usd"]
    assert est > 0
    spend_file.add(1.0)
    cap = 1.0 + est / 2          # the saved cap cannot take this image
    refused = _summary(cap)
    assert refused["allowed"] is False
    assert refused["reason"].startswith("refused: est $") and "daily cap" in refused["reason"]
    assert "allowed today" not in refused["reason"]

    spend_file.add_extra(est)
    allowed = _summary(cap)
    assert allowed["allowed"] is True and allowed["reason"] is None
    assert allowed["est_usd"] == est

    # The extra is a number apart: the spent total is still the spent total.
    state = budget_mod.day_state()
    assert state.spent == pytest.approx(1.0) and state.extra == pytest.approx(est)

    # An extra that is still too small keeps the refusal, and the text names it.
    spend_file.clear_extra()
    spend_file.add_extra(est / 4)
    still = _summary(cap)
    assert still["allowed"] is False and "allowed today" in still["reason"]


def test_budget_check_runner_callable_reads_the_extra_each_call(spend_file):
    budget_obj = budget_mod.Budget(True, 100.0, 1.0, 100.0, "free")
    check = gating.budget_check(budget_obj)
    spend_file.add(0.9)
    with pytest.raises(budget_mod.BudgetRefused):
        check(0.3, None)
    spend_file.add_extra(0.5)
    check(0.3, None)             # the extra, added after the callable was built, counts


# ----------------------------------------------------------------- assets caps

class _Ledger:
    def totals(self, ep=None):
        return {"est_usd": 0.0}


ENV = {"ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "50", "DAILY_CAP_USD": "4", "PER_STORY_CAP_USD": "50"}


def test_assets_caps_left_counts_the_extra(spend_file):
    ec = SimpleNamespace(ep=1)
    spend_file.add(3.5)
    caps, over = assets.spending_caps(ec, 1.0, env=ENV, ledger=_Ledger())
    assert caps["day"] == {"cap_usd": 4.0, "spent_usd": 3.5, "left_usd": 0.5}      # no extra: unchanged shape
    assert over is not None and "would bring today to $4.50 of the $4.00 daily cap" in over
    assert "allowed today" not in over

    spend_file.add_extra(2.0)
    caps, over = assets.spending_caps(ec, 1.0, env=ENV, ledger=_Ledger())
    assert caps["day"] == {"cap_usd": 4.0, "spent_usd": 3.5, "left_usd": 2.5, "extra_usd": 2.0}
    assert caps["episode"]["left_usd"] == 50.0 and "extra_usd" not in caps["episode"]
    assert over is None

    caps, over = assets.spending_caps(ec, 3.0, env=ENV, ledger=_Ledger())          # 3.5 + 3.0 > 4.0 + 2.0
    assert over is not None and "of the $4.00 daily cap + $2.00 allowed today" in over


# --------------------------------------------------------------- voice estimate

class _Stories:
    def __init__(self, root):
        self.root = root

    def story_dir(self, story_id):
        return str(self.root / story_id)


class _TtsAdapter:
    def estimate(self, link, request):
        return 0.30


def _voice_verdict(tmp_path, monkeypatch, spend_file):
    link = voices._chain_link("gemini", "Kore")
    monkeypatch.setattr(gen, "is_paid", lambda candidate: True)   # Gemini's speech is free: price it anyway
    stories = _Stories(tmp_path / "stories")
    (tmp_path / "stories" / "s1").mkdir(parents=True, exist_ok=True)
    env = {"GOOGLE_API_KEY": "k", **ENV, "DAILY_CAP_USD": "1"}
    items = [({"provider": "gemini", "voice_id": "Kore"}, "Hello there", "Anna")]
    return voices.estimate_lines(stories, "s1", items, env=env, adapters={(gen.TTS, link.provider): _TtsAdapter()})


def test_voice_estimate_allows_with_today_extra(tmp_path, monkeypatch, spend_file):
    spend_file.add(0.9)                                  # $0.90 + $0.30 > the $1.00 cap
    refused = _voice_verdict(tmp_path, monkeypatch, spend_file)
    assert refused["ready"] is False
    assert "would bring today to $1.20 of the $1.00 daily cap" in refused["voices"][0]["reason"]
    assert "allowed today" not in refused["voices"][0]["reason"]

    spend_file.add_extra(0.5)
    allowed = _voice_verdict(tmp_path, monkeypatch, spend_file)
    assert allowed["ready"] is True, allowed
    assert allowed["voices"][0]["est_usd"] == 0.3 and allowed["voices"][0]["reason"] is None


# ---------------------------------------------------------- the agent run's caps

def test_agent_run_caps_count_the_extra(tmp_path, spend_file):
    from clipping.aistory import workflow

    stories = _Stories(tmp_path / "stories")
    story = {"story_id": "s1"}
    spend_file.add(3.5)
    caps, _budget, spent = workflow._agent_caps(stories, story, env=ENV)
    assert caps["day"] == {"cap_usd": 4.0, "spent_usd": 3.5, "left_usd": 0.5}
    assert spent["day"] == 3.5 and spent["day_extra"] == 0.0

    spend_file.add_extra(2.0)
    caps, _budget, spent = workflow._agent_caps(stories, story, env=ENV)
    assert caps["day"] == {"cap_usd": 4.0, "spent_usd": 3.5, "left_usd": 2.5, "extra_usd": 2.0}
    assert spent["day"] == 3.5 and spent["day_extra"] == 2.0
