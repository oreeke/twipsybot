import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from cachetools import TTLCache

from ...db.sqlite import DBManager
from ...shared.config import Config
from ...shared.config_keys import ConfigKeys
from ...shared.constants import RESPONSE_LIMIT_CACHE_MAX, RESPONSE_LIMIT_CACHE_TTL
from ...shared.utils import normalize_tokens


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
            maxsize=RESPONSE_LIMIT_CACHE_MAX,
            ttl=RESPONSE_LIMIT_CACHE_TTL,
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

    def _in_user_list(self, key: str, *, user_id: str, handle: str | None) -> bool:
        users = set(normalize_tokens(self._config.get(key), lower=True))
        return bool(users) and not users.isdisjoint(
            self._user_candidates(user_id=user_id, handle=handle)
        )

    def is_response_blacklisted_user(self, *, user_id: str, handle: str | None) -> bool:
        return self._in_user_list(
            ConfigKeys.REPLY_BLACKLIST, user_id=user_id, handle=handle
        )

    def _duration_config_seconds(self, key: str) -> int:
        value = self._config.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

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
        if any(
            self._in_user_list(key, user_id=user_id, handle=handle)
            for key in (ConfigKeys.REPLY_WHITELIST, ConfigKeys.BOT_ADMINS)
        ):
            return False, ""
        now = time.time()
        state = await self._get_response_limit_state(user_id)
        if state.blocked_until_ts is not None and now >= state.blocked_until_ts:
            state.turns = 0
            state.blocked_until_ts = None
            await self._save_response_limit_state(user_id, state)
        if state.blocked_until_ts is not None and now < state.blocked_until_ts:
            return True, self._config.get(ConfigKeys.REPLY_MAX_TURNS_MSG)
        interval = self._duration_config_seconds(ConfigKeys.REPLY_RATE_LIMIT)
        if (
            interval > 0
            and state.last_reply_ts is not None
            and now - state.last_reply_ts < interval
        ):
            return True, self._config.get(ConfigKeys.REPLY_RATE_LIMIT_MSG)
        max_turns = self._config.get(ConfigKeys.REPLY_MAX_TURNS)
        if self._turns_limited() and state.turns >= max_turns:
            release = self._duration_config_seconds(ConfigKeys.REPLY_TURNS_RELEASE)
            if release < 0:
                await self._blacklist_user(user_id)
                state.turns = 0
                state.blocked_until_ts = None
            else:
                state.blocked_until_ts = now + release
            await self._save_response_limit_state(user_id, state)
            return True, self._config.get(ConfigKeys.REPLY_MAX_TURNS_MSG)
        return False, ""

    def _turns_limited(self) -> bool:
        max_turns = self._config.get(ConfigKeys.REPLY_MAX_TURNS)
        return isinstance(max_turns, int) and max_turns >= 0

    async def record_response(self, user_id: str, *, count_turn: bool) -> None:
        state = await self._get_response_limit_state(user_id)
        state.last_reply_ts = time.time()
        if count_turn and self._turns_limited():
            state.turns += 1
        await self._save_response_limit_state(user_id, state)
