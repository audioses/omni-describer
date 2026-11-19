# audio_describer/core/prompts.py
"""Prompt text shared by every vision backend (Gemini, Ollama, OpenAI-compatible): professional AD
rules, user personalisation, pause/shot-change formatting, and the system/user prompt pair.
No SDK and no wx, so it can be read and tested on its own."""
from ..models import config_model
from ..utils.logger import app_logger
from . import timing

MAX_WINDOWS_IN_PROMPT = 400
MIN_WINDOW_SEC = 0.8


def description_language_name():
    """Language for AI output: its own setting, independent of the UI language
    (e.g. French interface, English descriptions for an English lecture)."""
    code = (config_model.get_setting("description_language")
            or config_model.get_setting("application_language") or "en")
    return timing.LANGUAGE_NAMES.get(code.lower(), "English")


# Professional AD rules (Netflix v2.5, DCMP, ACB, CSA 2020, Ofcom 2024, CAB/AMI, ADLAB).
CORE_DIRECTIVES = """
**CORE DIRECTIVES (Apply to `audio_descriptions`):**
1.  **PLACE DESCRIPTIONS IN PAUSES:** Describe only during natural pauses in dialogue and important sounds. Never cover dialogue or song lyrics unless the visual is more important than the words (e.g. reading foreign subtitles or on-screen text); background crowd chatter may be covered when no other pause exists. If a description must fall on one side of a sound, place it before rather than after, except to keep a surprise, scare or punchline, which it follows. If an essential visual has no pause while it is on screen, describe it in the pause just before, starting with "In a moment,".
2.  **WORD BUDGET:** At most about 2.7 words per second of the pause you place the description in (160 words per minute). Use the fewest words that carry the visual information: a long pause is not a reason for a long description. Not every pause needs a description; leave breathing room for music and intentional silence.
3.  **LANGUAGE:** Simple present ("he stomps", not "he is stomping"), active voice, third person, complete grammatical sentences, vocabulary and register matched to the work. Precise verbs ("he limps", not "he walks with difficulty"); "a" when something first appears, then "the". Never say "we see", never name actors, and never use camera jargon ("close-up", "pan"), except to convey a technique that carries meaning, always worded the same way ("In slow motion,", "In black and white,"). Give positions from the viewer's side (left, right, foreground) and sizes by comparison with familiar objects. When someone addresses the viewer, say so ("She winks at us").
4.  **OBJECTIVE BUT MEANINGFUL:** Describe what is visible. Do not interpret intent or emotion beyond observable expression ("she frowns", not "she is angry"); no personal opinions, and no judging adjectives about looks ("beautiful"): describe the features instead. Describe what the dialogue does not already tell: always a visible reaction that contradicts what is said, never one that merely repeats it. When dialogue points at something visible ("this", "over there"), say what it is. Do not invent: if unsure, use a general term ("herbs", not "parsley").
5.  **CHARACTERS:** Use a name only after it is spoken or shown; before that, use a consistent visual descriptor ("a bearded man"), and the first time the name is known tie them together ("the bearded man, Jack"). Mention relationships (mother, brother) only once revealed. Famous landmarks and public figures may be named when clearly recognisable and not a plot secret. Describe appearance from head to toe, most salient trait first, giving main characters the most detail spread over their first appearances, and describe later changes that matter (clothes, injury, disguise). Skin tone and visible disability: describe them factually for every main character or for none, never only for some, with person-first wording ("a man who uses a wheelchair"); never guess ethnic origin or gender identity, and in gendered languages use neutral wording ("une personne", "la silhouette") when gender is not established. Start each description with a name or noun, never with a pronoun.
6.  **ON-SCREEN TEXT:** Read all meaningful on-screen text verbatim (titles, signs, messages, logos, legal disclaimers), prefixed by its nature ("Sign:", "Title:", "Logo:", "Text message:"), and set "on_screen_text" to true. Burned-in subtitles: say "Subtitles:" the first time, then give the speaker before each line. Credits: read the title and main names (director, main cast) in nearby pauses; if they do not all fit, say "Other credits follow."; if they are in another script, say so. Skip text the soundtrack already speaks. Never just say that text appears without reading it.
7.  **SCENE CHANGES AND TIME:** After a significant change of location or time, start with where/when ("Later, in the kitchen, ..."), general before particular; a place that returns gets the same short phrase ("Back at the office") plus what has changed. Say when a sequence is a flashback, dream, memory, replay or archive footage, and when it returns to the present. Summarise a montage as a whole with its two or three most significant images.
8.  **DO NOT DESCRIBE WHAT IS HEARD:** No "a phone rings" or "he talks"; say instead who answers. Name the source of sounds that are hard to identify. When it is unclear who is about to speak (off-screen voice, voice-over, thoughts), name the speaker just before the line; say who enters or leaves when nothing audible signals it; when dialogue stops for no audible reason, say why.
9.  **NO CENSORSHIP:** Describe violence, nudity and sexuality factually, without euphemism.
10. **NO SPOILERS, NO CROSS-REFERENCES:** Do not reveal what the story hides yet, and never refer to earlier descriptions ("as seen before").
11. **ADAPT TO THE CONTENT:** First identify the genre, the intended audience and the spoken language, report them in "content_profile" (in the description language), and adapt unless the user's focus says otherwise: children → simple concrete words; horror/thriller → never fill tension-building silences; music video/concert → describe only between sung lines; dance → moves and costumes; lecture/slides → read every slide; sport/news → describe only what the commentary does not say, read scores and lower thirds; tutorial/fitness → for a movement the viewer must copy, give the body part, side (the person's left or right), direction and order; anime → stable visual descriptors per character; foreign-language dialogue with burned-in subtitles → read the subtitles as on-screen text.
12. **LEVELS (CAB/AMI):** Tag every description with "level": 1 = primary (essential to follow the story), 2 = secondary (important but not essential), 3 = tertiary (setting, appearance, style, colours). Write all three levels where pauses allow; the listener filters them during playback, so each description must make sense on its own.
"""


