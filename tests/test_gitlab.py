import json
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
import respx

from app.models import Role
from app.providers import make_provider
from app.providers.base import AuthError, Identity, ProviderError
from app.providers.gitlab import GitLab
from tests.factories import item

HOST = "gitlab.example.com"
BASE = f"https://{HOST}/api/v4"
MR = f"{BASE}/projects/7/merge_requests/1"


def mr_json(iid: int, author: str = "alice", **extra) -> dict:
    return {
        "iid": iid,
        "project_id": 7,
        "title": f"MR {iid}",
        "web_url": f"https://{HOST}/g/app/-/merge_requests/{iid}",
        "author": {"username": author},
        "references": {"full": f"g/app!{iid}"},
        "created_at": "2026-09-20T10:00:00.000Z",
        "updated_at": "2026-09-24T10:00:00.000Z",
        "state": "opened",
        "draft": False,
        **extra,
    }


def gl_note(nid: int, author: str, body: str = "hi", *, system=False, resolvable=True, resolved=False) -> dict:
    return {
        "id": nid,
        "author": {"username": author},
        "body": body,
        "created_at": "2026-09-24T10:00:00.000Z",
        "system": system,
        "resolvable": resolvable,
        "resolved": resolved,
    }


@pytest_asyncio.fixture
async def gl():
    async with httpx.AsyncClient() as client:
        yield GitLab(HOST, "glpat-test", client)


@respx.mock
async def test_whoami_with_scopes(gl):
    respx.get(f"{BASE}/user").mock(return_value=httpx.Response(200, json={"username": "me"}))
    respx.get(f"{BASE}/personal_access_tokens/self").mock(
        return_value=httpx.Response(200, json={"scopes": ["api", "read_user"]})
    )
    assert await gl.whoami() == Identity("me", frozenset({"api", "read_user"}))


@respx.mock
async def test_whoami_old_gitlab_has_unknown_scopes(gl):
    respx.get(f"{BASE}/user").mock(return_value=httpx.Response(200, json={"username": "me"}))
    respx.get(f"{BASE}/personal_access_tokens/self").mock(return_value=httpx.Response(404))
    assert await gl.whoami() == Identity("me", None)


@respx.mock
async def test_whoami_rejected_token(gl):
    respx.get(f"{BASE}/user").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await gl.whoami()


@respx.mock
async def test_sends_private_token_header(gl):
    route = respx.get(f"{BASE}/user").mock(return_value=httpx.Response(200, json={"username": "me"}))
    respx.get(f"{BASE}/personal_access_tokens/self").mock(return_value=httpx.Response(404))
    await gl.whoami()
    assert route.calls.last.request.headers["PRIVATE-TOKEN"] == "glpat-test"


@respx.mock
async def test_list_items_merges_roles_and_follows_pages(gl):
    respx.get(f"{BASE}/merge_requests", params__contains={"reviewer_username": "me", "page": "1"}).mock(
        return_value=httpx.Response(200, json=[mr_json(1)], headers={"x-next-page": "2"})
    )
    respx.get(f"{BASE}/merge_requests", params__contains={"reviewer_username": "me", "page": "2"}).mock(
        return_value=httpx.Response(200, json=[mr_json(2), mr_json(9, author="me")])
    )
    respx.get(f"{BASE}/merge_requests", params__contains={"assignee_username": "me"}).mock(
        return_value=httpx.Response(200, json=[mr_json(3, draft=True)])
    )
    respx.get(f"{BASE}/merge_requests", params__contains={"author_username": "me"}).mock(
        return_value=httpx.Response(200, json=[mr_json(9, author="me")])
    )
    items = {i.iid: i for i in await gl.list_items("me")}
    assert set(items) == {1, 2, 3, 9}
    assert items[1].role is Role.REVIEWER and items[9].role is Role.AUTHOR
    assert items[3].draft
    assert items[1].key == f"{HOST}:7:1" and items[1].project == "g/app" and items[1].ref == "!1"


