"""The package's public surface stays Django-free on import (PEP 562)."""
import subprocess
import sys


def test_import_is_django_free():
    """`import stapel_reviews` must not pull in Django or need configured
    settings — the lazy PEP 562 export contract."""
    code = (
        "import sys; import stapel_reviews; "
        "assert 'django' not in sys.modules, 'importing stapel_reviews pulled in django'; "
        "assert hasattr(stapel_reviews, 'reviews_settings'); "
        "print('ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_dir_lists_public_names():
    import stapel_reviews

    assert "reviews_settings" in dir(stapel_reviews)
