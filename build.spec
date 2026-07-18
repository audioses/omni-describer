# clean_build.spec
# A fresh, from-scratch configuration for PyInstaller.

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_all, copy_metadata

# google.genai (the new SDK) is a namespace package with data files and lazily
# imported submodules; collect_all grabs modules + data + binaries so the frozen
# build can `from google import genai`. Missing this = "Gemini SDK could not be
# loaded" at runtime.
genai_datas, genai_binaries, genai_hiddenimports = collect_all('google.genai')
# collect_all copies metadata by import name ('google.genai') and misses it —
# the distribution is 'google-genai'. Add it explicitly so importlib.metadata
# lookups resolve in the frozen build.
genai_datas += copy_metadata('google-genai')

# Part 1: Gather all the necessary Python modules.
hidden_imports = [
    *genai_hiddenimports,
    *collect_submodules('google.generativeai'),
    *collect_submodules('google.api_core'),
    *collect_submodules('google.auth'),
    *collect_submodules('cryptography'),
    'pyttsx3.drivers.sapi5',
    'pygame._sdl2.font',
    'openai',
    'grpc._cython',
    'accessible_output2',
    'vlc',  # python-vlc binding; imported lazily inside a try/except so add it
            # explicitly. The libvlc runtime itself is bundled into bin/vlc by CI.
]

# Part 2: Gather all the necessary data files and binaries.
added_files = [
    *genai_datas,
    ('audio_describer/bin', 'bin'),
    ('audio_describer/notifs', 'notifs'),
    ('audio_describer/doc', 'doc'),
    ('audio_describer/locale', 'locale')
]

# Part 3: Define the main analysis.
a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=genai_binaries,
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'torch', 'torchaudio', 'torchvision',
        'numpy', 'pandas', 'scipy', 'sklearn',
        'IPython', 'Cython', 'astroid', 'pylint',
        'av', 'matplotlib', 'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,  # Remove encryption here; handled by CLI
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# Part 4: Configure the final executable.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='omni_describer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Part 5: Collect everything into a single folder for distribution.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='omni_describer_dist',
)