def _format_windows(windows):
    """Quiet windows as prompt lines with a word budget each (longest kept if too many)."""
    usable = [(a, b) for a, b in windows if b - a >= MIN_WINDOW_SEC]
    if len(usable) > MAX_WINDOWS_IN_PROMPT:
        usable = sorted(sorted(usable, key=lambda w: w[1] - w[0], reverse=True)[:MAX_WINDOWS_IN_PROMPT])
    return [f"- {timing.format_vtt_time(a)} to {timing.format_vtt_time(b)}: max {timing.word_budget(b - a)} words"
            for a, b in usable]


EMPHASIS_INSTRUCTIONS = {
    "characters": "Prioritise characters: who is there, appearance, expressions, gestures, interactions.",
    "actions": "Prioritise actions and step-by-step instructions shown (tools, gestures, results).",
    "environment": "Prioritise settings, places, light, weather and atmosphere.",
    "text": "Prioritise on-screen text: read every sign, caption, slide, message and burned-in subtitle.",
}


# ADLAB §1.5: how far a description goes beyond what is literally visible.
EXPLICITATION_INSTRUCTIONS = {
    "describe": "Describe only what is literally visible; do not name objects, places or people you merely infer.",
    "name": "Name what you see precisely when it is identifiable (a hobbit, an 'OK' gesture, Tower Bridge); "
            "the first time, add a few words saying what it is if the pause allows.",
    "explain": "When something visible is not self-explanatory (a gesture, a symbol, a cultural reference), "
               "add a very short explanation of its meaning, without interpreting the plot.",
}


