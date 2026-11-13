"""Windows global shortcut settings."""

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QKeySequenceEdit, QLineEdit

from phonecam.core.deps import phonecam_hotkeys
from phonecam.i18n import t
from phonecam.qt.widgets import ErrorLabel, Hint, Page, bound_switch, button


def _sequence(combo):
    return QKeySequence.fromString(combo.replace("win+", "Meta+"), QKeySequence.SequenceFormat.PortableText)


def build(ctrl):
    page = Page()
    options = page.add_section(t("shortcuts.title"))
    options.add_row("keyboard", t("shortcuts.enable.label"), t("shortcuts.enable.hint")).addControl(
        bound_switch(ctrl, "hotkeys_enabled"))
    keys = page.add_section(t("shortcuts.list.title"))
    if phonecam_hotkeys is None:
        keys.add_padded(Hint(t("shortcuts.windows_only")))
        return page.finish()

    icons = {"camera": "camera", "mic": "mic", "blur": "sparkles", "pause": "pause",
             "rec": "record", "zoom_in": "zoom", "zoom_out": "zoom"}
    editors = {}
    rows = {}
    message = ErrorLabel("")

    def refresh():
        bindings = ctrl.get("hotkeys") or {}
        for action, editor in editors.items():
            seq = _sequence(bindings.get(action, ""))
            if editor.keySequence() != seq:
                editor.setKeySequence(seq)
            editor.findChild(QLineEdit).setPlaceholderText(t("shortcuts.key.placeholder"))
            taken = ctrl.get("hotkeys_enabled", True) and bindings.get(action) and action in ctrl.hotkey_conflicts
            rows[action].setHint(t("shortcuts.taken") if taken else "")

    def assign(action, combo):
        bindings = dict(ctrl.get("hotkeys") or {})
        duplicate = next((a for a, v in bindings.items() if a != action and v == combo), None) if combo else None
        if duplicate:
            message.setText(t("shortcuts.msg.duplicate", key=phonecam_hotkeys.combo_text(combo),
                              action=t(f"shortcuts.action.{duplicate}")))
            refresh()
            return
        message.setText("")
        bindings[action] = combo
        ctrl.changed("hotkeys", bindings)

    def edited(action):
        seq = editors[action].keySequence()
        if seq.isEmpty():
            assign(action, "")
            return
        raw = seq.toString(QKeySequence.SequenceFormat.PortableText).lower().replace("meta+", "win+")
        normalized = phonecam_hotkeys.normalize_combo(raw)
        if normalized:
            assign(action, normalized)
        else:
            message.setText(t("shortcuts.msg.need_modifier"))
            refresh()

    for action, _, _ in phonecam_hotkeys.ACTIONS:
        row = keys.add_row(icons[action], t(f"shortcuts.action.{action}"))
        rows[action] = row
        editor = QKeySequenceEdit()
        editor.setMaximumSequenceLength(1)
        editor.setMinimumWidth(165)
        editor.setAccessibleName(t(f"shortcuts.action.{action}"))
        editor.editingFinished.connect(lambda a=action: edited(a))
        editors[action] = editor
        row.addControl(editor)
        clear = button(t("shortcuts.key.none"))
        clear.clicked.connect(lambda _, a=action: assign(a, ""))
        row.addControl(clear)
    def reset_bindings():
        message.setText("")
        ctrl.changed("hotkeys", dict(phonecam_hotkeys.DEFAULT_HOTKEYS))

    reset = button(t("shortcuts.reset"))
    reset.clicked.connect(reset_bindings)
    keys.add_row("rotate", t("shortcuts.reset")).addControl(reset)
    keys.add_padded(message)
    keys.add_padded(Hint(t("shortcuts.hint")))
    if not phonecam_hotkeys.is_windows():
        keys.add_padded(Hint(t("shortcuts.windows_only")))

    ctrl.settings_changed.connect(lambda key, _: refresh() if key in ("hotkeys", "hotkeys_enabled") else None)
    refresh()
    return page.finish()
