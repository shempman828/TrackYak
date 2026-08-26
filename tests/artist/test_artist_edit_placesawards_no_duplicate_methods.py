"""Regression test for a bug where PlacesAwardsTab defined `_reload_and_refresh`
twice; Python silently kept only the second definition, making the first dead
code. This statically checks the module for any class defining the same
method name more than once, which Python would otherwise shadow silently.
"""
import ast
import inspect

from src.artist import artist_edit_placesawards


def test_no_duplicate_method_definitions_in_classes():
    source = inspect.getsource(artist_edit_placesawards)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        method_names = [
            item.name
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        duplicates = {name for name in method_names if method_names.count(name) > 1}
        assert not duplicates, (
            f"Class {node.name!r} defines duplicate method(s) {duplicates}; "
            "the earlier definition would be silently shadowed."
        )
