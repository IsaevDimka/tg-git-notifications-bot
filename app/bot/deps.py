"""Accessors for the shared objects in Application.bot_data (wired in app.main)."""

from app.config import Config
from app.crypto import TokenBox
from app.providers.base import Provider
from app.storage.store import Account, Store


def store(ctx) -> Store:
    return ctx.bot_data["store"]


def cfg(ctx) -> Config:
    return ctx.bot_data["cfg"]


def box(ctx) -> TokenBox:
    return ctx.bot_data["box"]


def provider(ctx, kind: str, host: str, token: str) -> Provider:
    return ctx.bot_data["make_provider"](kind, host, token)


def provider_for(ctx, account: Account) -> Provider:
    return provider(ctx, account.kind, account.host, box(ctx).open(account.token_enc))
