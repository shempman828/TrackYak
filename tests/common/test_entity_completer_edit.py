"""Regression test: pressing Enter with a completer suggestion highlighted
must commit *that* suggestion, not whatever partial text is still in the
field.

Both EntityCompleterEdit and BoundedSearchEdit wire their QCompleter
manually via setWidget() rather than QLineEdit.setCompleter(), so Qt does
no Return-vs-completer coordination on its own: the widgets' Return
handling used to fall straight through to QLineEdit's native keyPressEvent,
which fires returnPressed() -- and whatever add-this-entity slot is
connected to it -- with the text as typed. The completer's activated()
signal (which swaps in the highlighted suggestion) only arrived afterward,
so a caller like tag_association_tab._add() read the raw partial text and
matched_id()==None, creating a new entity out of the partial string instead
of using the one the user picked.
"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from src.common.entity_completer_edit import BoundedSearchEdit, EntityCompleterEdit


class _StubEntity:
    def __init__(self, genre_id, genre_name):
        self.genre_id = genre_id
        self.genre_name = genre_name


class _StubGet:
    def get_all_entities(self, model_name, **kwargs):
        return [_StubEntity(1, "Rock"), _StubEntity(2, "Rockabilly")]


class _StubController:
    def __init__(self):
        self.get = _StubGet()


def _highlight_first_suggestion(widget) -> None:
    """Select the popup's first row the way arrowing down to it would --
    done directly on the popup's model rather than via a synthetic Down
    keypress because the popup's real-desktop keyboard grab (what makes
    Down move its own selection instead of the line edit's) doesn't
    establish under the headless/offscreen QPA platform tests run under."""
    popup = widget._completer.popup()
    popup.setCurrentIndex(popup.model().index(0, 0))


def test_entity_completer_edit_enter_picks_highlighted_suggestion(qapp):
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Rockabilly": 2, "Pop": 3})

    captured = {}
    widget.returnPressed.connect(
        lambda: captured.update(text=widget.text(), matched_id=widget.matched_id())
    )

    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    QTest.keyClick(widget, Qt.Key_Return)

    assert captured == {"text": "Rock", "matched_id": 1}


def test_bounded_search_edit_enter_picks_highlighted_suggestion(qapp):
    widget = BoundedSearchEdit(_StubController(), "Genre", "genre_name", "genre_id")

    captured = {}
    widget.returnPressed.connect(
        lambda: captured.update(text=widget.text(), matched_id=widget.matched_id())
    )

    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    QTest.keyClick(widget, Qt.Key_Return)

    assert captured == {"text": "Rock", "matched_id": 1}


def test_entity_completer_edit_reset_clears_matched_id_before_signal(qapp):
    """reset()'s self.clear() is setText(""), which -- like the completion-
    pick path above -- emits textChanged synchronously. A textChanged
    listener (e.g. a dialog gating its OK button on matched_id()) must see
    matched_id() already cleared to None from inside that signal, not the
    stale id from before reset() was called."""
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Rockabilly": 2, "Pop": 3})
    widget._completer.activated.emit("Rock")
    assert widget.matched_id() == 1

    seen_during_signal = []
    widget.textChanged.connect(lambda _text: seen_during_signal.append(widget.matched_id()))

    widget.reset()

    assert seen_during_signal == [None]
    assert widget.matched_id() is None


def test_bounded_search_edit_reset_clears_matched_id_before_signal(qapp):
    widget = BoundedSearchEdit(_StubController(), "Genre", "genre_name", "genre_id")
    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    widget._on_completion_picked("Rock")
    assert widget.matched_id() == 1

    seen_during_signal = []
    widget.textChanged.connect(lambda _text: seen_during_signal.append(widget.matched_id()))

    widget.reset()

    assert seen_during_signal == [None]
    assert widget.matched_id() is None