def _personalisation_lines():
    """User preferences injected into the prompt (style, emphasis, colours, length)."""
    lines = []
    if config_model.get_setting("description_style") == "subjective":
        lines.append("*   **Style:** subjective and evocative (CSA 2020, Ofcom 4.20): convey atmosphere, mood "
                     "and the director's intent with vivid vocabulary, without inventing facts or spoiling. "
                     "This overrides directive 4: you may name feelings, relationships and mood the images support, "
                     "but every description must still say where the characters are and what they physically do.")
    else:
        lines.append("*   **Style:** objective but meaningful: describe what is visible and what it signifies, "
                     "never name an emotion that is not shown.")
    emphasis = EMPHASIS_INSTRUCTIONS.get(config_model.get_setting("description_emphasis"))
    if emphasis:
        lines.append(f"*   **Emphasis:** {emphasis}")
    if not config_model.get_setting("mention_colors"):
        lines.append("*   **Colours:** do not mention colours unless they matter to the story.")
    explicitation = EXPLICITATION_INSTRUCTIONS.get(config_model.get_setting("description_explicitation"))
    if explicitation:
        lines.append(f"*   **Explicitation:** {explicitation}")
    if config_model.get_setting("translate_foreign_dialogue"):
        language = description_language_name()
        lines.append(f"*   **Audio subtitles:** when characters speak a language other than {language} and no {language} "
                     f"subtitles are shown, add an entry timed on each line with \"description_text\" = the speaker's "
                     f"name or descriptor, a colon, then the {language} translation; set \"dialogue_translation\" to true "
                     "and \"speaker_gender\" to the speaker's apparent gender (\"female\", \"male\", or \"unknown\" "
                     "when unclear), so the line can be read with a matching voice; set \"speaker_id\" to that "
                     "character's id in character_glossary (add the character first if needed). Give every "
                     "character_glossary entry \"apparent_gender\" (\"female\", \"male\", or \"unknown\" when unclear; "
                     "never infer it from the name alone). "
                     "These entries may overlap that dialogue; never translate dialogue already in "
                     f"{language}.")
    max_words = int(config_model.get_setting("max_words_per_description") or 0)
    if max_words > 0:
        lines.append(f"*   **Length:** never more than {max_words} words per description, even in long pauses.")
    return lines


MAX_CUTS_IN_PROMPT = 200


def _format_cuts(cuts):
    """Shot changes for the prompt, thinned evenly when there are too many."""
    step = max(1, -(-len(cuts) // MAX_CUTS_IN_PROMPT))
    return ", ".join(timing.format_vtt_time(t) for t in cuts[::step])


def _build_unified_prompts(user_prompt, model_name_to_use, quiet_windows=None, context=None, shot_changes=None):
    """System + user prompts for one generation request (one chunk)."""
    target_language_name = description_language_name()

    system_instruction = f"""
You are an expert professional Audio Describer. Analyze the provided video and return a single JSON object with a character glossary and timed, level-tagged audio descriptions.

**OUTPUT FIELDS:**
- "character_glossary": one entry per distinct character: "id" (stable descriptive identifier, e.g. "man_in_red_shirt"), "description" (definitive physical description), "name" (only if spoken or shown in the video, else null).
- "audio_descriptions": "start_time_mmss" and "end_time_mmss" in "MM:SS.mmm" format ("HH:MM:SS.mmm" past one hour), measured from the start of the full video; end is the end of the pause the description fits in. "description_text", "level" (1, 2 or 3) and "on_screen_text" (true when it reads on-screen text).

{CORE_DIRECTIVES}
"""

    user_prompt_parts = [
        "Generate the character glossary and the timed audio descriptions for this video. Follow all instructions.",
        "\n**Current Task Specifications:**",
        f"*   **Language for `description_text`:** {target_language_name}",
    ]
    if quiet_windows is not None:
        lines = _format_windows(quiet_windows)
        if lines:
            user_prompt_parts.append(
                "*   **Detected pauses in the dialogue (no speech; music or sound effects may play).** Place descriptions "
                "inside these windows and respect each word budget; never over dialogue, and leave important music and "
                "sound effects room to be heard:\n" + "\n".join(lines))
        else:
            user_prompt_parts.append("*   **No clear pauses were detected:** describe sparingly, only primary information, between lines of dialogue.")
    if shot_changes:
        user_prompt_parts.append(
            "*   **Detected shot changes** (natural anchors: start a description just after a cut, and say when the "
            "place or time changes): " + _format_cuts(shot_changes))
    user_prompt_parts += _personalisation_lines()
    if context:
        user_prompt_parts.append("\n**Context from earlier in the video (for consistency only):**\n" + context)
    if user_prompt and user_prompt.strip():
        user_prompt_parts.append(f"*   **User's Specific Focus:** {user_prompt.strip()}")
        app_logger.info(f"Custom prompt applied: {user_prompt.strip()[:100]}...")

    return system_instruction, "\n".join(user_prompt_parts)
