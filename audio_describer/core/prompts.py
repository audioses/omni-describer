# audio_describer/core/prompts.py
"""Prompts sent to Gemini for audio description.

The text itself lives in prompts/rules/en.json (professional AD rules, output fields, personalisation
lines). A translation rules/<description language>.json and the user's own <data folder>/ad_rules.json
override it key by key. This module only assembles the pieces. No SDK and no wx."""
import json
import os

from .. import config
from ..models import config_model
from ..utils.logger import app_logger
from . import timing

MAX_WINDOWS_IN_PROMPT = 400
MIN_WINDOW_SEC = 0.8
MAX_CUTS_IN_PROMPT = 200
USER_RULES_FILE = "ad_rules.json"


def description_language_name():
    """Language for AI output: its own setting, independent of the UI language
    (e.g. French interface, English descriptions for an English lecture)."""
    code = (config_model.get_setting("description_language")
            or config_model.ui_language() or "en")
    return timing.LANGUAGE_NAMES.get(code.lower(), "English")


def _read_rules(path):
    """One rules file as a dict; a missing or broken file counts as empty (logged), never fatal."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data
    except (OSError, ValueError) as e:
        app_logger.error(f"AD rules file {path} ignored: {e}")
        return {}


def load_rules():
    """English rules, then the description language's translation, then the user's file, key by key.
    A value of the wrong type is ignored so a typo cannot break generation. Read on every call: edits
    apply to the next description without restarting."""
    folder = os.path.join(config.get_prompts_dir(), "rules")
    rules = _read_rules(os.path.join(folder, "en.json"))
    lang = (config_model.get_setting("description_language") or config_model.ui_language() or "en").lower()
    overrides = []
    if lang != "en":
        overrides.append(_read_rules(os.path.join(folder, f"{lang}.json")))
    overrides.append(_read_rules(os.path.join(config_model.APP_DATA_DIR, USER_RULES_FILE)))
    for layer in overrides:
        for key, value in layer.items():
            if key in rules and type(value) is not type(rules[key]):
                app_logger.warning(f"AD rules: '{key}' has the wrong type, kept the default.")
            elif isinstance(value, dict) and isinstance(rules.get(key), dict):
                rules[key] = {**rules[key], **value}
            else:
                rules[key] = value
    return rules


def _fill(template, **values):
    """Placeholders like {language}; plain replace, so braces elsewhere in the text are left alone."""
    for name, value in values.items():
        template = template.replace("{" + name + "}", str(value))
    return template


def core_directives(rules=None):
    """The numbered professional AD rules (Netflix v2.5, DCMP, ACB, CSA 2020, Ofcom 2024, CAB/AMI, ADLAB)."""
    rules = rules or load_rules()
    lines = [f"{i}.{'  ' if i < 10 else ' '}{text}" for i, text in enumerate(rules.get("core_directives", []), 1)]
    return "\n" + "\n".join([rules.get("core_directives_title", "")] + lines) + "\n"


def system_prompt(kind, rules=None):
    """System prompt for the whole video ('video')."""
    rules = rules or load_rules()
    return _fill(rules.get(f"system_{kind}", "{core_directives}"), core_directives=core_directives(rules))


def _format_windows(windows, rules=None):
    """Quiet windows as prompt lines with a word budget each (longest kept if too many)."""
    template = (rules or load_rules()).get("pause_line", "- {start} to {end}: max {words} words")
    usable = [(a, b) for a, b in windows if b - a >= MIN_WINDOW_SEC]
    if len(usable) > MAX_WINDOWS_IN_PROMPT:
        usable = sorted(sorted(usable, key=lambda w: w[1] - w[0], reverse=True)[:MAX_WINDOWS_IN_PROMPT])
    return [_fill(template, start=timing.format_vtt_time(a), end=timing.format_vtt_time(b), words=timing.word_budget(b - a))
            for a, b in usable]


def _personalisation_lines(rules=None):
    """User preferences injected into the prompt (style, emphasis, colours, length)."""
    rules = rules or load_rules()
    lines = [rules.get("style_subjective" if config_model.get_setting("description_style") == "subjective"
                       else "style_objective", "")]
    emphasis = rules.get("emphasis", {}).get(config_model.get_setting("description_emphasis"))
    if emphasis:
        lines.append(_fill(rules.get("emphasis_line", "{text}"), text=emphasis))
    if not config_model.get_setting("mention_colors"):
        lines.append(rules.get("no_colours", ""))
    explicitation = rules.get("explicitation", {}).get(config_model.get_setting("description_explicitation"))
    if explicitation:
        lines.append(_fill(rules.get("explicitation_line", "{text}"), text=explicitation))
    if config_model.get_setting("translate_foreign_dialogue"):
        lines.append(_fill(rules.get("audio_subtitles", ""), language=description_language_name()))
    max_words = int(config_model.get_setting("max_words_per_description") or 0)
    if max_words > 0:
        lines.append(_fill(rules.get("length", ""), max_words=max_words))
    return [line for line in lines if line]


def _format_cuts(cuts):
    """Shot changes for the prompt, thinned evenly when there are too many."""
    step = max(1, -(-len(cuts) // MAX_CUTS_IN_PROMPT))
    return ", ".join(timing.format_vtt_time(t) for t in cuts[::step])


def _build_unified_prompts(user_prompt, model_name_to_use, quiet_windows=None, context=None, shot_changes=None):
    """System + user prompts for one generation request (one chunk)."""
    rules = load_rules()
    parts = [rules.get("task", ""), rules.get("task_header", ""),
             _fill(rules.get("language_line", ""), language=description_language_name())]
    if quiet_windows is not None:
        lines = _format_windows(quiet_windows, rules)
        parts.append(_fill(rules.get("pauses", ""), pauses="\n".join(lines)) if lines else rules.get("no_pauses", ""))
    if shot_changes:
        parts.append(_fill(rules.get("shot_changes", ""), cuts=_format_cuts(shot_changes)))
    parts += _personalisation_lines(rules)
    if context:
        parts.append(_fill(rules.get("context", ""), context=context))
    if user_prompt and user_prompt.strip():
        parts.append(_fill(rules.get("user_focus", ""), focus=user_prompt.strip()))
        app_logger.info(f"Custom prompt applied: {user_prompt.strip()[:100]}...")
    return system_prompt("video", rules), "\n".join(parts)
