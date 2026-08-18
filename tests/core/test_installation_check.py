from importlib.metadata import PackageNotFoundError

import pytest

from src.core import installation_check as ic


def test_check_python_version_ok_for_current_interpreter():
    assert ic.check_python_version() is None


def test_check_python_version_flags_old_interpreter(monkeypatch):
    monkeypatch.setattr(ic.sys, "version_info", (3, 8, 0, "final", 0))

    error = ic.check_python_version()

    assert error is not None
    assert "3.10" in error
    assert "3.8.0" in error


def test_check_required_packages_reports_missing_distribution(monkeypatch):
    real_version = ic.version

    def fake_version(name):
        if name == "SQLAlchemy":
            raise PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(ic, "version", fake_version)

    assert ic.check_required_packages() == ["SQLAlchemy"]


def test_check_required_packages_empty_when_all_installed():
    assert ic.check_required_packages() == []


def test_check_audio_fingerprint_backend_none_when_pyacoustid_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "acoustid":
            raise ImportError("no acoustid")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert ic.check_audio_fingerprint_backend() is None


def test_check_audio_fingerprint_backend_warns_without_any_backend(monkeypatch):
    import acoustid

    monkeypatch.setattr(acoustid, "have_chromaprint", False, raising=False)
    monkeypatch.setattr(acoustid, "have_audioread", False, raising=False)
    monkeypatch.setattr(ic.shutil, "which", lambda _cmd: None)

    warning = ic.check_audio_fingerprint_backend()

    assert warning is not None
    assert "fpcalc" in warning


def test_check_audio_fingerprint_backend_silent_with_fpcalc_on_path(monkeypatch):
    import acoustid

    monkeypatch.setattr(acoustid, "have_chromaprint", False, raising=False)
    monkeypatch.setattr(acoustid, "have_audioread", False, raising=False)
    monkeypatch.setattr(ic.shutil, "which", lambda cmd: "/usr/bin/fpcalc" if cmd == "fpcalc" else None)

    assert ic.check_audio_fingerprint_backend() is None


def test_verify_installation_exits_on_missing_packages(monkeypatch, capsys):
    monkeypatch.setattr(ic, "check_required_packages", lambda: ["SomePackage"])

    with pytest.raises(SystemExit) as excinfo:
        ic.verify_installation()

    assert excinfo.value.code == 1
    assert "SomePackage" in capsys.readouterr().err


def test_verify_installation_exits_on_old_python(monkeypatch, capsys):
    monkeypatch.setattr(ic, "check_python_version", lambda: "too old")

    with pytest.raises(SystemExit) as excinfo:
        ic.verify_installation()

    assert excinfo.value.code == 1
    assert "too old" in capsys.readouterr().err


def test_verify_installation_passes_cleanly_on_healthy_env(capsys):
    ic.verify_installation()

    assert capsys.readouterr().err == ""
