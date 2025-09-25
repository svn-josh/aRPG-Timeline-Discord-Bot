import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

API_BASE = os.getenv("ARPG_API_BASE")
TOKEN_URL = os.getenv("ARPG_TOKEN_URL")
CLIENT_ID = os.getenv("ARPG_CLIENT_ID")
CLIENT_SECRET = os.getenv("ARPG_CLIENT_SECRET")


@dataclass
class Game:
    slug: str
    name: str
    season_keyword: Optional[str]
    categories: List[str]


@dataclass
class Season:
    game_slug: str
    game_name: str
    season_key: str
    title: str
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    url: Optional[str]
    patch_notes_url: Optional[str]


def _to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_game(raw: Dict[str, Any]) -> Optional[Game]:
    slug = str(raw.get("slug") or "").strip()
    name = str(raw.get("name") or slug.title()).strip()
    if not slug:
        return None
    season_keyword = raw.get("seasonKeyword") or raw.get("season_keyword")
    cats = raw.get("categories")
    if isinstance(cats, list):
        categories = [str(c) for c in cats]
    else:
        categories = []
    return Game(slug=slug, name=name, season_keyword=season_keyword, categories=categories)


def _current_season_from_entry(entry: Dict[str, Any]) -> Optional[Season]:
    """Build a Season object from the 'current' block of a games/seasons entry."""
    game_slug = str(entry.get("game") or "").strip().lower()
    game_name = game_slug.replace("-", " ").title() if game_slug else "Unknown"
    block = entry.get("current") or {}
    if not isinstance(block, dict):
        return None
    title = str(block.get("name") or f"{game_name} Season")
    starts_at = _to_dt(block.get("start"))
    ends_at = _to_dt(block.get("end"))
    url = block.get("url") or None
    patch_notes_url = block.get("patchNotesUrl") or None
    season_key = str(block.get("name") or block.get("id") or block.get("slug") or block.get("code") or "")
    if starts_at:
        season_key = f"{season_key}:{int(starts_at.timestamp())}"
    if not season_key:
        return None
    return Season(
        game_slug=game_slug,
        game_name=game_name,
        season_key=season_key,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        url=url,
        patch_notes_url=patch_notes_url,
    )


def _next_season_from_entry(entry: Dict[str, Any]) -> Optional[Season]:
    """Build a Season object from the 'next' block (upcoming) if present."""
    game_slug = str(entry.get("game") or "").strip().lower()
    game_name = game_slug.replace("-", " ").title() if game_slug else "Unknown"
    block = entry.get("next") or {}
    if not isinstance(block, dict) or not block:
        return None
    title = str(block.get("name") or f"{game_name} Upcoming")
    starts_at = _to_dt(block.get("start"))
    ends_at = _to_dt(block.get("end"))
    url = block.get("url") or None
    patch_notes_url = block.get("patchNotesUrl") or None
    season_key_base = str(block.get("name") or block.get("id") or block.get("slug") or block.get("code") or "")
    if starts_at:
        season_key = f"{season_key_base}:{int(starts_at.timestamp())}"
    else:
        season_key = season_key_base
    if not season_key:
        return None
    # Intentionally no prefix so when 'next' becomes 'current' it yields same key; prevents duplicate.
    return Season(
        game_slug=game_slug,
        game_name=game_name,
        season_key=season_key,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        url=url,
        patch_notes_url=patch_notes_url,
    )


