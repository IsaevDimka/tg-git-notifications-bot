"""GitLab REST v4 — gitlab.com and self-hosted instances alike."""

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.models import Details, Mention, Note, ReviewItem, Role, Thread
from app.providers.base import AuthError, Identity, ProviderError
from app.timeutil import parse_ts

MAX_PAGES = 5
_PIPELINE = {
    "success": "success",
    "failed": "failed",
    "running": "running",
    "pending": "running",
    "created": "running",
    "preparing": "running",
    "scheduled": "running",
    "waiting_for_resource": "running",
}
_MENTION_ACTIONS = {"mentioned", "directly_addressed"}


class GitLab:
    kind = "gitlab"

    def __init__(self, host: str, token: str, client: httpx.AsyncClient):
        self.host = host
        self._base = f"https://{host}/api/v4"
        self._client = client
        self.rate_remaining: int | None = None
        self._headers = {"PRIVATE-TOKEN": token}

    # ---- transport ---------------------------------------------------------------------------

    def _check(self, r: httpx.Response) -> httpx.Response:
        if (remaining := r.headers.get("RateLimit-Remaining", "")).isdigit():
            self.rate_remaining = int(remaining)
        if 300 <= r.status_code < 400:  # never follow: PRIVATE-TOKEN would travel to the new host
            location = r.headers.get("location", "?")
            raise ProviderError(f"{self.host}: redirects to {location} — use the final address", status=r.status_code)
        if r.status_code == 401:
            raise AuthError(f"{self.host}: token rejected", status=401)
        if r.status_code >= 400:
            raise ProviderError(f"{self.host}: HTTP {r.status_code} on {r.request.url.path}", status=r.status_code)
        return r

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return self._check(await self._client.request(method, self._base + path, headers=self._headers, **kw))

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return (await self._req("GET", path, params=params)).json()

    async def _paged(self, path: str, params: dict | None = None) -> list[dict]:
        out: list[dict] = []
        page = 1
        while page and page <= MAX_PAGES:
            r = await self._req("GET", path, params={**(params or {}), "per_page": 100, "page": page})
            out.extend(r.json())
            nxt = r.headers.get("x-next-page", "")
            page = int(nxt) if nxt else 0
        return out

    def _mr_path(self, item: ReviewItem) -> str:
        return f"/projects/{item.project_id}/merge_requests/{item.iid}"

    # ---- read --------------------------------------------------------------------------------

    async def whoami(self) -> Identity:
        r = await self._client.get(self._base + "/user", headers=self._headers)
        if r.status_code in (401, 403):
            raise AuthError(f"{self.host}: token rejected", status=r.status_code)
        username = self._check(r).json()["username"]
        try:
            scopes: frozenset[str] | None = frozenset((await self._get("/personal_access_tokens/self"))["scopes"])
        except ProviderError:
            scopes = None
        return Identity(username, scopes)

    def _item(self, mr: dict, role: Role) -> ReviewItem:
        full = (mr.get("references") or {}).get("full", "")
        return ReviewItem(
            kind="gitlab",
            host=self.host,
            project_id=str(mr["project_id"]),
            iid=mr["iid"],
            title=mr["title"],
            url=mr["web_url"],
            author=mr["author"]["username"],
            project=full.split("!")[0] or str(mr["project_id"]),
            role=role,
            created_at=parse_ts(mr["created_at"]),
            updated_at=parse_ts(mr["updated_at"]),
            state=mr["state"],
            draft=bool(mr.get("draft") or mr.get("work_in_progress")),
            labels=tuple(mr.get("labels") or ()),
        )

    async def list_items(self, me: str) -> list[ReviewItem]:
        base = {"scope": "all", "state": "opened"}
        found: dict[str, ReviewItem] = {}
        for mr in await self._paged("/merge_requests", {**base, "reviewer_username": me}):
            if mr["author"]["username"] != me:
                it = self._item(mr, Role.REVIEWER)
                found[it.key] = it
        # Assignee = the MR's owner in GitLab terms, so assigned MRs are "mine" just like authored ones.
        for field in ("assignee_username", "author_username"):
            for mr in await self._paged("/merge_requests", {**base, field: me}):
                it = self._item(mr, Role.AUTHOR)
                found[it.key] = it
        return list(found.values())

    async def fetch_item(self, item: ReviewItem) -> ReviewItem:
        return self._item(await self._get(self._mr_path(item)), item.role)

    async def get_by_ref(self, project: str, iid: int, role: Role) -> ReviewItem:
        """Look up an MR by its project path (as in the URL) — for /watch."""
        return self._item(await self._get(f"/projects/{quote(project, safe='')}/merge_requests/{iid}"), role)

    async def details(self, item: ReviewItem) -> Details:
        path = self._mr_path(item)
        mr = await self._get(path)
        discussions = await self._paged(f"{path}/discussions")
        try:
            approvals = await self._get(f"{path}/approvals")
        except ProviderError:
            approvals = {}
        try:
            reviewers = await self._get(f"{path}/reviewers")
        except ProviderError:
            reviewers = [{"user": u, "state": ""} for u in mr.get("reviewers", [])]

        threads = []
        for disc in discussions:
            raw = [n for n in disc.get("notes", []) if not n.get("system")]
            if not raw:
                continue
            resolvable = any(n.get("resolvable") for n in raw)
            resolved = resolvable and all(n.get("resolved") for n in raw if n.get("resolvable"))
            notes = tuple(
                Note(
                    id=str(n["id"]),
                    author=n["author"]["username"],
                    body=n.get("body") or "",
                    created_at=parse_ts(n["created_at"]),
                    url=f"{item.url}#note_{n['id']}",
                )
                for n in raw
            )
            threads.append(Thread(id=disc["id"], resolvable=resolvable, resolved=resolved, notes=notes))

        approved = frozenset(a["user"]["username"] for a in approvals.get("approved_by", []))
        return Details(
            state=mr["state"],
            threads=tuple(threads),
            approved_by=approved,
            changes_requested_by=frozenset(
                r["user"]["username"] for r in reviewers if r.get("state") == "requested_changes"
            ),
            pending_reviewers=tuple(
                r["user"]["username"]
                for r in reviewers
                if r["user"]["username"] not in approved and r.get("state", "") in ("unreviewed", "")
            ),
            has_conflicts=bool(mr.get("has_conflicts")),
            approvals_left=approvals.get("approvals_left"),
            pipeline=_PIPELINE.get(((mr.get("head_pipeline") or {}).get("status")) or ""),
            head_sha=mr.get("sha"),
        )

    async def mentions(self, me: str, since: datetime | None) -> list[Mention]:
        out: list[Mention] = []
        for todo in await self._paged("/todos", {"state": "pending"}):
            if todo.get("action_name") not in _MENTION_ACTIONS:
                continue
            created = parse_ts(todo["created_at"])
            if since and created <= since:
                continue
            url = todo.get("target_url") or ""
            note_id = url.rsplit("#note_", 1)[1] if "#note_" in url else f"todo{todo['id']}"
            out.append(
                Mention(
                    note_id=note_id,
                    author=(todo.get("author") or {}).get("username", ""),
                    body=todo.get("body") or "",
                    url=url,
                    title=(todo.get("target") or {}).get("title", ""),
                    created_at=created,
                )
            )
        return out

    # ---- write -------------------------------------------------------------------------------

    async def reply(self, item: ReviewItem, thread_id: str, body: str) -> None:
        await self._req("POST", f"{self._mr_path(item)}/discussions/{thread_id}/notes", json={"body": body})

    async def resolve(self, item: ReviewItem, thread_id: str) -> None:
        await self._req("PUT", f"{self._mr_path(item)}/discussions/{thread_id}", params={"resolved": "true"})

    async def approve(self, item: ReviewItem) -> None:
        try:
            await self._req("POST", f"{self._mr_path(item)}/approve")
        except AuthError:
            await self.whoami()  # raises AuthError if the token itself is dead
            # the token works: GitLab answers 401 when you have already approved — nothing to do

    async def comment(self, item: ReviewItem, body: str) -> None:
        await self._req("POST", f"{self._mr_path(item)}/notes", json={"body": body})
