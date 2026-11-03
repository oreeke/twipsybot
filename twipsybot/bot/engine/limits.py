import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from cachetools import TTLCache

from ...clients.misskey.models import UserRef
from ...db.sqlite import DBManager
from ...shared.config import Config, ReplyConfig
from ...shared.utils import normalize_tokens

_CACHE_MAX = 2000
_CACHE_TTL = 86400


@dataclass(slots=True)
class _ResponseLimitState:
    last_reply_ts: float | None = None
    turns: int = 0
    blocked_until_ts: float | None = None


class ResponseLimiter:
    def __init__(
        self,
        *,
        config: Config,
        db: DBManager,
        instance_url: str,
        blacklist_user: Callable[[str], Awaitable[None]],
    ):
        self._config = config
        self._db = db
        self._instance_url = instance_url
        self._blacklist_user = blacklist_user
        self._response_limits: TTLCache[str, _ResponseLimitState] = TTLCache(
            maxsize=_CACHE_MAX,
            ttl=_CACHE_TTL,
            timer=time.monotonic,
        )

    def canonical_handle(self, username: str) -> str | None:
        host = urlparse(self._instance_url).hostname
        return f"{username}@{host}" if host else None

    def _user_candidates(self, *, user_id: str, handle: str | None) -> set[str]:
        candidates = {user_id.lower()}
        if handle:
            normalized = handle.lower().lstrip("@").strip()
            if normalized:
                candidates.add(normalized)
                candidates.add(f"@{normalized}")
                if "@" not in normalized and (
                    canonical := self.canonical_handle(normalized)
                ):
                    candidates.add(canonical)
                    candidates.add(f"@{canonical}")
        return candidates

    def _in_user_list(
        self, users: list[str], *, user_id: str, handle: str | None
    ) -> bool:
        listed = set(normalize_tokens(users, lower=True))
        return bool(listed) and not listed.isdisjoint(
            self._user_candidates(user_id=user_id, handle=handle)
        )

    @property
    def _reply(self) -> ReplyConfig:
        return self._config.settings.reply

    def is_ignored_bot(self, user: UserRef) -> bool:
        return user.is_bot and self._reply.ignore_bots

    def is_response_blacklisted_user(self, *, user_id: str, handle: str | None) -> bool:
        return self._in_user_list(self._reply.blacklist, user_id=user_id, handle=handle)

    async def _get_response_limit_state(self, user_id: str) -> _ResponseLimitState:
        if user_id in self._response_limits:
            return self._response_limits[user_id]
        last_reply_ts = None
        turns = 0
        blocked_until_ts = None
        row = await self._db.get_response_limit_state(user_id)
        if row:
            last_reply_ts, turns, blocked_until_ts = row
            if blocked_until_ts == -1:
                await self._blacklist_user(user_id)
                turns = 0
                blocked_until_ts = None
        state = _ResponseLimitState(
            last_reply_ts=last_reply_ts,
            turns=int(turns or 0),
            blocked_until_ts=blocked_until_ts,
        )
        self._response_limits[user_id] = state
        if row and row[2] == -1:
            await self._save_response_limit_state(user_id, state)
        return state

    async def _save_response_limit_state(
        self, user_id: str, state: _ResponseLimitState
    ) -> None:
        await self._db.set_response_limit_state(
            user_id=user_id,
            last_reply_ts=state.last_reply_ts,
            turns=state.turns,
            blocked_until_ts=state.blocked_until_ts,
        )

    async def get_response_block_reply(
        self, *, user_id: str, handle: str | None
    ) -> tuple[bool, str]:
        reply = self._reply
        if any(
            self._in_user_list(users, user_id=user_id, handle=handle)
            for users in (reply.whitelist, self._config.settings.bot.admins)
        ):
            return False, ""
        now = time.time()
        state = await self._get_response_limit_state(user_id)
        if state.blocked_until_ts is not None and now >= state.blocked_until_ts:
            state.turns = 0
            state.blocked_until_ts = None
            await self._save_response_limit_state(user_id, state)
        if state.blocked_until_ts is not None and now < state.blocked_until_ts:
            return True, reply.max_turns_msg
        interval = reply.rate_limit
        if (
            interval > 0
            and state.last_reply_ts is not None
            and now - state.last_reply_ts < interval
        ):
            return True, reply.rate_limit_msg
        if self._turns_limited() and state.turns >= reply.max_turns:
            release = reply.turns_release
            if release < 0:
                await self._blacklist_user(user_id)
                state.turns = 0
                state.blocked_until_ts = None
            else:
                state.blocked_until_ts = now + release
            await self._save_response_limit_state(user_id, state)
            return True, reply.max_turns_msg
        return False, ""

    def _turns_limited(self) -> bool:
        return self._reply.max_turns >= 0

    async def record_response(self, user_id: str, *, count_turn: bool) -> None:
        state = await self._get_response_limit_state(user_id)
        state.last_reply_ts = time.time()
        if count_turn and self._turns_limited():
            state.turns += 1
        await self._save_response_limit_state(user_id, state)
