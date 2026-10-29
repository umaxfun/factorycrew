#!/usr/bin/env python3
"""Bridge between the kc-companion mod (UDP) and the dispatcher / MCP server (HTTP on 127.0.0.1).

Stdlib only. One asyncio loop holds:
  * a UDP socket the game sends to (BRIDGE_PORT, mod constant 34199);
  * an HTTP API for the dispatcher (long-poll asks) and the MCP server (query/mark/unmark/reply).

Protocol: SPEC.md, "Mod <-> bridge protocol".
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field

VERSION = 1
PACKET_MAX = 4000  # bytes per UDP packet to the game; see encode()
MAX_PARTS = 1024
OUTBOX_LIMIT = 200  # replies/statuses waiting for an offline game
ATLAS_TIMEOUT = 300.0  # a big base at a small per-tick budget takes a while; the game never stalls for it
ACTIVITY_MAX = 80  # chars of a progress note shown in the window's status line
TRACE_MAX = 240  # chars of one trace line (what Kira wrote / which tool she ran) for the window

log = logging.getLogger("factorio-bridge")


class BridgeError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass
class Settings:
    host: str = "127.0.0.1"
    http_host: str = "127.0.0.1"  # 0.0.0.0 only inside a test container whose port is published to host loopback
    udp_port: int = 34199
    game_port: int = 34200
    http_port: int = 34198
    offline_after: float = 15.0  # no heartbeat for this long => game offline
    idle_after: float = 90.0  # thinking without reply for this long => idle "no_reply"
    request_timeout: float = 10.0  # query/mark/unmark waiting for the game's result
    atlas_prefetch: bool = True  # read the player's surface for the atlas when a game connects
    lease: float = 120.0  # an ask taken by the dispatcher returns to the queue unless confirmed


@dataclass
class GameState:
    last_seen: float | None = None
    run_id: str | None = None
    run_name: str | None = None
    tick: int | None = None
    tabs: list = field(default_factory=list)
    atlas_version: int | None = None
    atlas_dirty: int | None = None


# ── framing ──────────────────────────────────────────────────────────────


def ago(t: float | None) -> float | None:
    """Seconds since a time.monotonic() stamp, or None if it never happened."""
    return None if t is None else round(time.monotonic() - t, 1)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def encode(msg: dict) -> list[bytes]:
    """Split into UDP packets of at most PACKET_MAX bytes each.

    The game's receive buffer starts at 4 KB and only doubles after it has DROPPED a packet that
    did not fit (measured on 2.1.23: 8000 lost, then 8150 ok; 8192 lost, then 8193 ok). So every
    packet to the game must fit the initial buffer, measured in encoded bytes, escapes included.
    """
    msg = dict(msg, v=VERSION)
    s = _dump(msg)
    if len(s.encode()) <= PACKET_MAX:
        return [s.encode()]
    head = len(_dump({"v": VERSION, "type": "part", "id": msg.get("id"), "part": 99999,
                      "parts": 99999, "data": ""}).encode())
    budget = PACKET_MAX - head
    slices, cur, cost = [], [], 0
    for ch in s:
        c = len(_dump(ch).encode()) - 2  # bytes this char takes inside a JSON string
        if cost + c > budget:
            slices.append("".join(cur))
            cur, cost = [], 0
        cur.append(ch)
        cost += c
    if cur:
        slices.append("".join(cur))
    return [_dump({"v": VERSION, "type": "part", "id": msg.get("id"), "part": i + 1,
                   "parts": len(slices), "data": sl}).encode() for i, sl in enumerate(slices)]


class Reassembler:
    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}

    def feed(self, payload: bytes) -> dict | None:
        try:
            msg = json.loads(payload.decode("utf-8", errors="replace"))
        except ValueError:
            log.warning("dropping non-JSON packet (%d bytes)", len(payload))
            return None
        if not isinstance(msg, dict):
            return None
        if msg.get("type") != "part":
            return msg
        key, n, i = str(msg.get("id")), msg.get("parts"), msg.get("part")
        if not isinstance(n, int) or not isinstance(i, int) or not 1 <= i <= n <= MAX_PARTS:
            return None
        p = self.pending.setdefault(key, {"parts": n, "slices": {}, "t": time.monotonic()})
        p["slices"][i] = msg.get("data") or ""
        if len(p["slices"]) < p["parts"]:
            return None
        del self.pending[key]
        whole = "".join(p["slices"][k] for k in range(1, n + 1))
        return self.feed(whole.encode())

    def gc(self, older_than: float = 30.0) -> None:
        now = time.monotonic()
        for k in [k for k, p in self.pending.items() if now - p["t"] > older_than]:
            del self.pending[k]


# ── atlas store: the structure of a surface, merged from a full dump and partial ones ──


class AtlasStore:
    """Entity lines kept per chunk with their names resolved, so a partial dump (only the chunks
    that changed, with its own name table) can replace those chunks, and the merged base can be
    served as one dump again (mod/kc-companion/scripts/atlas.lua has the format)."""

    def __init__(self, text: str, data: dict, run_id):
        self.run_id = run_id
        self.names: dict[str, tuple] = {}   # name -> (type, w, h, extras)
        self.chunks: dict[tuple, list] = {}  # (cx, cy) -> [(name, rest of the E line)]
        self.recipes: dict[str, str] = {}
        self.trains: list[str] = []
        self.head = ""
        self.tick = None
        self.version = None
        self.apply(text, data, full=True)

    def apply(self, text: str, data: dict, full: bool) -> int:
        ids: dict[str, str] = {}
        chunks: dict[tuple, list] = {}
        listed: set[tuple] = set()
        recipes: dict[str, str] = {}
        trains: list[str] = []
        for line in text.split("\n"):
            k = line[:2]
            if k == "E|":
                f = line.split("|", 5)
                name = ids[f[1]]
                key = (int(f[2]) // 64, int(f[3]) // 64)  # doubled coordinates: 64 per chunk
                chunks.setdefault(key, []).append((name, "|".join(f[2:])))
            elif k == "N|":
                f = line.split("|", 6)
                ids[f[1]] = f[2]
                self.names[f[2]] = (f[3], f[4], f[5], f[6] if len(f) > 6 else "")
            elif k == "R|":
                recipes[line.split("|", 2)[1]] = line
            elif k == "T|":
                trains.append(line)
            elif k == "C|":
                _, cx, cy = line.split("|")
                listed.add((int(cx), int(cy)))
            elif k == "H|":
                self.head = line
                try:
                    self.tick = int(line.split("|")[3])
                except (IndexError, ValueError):
                    self.tick = None
        if full:
            self.chunks = chunks
        else:
            for key in listed:
                self.chunks.pop(key, None)
            self.chunks.update(chunks)
        self.recipes.update(recipes)
        self.trains = trains
        self.version = (data or {}).get("version")
        return len(listed) if not full else len(chunks)

    def text(self) -> str:
        out, ids = [], {}
        h = self.head.split("|")
        out.append("|".join(h[:5] + ["full"]))
        body = []
        n = 0
        for key in sorted(self.chunks):
            for name, rest in self.chunks[key]:
                if name not in ids:
                    n += 1
                    ids[name] = n
                    t, w, hh, ex = self.names[name]
                    out.append(f"N|{n}|{name}|{t}|{w}|{hh}" + (f"|{ex}" if ex else ""))
                body.append(f"E|{ids[name]}|{rest}")
        out += body
        out += list(self.recipes.values()) + self.trains
        out.append(f"Z|{len(body)}|{len(self.chunks)}|0")
        return "\n".join(out)


# ── bridge ───────────────────────────────────────────────────────────────


def normalize_lists(kind: str, res: dict) -> dict:
    """Factorio's table_to_json renders an empty Lua table as {}; give list fields a stable [] shape."""
    def fix(v):
        return [] if v == {} else v
    data = res.get("data")
    if kind == "mark" and isinstance(data, dict):
        data["ids"], data["failed"] = fix(data.get("ids", [])), fix(data.get("failed", []))
    elif kind == "list_marks":
        res["data"] = fix(data if data is not None else [])
    return res


