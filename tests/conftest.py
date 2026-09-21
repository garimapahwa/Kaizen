import pytest


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path_factory, monkeypatch):
    """Never let a test create ./kaizen-workspace inside the repository."""
    monkeypatch.setenv("KAIZEN_WORKSPACE", str(tmp_path_factory.mktemp("workspace")))
    # A developer's own Supabase settings must never send test sign-ups to a real project.
    monkeypatch.delenv("KAIZEN_SUPABASE_URL", raising=False)
    monkeypatch.delenv("KAIZEN_SUPABASE_KEY", raising=False)