class ARPGApiClient:
    def __init__(self, *, session: Optional[aiohttp.ClientSession] = None, db=None, logger=None) -> None:
        self.session = session
        self.db = db
        self.logger = logger
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._token_fail_until: Optional[datetime] = None

    def _token_key(self) -> str:
        if TOKEN_URL and TOKEN_URL.strip():
            return f"token:{TOKEN_URL.strip()}"
        if API_BASE and API_BASE.strip():
            return f"token:{API_BASE.rstrip('/')}/token"
        return "arpg_api"

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self.session

    async def _fetch_token(self) -> Optional[Tuple[str, datetime]]:
        if not CLIENT_ID or not CLIENT_SECRET:
            if self.logger:
                self.logger.error("ARPG API credentials not set (ARPG_CLIENT_ID/ARPG_CLIENT_SECRET)")
            return None
        sess = await self.get_session()
        payload = {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}

        token_urls: List[str] = []
        if TOKEN_URL:
            token_urls.append(TOKEN_URL)
        elif API_BASE:
            token_urls.append(f"{API_BASE.rstrip('/')}/token")
        else:
            if self.logger:
                self.logger.error("ARPG_TOKEN_URL or ARPG_API_BASE must be set in environment.")
            return None

        for turl in token_urls:
            try:
                if self.logger:
                    self.logger.info(f"Token request POST {turl} (json camel)")
                async with sess.post(turl, json=payload, headers={"Accept": "application/json"}) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        if self.logger:
                            self.logger.warning(f"Token request failed HTTP {resp.status} | body={body[:300]}")
                        continue
                    data = await resp.json(content_type=None)
                    token = data.get("access_token") or data.get("token") or data.get("jwt")
                    if not token:
                        continue
                    now = datetime.now(timezone.utc)
                    if "expires_in" in data:
                        exp = now + timedelta(seconds=float(data["expires_in"]))
                    elif "exp" in data:
                        exp = datetime.fromtimestamp(float(data["exp"]), tz=timezone.utc)
                    elif "expires_at" in data:
                        try:
                            exp = datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00"))
                        except Exception:
                            exp = now + timedelta(hours=1)
                    else:
                        exp = now + timedelta(hours=1)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    return str(token), exp
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Token request error at {turl}: {e}")
        return None

    async def _get_access_token(self) -> Optional[str]:
        now = datetime.now(timezone.utc)
        if self._token_fail_until and now < self._token_fail_until:
            return None

        def valid(exp: Optional[datetime]) -> bool:
            return exp is not None and (exp - now) > timedelta(seconds=60)

        if self._access_token and valid(self._token_expires_at):
            return self._access_token

        try:
            if self.db:
                # specific -> legacy -> latest
                token_key = self._token_key()
                token, exp_iso = await self.db.get_api_token(token_key)
                if not token:
                    token, exp_iso = await self.db.get_api_token("arpg_api")
                if not token:
                    _, token, exp_iso = await self.db.get_latest_api_token()
                if token and exp_iso:
                    try:
                        exp_str = str(exp_iso).strip()
                        if "Z" in exp_str:
                            exp_str = exp_str.replace("Z", "+00:00")
                        exp_dt = datetime.fromisoformat(exp_str)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        exp_dt = None
                    if valid(exp_dt):
                        self._access_token = token
                        self._token_expires_at = exp_dt
                        return token
        except Exception as e:
            if self.logger:
                self.logger.warning(f"DB token read failed: {e}")

        token_data = await self._fetch_token()
        if not token_data:
            self._token_fail_until = now + timedelta(minutes=10)
            return None
        token, exp = token_data
        self._access_token = token
        self._token_expires_at = exp
        self._token_fail_until = None

        try:
            if self.db:
                exp_iso = exp.astimezone(timezone.utc).isoformat()
                await self.db.set_api_token(self._token_key(), token, exp_iso)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"DB token write failed: {e}")
        return self._access_token

    async def fetch_games(self) -> List[Game]:
        sess = await self.get_session()
        if not API_BASE:
            if self.logger:
                self.logger.error("ARPG_API_BASE must be set in environment.")
            return []
        url = f"{API_BASE.rstrip('/')}/games"
        try:
            token = await self._get_access_token()
            headers = {"Accept": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if self.logger:
                self.logger.info(f"GET {url}")
            async with sess.get(url, headers=headers) as resp:
                if resp.status == 401:
                    self._access_token = None
                    try:
                        if self.db:
                            await self.db.set_api_token(self._token_key(), "", None)
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"DB token invalidate failed: {e}")
                    token = await self._get_access_token()
                    headers = {"Accept": "application/json"}
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                    if self.logger:
                        self.logger.info(f"GET {url} (after token refresh)")
                    async with sess.get(url, headers=headers) as resp2:
                        if resp2.status != 200:
                            body = await resp2.text()
                            if self.logger:
                                self.logger.warning(f"Games fetch failed HTTP {resp2.status} | body={body[:300]}")
                            return []
                        data = await resp2.json(content_type=None)
                else:
                    if resp.status != 200:
                        body = await resp.text()
                        if self.logger:
                            self.logger.warning(f"Games fetch failed HTTP {resp.status} | body={body[:300]}")
                        return []
                    data = await resp.json(content_type=None)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to fetch games: {e}")
            return []

        items: List[Dict[str, Any]]
        if isinstance(data, dict) and isinstance(data.get("games"), list):
            items = data["games"]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        out: List[Game] = []
        for raw in items:
            g = _normalize_game(raw)
            if g:
                out.append(g)
        return out

    async def fetch_active_seasons(self) -> List[Season]:
        """Fetch active seasons plus upcoming next seasons from /games/seasons (scope=active).

        For each game entry, both the 'current' and (if present) 'next' blocks are returned.
        The season_key is constructed identically for current and next so that when a 'next' season
        transitions to 'current' it does not produce duplicate announcements (already seen).
        """
        sess = await self.get_session()
        if not API_BASE:
            if self.logger:
                self.logger.error("ARPG_API_BASE must be set in environment.")
            return []
        url = f"{API_BASE.rstrip('/')}/games/seasons?scope=active"
        try:
            token = await self._get_access_token()
            headers = {"Accept": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if self.logger:
                self.logger.info(f"GET {url}")
            async with sess.get(url, headers=headers) as resp:
                if resp.status == 401:
                    self._access_token = None
                    token = await self._get_access_token()
                    headers = {"Accept": "application/json"}
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                    if self.logger:
                        self.logger.info(f"GET {url} (after token refresh)")
                    async with sess.get(url, headers=headers) as resp2:
                        if resp2.status != 200:
                            body = await resp2.text()
                            if resp2.status == 400 and "scope" in body:
                                # already has scope param; nothing else to do
                                if self.logger:
                                    self.logger.warning(
                                        f"Seasons fetch failed (scope issue) HTTP {resp2.status} | body={body[:300]}"
                                    )
                            else:
                                if self.logger:
                                    self.logger.warning(
                                        f"Seasons fetch failed HTTP {resp2.status} | body={body[:300]}"
                                    )
                            return []
                        data = await resp2.json(content_type=None)
                else:
                    if resp.status != 200:
                        body = await resp.text()
                        if resp.status == 400 and "scope" in body:
                            # maybe server expected explicit scope; we already added it, treat as failure
                            if self.logger:
                                self.logger.warning(
                                    f"Seasons fetch failed (scope) HTTP {resp.status} | body={body[:300]}"
                                )
                            return []
                        else:
                            if self.logger:
                                self.logger.warning(
                                    f"Seasons fetch failed HTTP {resp.status} | body={body[:300]}"
                                )
                            return []
                    data = await resp.json(content_type=None)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to fetch active seasons: {e}")
            return []

        # Expecting { "seasons": [ { game, current {..}, next {...}? }, ... ] }
        items: List[Dict[str, Any]] = []
        if isinstance(data, dict) and isinstance(data.get("seasons"), list):
            items = data["seasons"]
        elif isinstance(data, list):
            items = data

        out: List[Season] = []
        for entry in items:
            cur = _current_season_from_entry(entry)
            if cur:
                out.append(cur)
            nxt = _next_season_from_entry(entry)
            if nxt:
                # Skip if duplicate key already present (same key logic as current)
                if not any(existing.season_key == nxt.season_key and existing.game_slug == nxt.game_slug for existing in out):
                    out.append(nxt)
        return out

    async def get_cached_games(self, ttl_minutes: int = 30) -> List[Game]:
        """Return cached games if fresh; otherwise fetch and cache them in DB."""
        key = "games:list"
        try:
            if self.db:
                value_json, exp_iso = await self.db.get_api_cache(key)
                if value_json and exp_iso:
                    try:
                        exp_str = exp_iso.replace("Z", "+00:00")
                        exp_dt = datetime.fromisoformat(exp_str)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                        if exp_dt > datetime.now(timezone.utc):
                            arr = json.loads(value_json)
                            out: List[Game] = []
                            for raw in arr:
                                g = _normalize_game(raw)
                                if g:
                                    out.append(g)
                            return out
                    except Exception:
                        pass
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Cache read failed: {e}")

        games = await self.fetch_games()
        try:
            if self.db and games:
                # store as list of dicts
                payload = json.dumps([
                    {"slug": g.slug, "name": g.name, "seasonKeyword": g.season_keyword, "categories": g.categories}
                    for g in games
                ])
                exp = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
                await self.db.set_api_cache(key, payload, exp)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Cache write failed: {e}")
        return games

    async def get_cached_active_seasons(self, ttl_minutes: int = 5, force_refresh: bool = False) -> List[Season]:
        """Return cached active seasons (via /games/seasons) if fresh unless force_refresh is True."""
        key = "seasons:active"
        now = datetime.now(timezone.utc)
        if not force_refresh and self.db:
            try:
                value_json, exp_iso = await self.db.get_api_cache(key)
                if value_json and exp_iso:
                    try:
                        exp_str = exp_iso.replace("Z", "+00:00")
                        exp_dt = datetime.fromisoformat(exp_str)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                        if exp_dt > now:
                            raw_list = json.loads(value_json)
                            out: List[Season] = []
                            for obj in raw_list:
                                # reconstruct Season
                                try:
                                    starts = _to_dt(obj.get("starts_at")) if obj.get("starts_at") else None
                                    ends = _to_dt(obj.get("ends_at")) if obj.get("ends_at") else None
                                    out.append(Season(
                                        game_slug=obj.get("game_slug"),
                                        game_name=obj.get("game_name"),
                                        season_key=obj.get("season_key"),
                                        title=obj.get("title"),
                                        starts_at=starts,
                                        ends_at=ends,
                                        url=obj.get("url"),
                                        patch_notes_url=obj.get("patch_notes_url"),
                                    ))
                                except Exception:
                                    continue
                            return out
                    except Exception:
                        pass
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Season cache read failed: {e}")

        seasons = await self.fetch_active_seasons()
        if self.db and seasons:
            try:
                payload = json.dumps([
                    {
                        "game_slug": s.game_slug,
                        "game_name": s.game_name,
                        "season_key": s.season_key,
                        "title": s.title,
                        "starts_at": s.starts_at.isoformat() if s.starts_at else None,
                        "ends_at": s.ends_at.isoformat() if s.ends_at else None,
                        "url": s.url,
                        "patch_notes_url": s.patch_notes_url,
                    }
                    for s in seasons
                ])
                exp = (now + timedelta(minutes=ttl_minutes)).isoformat()
                await self.db.set_api_cache(key, payload, exp)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Season cache write failed: {e}")
        return seasons
