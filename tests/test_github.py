import json
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
import respx

from app.models import Role
from app.providers import make_provider
from app.providers.base import AuthError, Identity, ProviderError
from app.providers.github import GitHub
from tests.factories import item

API = "https://api.github.com"


def search_pr(number: int, author: str = "alice", **extra) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/acme/app/pull/{number}",
        "repository_url": f"{API}/repos/acme/app",
        "user": {"login": author},
        "created_at": "2026-09-20T10:00:00Z",
        "updated_at": "2026-09-24T10:00:00Z",
        "draft": False,
        **extra,
    }


def gh_item(number=5, role=Role.REVIEWER, author="alice"):
    it = item(role, number, author=author, kind="github", host="github.com")
    return type(it)(**{**it.__dict__, "project_id": "acme/app", "project": "acme/app"})


def comment(db_id: int, login: str, body: str = "hi") -> dict:
    return {"databaseId": db_id, "author": {"login": login}, "body": body,
            "createdAt": "2026-09-24T10:00:00Z", "url": f"https://github.com/acme/app/pull/5#c{db_id}"}


@pytest_asyncio.fixture
async def gh():
    async with httpx.AsyncClient() as client:
        yield GitHub("ghp_test", client)


@respx.mock
async def test_whoami_classic_token_scopes(gh):
    respx.get(f"{API}/user").mock(return_value=httpx.Response(
        200, json={"login": "me"}, headers={"x-oauth-scopes": "repo, read:org"}))
    assert await gh.whoami() == Identity("me", frozenset({"repo", "read:org"}))


@respx.mock
async def test_whoami_fine_grained_token_has_unknown_scopes(gh):
    respx.get(f"{API}/user").mock(return_value=httpx.Response(200, json={"login": "me"}))
    assert (await gh.whoami()).scopes is None


@respx.mock
async def test_whoami_rejected(gh):
    respx.get(f"{API}/user").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await gh.whoami()


@respx.mock
async def test_bearer_header(gh):
    route = respx.get(f"{API}/user").mock(return_value=httpx.Response(200, json={"login": "me"}))
    await gh.whoami()
    assert route.calls.last.request.headers["Authorization"] == "Bearer ghp_test"


@respx.mock
async def test_list_items_roles(gh):
    def respond(request):
        q = request.url.params["q"]
        if "review-requested:me" in q:
            return httpx.Response(200, json={"items": [search_pr(1)]})
        if "reviewed-by:me" in q:
            return httpx.Response(200, json={"items": [search_pr(2, draft=True)]})
        if "assignee:me" in q:
            return httpx.Response(200, json={"items": []})
        if "author:me" in q:
            return httpx.Response(200, json={"items": [search_pr(3, author="me")]})
        return httpx.Response(400)

    respx.get(f"{API}/search/issues").mock(side_effect=respond)
    items = {i.iid: i for i in await gh.list_items("me")}
    assert items[1].role is Role.REVIEWER and items[3].role is Role.AUTHOR
    assert items[2].draft
    assert items[1].project_id == "acme/app" and items[1].ref == "#1" and items[1].key == "github.com:acme/app:1"


