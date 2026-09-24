"""GitHub: REST for lists and writes, one GraphQL query per pull request for the details."""

from datetime import datetime
from typing import Any

import httpx

from app.models import Details, Mention, Note, ReviewItem, Role, Thread, mentions_user
from app.providers.base import AuthError, Identity, ProviderError
from app.timeutil import parse_ts

API = "https://api.github.com"
_STATE = {"OPEN": "opened", "MERGED": "merged", "CLOSED": "closed"}
_ROLLUP = {"SUCCESS": "success", "FAILURE": "failed", "ERROR": "failed", "PENDING": "running", "EXPECTED": "running"}

DETAILS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      state
      mergeable
      headRefOid
      reviewRequests(first: 50) { nodes { requestedReviewer { ... on User { login } } } }
      latestOpinionatedReviews(first: 50) { nodes { state author { login } } }
      reviews(last: 50) { nodes { databaseId body createdAt url author { login } } }
      reviewThreads(first: 100) {
        nodes { id isResolved comments(first: 100) { nodes { databaseId body createdAt url author { login } } } }
      }
      comments(last: 100) { nodes { databaseId body createdAt url author { login } } }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    }
  }
}
"""

RESOLVE_MUTATION = """
mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id } } }
"""


def _login(node: dict | None) -> str:
    return ((node or {}).get("author") or {}).get("login") or "ghost"


def _note(n: dict) -> Note:
    return Note(
        id=str(n["databaseId"]),
        author=_login(n),
        body=n.get("body") or "",
        created_at=parse_ts(n["createdAt"]),
        url=n["url"],
    )


class GitHub:
    kind = "github"
    host = "github.com"

    def __init__(self, token: str, client: httpx.AsyncClient):
        self._client = client
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ---- transport ---------------------------------------------------------------------------

    def _check(self, r: httpx.Response) -> httpx.Response:
        if 300 <= r.status_code < 400:
            location = r.headers.get("location", "?")
            raise ProviderError(f"github.com: redirects to {location}", status=r.status_code)
        if r.status_code == 401:
            raise AuthError("github.com: token rejected", status=401)
        if r.status_code >= 400:
            raise ProviderError(f"github.com: HTTP {r.status_code} on {r.request.url.path}", status=r.status_code)
        return r

    async def _req(self, method: str, url: str, **kw) -> httpx.Response:
        full = API + url if url.startswith("/") else url
        return self._check(await self._client.request(method, full, headers=self._headers, **kw))

    async def _graphql(self, query: str, variables: dict) -> dict:
        data = (await self._req("POST", "/graphql", json={"query": query, "variables": variables})).json()
        if data.get("errors"):
            raise ProviderError(f"github.com: {data['errors'][0].get('message', 'GraphQL error')}")
        return data["data"]

    @staticmethod
    def _repo(item: ReviewItem) -> str:
        return f"/repos/{item.project_id}"

    # ---- read --------------------------------------------------------------------------------

    async def whoami(self) -> Identity:
        r = await self._req("GET", "/user")
        header = r.headers.get("x-oauth-scopes")
        scopes = frozenset(s.strip() for s in header.split(",") if s.strip()) if header is not None else None
        return Identity(r.json()["login"], scopes)

    def _item(self, pr: dict, role: Role, project: str, state: str = "opened") -> ReviewItem:
        return ReviewItem(
            kind="github",
            host=self.host,
            project_id=project,
            iid=pr["number"],
            title=pr["title"],
            url=pr["html_url"],
            author=(pr.get("user") or {}).get("login") or "ghost",
            project=project,
            role=role,
            created_at=parse_ts(pr["created_at"]),
            updated_at=parse_ts(pr["updated_at"]),
            state=state,
            draft=bool(pr.get("draft")),
            labels=tuple(label["name"] for label in pr.get("labels") or () if label.get("name")),
        )

    async def _search(self, query: str) -> list[dict]:
        r = await self._req("GET", "/search/issues", params={"q": query, "per_page": 100})
        return r.json().get("items", [])

    async def list_items(self, me: str) -> list[ReviewItem]:
        base = "is:pr is:open archived:false"
        found: dict[str, ReviewItem] = {}
        for query in (f"{base} review-requested:{me}", f"{base} reviewed-by:{me} -author:{me}"):
            for pr in await self._search(query):
                it = self._item(pr, Role.REVIEWER, pr["repository_url"].split("/repos/", 1)[1])
                found[it.key] = it
        for query in (f"{base} assignee:{me}", f"{base} author:{me}"):  # assigned = mine, like authored
            for pr in await self._search(query):
                it = self._item(pr, Role.AUTHOR, pr["repository_url"].split("/repos/", 1)[1])
                found[it.key] = it
        return list(found.values())

    async def fetch_item(self, item: ReviewItem) -> ReviewItem:
        return await self.get_by_ref(item.project_id, item.iid, item.role)

    async def get_by_ref(self, project: str, iid: int, role: Role) -> ReviewItem:
        pr = (await self._req("GET", f"/repos/{project}/pulls/{iid}")).json()
        state = "merged" if pr.get("merged") else ("opened" if pr["state"] == "open" else "closed")
        return self._item(pr, role, project, state)

    async def details(self, item: ReviewItem) -> Details:
        owner, name = item.project_id.split("/", 1)
        data = await self._graphql(DETAILS_QUERY, {"owner": owner, "name": name, "number": item.iid})
        pr = data["repository"]["pullRequest"]

        threads: list[Thread] = []
        for th in pr["reviewThreads"]["nodes"]:
            notes = tuple(_note(c) for c in th["comments"]["nodes"])
            if notes:
                threads.append(Thread(f"{th['id']}|{notes[0].id}", True, th["isResolved"], notes))
        for rv in pr["reviews"]["nodes"]:
            if (rv.get("body") or "").strip():
                threads.append(Thread(f"rv{rv['databaseId']}", False, False, (_note(rv),)))
        for c in pr["comments"]["nodes"]:
            threads.append(Thread(f"ic{c['databaseId']}", False, False, (_note(c),)))

        opinions = {_login(r): r["state"] for r in pr["latestOpinionatedReviews"]["nodes"]}
        requested = [(r.get("requestedReviewer") or {}).get("login") for r in pr["reviewRequests"]["nodes"]]
        commits = pr["commits"]["nodes"] or [{}]
        rollup = ((commits[0].get("commit") or {}).get("statusCheckRollup") or {}).get("state")
        return Details(
            state=_STATE[pr["state"]],
            threads=tuple(threads),
            approved_by=frozenset(u for u, s in opinions.items() if s == "APPROVED"),
            changes_requested_by=frozenset(u for u, s in opinions.items() if s == "CHANGES_REQUESTED"),
            pending_reviewers=tuple(login for login in requested if login),
            has_conflicts=pr.get("mergeable") == "CONFLICTING",
            approvals_left=None,
            pipeline=_ROLLUP.get(rollup or ""),
            head_sha=pr.get("headRefOid"),
        )

    async def mentions(self, me: str, since: datetime | None) -> list[Mention]:
        params: dict[str, Any] = {"participating": "true"}
        if since:
            params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[Mention] = []
        for n in (await self._req("GET", "/notifications", params=params)).json():
            if n.get("reason") != "mention":
                continue
            subject = n.get("subject") or {}
            url = subject.get("latest_comment_url") or subject.get("url")
            if not url:
                continue
            c = (await self._req("GET", url)).json()
            body = c.get("body") or ""
            if not mentions_user(body, me):
                continue
            out.append(
                Mention(
                    note_id=str(c["id"]) if subject.get("latest_comment_url") else f"n{n['id']}",
                    author=(c.get("user") or {}).get("login") or "ghost",
                    body=body,
                    url=c.get("html_url", ""),
                    title=subject.get("title", ""),
                    created_at=parse_ts(c.get("created_at") or n["updated_at"]),
                )
            )
        return out

    # ---- write -------------------------------------------------------------------------------

    async def reply(self, item: ReviewItem, thread_id: str, body: str) -> None:
        if "|" in thread_id:
            first_comment = thread_id.split("|", 1)[1]
            await self._req(
                "POST", f"{self._repo(item)}/pulls/{item.iid}/comments/{first_comment}/replies", json={"body": body}
            )
        else:
            await self.comment(item, body)

    async def resolve(self, item: ReviewItem, thread_id: str) -> None:
        await self._graphql(RESOLVE_MUTATION, {"id": thread_id.split("|", 1)[0]})

    async def approve(self, item: ReviewItem) -> None:
        await self._req("POST", f"{self._repo(item)}/pulls/{item.iid}/reviews", json={"event": "APPROVE"})

    async def comment(self, item: ReviewItem, body: str) -> None:
        await self._req("POST", f"{self._repo(item)}/issues/{item.iid}/comments", json={"body": body})
