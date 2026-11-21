# audio_describer/core/prompts.py
"""Prompts sent to Gemini for audio description.

The text lives in prompts/rules/en.json (AD rules, task lines). A translation rules/<language>.json
and the user's own <data folder>/ad_rules.json override it key by key. This module only assembles
the pieces. No SDK and no wx."""
import json
import os

from .. import config
from ..models import config_model
from ..utils.logger import app_logger

USER_RULES_FILE = "ad_rules.json"
LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "ar": "Arabic", "pt": "Portuguese",
                  "it": "Italian", "ru": "Russian", "uk": "Ukrainian", "vi": "vietnamese", "tr": "Turkish"}


def _language_code():
    return (config_model.get_setting("application_language") or "en").lower()


def _read_rules(path):
    """One rules file as a dict; a missing or broken file counts as empty (logged), never fatal."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data
    except (OSError, ValueError) as e:
        app_logger.error(f"AD rules file {path} ignored: {e}")
        return {}


def load_rules():
    """English rules, then the language's translation, then the user's file, key by key.
    A value of the wrong type is ignored so a typo cannot break generation. Read on every call:
    edits apply to the next description without restarting."""
    folder = os.path.join(config.get_prompts_dir(), "rules")
    rules = _read_rules(os.path.join(folder, "en.json"))
    lang = _language_code()
    overrides = [_read_rules(os.path.join(folder, f"{lang}.json"))] if lang != "en" else []
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
    """Placeholders like {language}; plain replace, so the JSON example braces are left alone."""
    for name, value in values.items():
        template = template.replace("{" + name + "}", str(value))
    return template


def core_directives(rules):
    lines = [f"{i}.{'  ' if i < 10 else ' '}{text}" for i, text in enumerate(rules.get("core_directives", []), 1)]
    return "\n" + "\n".join([rules.get("core_directives_title", "")] + lines) + "\n"


def build_unified_prompts(user_prompt, initial_glossary=None):
    """System + user prompts for one generation request; initial_glossary = characters reused from the previous video."""
    rules = load_rules()
    system_instruction = _fill(rules.get("system_video", "{core_directives}"), core_directives=core_directives(rules))

    verbosity_setting = config_model.get_setting("gemini_description_verbosity")
    verbosity = rules.get("verbosity", {})
    verbosity_instruction = verbosity.get(verbosity_setting) or verbosity.get(config.VERBOSITY_STANDARD, "")
    app_logger.info(f"Using verbosity setting: {verbosity_setting} -> {verbosity_instruction[:50]}...")

    language = LANGUAGE_NAMES.get(_language_code(), "English")
    task_key = "task_with_glossary" if config_model.get_setting("enable_character_glossary") else "task"
    parts = [
        rules.get(task_key, ""),
        rules.get("task_header", ""),
        _fill(rules.get("language_line", ""), language=language),
        _fill(rules.get("verbosity_line", ""), text=verbosity_instruction),
    ]
    if config_model.get_setting("translate_foreign_dialogue"):
        parts.append(_fill(rules.get("dialogue_translation_line", ""), language=language))
    if initial_glossary:
        parts.append(_fill(rules.get("known_characters_line", ""),
                           characters=json.dumps(initial_glossary, ensure_ascii=False)))
    if user_prompt and user_prompt.strip():
        parts.append(_fill(rules.get("user_focus", ""), focus=user_prompt.strip()))
        app_logger.info(f"Custom prompt applied: {user_prompt.strip()[:100]}...")
    else:
        app_logger.info("No custom prompt provided by user.")
    return system_instruction, "\n".join(parts)
