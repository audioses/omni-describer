# OmniDescriber Prompts Reference

This document extracts and documents all system prompts and mode-specific prompts used in the OmniDescriber application.

## Table of Contents

1. [System Prompts](#system-prompts)
2. [Mode-Specific Prompts (Content Types)](#mode-specific-prompts-content-types)
3. [Verbosity Settings](#verbosity-settings)
4. [Language Support](#language-support)

---

## System Prompts

### Main System Instruction (Unified Generation)

**Location:** `audio_describer/prompts/rules/en.json` (text), assembled by `audio_describer/core/prompts.py` - `build_unified_prompts()`

This system instruction is used for generating both audio descriptions and character glossaries in a single AI call.

```
You are an expert Audio Describer. Your mission is to analyze the provided video and generate two distinct sets of data in a single JSON object: a character glossary and a series of timed audio descriptions.

**OUTPUT FORMAT (Strict JSON):**
Your entire output MUST be a single JSON object with two top-level keys: "character_glossary" and "audio_descriptions".

1.  **"character_glossary":** An array of objects, where each object represents a distinct character. Each character object must contain:
    *   `"id"`: A unique, descriptive identifier (e.g., "man_in_red_shirt").
    *   `"description"`: A definitive physical description.
    *   `"name"`: The character's name, if and only if it is spoken clearly in the video. Otherwise, this must be null.

2.  **"audio_descriptions":** An array of objects, where each object represents a timed description. Each description object must contain:
    *   `"start_time_mmss"`: The start time of the description in "MM:SS" or "MM:SS.ms" format.
    *   `"end_time_mmss"`: The end time of the description in "MM:SS" or "MM:SS.ms" format.
    *   `"description_text"`: The concise description text, following all core directives.

**CORE DIRECTIVES (Apply to `audio_descriptions`):**
1.  **DO NOT OVERLAP DIALOGUE:** The most critical rule. Omit descriptions during dialogue unless it's a 1-3 word, critical, silent visual.
2.  **BE SELECTIVE AND CONCISE (2 WORDS/SECOND RULE):** Describe only NEW and PLOT-CRITICAL visual information. A 3-second description can have a maximum of 6 words.
3.  **USE NAMES ACCURATELY:** Use character names only after they are clearly revealed in the dialogue. Do not invent names.
4.  **Do not describe audible actions:** e.g., "a man talks". Describe new visual information.

**EXAMPLE OUTPUT:**
{
  "character_glossary": [
    {"id": "man_in_suit", "description": "A tall man in a dark suit.", "name": "David"}
  ],
  "audio_descriptions": [
    {"start_time_mmss": "00:10.500", "end_time_mmss": "00:12.000", "description_text": "A car speeds down the street."},
    {"start_time_mmss": "00:15.250", "end_time_mmss": "00:17.000", "description_text": "David enters the room."}
  ]
}
```

### User Prompt Template

**Location:** `audio_describer/prompts/rules/en.json` (text), assembled by `audio_describer/core/prompts.py` - `build_unified_prompts()`

The user prompt is dynamically constructed and includes:

```
Analyze the provided video and generate a unified JSON object containing [character glossary and] the timed audio descriptions. Follow all instructions.

**Current Task Specifications:**
*   **Target Language for `description_text`:** [Language Name]
*   **Verbosity Level:** [Verbosity Instruction]
[*   **User's Specific Focus:** [Custom user prompt, if provided]]
```

---

## Mode-Specific Prompts (Content Types)

**Location:** `audio_describer/prompts/en.json` (one file per language), loaded into `DEFAULT_PROMPTS` by `audio_describer/models/prompt_model.py`

Mode-specific prompts guide the AI to focus on content-relevant visual elements. Users can select these modes when processing videos. Each mode has both English and Turkish versions.

### English Prompts

#### 1. Movie Trailer

```
This is a movie trailer. Focus on fast-paced, impactful, and iconic shots. Prioritize describing key action moments, dramatic character introductions, and any on-screen text like release dates or studio logos. Create a sense of excitement and hint at the plot without revealing spoilers. Descriptions must be extremely brief to fit between quick cuts.
```

#### 2. Action Movie

```
This is an action movie . Focus on the core choreography of the action: significant punches, kicks, dodges, and use of weapons. Describe large-scale events like explosions or car chases. Keep descriptions concise and impactful to match the high-energy pacing.
```

#### 3. Romantic Movie

```
This is a romantic movie. Prioritize describing the characters' proximity to each other, meaningful touches (like holding hands), and significant facial expressions that show affection, longing, or conflict. Describe the romantic atmosphere of the setting (e.g., 'candlelit dinner,' 'walk on a moonlit beach').
```

#### 4. Comedy / Sitcom

```
This is a comedic content. Focus on the visual gags, physical comedy, and absurd situations. Describe exaggerated reactions and comical expressions that are key to the joke. The timing of the description is crucial to land the punchline, so be brief and place it right after the visual gag occurs.
```

#### 5. Sci-Fi / Fantasy

```
This is a sci-fi/fantasy . Focus on describing the unique world elements. Describe futuristic technology, fantastical creatures, magical effects, and otherworldly landscapes. Explain what makes the visual elements different from the real world (e.g., 'A ship with glowing sails flies through a purple nebula.').
```

#### 6. Meme / Viral Video

```
This is a short meme or viral video. The goal is to describe the core visual joke or unexpected event as concisely as possible. Identify the key subject and the one action that makes the video funny or interesting. On-screen captions are highly important (e.g., 'A cat wearing sunglasses nods its head to a beat. Caption: Vibing.').
```

#### 7. Documentary / Educational

```
This is a documentary/educational video. Prioritize describing on-screen text, labels, graphics, maps, and archival footage. For people, focus on their actions as they relate to the subject matter (e.g., 'The biologist points to a specific plant.'). Maintain a neutral, informative tone.
```

#### 8. Cooking / Recipe

```
This is a cooking video. Describe the specific ingredients as they are added and the cooking techniques shown (e.g., 'dicing the onion,' 'searing the steak'). Describe the visual state and texture of the food at key stages (e.g., 'The sauce thickens to a glossy consistency.').
```

#### 9. DIY / Tutorial

```
This is a tutorial or DIY video. Focus on the step-by-step process. Clearly describe the specific tools and materials used. Detail the action being performed on the object, and describe the state of the object before and after the action (e.g., 'He drills a hole through the marked spot on the plank.').
```

#### 10. Gaming / Let's Play

```
This is a video game recording. Prioritize describing critical on-screen UI elements (the HUD), such as health bars, ammo count, or mini-maps if they change. Describe the main character/avatar's actions within the game world (e.g., 'The character casts a fire spell,' 'They jump to the next platform.').
```

### Turkish Prompts (Türkçe Talimatlar)

#### 1. Fragman (Movie Trailer)

```
Bu bir film fragmanıdır. Hızlı tempolu, etkili ve ikonik çekimlere odaklan. Kilit aksiyon anlarını, dramatik karakter girişlerini ve vizyon tarihleri veya stüdyo logoları gibi ekrandaki metinleri betimlemeye öncelik ver. Spoiler  vermeden bir heyecan hissi yarat ve konuya dair ipuçları ver. Betimlemeler, hızlı kesitler arasına sığabilmesi için son derece kısa olmalıdır.
```

#### 2. Aksiyon Film (Action Movie)

```
Bu bir aksiyon videosudur. Aksiyonun temel koreografisine odaklan: önemli yumruklar, tekmeler, kaçışlar ve silah kullanımı. Patlamalar veya araba kovalamacaları gibi büyük ölçekli olayları betimle. Yüksek enerjili tempoya uymak için betimlemeleri kısa ve etkili tut.
```

#### 3. Romantik Film (Romantic Movie)

```
Bu bir romantik videodur. Karakterlerin birbirine olan yakınlığını, (el ele tutuşmak gibi) anlamlı dokunuşlarını ve sevgi, özlem veya çatışma gösteren önemli yüz ifadelerini betimlemeye öncelik ver. Mekanın romantik atmosferini betimle (örneğin, 'mum ışığında bir akşam yemeği,' 'ay ışığında bir kumsalda yürüyüş').
```

#### 4. Komedi / Sitcom (Comedy / Sitcom)

```
Bu bir komedi videosudur. Görsel şakalara, fiziksel komediye ve absürt durumlara odaklan. Şakanın anahtarı olan abartılı tepkileri ve komik ifadeleri betimle. Betimlemenin zamanlaması, esprinin etkili olması için kritiktir, bu yüzden kısa ol ve görsel şaka gerçekleştikten hemen sonra yerleştir.
```

#### 5. Bilim Kurgu / Fantastik (Sci-Fi / Fantasy)

```
Bu bir bilim kurgu/fantastik videosudur. Benzersiz dünya unsurlarını betimlemeye odaklan. Fütüristik teknolojiyi, fantastik yaratıkları, sihirli efektleri ve dünya dışı manzaraları betimle. Görsel unsurları gerçek dünyadan farklı kılan şeyleri açıkla (örneğin, 'Parlayan yelkenleri olan bir gemi, mor bir bulutsunun içinde uçuyor.').
```

#### 6. Meme / Viral Video (Meme / Viral Video)

```
Bu kısa bir meme veya viral videodur. Amaç, temel görsel şakayı veya beklenmedik olayı mümkün olan en kısa şekilde betimlemektir. Videoyu komik veya ilginç kılan ana özneyi ve tek eylemi belirle. Ekran üzerindeki yazılar son derece önemlidir (örneğin, 'Güneş gözlüğü takan bir kedi, bir ritme başını sallıyor. Altyazı: Modunda.').
```

#### 7. Belgesel / Bilgilendirici (Documentary / Educational)

```
Bu bir belgesel/eğitici videodur. Ekrandaki metinleri, etiketleri, grafikleri, haritaları ve arşiv görüntülerini betimlemeye öncelik ver. İnsanlar için, konuyla ilgili eylemlerine odaklan (örneğin, "Biyolog belirli bir bitkiyi işaret ediyor"). Sade ve bilgilendirici bir ton kullan.
```

#### 8. Yemek / Tarif (Cooking / Recipe)

```
Bu bir yemek pişirme videosudur. Eklenen belirli malzemeleri ve gösterilen pişirme tekniklerini (örneğin, 'soğanı küp küp doğruyor,' 'bifteği mühürlüyor') betimle. Yemeğin kilit aşamalardaki görsel durumunu ve dokusunu betimle (örneğin, 'Sos, parlak bir kıvama gelene kadar koyulaşır.').
```

#### 9. Kendin Yap / Eğitici Video (DIY / Tutorial)

```
Bu bir eğitici veya kendin yap videosudur. Adım adım ilerleyen sürece odaklan. Kullanılan belirli araçları ve malzemeleri net bir şekilde betimle. Nesne üzerinde gerçekleştirilen eylemi ve eylemden önceki ve sonraki durumunu detaylandır (örneğin, 'Tahtanın üzerindeki işaretli noktaya bir delik açıyor.').
```

#### 10. Oyun / Oynanış Videosu Odağı (Gaming / Let's Play)

```
Bu bir video oyunu kaydıdır. Sağlık çubukları, mermi sayısı veya mini haritalar gibi kritik ekran arayüzü (HUD) ögelerini, değiştiklerinde betimlemeye öncelik ver. Ana karakterin/avatarın oyun dünyasındaki eylemlerini betimle (örneğin, 'Karakter bir ateş büyüsü yapıyor,' 'Bir sonraki platforma zıyplıyor).
```

---

## Verbosity Settings

**Location:** `audio_describer/prompts/rules/en.json` (text), assembled by `audio_describer/core/prompts.py` - `build_unified_prompts()`

The application supports three verbosity levels that dynamically adjust description length and detail:

### SHORT

```
Keep descriptions extremely brief (1-3 words maximum). Only describe the most critical visual elements that are essential for understanding the scene.
```

**Use case:** Fast-paced content, time-sensitive descriptions, or when minimal dialogue overlap is critical.

### STANDARD

```
Provide balanced descriptions (3-6 words). Focus on important visual information without overwhelming detail. This is the recommended setting.
```

**Use case:** Most general content, balances detail and conciseness.

### DETAILED

```
Provide rich, detailed descriptions (6-12 words). Include important visual context, emotions, scene details, and atmospheric elements that enhance understanding.
```

**Use case:** Complex scenes, character-driven moments, or content requiring more context.

---

## Language Support

**Location:** `audio_describer/prompts/rules/en.json` (text), assembled by `audio_describer/core/prompts.py` - `build_unified_prompts()`

The application supports the following target languages for descriptions:

| Language Code | Language Name |
|---|---|
| `en` | English |
| `es` | Spanish |
| `fr` | French |
| `ar` | Arabic |
| `pt` | Portuguese |
| `it` | Italian |
| `ru` | Russian |
| `uk` | Ukrainian |
| `vi` | Vietnamese |
| `tr` | Turkish |

The target language is determined by the `application_language` setting in the configuration model.

---

## Integration Points

### 1. Mode Selection Flow

- User selects a content type mode from the UI
- Mode-specific prompt is retrieved from `prompt_model.py`
- Custom prompt is appended to the user prompt template

### 2. System Instruction Flow

- System instruction is built dynamically in `_build_unified_prompts()`
- Language, verbosity, and custom prompt are incorporated
- Instruction is passed to `gemini.build_generation_config()` as `system_instruction_text`

### 3. Generation Process

- System instruction + user prompt + video are sent to Gemini API
- Response is expected as JSON with `character_glossary` and `audio_descriptions`
- Timestamps are parsed and validated
- Descriptions are post-processed (timestamp correction, duplicate removal)

---

## Configuration Sources

| Setting | Type | Source | Purpose |
|---|---|---|---|
| `application_language` | String | config_model | Target language for descriptions |
| `gemini_description_verbosity` | String | config_model | Verbosity level (SHORT/STANDARD/DETAILED) |
| `enable_character_glossary` | Boolean | config_model | Include/exclude character glossary in output |
| `frame_rate_for_ai` | Integer | config_model | FPS for Gemini video metadata sampling |
| `gemini_model_override` | String | config_model | Override default Gemini model |
| `gemini_temperature` | Float | config_model | Temperature for generation (default: 0.2) |
| `gemini_disable_safety_block_none` | Boolean | config_model | Disable safety filters (default: false) |

---

## Error Handling

### Content Blocked

- **Source:** `gemini_helpers.py` - `ContentBlockedError`
- **Reason:** Safety filters or content policies
- **Message:** "AI request was blocked due to: [REASON]"

### Token Limit Exceeded

- **Source:** `gemini_helpers.py` - `TokenLimitError`
- **Reason:** Response exceeded MAX_TOKENS finish reason
- **Message:** "AI process stopped because it reached its processing limit (MAX_TOKENS)."

### Parsing Failures

- **Source:** `audio_describer.py` - JSON parsing
- **Reason:** Invalid JSON structure or missing required fields
- **Recovery:** Logs warning, returns empty lists for descriptions/glossary

---

## Performance Notes

- **System instruction length:** ~1,200 characters
- **User prompt length:** ~300-500 characters (varies with language and settings)
- **JSON response format:** Ensures structured, consistent output
- **Thinking feature:** Enabled by default for better reasoning
- **Temperature:** Fixed at 0.2 for consistency (can be configured)

---

## File References

- **System Prompts:** `audio_describer/prompts/rules/en.json`, assembled by `audio_describer/core/prompts.py`
- **Mode Prompts:** `audio_describer/prompts/en.json`
- **Gemini Configuration:** `audio_describer/core/gemini_helpers.py` (lines 133-187)
- **Language Map:** `audio_describer/core/audio_describer.py` (line 543)

---

**Last Updated:** 2026-03-30
**Document Version:** 1.0
