# audio_describer/ui/scene_explorer_dialog.py
"""Accessible scene explorer (audit P2): a plain wx dialog that screen readers read natively,
replacing the old pygame window. Objects are listed with clock positions relative to the frame
centre (12 o'clock = top) and selecting one plays a cue panned to its horizontal position."""
from ..i18n_setup import _
import math
import wx

from ..utils import sound_player
from .accessibility_utils import speak_message


def clock_position(coord, grid_size):
    """(x, y) on a WxH grid -> (hour 1-12 or None for the centre, distance 0-1, pan -1..1)."""
    try:
        w, h = max(1, int(grid_size[0])), max(1, int(grid_size[1]))
        x, y = float(coord[0]), float(coord[1])
    except (TypeError, ValueError, IndexError):
        return None, 0.0, 0.0
    dx = (x + 0.5) / w - 0.5          # -0.5 left .. 0.5 right
    dy = (y + 0.5) / h - 0.5          # -0.5 top  .. 0.5 bottom
    distance = min(1.0, math.hypot(dx, dy) / 0.5)
    pan = max(-1.0, min(1.0, 2 * dx))
    if distance < 0.2:
        return None, distance, pan
    hour = round(math.degrees(math.atan2(dx, -dy)) / 30) % 12
    return (hour or 12), distance, pan


def position_text(coord, grid_size):
    hour, distance, _pan = clock_position(coord, grid_size)
    if hour is None:
        return _("in the centre")
    if distance > 0.7:
        return _("at %d o'clock, near the edge") % hour
    return _("at %d o'clock") % hour


class SceneExplorerDialog(wx.Dialog):
    def __init__(self, parent, scene_data):
        super().__init__(parent, title=_("Scene Explorer"), size=(620, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        scene_data = scene_data if isinstance(scene_data, dict) else {}
        self.grid_size = scene_data.get("grid_size") or [10, 10]
        self.objects = [o for o in (scene_data.get("objects") or []) if isinstance(o, dict)]
        overall = scene_data.get("overall_description") or _("No overall description was provided.")

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("&Scene:")), 0, wx.LEFT | wx.TOP, 5)
        self.overall_ctrl = wx.TextCtrl(self, value=overall, style=wx.TE_MULTILINE | wx.TE_READONLY)
        sizer.Add(self.overall_ctrl, 1, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("&Objects (12 o'clock is the top of the picture):")), 0, wx.LEFT, 5)
        self.list_box = wx.ListBox(self, choices=[self._item_label(o) for o in self.objects])
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("&Details:")), 0, wx.LEFT, 5)
        self.details_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)
        sizer.Add(self.details_ctrl, 1, wx.EXPAND | wx.ALL, 5)

        sizer.Add(self.CreateButtonSizer(wx.CLOSE), 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.list_box.Bind(wx.EVT_LISTBOX, self.OnSelect)
        self.list_box.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: speak_message(self.details_ctrl.GetValue(), interrupt=True))

        if self.objects:
            self.list_box.SetSelection(0)
            self._show(0, play_cue=False)
            self.list_box.SetFocus()
        else:
            self.list_box.Append(_("There are no objects in this scene."))
            self.overall_ctrl.SetFocus()

    def _coord(self, obj):
        return (obj.get("spatial_info") or {}).get("start_coord")

    def _item_label(self, obj):
        return "%s, %s" % (obj.get("label") or _("Unnamed Object"), position_text(self._coord(obj), self.grid_size))

    def _show(self, index, play_cue=True):
        obj = self.objects[index]
        parts = [obj.get("label") or "", obj.get("action_description") or "",
                 obj.get("detailed_description") or _("No further details available.")]
        self.details_ctrl.SetValue("\n".join(p for p in parts if p))
        if play_cue:
            sound_player.play_panned("start.mp3", clock_position(self._coord(obj), self.grid_size)[2])

    def OnSelect(self, event):
        index = self.list_box.GetSelection()
        if 0 <= index < len(self.objects):
            self._show(index)
