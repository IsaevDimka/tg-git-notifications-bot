import httpx

from app.providers.base import Provider
from app.providers.github import GitHub
from app.providers.gitlab import GitLab


def make_provider(kind: str, host: str, token: str, client: httpx.AsyncClient) -> Provider:
    if kind == "gitlab":
        return GitLab(host, token, client)
    if kind == "github":
        return GitHub(token, client)
    raise ValueError(f"unknown provider {kind!r}")
