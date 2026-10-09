"""Credits panel: auto-scroll timers are cancellable, names are censored, a user scroll pauses the auto-scroll."""

from types import SimpleNamespace

import pytest

from src.nowplaying import nowplaying_credits as credits_mod
from src.nowplaying.nowplaying_credits import _CreditsPanel


@pytest.fixture
def panel(qapp):
    p = _CreditsPanel()
    p.resize(300, 120)
    yield p
    p.stop()
    p.deleteLater()


def _role(name, artist_id, role):
    return SimpleNamespace(credited_name=name, artist_id=artist_id, role=SimpleNamespace(role_name=role))


def _track(n=30):
    return SimpleNamespace(artist_roles=[_role(f"Player {i}", i, "Guitar") for i in range(n)])


def test_load_schedules_the_start_on_a_member_timer(panel):
    panel.load_credits(_track())
    assert panel._settle_timer.isActive()


def test_stop_cancels_every_pending_start_and_resume(panel):
    panel.load_credits(_track())
    panel._maybe_start_scroll()
    panel._pause_timer.start()
    panel.stop()
    assert not panel._settle_timer.isActive()
    assert not panel._pause_timer.isActive()
    assert not panel._timer.isActive()


def test_new_track_cancels_the_previous_tracks_pending_resume(panel):
    panel.load_credits(_track())
    panel._pause_timer.start()
    panel.load_credits(_track(2))
    assert not panel._pause_timer.isActive()


def test_hide_stops_the_scroll(panel):
    panel.show()
    panel.load_credits(_track())
    panel._resume()
    assert panel._timer.isActive()
    panel.hide()
    assert not panel._timer.isActive()
    assert not panel._settle_timer.isActive()


def test_credited_names_are_censored(panel, monkeypatch):
    monkeypatch.setattr(credits_mod, "censor_text", lambda t: t.replace("Bad", "B**"))
    panel.load_credits(SimpleNamespace(artist_roles=[_role("Bad Name", 1, "Drums")]))
    texts = [lbl.text() for lbl in panel._container.findChildren(credits_mod.QLabel)]
    assert "B** Name" in texts


def test_user_scroll_moves_the_auto_scroll_and_pauses_it(qapp, panel):
    panel.show()
    panel.load_credits(_track())
    for _ in range(3):  # layout requests are posted events; let them settle
        qapp.processEvents()
    panel._resume()
    sb = panel._area.verticalScrollBar()
    assert sb.maximum() > 40
    sb.setValue(40)  # a wheel scroll lands here
    assert panel._pos == 40.0
    assert panel._paused is True
    assert panel._pause_timer.isActive()


def test_own_ticks_are_not_taken_for_a_user_scroll(qapp, panel):
    panel.show()
    panel.load_credits(_track())
    for _ in range(3):  # layout requests are posted events; let them settle
        qapp.processEvents()
    panel._resume()
    for _ in range(5):
        panel._tick()
    assert panel._paused is False