class Bridge(asyncio.DatagramProtocol):
    def __init__(self, settings: Settings):
        self.s = settings
        self.game = GameState()
        self.transport: asyncio.DatagramTransport | None = None
        self.reasm = Reassembler()
        self.pending: dict[str, asyncio.Future] = {}
        self.atlas_parts: dict[str, dict[int, str]] = {}  # request id -> seq -> text of a running dump
        self.atlas_store: dict[str, AtlasStore] = {}  # requested surface ("" = the player's) -> structure
        self.atlas_lock = asyncio.Lock()
        self.asks: deque[dict] = deque()  # waiting for the dispatcher
        self.leased: dict[str, tuple[dict, float]] = {}  # taken, not yet confirmed
        self.seen_asks: deque[str] = deque(maxlen=1000)
        self.ask_event = asyncio.Event()
        self.outbox: deque[dict] = deque()  # reply/status for an offline game
        self.idle_timers: dict[tuple, asyncio.TimerHandle] = {}
        self.working: dict[tuple, dict] = {}  # (run_id, chat) thinking -> {"steps", "activity"}
        self.answered: set[tuple] = set()  # (run_id, chat) whose last question got an answer
        self.current_ask: dict[tuple, str | None] = {}  # (run_id, chat) -> id of its last question
        self.ids = itertools.count(1)
        self.dispatcher_seen: float | None = None  # last GET /asks
        self.agent_seen: float | None = None  # last tool call of Kira's (via the MCP server)

    # ── UDP side ──

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        msg = self.reasm.feed(data)
        if msg is not None:
            try:
                self.on_game_message(msg)
            except Exception:  # never let one bad packet kill the loop
                log.exception("failed to handle %r", msg.get("type"))

    def send_game(self, msg: dict) -> None:
        if not self.transport:
            return
        for packet in encode(msg):
            self.transport.sendto(packet, (self.s.host, self.s.game_port))

    def online(self) -> bool:
        return self.game.last_seen is not None and time.monotonic() - self.game.last_seen <= self.s.offline_after

    def on_game_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t in ("hello", "heartbeat"):
            g = self.game
            g.last_seen = time.monotonic()
            fresh = t == "hello" or g.run_id != msg.get("run_id")
            g.run_id, g.run_name, g.tick = msg.get("run_id"), msg.get("run_name"), msg.get("tick")
            g.tabs = msg.get("tabs") or []
            g.atlas_version = msg.get("atlas_version")
            g.atlas_dirty = msg.get("atlas_dirty")
            self.send_game({"type": "pong", "id": msg.get("id")})
            self.flush_outbox()
            if fresh and self.s.atlas_prefetch:
                asyncio.get_running_loop().create_task(self.atlas_prefetch())
        elif t == "ask":
            self.game.last_seen = time.monotonic()
            self.send_game({"type": "ack", "id": msg.get("id")})
            if msg.get("id") in self.seen_asks:
                return
            self.seen_asks.append(msg.get("id"))
            self.asks.append({k: msg.get(k) for k in ("id", "chat", "text", "run_id", "run_name", "tick", "player",
                                                    "pointers")})
            self.ask_event.set()
            log.info("ask %s [%s] %r", msg.get("id"), msg.get("chat"), msg.get("text"))
        elif t == "library":
            # the window's saved-panel library: the dispatcher (inside Kiro Crew) reads the
            # artifacts and posts /library_result; it rides the ask queue but is no question
            self.game.last_seen = time.monotonic()
            if msg.get("id") in self.seen_asks:
                return
            self.seen_asks.append(msg.get("id"))
            self.asks.append({"kind": "library", **{k: msg.get(k) for k in ("id", "op", "slug", "run_id", "run_name")}})
            self.ask_event.set()
            log.info("library %s %s %s", msg.get("id"), msg.get("op"), msg.get("slug") or "")
        elif t == "atlas_part":
            # a slice of an atlas dump in progress; the final "result" says how many there were
            parts = self.atlas_parts.get(str(msg.get("id")))
            if parts is not None and isinstance(msg.get("seq"), int) and isinstance(msg.get("text"), str):
                parts[msg["seq"]] = msg["text"]
        elif t == "result":
            fut = self.pending.pop(str(msg.get("id")), None)
            if fut and not fut.done():
                fut.set_result(msg)
        else:
            log.debug("ignoring %r", t)

    def flush_outbox(self) -> None:
        keep: deque[dict] = deque()
        while self.outbox:
            m = self.outbox.popleft()
            if m.get("run_id") in (None, self.game.run_id):
                self.send_game(m)
            else:
                keep.append(m)  # meant for a run that is not loaded now
        self.outbox = keep

    def deliver(self, msg: dict) -> bool:
        """Send now if the game is online, else queue. Returns True if sent now."""
        if self.online():
            self.send_game(msg)
            return True
        self.outbox.append(msg)
        while len(self.outbox) > OUTBOX_LIMIT:
            self.outbox.popleft()
        return False

    # ── requests to the game ──

    async def request(self, msg: dict, timeout: float | None = None) -> dict:
        if not self.online():
            raise BridgeError("game_offline", "no heartbeat from the game; is it running with --enable-lua-udp and not paused?")
        rid = f"b{next(self.ids)}"
        msg = dict(msg, id=rid)
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        if msg["type"] == "atlas":
            self.atlas_parts[rid] = {}
        started = time.monotonic()
        self.send_game(msg)
        try:
            res = await asyncio.wait_for(fut, timeout or self.s.request_timeout)
        except asyncio.TimeoutError:
            raise BridgeError("game_timeout", "the game did not answer in time (paused, or a query is still running)")
        finally:
            self.pending.pop(rid, None)
            parts = self.atlas_parts.pop(rid, None)
        res = {k: v for k, v in res.items() if k not in ("v", "type", "id")}
        if msg["type"] == "atlas" and res.get("ok"):
            want = (res.get("data") or {}).get("parts")
            if not isinstance(want, int) or sorted(parts or {}) != list(range(1, want + 1)):
                raise BridgeError("atlas_incomplete", f"got {len(parts or {})} of {want} dump parts (a packet was lost); "
                                                      "ask again")
            res["text"] = "\n".join(parts[i] for i in range(1, want + 1))
        res["ms"] = round((time.monotonic() - started) * 1000)
        return normalize_lists(msg["type"], res)

    # ── atlas ──

    async def atlas(self, surface: str, refresh: bool, have_version, timeout: float) -> dict:
        async with self.atlas_lock:
            st = self.atlas_store.get(surface)
            g = self.game
            started = time.monotonic()
            how = "cached"
            if refresh or st is None or st.run_id != g.run_id or g.atlas_dirty == -1:
                r = await self.request({"type": "atlas", "surface": surface or None}, timeout)
                if not r.get("ok"):
                    return r
                st = AtlasStore(r["text"], r.get("data"), g.run_id)
                self.atlas_store[surface] = st
                how, read = "full", len(st.chunks)
            elif (g.tick is None or st.tick is None or g.tick > st.tick) and \
                    (g.atlas_version != st.version or g.atlas_dirty):
                # a heartbeat from before the last dump says nothing about what changed after it
                r = await self.request({"type": "atlas", "surface": surface or None, "chunks": "dirty"}, timeout)
                if not r.get("ok"):
                    return r
                data = r.get("data") or {}
                if data.get("partial"):
                    read = st.apply(r["text"], data, full=False)
                    how = "partial"
                else:
                    st = AtlasStore(r["text"], data, g.run_id)
                    self.atlas_store[surface] = st
                    how, read = "full", len(st.chunks)
            else:
                read = 0
            out = {"ok": True, "version": st.version, "how": how, "chunks_read": read,
                   "ms": round((time.monotonic() - started) * 1000)}
            if have_version is not None and have_version == st.version and how == "cached":
                out["unchanged"] = True
            else:
                out["text"] = st.text()
            return out

    async def atlas_prefetch(self) -> None:
        """Read the player's surface once the game is online, so the first question does not wait
        for a full dump (a large base takes about a minute in the background)."""
        try:
            await asyncio.sleep(3)
            if self.online() and "" not in self.atlas_store:
                await self.atlas("", False, None, ATLAS_TIMEOUT)
                log.info("atlas prefetched: %d chunks", len(self.atlas_store[""].chunks))
        except Exception:
            log.exception("atlas prefetch failed")

    # ── asks / thinking ──

    def requeue_expired(self) -> None:
        now = time.monotonic()
        for aid in [a for a, (_, exp) in self.leased.items() if exp <= now]:
            ask, _ = self.leased.pop(aid)
            self.asks.appendleft(ask)
            log.info("lease expired, ask %s back in the queue", aid)

    async def take_asks(self, wait: float, lease: float | None) -> list[dict]:
        deadline = time.monotonic() + wait
        while True:
            self.requeue_expired()
            if self.asks:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            self.ask_event.clear()
            # wake also for lease expiry
            nearest = min((exp for _, exp in self.leased.values()), default=None)
            if nearest is not None:
                remaining = min(remaining, max(0.05, nearest - time.monotonic()))
            try:
                await asyncio.wait_for(self.ask_event.wait(), remaining)
            except asyncio.TimeoutError:
                pass
        out = []
        exp = time.monotonic() + (lease if lease is not None else self.s.lease)
        while self.asks:
            ask = self.asks.popleft()
            self.leased[ask["id"]] = (ask, exp)
            out.append(ask)
            if ask.get("kind") != "library":  # a library request starts no turn
                self.set_thinking(ask["run_id"], ask["chat"], ask.get("id"))
        return out

    def confirm_asks(self, ids: list[str]) -> int:
        return sum(1 for i in ids if self.leased.pop(i, None) is not None)

    def set_thinking(self, run_id, chat, ask_id=None) -> None:
        key = (run_id, chat)
        self.working[key] = {"steps": 0, "activity": ""}
        self.answered.discard(key)  # a new question: its answer is still to come
        self.current_ask[key] = ask_id
        self.deliver({"type": "status", "run_id": run_id, "chat": chat, "state": "thinking"})
        self.arm_idle(key)

    def arm_idle(self, key) -> None:
        old = self.idle_timers.pop(key, None)
        if old:
            old.cancel()
        self.idle_timers[key] = asyncio.get_running_loop().call_later(self.s.idle_after, self.idle_timeout, key)

    def activity(self, chat: str | None, text: str, step: bool = True) -> int:
        """A sign of life from a running turn: count the step, show what it does, restart the idle clock.

        The agent's tool calls carry no ask id, and factorio_query carries no chat, so a call without
        a chat counts for every tab of the current run that is thinking (normally exactly one).
        Returns how many tabs were updated.
        """
        keys = [k for k in self.working if k[0] == self.game.run_id and (chat is None or k[1] == chat)]
        for key in keys:
            w = self.working[key]
            if step:
                w["steps"] += 1
            if text:
                w["activity"] = text[:ACTIVITY_MAX]
            self.deliver({"type": "status", "run_id": key[0], "chat": key[1], "state": "thinking",
                          "steps": w["steps"], "activity": w["activity"]})
            self.arm_idle(key)
        return len(keys)

    def idle_timeout(self, key) -> None:
        self.idle_timers.pop(key, None)
        self.working.pop(key, None)
        run_id, chat = key
        log.info("no reply in %ss for [%s], sending idle", self.s.idle_after, chat)
        self.deliver({"type": "status", "run_id": run_id, "chat": chat, "state": "idle", "note": "no_reply"})

    def reply(self, chat: str, text: str, fallback: bool = False, ask_id: str | None = None) -> dict:
        """An answer for a tab. `fallback`: the dispatcher's copy of the turn's last text, sent when
        it saw no factorio_reply in the transcript; dropped if this turn already answered, or if
        `ask_id` is not the tab's current question (a copy of an earlier turn). The transcript is
        not a reliable signal for either; the bridge's own record is."""
        run_id = self.game.run_id
        key = (run_id, chat)
        if fallback and key in self.answered:
            log.info("dropped the dispatcher's copy for [%s]: the turn already answered", chat)
            return {"ok": True, "delivered": False, "queued": False, "skipped": "already_answered"}
        if fallback and ask_id and self.current_ask.get(key) != ask_id:
            log.info("dropped the dispatcher's copy for [%s]: %s is not its current question", chat, ask_id)
            return {"ok": True, "delivered": False, "queued": False, "skipped": "stale"}
        if fallback:
            log.info("forwarded the dispatcher's copy for [%s] (the turn had no factorio_reply)", chat)
        self.answered.add((run_id, chat))
        timer = self.idle_timers.pop((run_id, chat), None)
        if timer:
            timer.cancel()
        self.working.pop((run_id, chat), None)
        msg = {"type": "reply", "id": f"r{next(self.ids)}", "run_id": run_id, "chat": chat, "text": text}
        sent = self.deliver(msg)
        return {"ok": True, "delivered": sent, "queued": not sent}

    def health(self) -> dict:
        g = self.game
        return {
            "ok": True, "game_online": self.online(), "run_id": g.run_id, "run_name": g.run_name,
            "tick": g.tick, "tabs": g.tabs, "atlas_version": g.atlas_version, "atlas_dirty": g.atlas_dirty,
            "atlas_cached": {k or "player": v.version for k, v in self.atlas_store.items() if v.run_id == g.run_id},
            "last_seen_s": None if g.last_seen is None else round(time.monotonic() - g.last_seen, 2),
            "queued_asks": len(self.asks), "leased_asks": len(self.leased), "outbox": len(self.outbox),
            "dispatcher_seen_s": ago(self.dispatcher_seen), "agent_seen_s": ago(self.agent_seen),
        }

    # ── HTTP side ──

    async def route(self, method: str, path: str, q: dict, body: dict) -> tuple[int, dict]:
        # liveness of the two clients, for the app's status page: the dispatcher polls /asks,
        # the MCP server flags every tool call of Kira's with _agent
        if body.get("_agent"):
            self.agent_seen = time.monotonic()
        if method == "GET" and path == "/health":
            return 200, self.health()
        if method == "GET" and path == "/asks":
            self.dispatcher_seen = time.monotonic()
            wait = min(float(q.get("wait", 0)), 60.0)
            lease = float(q["lease"]) if "lease" in q else None
            return 200, {"ok": True, "asks": await self.take_asks(wait, lease)}
        if method == "POST" and path == "/asks/done":
            return 200, {"ok": True, "confirmed": self.confirm_asks(list(body.get("ids") or []))}
        if method == "POST" and path == "/reply":
            chat, text = body.get("chat"), body.get("text")
            if not isinstance(chat, str) or not isinstance(text, str):
                raise BridgeError("bad_request", "chat and text are required strings")
            ask_id = body.get("ask_id") if isinstance(body.get("ask_id"), str) else None
            return 200, self.reply(chat, text, fallback=body.get("fallback") is True, ask_id=ask_id)
        if method == "POST" and path == "/library_result":
            # the dispatcher's answer to a library request: a list, a panel spec, a deletion
            if not isinstance(body.get("id"), str):
                raise BridgeError("bad_request", "id is required")
            msg = {k: v for k, v in body.items() if k not in ("type", "v", "_agent")}
            msg["type"] = "library_result"
            return 200, {"ok": True, "delivered": self.deliver(msg)}
        if method == "POST" and path == "/trace":
            # a step of a running turn, from the dispatcher following the chat transcript
            chat, text, kind = body.get("chat"), body.get("text"), body.get("kind") or "text"
            if not isinstance(chat, str) or not isinstance(text, str):
                raise BridgeError("bad_request", "chat and text are required strings")
            key = (self.game.run_id, chat)
            if key not in self.working:
                return 200, {"ok": True, "sent": False}  # the turn already answered or timed out
            self.arm_idle(key)
            sent = self.deliver({"type": "trace", "run_id": key[0], "chat": chat, "kind": kind,
                                 "text": text[:TRACE_MAX]})
            return 200, {"ok": True, "sent": sent}
        if method == "POST" and path == "/alive":
            # the agent is busy off the game (factorio_compute): a step, or just a heartbeat
            text = body.get("text") if isinstance(body.get("text"), str) else ""
            n = self.activity(None, text, step=body.get("step", True) is not False) if body.get("_agent") else 0
            return 200, {"ok": True, "updated": n}
        if method == "POST" and path == "/progress":
            chat, text = body.get("chat"), body.get("text")
            if not isinstance(chat, str) or not isinstance(text, str):
                raise BridgeError("bad_request", "chat and text are required strings")
            return 200, {"ok": True, "updated": self.activity(chat, text, step=False)}
        if method == "POST" and path == "/atlas":
            # the structure of a surface (docs/atlas.md): kept here, refreshed by partial dumps of the
            # chunks that changed; `raw` asks the game for a dump as is, bypassing the store
            if body.get("_agent"):
                self.activity(None, "reading the base")
            if body.get("raw"):
                msg = {"type": "atlas", "surface": body.get("surface"), "budget": body.get("budget"),
                       "send_bytes": body.get("send_bytes"), "max_chunks": body.get("max_chunks")}
                async with self.atlas_lock:  # the mod runs one dump at a time (a prefetch may be running)
                    return 200, await self.request(msg, float(body.get("timeout") or ATLAS_TIMEOUT))
            return 200, await self.atlas(body.get("surface") or "", bool(body.get("refresh")),
                                         body.get("have_version"), float(body.get("timeout") or ATLAS_TIMEOUT))
        if method == "POST" and path in ("/query", "/mark", "/unmark", "/list_marks"):
            # Only the agent's own calls (the MCP server flags them) are steps of a turn: tests,
            # tools/ui_dev.py or anything else polling the game must not move the counter.
            if body.get("_agent"):
                chat = body.get("chat") if isinstance(body.get("chat"), str) else None
                self.activity(chat, "")
            msg = {k: v for k, v in body.items() if k not in ("id", "type", "v", "timeout", "_agent")}
            msg["type"] = path[1:]
            if path == "/query" and not isinstance(body.get("lua"), str):
                raise BridgeError("bad_request", "lua is required")
            if path in ("/mark", "/unmark") and not isinstance(body.get("chat"), str) and not body.get("all"):
                raise BridgeError("bad_request", "chat is required")
            return 200, await self.request(msg, body.get("timeout"))
        return 404, {"ok": False, "error": "not_found", "message": f"{method} {path}"}

    async def handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, obj = 500, {"ok": False, "error": "internal"}
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            method, target, _ = line.decode("latin-1").split(" ", 2)
            headers = {}
            while True:
                h = await reader.readline()
                if h in (b"\r\n", b"\n", b""):
                    break
                k, _, v = h.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            n = int(headers.get("content-length") or 0)
            raw = await reader.readexactly(n) if n else b""
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                raise BridgeError("bad_request", "body must be a JSON object")
            path, _, qs = target.partition("?")
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(qs).items()}
            status, obj = await self.route(method, path, q, body)
        except BridgeError as e:
            status, obj = 200, {"ok": False, "error": e.code, "message": e.message}
        except (ValueError, json.JSONDecodeError) as e:
            status, obj = 400, {"ok": False, "error": "bad_request", "message": str(e)}
        except Exception as e:
            log.exception("http handler failed")
            obj = {"ok": False, "error": "internal", "message": str(e)}
        data = json.dumps(obj, ensure_ascii=False).encode()
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "Error")
        try:
            writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
            await writer.drain()
        finally:
            writer.close()

    async def housekeeping(self) -> None:
        while True:
            await asyncio.sleep(5)
            self.reasm.gc()


