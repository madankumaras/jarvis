import sys


def test_python_is_312():
    assert sys.version_info[:2] == (3, 12)


def test_jarvis_package_imports():
    import jarvis

    assert jarvis is not None


def test_audio_stack_imports():
    import sounddevice
    import numpy

    assert sounddevice is not None
    assert numpy is not None