@respx.mock
async def test_details_from_graphql(gh):
    pr = {
        "state": "OPEN",
        "mergeable": "CONFLICTING",
        "headRefOid": "def456",
        "reviewRequests": {"nodes": [{"requestedReviewer": {"login": "erin"}}, {"requestedReviewer": {}}]},
        "latestOpinionatedReviews": {"nodes": [
            {"state": "APPROVED", "author": {"login": "bob"}},
            {"state": "CHANGES_REQUESTED", "author": {"login": "dave"}},
        ]},
        "reviews": {"nodes": [
            {"databaseId": 70, "body": "Overall good", "createdAt": "2026-09-24T10:00:00Z",
             "url": "u70", "author": {"login": "bob"}},
            {"databaseId": 71, "body": "", "createdAt": "2026-09-24T10:00:00Z", "url": "u71",
             "author": {"login": "dave"}},
        ]},
        "reviewThreads": {"nodes": [
            {"id": "PRRT_1", "isResolved": False, "comments": {"nodes": [comment(10, "me"), comment(11, "alice")]}},
        ]},
        "comments": {"nodes": [comment(20, None)]},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]},
    }
    route = respx.post(f"{API}/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"repository": {"pullRequest": pr}}}))
    d = await gh.details(gh_item())
    sent = json.loads(route.calls.last.request.content)
    assert sent["variables"] == {"owner": "acme", "name": "app", "number": 5}
    threads = {t.id: t for t in d.threads}
    assert set(threads) == {"PRRT_1|10", "rv70", "ic20"}
    assert threads["PRRT_1|10"].resolvable and not threads["rv70"].resolvable
    assert threads["ic20"].notes[0].author == "ghost"
    assert d.approved_by == frozenset({"bob"}) and d.changes_requested_by == frozenset({"dave"})
    assert d.pending_reviewers == ("erin",)
    assert d.has_conflicts and d.pipeline == "failed" and d.approvals_left is None and d.state == "opened"
    assert d.head_sha == "def456"


@respx.mock
async def test_graphql_errors_raise(gh):
    respx.post(f"{API}/graphql").mock(return_value=httpx.Response(200, json={"errors": [{"message": "nope"}]}))
    with pytest.raises(ProviderError, match="nope"):
        await gh.details(gh_item())


@respx.mock
async def test_mentions_from_notifications(gh):
    respx.get(f"{API}/notifications").mock(return_value=httpx.Response(200, json=[
        {"id": "1", "reason": "mention", "updated_at": "2026-09-24T11:00:00Z",
         "subject": {"title": "Fix bug", "latest_comment_url": f"{API}/repos/acme/app/issues/comments/99"}},
        {"id": "2", "reason": "subscribed", "updated_at": "2026-09-24T11:00:00Z",
         "subject": {"title": "Other", "latest_comment_url": f"{API}/repos/acme/app/issues/comments/98"}},
    ]))
    respx.get(f"{API}/repos/acme/app/issues/comments/99").mock(return_value=httpx.Response(200, json={
        "id": 99, "user": {"login": "bob"}, "body": "@me please check", "html_url": "https://github.com/x#c99",
        "created_at": "2026-09-24T11:00:00Z",
    }))
    since = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    [m] = await gh.mentions("me", since)
    assert (m.note_id, m.author, m.title) == ("99", "bob", "Fix bug")


@respx.mock
async def test_mentions_latest_comment_without_mention_is_skipped(gh):
    respx.get(f"{API}/notifications").mock(return_value=httpx.Response(200, json=[
        {"id": "1", "reason": "mention", "updated_at": "2026-09-24T11:00:00Z",
         "subject": {"title": "T", "latest_comment_url": f"{API}/c/1"}},
    ]))
    respx.get(f"{API}/c/1").mock(return_value=httpx.Response(200, json={
        "id": 1, "user": {"login": "bob"}, "body": "unrelated follow-up", "html_url": "h",
        "created_at": "2026-09-24T11:00:00Z"}))
    assert await gh.mentions("me", None) == []


@respx.mock
async def test_write_actions(gh):
    thread_reply = respx.post(f"{API}/repos/acme/app/pulls/5/comments/10/replies").mock(
        return_value=httpx.Response(201, json={}))
    issue_comment = respx.post(f"{API}/repos/acme/app/issues/5/comments").mock(
        return_value=httpx.Response(201, json={}))
    review = respx.post(f"{API}/repos/acme/app/pulls/5/reviews").mock(return_value=httpx.Response(200, json={}))
    graphql = respx.post(f"{API}/graphql").mock(return_value=httpx.Response(200, json={"data": {}}))
    it = gh_item()
    await gh.reply(it, "PRRT_1|10", "thanks")
    await gh.reply(it, "ic20", "general reply")
    await gh.resolve(it, "PRRT_1|10")
    await gh.approve(it)
    assert json.loads(thread_reply.calls.last.request.content) == {"body": "thanks"}
    assert json.loads(issue_comment.calls.last.request.content) == {"body": "general reply"}
    assert json.loads(review.calls.last.request.content) == {"event": "APPROVE"}
    assert json.loads(graphql.calls.last.request.content)["variables"] == {"id": "PRRT_1"}


@respx.mock
async def test_fetch_item_merged(gh):
    respx.get(f"{API}/repos/acme/app/pulls/5").mock(return_value=httpx.Response(200, json={
        **search_pr(5, author="me"), "state": "closed", "merged": True,
    }))
    fresh = await gh.fetch_item(gh_item(role=Role.AUTHOR, author="me"))
    assert fresh.state == "merged" and fresh.role is Role.AUTHOR


async def test_make_provider_github():
    async with httpx.AsyncClient() as client:
        assert isinstance(make_provider("github", "github.com", "t", client), GitHub)


@respx.mock
async def test_redirect_is_an_error(gh):
    respx.get(f"{API}/user").mock(return_value=httpx.Response(301, headers={"location": "https://elsewhere/"}))
    with pytest.raises(ProviderError, match="elsewhere"):
        await gh.whoami()


@respx.mock
async def test_assigned_pr_counts_as_mine(gh):
    def respond(request):
        q = request.url.params["q"]
        if "assignee:me" in q:
            return httpx.Response(200, json={"items": [search_pr(4, author="bob", labels=[{"name": "blocker"}])]})
        return httpx.Response(200, json={"items": []})

    respx.get(f"{API}/search/issues").mock(side_effect=respond)
    [it] = await gh.list_items("me")
    assert it.iid == 4 and it.role is Role.AUTHOR and it.labels == ("blocker",)


@respx.mock
async def test_get_by_ref(gh):
    respx.get(f"{API}/repos/acme/app/pulls/5").mock(return_value=httpx.Response(200, json={
        **search_pr(5, author="bob"), "state": "open", "merged": False}))
    it = await gh.get_by_ref("acme/app", 5, Role.WATCHER)
    assert it.role is Role.WATCHER and it.state == "opened" and it.key == "github.com:acme/app:5"
