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
