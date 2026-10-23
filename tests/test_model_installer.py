import hashlib
import io

import pytest

from tools import install_models


def test_model_hash_checked_before_atomic_install(tmp_path, monkeypatch):
    content = b"trusted model fixture"
    expected = hashlib.sha256(content).hexdigest()
    destination = tmp_path / "model.bin"
    monkeypatch.setattr(
        install_models.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(content)
    )
    assert (
        install_models.download("https://publisher.invalid/model", destination, expected)["sha256"]
        == expected
    )
    assert destination.read_bytes() == content


def test_bad_download_never_becomes_installed_file(tmp_path, monkeypatch):
    destination = tmp_path / "model.bin"
    monkeypatch.setattr(
        install_models.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"corrupt")
    )
    with pytest.raises(ValueError, match="SHA-256"):
        install_models.download("https://publisher.invalid/model", destination, "0" * 64)
    assert list(tmp_path.iterdir()) == []


def test_cached_model_is_not_trusted_without_hash(tmp_path, monkeypatch):
    destination = tmp_path / "model.bin"
    destination.write_bytes(b"damaged cached file")
    monkeypatch.setattr(
        install_models.urllib.request,
        "urlopen",
        lambda *a, **kw: pytest.fail("must not download silently"),
    )
    with pytest.raises(ValueError, match="SHA-256"):
        install_models.download("https://publisher.invalid/model", destination, "0" * 64)
    assert destination.read_bytes() == b"damaged cached file"
