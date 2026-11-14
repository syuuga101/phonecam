import ast
import json
import re
from pathlib import Path
from string import Formatter

import pytest

from phonecam import i18n

WINDOWS = Path(__file__).resolve().parents[1]
LANGUAGES = ("ja", "en")
LOCALES = {lang: json.loads((WINDOWS / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
           for lang in LANGUAGES}


def fields(template):
    names = set()
    for _, name, spec, _ in Formatter().parse(template):
        if name is not None:
            names.add(re.split(r"[.\[]", name, maxsplit=1)[0])
            names.update(fields(spec))
    return names


def test_locale_keys_match():
    assert LOCALES["ja"]
    assert LOCALES["ja"].keys() == LOCALES["en"].keys()


@pytest.mark.parametrize("language", LANGUAGES)
def test_all_translations_format(language, monkeypatch):
    monkeypatch.setattr(i18n, "_LOCALES", {})
    i18n.set_language(language)
    for key, template in LOCALES[language].items():
        args = dict.fromkeys(fields(template), 42)
        assert i18n.t(key, **args) == template.format(**args), key


def literal_keys(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.IfExp):
        return (*literal_keys(node.body), *literal_keys(node.orelse))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return tuple(left + right for left in literal_keys(node.left) for right in literal_keys(node.right))
    return ()


def translation_calls():
    sources = sorted((WINDOWS / "phonecam").rglob("*.py")) + [WINDOWS / "phonecam_setup.py"]
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        modules = {"phonecam.i18n", "phonecam_setup"}
        names = {"t"} if path.name == "phonecam_setup.py" else set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in modules:
                names.update(item.asname or item.name for item in node.names if item.name == "t")
            elif isinstance(node, ast.ImportFrom) and node.module == "phonecam":
                modules.update(item.asname or item.name for item in node.names if item.name == "i18n")
            elif isinstance(node, ast.Import):
                modules.update(item.asname for item in node.names if item.asname and item.name in modules)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            if (isinstance(fn, ast.Name) and fn.id in names
                    or isinstance(fn, ast.Attribute) and fn.attr == "t" and ast.unparse(fn.value) in modules):
                yield path.relative_to(WINDOWS).as_posix(), node


def test_translation_calls_supply_matching_kwargs():
    errors = []
    checked = 0
    for path, call in translation_calls():
        supplied = {keyword.arg for keyword in call.keywords if keyword.arg is not None}
        unpacked = len(supplied) != len(call.keywords)
        for key in literal_keys(call.args[0]):
            checked += 1
            for lang, catalog in LOCALES.items():
                if key not in catalog:
                    errors.append(f"{path}:{call.lineno} [{lang}] {key}: missing key")
                    continue
                expected = fields(catalog[key])
                if supplied - expected or (not unpacked and supplied != expected):
                    errors.append(f"{path}:{call.lineno} [{lang}] {key}: "
                                  f"expected {sorted(expected)}, got {sorted(supplied)}")
    assert checked
    assert not errors, "\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# 動的に組み立てるキー (f-string・"接頭辞" + 値 など)
#
# 「静的なキーが存在する」だけでは、v2.9.0 のように「Tk だけで使うと見なして消したキー」が実は Qt 側の
# 動的な参照 (f"status.{name}.{key}" など) から使われていた、という事故を防げない。
# そこで、動的な参照ごとに「入りうる値の全組み合わせ」をコード中の定数から作り、すべて存在することを確かめる。
# コードに新しい動的参照を足したら、ここに値の一覧を登録する (登録がないとテストが落ちる)。
# ---------------------------------------------------------------------------

def _literal_assignment(path, name):
    tree = ast.parse((WINDOWS / path).read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path}: {name} not found")


def _constants():
    from phonecam.core.settings import SCREEN_AUTO_VALUES
    from phonecam.core.softcam_shmem import IDLE_STOP_LABELS, RUN_MODE_LABELS, WATCH_STATE_LABELS
    from phonecam.core.stats import THERMAL_LEVELS
    import phonecam_hotkeys
    return {
        "levels": list(THERMAL_LEVELS),
        "screen_auto": list(SCREEN_AUTO_VALUES),
        "idle": [value for value, _ in IDLE_STOP_LABELS],
        "run_modes": [value for value, _ in RUN_MODE_LABELS],
        "watch": list(WATCH_STATE_LABELS),
        "actions": list(phonecam_hotkeys.ACTION_NAMES),
        "nav": [key for key, _ in _literal_assignment("phonecam/qt/main_window.py", "NAV_ITEMS")],
    }


STATUS_ROWS = {   # qt/pages/status.py の sections と同じ並び
    "iphone": ("thermal", "pressure", "factors", "hot", "battery", "lowpower"),
    "video": ("sentfps", "dropped", "dropreasons", "recdropped", "encode", "processing",
              "bitrate_sent", "bitrate_target", "adaptive"),
    "pc": ("codec", "decoder", "out", "watch", "dshow"),
    "latency": ("iphone", "comm", "pc", "total", "rtt"),
}
STATUS_HINTS = (("video", "hint"), ("pc", "watch_hint"), ("latency", "hint"))


def dynamic_families():
    """{コード中の式 (ast.unparse): そこから作られうるキーの一覧}"""
    c = _constants()
    return {
        "f'video.vcam.word_{action}'": [f"video.vcam.word_{a}" for a in ("register", "unregister")],
        "f'video.vcam.{key}'": [f"video.vcam.{k}" for k in ("unchanged", "cancelled", "timeout", "failed")],
        "f'labels.sec.{auto}'": [f"labels.sec.{v}" for v in c["screen_auto"]],
        "'connection.thermal.' + thermal": [f"connection.thermal.{v}" for v in c["levels"]],
        "'connection.state.' + key": [f"connection.state.{v}" for v in ("live", "idle", "paused", "off")],
        "'compact.sum.run.' + mode": [f"compact.sum.run.{m}" for m in ("auto", "always", "manual")],
        "'labels.runmode.' + v": [f"labels.runmode.{v}" for v in c["run_modes"]],
        "'labels.sec.%d' % v": [f"labels.sec.{v}" for v in c["idle"] + c["screen_auto"]],
        "'labels.watch.' + k": [f"labels.watch.{v}" for v in c["watch"]],
        "'nav.' + key": [f"nav.{v}" for v in c["nav"]],
        "f'audio.priority.{v}'": [f"audio.priority.{v}" for v in ("low", "stable")],
        "f'audio.{name}.label'": [f"audio.{n}.label" for n in
                                  ("info", "error", "path", "micpath", "mode", "latency", "under")],
        "'connection.num.%s' % key": [f"connection.num.{k}" for k in ("res", "latency", "thermal", "battery")],
        "f'effects.color.{key}.label'": [f"effects.color.{k}.label" for k in
                                         ("brightness", "contrast", "saturation", "sharpness")],
        "f'quality.{name}.title'": [f"quality.{n}.title" for n in ("exposure", "wb", "focus")],
        "f'quality.{name}.mode.label'": [f"quality.{n}.mode.label" for n in ("exposure", "wb", "focus")],
        "f'quality.{name}.label'": [f"quality.{n}.label" for n in
                                    ("exposure.bias", "exposure.iso", "wb.temperature", "wb.tint", "focus.position", "zoom")],
        "f'recording.{key}.label'": [f"recording.{k}.label" for k in ("state", "file", "size", "free")],
        "f'recording.{name}.label'": [f"recording.{n}.label" for n in
                                      ("preset", "resolution", "container", "audio", "photos")],
        "f'settings.{key}.{value}'": [f"settings.{k}.{v}" for k, vs in
                                      (("language", ("system", "ja", "en")), ("theme", ("system", "light", "dark")))
                                      for v in vs],
        "f'settings.{key}.label'": [f"settings.{k}.label" for k in ("language", "theme")],
        "f'settings.about.{name}'": [f"settings.about.{n}" for n in ("repo", "guide", "translate", "issues")],
        "f'shortcuts.action.{action}'": [f"shortcuts.action.{a}" for a in c["actions"]],
        "f'shortcuts.action.{duplicate}'": [f"shortcuts.action.{a}" for a in c["actions"]],
        "f'status.{name}.title'": [f"status.{n}.title" for n in STATUS_ROWS],
        "f'status.{name}.{key}'": [f"status.{n}.{k}" for n, ks in STATUS_ROWS.items() for k in ks],
        "f'status.{name}.{hint}'": [f"status.{n}.{h}" for n, h in STATUS_HINTS],
        "f'status.iphone.factor.{f}'": [f"status.iphone.factor.{f}" for f in
                                        ("systemTemperature", "peakPower", "depthModuleTemperature", "cameraTemperature")],
        "f'status.graph.{level}'": [f"status.graph.{v}" for v in c["levels"]],
        "f'status.graph.{name}'": [f"status.graph.{n}" for n in ("thermal", "fps")],
        "f'status.graph.{THERMAL_LEVELS[tick]}'": [f"status.graph.{v}" for v in c["levels"]],
        "f'video.{name}.label'": [f"video.{n}.label" for n in
                                  ("lens", "resolution", "fps", "outsize", "canvas", "fit")],
        "f'video.vcam.{action}'": [f"video.vcam.{a}" for a in ("register", "unregister")],
        "f'video.vcam.status.{state}'": [f"video.vcam.status.{s}" for s in ("ok", "outdated", "missing", "unavailable")],
        "'wireless.route.' + self.route": [f"wireless.route.{r}" for r in ("tether", "hotspot", "lan", "registered")],
        "'wireless.mode_' + value": [f"wireless.mode_{v}" for v in ("auto", "usb", "wifi")],
        "'setup.wizard.components_%d' % name_n": [f"setup.wizard.components_{n}" for n in (1, 3, 5)],
        "'setup.wizard.components_%d' % purpose_n": [f"setup.wizard.components_{n}" for n in range(2, 9)],
        "'setup.wizard.manual_text_%d' % n": [f"setup.wizard.manual_text_{n}" for n in range(1, 5)],
        "'settings.language.name_' + str(code)": [f"settings.language.name_{c}" for c in LANGUAGES],
    }


def _is_covered_by_constants(node):
    """変数・属性・添字の参照は、その値になりうる文字列がコード中の定数として書かれている
    (下の test_key_like_constants_exist が、書かれたキーを全部確かめる)。"""
    return isinstance(node, (ast.Name, ast.Attribute, ast.Subscript))


def test_dynamic_keys_exist():
    families = dynamic_families()
    unregistered = []
    seen = set()
    for path, call in translation_calls():
        arg = call.args[0]
        if literal_keys(arg) or _is_covered_by_constants(arg):
            continue
        expr = ast.unparse(arg)
        seen.add(expr)
        if expr not in families:
            unregistered.append(f"{path}:{call.lineno} {expr}")
    assert not unregistered, ("動的なキーが tests/test_i18n.py の dynamic_families に登録されていません "
                              "(入りうる値をすべて書いてください):\n" + "\n".join(unregistered))
    stale = sorted(set(families) - seen)
    assert not stale, f"dynamic_families に、コードに無い式があります: {stale}"
    errors = []
    for expr, keys in families.items():
        assert keys, expr
        for key in keys:
            for lang, catalog in LOCALES.items():
                if key not in catalog:
                    errors.append(f"[{lang}] {key}   <- {expr}")
    assert not errors, "\n" + "\n".join(errors)


NOT_KEYS = {"setup.exe", "setup.msi"}   # ファイル名


def _key_like_constants():
    prefixes = {key.split(".")[0] for key in LOCALES["ja"]}
    pattern = re.compile(r"^[a-z][a-z_0-9]*(\.[A-Za-z_0-9]+)+$")
    sources = sorted((WINDOWS / "phonecam").rglob("*.py")) + sorted(WINDOWS.glob("phonecam_*.py"))
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and pattern.match(node.value) and node.value.split(".")[0] in prefixes
                    and node.value not in NOT_KEYS and not node.value.endswith("_")):
                yield path.relative_to(WINDOWS).as_posix(), node.lineno, node.value


def test_key_like_constants_exist():
    """t() に直接渡さず、タプルや辞書に入れて後で t(name) に渡すキー (PRIMARY / GROUPS など) も存在すること。"""
    checked = 0
    errors = []
    for path, line, key in _key_like_constants():
        checked += 1
        for lang, catalog in LOCALES.items():
            if key not in catalog:
                errors.append(f"{path}:{line} [{lang}] {key}")
    assert checked
    assert not errors, "\n" + "\n".join(errors)


def test_no_translation_at_import_time():
    """import 時に t() を評価して定数にすると、言語を切り替えても古い言語のまま残る。関数の中 (使うとき) で評価すること。"""
    errors = []
    sources = sorted((WINDOWS / "phonecam").rglob("*.py")) + [WINDOWS / "phonecam_setup.py"]

    def walk(node, inside, path):
        for child in ast.iter_child_nodes(node):
            now = inside or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if (not inside and isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                    and child.func.id == "t"):
                errors.append(f"{path}:{child.lineno} {ast.unparse(child)[:70]}")
            walk(child, now, path)

    for path in sources:
        walk(ast.parse(path.read_text(encoding="utf-8-sig")), False, path.relative_to(WINDOWS).as_posix())
    assert not errors, ("import 時に t() を評価しています (言語の切り替えに追従しません):\n" + "\n".join(errors))
