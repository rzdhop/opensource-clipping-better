"""The Settings page has four tabs (spec 8.6): Providers (LLM/STT), Generation,
Local hardware, Budget. Text guards, like the other page guards (DEC-012):
every generation status and verdict the backend sends has a glyph or a
colour, the page can test a chain and a single paid link, and the hardware
panel is fed by GET /api/hardware."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = (ROOT / "web" / "dashboard" / "src" / "pages" / "Settings.jsx").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "dashboard" / "src" / "index.css").read_text(encoding="utf-8")
MODELS = (ROOT / "web" / "api" / "models.py").read_text(encoding="utf-8")


def _literal_values(source, class_name, field):
    body = source[source.index(f"class {class_name}"):]
    body = body[: body.index("\nclass ")] if "\nclass " in body else body
    match = re.search(rf"^\s+{field}:\s*Literal\[([^\]]+)\]", body, re.M)
    assert match, (class_name, field)
    return set(re.findall(r'"(\w+)"', match.group(1)))


def _js_object_keys(source, const):
    start = source.index(f"const {const} = {{") + len(f"const {const} = {{")
    depth, top = 0, []
    for char in source[start:]:
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            top.append(char)
    return set(re.findall(r"(\w+):", "".join(top)))


def test_the_four_tabs_exist_and_the_last_one_is_remembered():
    tabs = _js_object_keys_list()
    assert tabs == ["providers", "generation", "hardware", "budget"]
    for label in ("Providers", "Generation", "Local hardware", "Budget"):
        assert label in PAGE, label
    assert "'rzc_settings_tab'" in PAGE
    assert ".settings-tabs" in CSS and ".settings-tab.active" in CSS


def _js_object_keys_list():
    block = PAGE[PAGE.index("const SETTINGS_TABS = ["):]
    block = block[: block.index("]\n")]
    return re.findall(r"id:\s*'(\w+)'", block)


def test_every_generation_status_and_verdict_is_drawn():
    statuses = _literal_values(MODELS, "GenerationLinkResult", "status")
    verdicts = _literal_values(MODELS, "GenerationChainTestResponse", "verdict")
    assert statuses == {"ok", "failed", "no_key", "no_adapter", "unreachable", "refused", "skipped"}
    assert statuses <= _js_object_keys(PAGE, "GEN_STATUS_GLYPH")
    assert verdicts <= _js_object_keys(PAGE, "GEN_VERDICT_STYLE")


def test_the_page_can_test_a_chain_and_one_paid_link():
    assert re.search(r"import \{[^}]*testGenerationChain[^}]*\} from '../api'", PAGE)
    assert "testGenerationChain({ kind" in PAGE
    assert "link" in PAGE[PAGE.index("testGenerationChain({ kind"):][:80], "the per-link test names the link"
    assert "artifact_url" in PAGE and "<img" in PAGE and "<audio" in PAGE


def test_the_hardware_tab_is_fed_by_the_api_and_edits_the_local_urls():
    assert re.search(r"import \{[^}]*fetchHardware[^}]*\} from '../api'", PAGE)
    assert "fetchHardware(" in PAGE
    for field in ("local_comfyui_url", "local_ollama_url"):
        assert f"payload.{field} =" in PAGE, field
    assert "recommendations" in PAGE and "install_hint" in PAGE
    assert "usage_today" in PAGE


def test_the_budget_section_lives_in_its_tab_and_the_keys_keep_their_badges():
    budget = PAGE.index("tab === 'budget'")
    assert PAGE.index("💰 Budget") > budget
    generation = PAGE.index("tab === 'generation'")
    assert PAGE.index("fal_key_set") > generation
    assert PAGE.index("💻 System Info") > PAGE.index("tab === 'hardware'")
