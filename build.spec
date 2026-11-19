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
# Local Piper voices: sherpa-onnx ships native DLLs (and onnxruntime) inside its package.
sherpa_datas, sherpa_binaries, sherpa_hiddenimports = collect_all('sherpa_onnx')

# Part 1: Gather all the necessary Python modules.
hidden_imports = [
    *genai_hiddenimports,
    *sherpa_hiddenimports,
    *collect_submodules('google.auth'),
    *collect_submodules('cryptography'),
    'pyttsx3.drivers.sapi5',
    'openai',
    'accessible_output2',
    'vlc',  # python-vlc binding; imported lazily inside a try/except so add it
            # explicitly. The libvlc runtime itself is bundled into bin/vlc by CI.
]

# Part 2: Gather all the necessary data files and binaries.
added_files = [
    *genai_datas,
    *sherpa_datas,
    ('audio_describer/bin', 'bin'),
    ('audio_describer/notifs', 'notifs'),
    ('audio_describer/doc', 'doc'),
    ('audio_describer/locale', 'locale')
]

# Part 3: Define the main analysis.
a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=genai_binaries + sherpa_binaries,
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'torch', 'torchaudio', 'torchvision',
        'pandas', 'scipy', 'sklearn',  # numpy kept: onnxruntime (Silero VAD) needs it
        'IPython', 'Cython', 'astroid', 'pylint',
        'av', 'matplotlib', 'jupyter',
    ],
    noarchive=False,
)

# One recent, consistent Microsoft C++ runtime. Some packages ship a 2020 msvcp140.dll
# (14.27); with it onnxruntime (Silero pause detection) crashed the app with an access
# violation in MSVCP140.dll. Take the whole set from System32 instead (14.4x or newer).
import os
VC_RUNTIME = ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
              'concrt140.dll')
system32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
a.binaries = [b for b in a.binaries if b[0].lower() not in VC_RUNTIME]
for name in VC_RUNTIME:
    path = os.path.join(system32, name)
    if os.path.exists(path):
        a.binaries.append((name, path, 'BINARY'))

pyz = PYZ(a.pure)

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
    upx=False,  # UPX packing triggers antivirus false positives
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
    a.datas,
    strip=False,
    upx=False,  # UPX packing triggers antivirus false positives
    upx_exclude=[],
    name='omni_describer_dist',  # PyInstaller 6: libraries and data go to _internal/
)
