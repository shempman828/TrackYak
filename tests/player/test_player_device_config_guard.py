"""Regression: _get_device_config()'s except tuple must not itself crash.

`_close_stream()` already guards `self.sd.PortAudioError` with
`hasattr(self, "sd") and hasattr(self.sd, "PortAudioError")` before using it
in an except tuple, since referencing it unguarded raises AttributeError the
moment Python tries to match an exception against the tuple with `self.sd`
unset. `_get_device_config()` and `_prepare_exclusive_device()` used the
unguarded form; this exercises the fixed, guarded behavior.
"""

from src.player.core.player_device import OUTPUT_LATENCY, PlayerDeviceMixin


class _PortAudioError(Exception):
    pass


class _RaisingDefault:
    @property
    def device(self):
        raise _PortAudioError("device query failed")


class _FakeSd:
    PortAudioError = _PortAudioError
    default = _RaisingDefault()


class _Bare(PlayerDeviceMixin):
    def __init__(self, sd):
        self.sd = sd
        self.current_device = None
        self.exclusive_mode = False


def test_get_device_config_catches_portaudioerror_via_the_guarded_tuple():
    host = _Bare(_FakeSd())
    config = host._get_device_config()
    assert config == {"device": None, "latency": OUTPUT_LATENCY}


def test_get_device_config_does_not_need_sd_to_have_portaudioerror():
    # A plain object without PortAudioError (e.g. sd never initialized) must
    # not make the guard itself raise while building the except tuple.
    class _NoPortAudioErrorSd:
        default = _RaisingDefault()

    host = _Bare(_NoPortAudioErrorSd())
    # _RaisingDefault raises a plain _PortAudioError, which the guarded
    # except tuple (OSError, IndexError, TypeError only, since this sd has
    # no PortAudioError attr) does NOT catch -- it must propagate, not be
    # swallowed by a broken guard.
    try:
        host._get_device_config()
    except _PortAudioError:
        pass
    else:
        raise AssertionError("expected the unmatched exception to propagate")