async def serve(settings: Settings) -> None:
    loop = asyncio.get_running_loop()
    bridge = Bridge(settings)
    await loop.create_datagram_endpoint(lambda: bridge, local_addr=(settings.host, settings.udp_port))
    server = await asyncio.start_server(bridge.handle_http, settings.http_host, settings.http_port)
    log.info("listening udp %s:%d (game at :%d), http %s:%d", settings.host, settings.udp_port,
             settings.game_port, settings.http_host, settings.http_port)
    asyncio.create_task(bridge.housekeeping())
    async with server:
        await server.serve_forever()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    d = Settings()
    p.add_argument("--udp-port", type=int, default=d.udp_port)
    p.add_argument("--game-port", type=int, default=d.game_port)
    p.add_argument("--http-port", type=int, default=d.http_port)
    p.add_argument("--http-host", default=d.http_host,
                   help="HTTP bind address; keep 127.0.0.1 except inside a container (no auth!)")
    p.add_argument("--offline-after", type=float, default=d.offline_after)
    p.add_argument("--idle-after", type=float, default=d.idle_after)
    p.add_argument("--request-timeout", type=float, default=d.request_timeout)
    p.add_argument("--lease", type=float, default=d.lease)
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    # Loopback by default: the API has no auth, anyone who can reach it can run Lua in the game.
    s = Settings(udp_port=a.udp_port, game_port=a.game_port, http_port=a.http_port, http_host=a.http_host,
                 offline_after=a.offline_after, idle_after=a.idle_after,
                 request_timeout=a.request_timeout, lease=a.lease)
    try:
        asyncio.run(serve(s))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
