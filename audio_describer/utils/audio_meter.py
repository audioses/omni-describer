# audio_describer/utils/audio_meter.py
"""Loudness of what the computer is playing (Windows Core Audio peak meter), so live screen
description can wait for a pause instead of talking over a video or a call (audit §10).
comtypes is already installed with pyttsx3. Returns None when unavailable."""
import ctypes
from ctypes import POINTER, HRESULT, c_float, c_uint, c_void_p

from .logger import app_logger

try:
    import comtypes
    from comtypes import COMMETHOD, GUID, IUnknown

    class IAudioMeterInformation(IUnknown):
        _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
        _methods_ = [COMMETHOD([], HRESULT, "GetPeakValue", (["out"], POINTER(c_float), "pfPeak"))]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], c_uint, "dwClsCtx"),
                               (["in"], c_void_p, "pActivationParams"), (["out"], POINTER(POINTER(IUnknown)), "ppInterface"))]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_uint), (["in"], c_uint), (["out"], POINTER(c_void_p))),
                     COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_uint, "dataFlow"), (["in"], c_uint, "role"),
                               (["out"], POINTER(POINTER(IMMDevice)), "ppDevice"))]

    CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    AVAILABLE = True
except Exception:  # not Windows, or comtypes missing
    AVAILABLE = False

E_RENDER, E_CONSOLE = 0, 0
_meter = None


def peak():
    """Current output peak, 0.0 (silent) to 1.0, or None if it cannot be read."""
    global _meter
    if not AVAILABLE:
        return None
    try:
        if _meter is None:
            enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
            device = enumerator.GetDefaultAudioEndpoint(E_RENDER, E_CONSOLE)
            unknown = device.Activate(ctypes.byref(IAudioMeterInformation._iid_), comtypes.CLSCTX_ALL, None)
            _meter = unknown.QueryInterface(IAudioMeterInformation)
        return float(_meter.GetPeakValue())
    except Exception as e:
        app_logger.warning(f"Audio meter unavailable: {e}")
        _meter = None
        return None


if __name__ == "__main__":
    value = peak()
    assert value is None or 0.0 <= value <= 1.0, value
    print("audio_meter self-check OK, peak =", value)