@respx.mock
async def test_details_maps_threads_approvals_and_pipeline(gl):
    respx.get(MR).mock(return_value=httpx.Response(200, json=mr_json(1, has_conflicts=True,
                                                                     head_pipeline={"status": "failed"})))
    respx.get(f"{MR}/discussions").mock(return_value=httpx.Response(200, json=[
        {"id": "d1", "notes": [gl_note(10, "me", "why?"), gl_note(11, "alice", "because")]},
        {"id": "d2", "notes": [gl_note(12, "bob", "done", resolved=True)]},
        {"id": "d3", "notes": [gl_note(13, "bot", "added 1 commit", system=True)]},
        {"id": "d4", "notes": [gl_note(14, "carol", "general", resolvable=False)]},
    ]))
    respx.get(f"{MR}/approvals").mock(return_value=httpx.Response(200, json={
        "approved_by": [{"user": {"username": "bob"}}], "approvals_left": 1,
    }))
    respx.get(f"{MR}/reviewers").mock(return_value=httpx.Response(200, json=[
        {"user": {"username": "bob"}, "state": "approved"},
        {"user": {"username": "dave"}, "state": "requested_changes"},
        {"user": {"username": "erin"}, "state": "unreviewed"},
    ]))
    d = await gl.details(item(Role.AUTHOR, 1, author="me"))
    threads = {t.id: t for t in d.threads}
    assert set(threads) == {"d1", "d2", "d4"}
    assert [n.id for n in threads["d1"].notes] == ["10", "11"]
    assert threads["d1"].resolvable and not threads["d1"].resolved
    assert threads["d2"].resolved
    assert not threads["d4"].resolvable
    assert threads["d1"].notes[1].url.endswith("/merge_requests/1#note_11")
    assert d.approved_by == frozenset({"bob"})
    assert d.changes_requested_by == frozenset({"dave"})
    assert d.pending_reviewers == ("dave", "erin")
    assert d.has_conflicts and d.approvals_left == 1 and d.pipeline == "failed"


@respx.mock
async def test_details_survives_missing_reviewers_and_approvals_endpoints(gl):
    respx.get(MR).mock(return_value=httpx.Response(200, json=mr_json(1, reviewers=[{"username": "bob"}])))
    respx.get(f"{MR}/discussions").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{MR}/approvals").mock(return_value=httpx.Response(403))
    respx.get(f"{MR}/reviewers").mock(return_value=httpx.Response(404))
    d = await gl.details(item(Role.REVIEWER, 1))
    assert d.pending_reviewers == ("bob",)
    assert d.approved_by == frozenset() and d.approvals_left is None


@respx.mock
async def test_forbidden_project_is_provider_error_not_auth(gl):
    respx.get(MR).mock(return_value=httpx.Response(403))
    with pytest.raises(ProviderError) as exc:
        await gl.details(item(Role.REVIEWER, 1))
    assert not isinstance(exc.value, AuthError)
    assert exc.value.status == 403


@respx.mock
async def test_mentions_from_todos(gl):
    respx.get(f"{BASE}/todos").mock(return_value=httpx.Response(200, json=[
        {"id": 1, "action_name": "mentioned", "author": {"username": "bob"}, "body": "@me look",
         "target_url": f"https://{HOST}/g/app/-/issues/5#note_77", "target": {"title": "Bug"},
         "created_at": "2026-09-24T11:00:00.000Z"},
        {"id": 2, "action_name": "assigned", "author": {"username": "bob"}, "body": "",
         "target_url": "x", "target": {"title": "X"}, "created_at": "2026-09-24T11:00:00.000Z"},
        {"id": 3, "action_name": "directly_addressed", "author": {"username": "carol"}, "body": "old",
         "target_url": f"https://{HOST}/g/app/-/merge_requests/1#note_5", "target": {"title": "Old"},
         "created_at": "2026-09-01T11:00:00.000Z"},
    ]))
    since = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    [m] = await gl.mentions("me", since)
    assert m.note_id == "77" and m.author == "bob" and m.title == "Bug"
    assert m.url.endswith("#note_77")


@respx.mock
async def test_write_actions(gl):
    reply = respx.post(f"{MR}/discussions/d1/notes").mock(return_value=httpx.Response(201, json={}))
    resolve = respx.put(f"{MR}/discussions/d1").mock(return_value=httpx.Response(200, json={}))
    approve = respx.post(f"{MR}/approve").mock(return_value=httpx.Response(201, json={}))
    comment = respx.post(f"{MR}/notes").mock(return_value=httpx.Response(201, json={}))
    it = item(Role.REVIEWER, 1)
    await gl.reply(it, "d1", "thanks")
    await gl.resolve(it, "d1")
    await gl.approve(it)
    await gl.comment(it, "@bob ping")
    assert json.loads(reply.calls.last.request.content) == {"body": "thanks"}
    assert resolve.calls.last.request.url.params["resolved"] == "true"
    assert approve.called
    assert json.loads(comment.calls.last.request.content) == {"body": "@bob ping"}


@respx.mock
async def test_fetch_item_reports_merged_state(gl):
    respx.get(MR).mock(return_value=httpx.Response(200, json=mr_json(1, author="me", state="merged")))
    fresh = await gl.fetch_item(item(Role.AUTHOR, 1, author="me"))
    assert fresh.state == "merged" and fresh.role is Role.AUTHOR


@respx.mock
async def test_server_error_is_provider_error(gl):
    respx.get(f"{BASE}/todos").mock(return_value=httpx.Response(502))
    with pytest.raises(ProviderError):
        await gl.mentions("me", None)


async def test_make_provider_gitlab():
    async with httpx.AsyncClient() as client:
        p = make_provider("gitlab", HOST, "t", client)
        assert isinstance(p, GitLab) and p.host == HOST
        with pytest.raises(ValueError):
            make_provider("bitbucket", "x", "t", client)
