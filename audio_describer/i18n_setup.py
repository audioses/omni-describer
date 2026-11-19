# audio_describer/i18n_setup.py
import gettext
import os
import wx
import sys

from . import config
from .utils.logger import app_logger
# DO NOT import config_model at the top level. This is the root of the circular import.
# from .models import config_model

_ = gettext.gettext
wx_locale = None  # Store to prevent garbage collection

def initialize_translations():
    global _
    global wx_locale

    from .models import config_model

    try:
        lang_code = config_model.ui_language()  # empty setting = follow Windows, never frozen
        app_logger.info(f"i18n: Using language code: {lang_code}")
    except Exception as e:
        app_logger.warning(f"i18n: Error getting lang setting (default 'en'): {e}")
        lang_code = "en"

    locale_dir = config.get_locale_dir()
    domain = "omni_describer"
    
    app_logger.info(f"i18n: lang='{lang_code}', domain='{domain}', dir='{locale_dir}'")
    app_logger.info(f"i18n: Expected .mo: {os.path.join(locale_dir, lang_code, 'LC_MESSAGES', domain + '.mo')}")

    try:
        translation_obj = gettext.translation(domain, localedir=locale_dir, languages=[lang_code], fallback=True)
        _ = translation_obj.gettext
        
        config.APP_NAME_TRANSLATABLE = _(config.APP_NAME_BASE_UNTRANSLATED)
        
        app_logger.info(f"i18n: SUCCESS: Translations initialized for '{lang_code}'.")
        app_logger.info(f"i18n: Translatable app name set to: '{config.APP_NAME_TRANSLATABLE}'")
    except FileNotFoundError:
        app_logger.warning(f"i18n: No .mo file for lang='{lang_code}', domain='{domain}'. Using default strings.")
    except Exception as e:
        app_logger.error(f"i18n: CRITICAL Error initializing gettext: {e}", exc_info=True)

    try:
        lang_info = wx.Locale.FindLanguageInfo(lang_code)
        if lang_info:
            wx.Locale.AddCatalogLookupPathPrefix(locale_dir)
            wx_locale = wx.Locale(lang_info.Language)
            wx_locale.AddCatalog('wxstd')
            lang_display_name = getattr(lang_info, 'Description', getattr(lang_info, 'LanguageName', 'Unknown'))
            app_logger.info(f"i18n: wx.Locale initialized for '{lang_code}' ({lang_display_name}).")
        else:
            app_logger.warning(f"i18n: No matching wx.Locale for lang_code '{lang_code}'. Dialogs may not localize.")
    except Exception as e:
        app_logger.error(f"i18n: Failed to initialize wx.Locale: {e}", exc_info=True)
    
    return lang_code