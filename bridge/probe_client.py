import json
import math
import socket
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BattleState:
    tick: int
    local_owner: int
    elixir: float
    elixir_raw: int
    hand_cards: list[int]
    deck_cards: list[int]
    crowns: tuple[int, int]
    entities: list[dict[str, Any]]
    raw: dict[str, Any]
    received_at: float = field(default_factory=time.perf_counter)

    @property
    def identity(self):
        return tuple(sorted((int(p['owner']), int(p.get('accountId', 0))) for p in self.raw['players']))

    @property
    def native_finalized(self):
        result = self.raw.get('battle_result')
        return isinstance(result, dict) and result.get('validated') is True and result.get('finalized') is True

    @property
    def native_winner(self):
        result = self.raw.get('battle_result', {})
        winner = result.get('world_result_raw') if self.native_finalized else None
        return winner if type(winner) is int and winner in (0, 1) else None


class ProbeClient:
    def __init__(self, host='127.0.0.1', port=26888, timeout=.4, account_id=None, owner=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.known_account_id = account_id
        self.owner_override = owner
        self.expected_deck = None
        self.last_error = None
        self.last_status = 'disconnected'
        self._last_tick = None
        self._identity = None
        self._last_change_time = 0.0
        self._fresh_ticks_count = 0
        self.last_query_ms = None

    def query(self):
        started = time.perf_counter()
        try:
            with socket.create_connection((self.host, self.port), self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(b'GET\n')
                data = bytearray()
                while b'\n' not in data and len(data) <= 1_048_576:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) > 1_048_576:
                    raise ValueError('probe response exceeds 1 MiB')
                result = json.loads(data.split(b'\n', 1)[0])
                if not isinstance(result, dict):
                    raise ValueError('probe response is not an object')
                self.last_error = None
                return result
        except (OSError, ValueError) as exc:
            self.last_error = str(exc)
            self.last_status = 'disconnected'
            return None
        finally:
            self.last_query_ms = (time.perf_counter() - started) * 1000

    def determine_local_owner(self, players):
        owners = {int(p['owner']) for p in players}
        if self.owner_override is not None:
            if self.owner_override not in owners:
                raise ValueError('configured owner absent from snapshot')
            return self.owner_override
        if self.known_account_id is not None:
            matches = [p for p in players if int(p.get('accountId', 0)) == self.known_account_id]
            if len(matches) != 1:
                raise ValueError('local account absent or ambiguous; check --account-id')
            return int(matches[0]['owner'])
        if self.expected_deck:
            matches = [p for p in players if set(p.get('deck', [])) == set(self.expected_deck)]
            if len(matches) == 1:
                self.known_account_id = int(matches[0].get('accountId', 0)) or None
                return int(matches[0]['owner'])
        raise ValueError('cannot identify local player; supply --account-id or --owner')

    def parse(self, data):
        if not data.get('in_battle'):
            self.last_status = 'idle'
            return None
        if data.get('entities_complete') is False:
            raise ValueError('incomplete native entity snapshot; pause instead of inferring tower destruction')
        entity_ids = [e.get('id') for e in data.get('entities', [])]
        if any(type(eid) is not int or not 0 < eid <= 0xffffffff for eid in entity_ids) or len(entity_ids) != len(set(entity_ids)):
            raise ValueError('invalid or duplicate native entity identity')
        players = data.get('players', [])
        owner = self.determine_local_owner(players)
        player = next(p for p in players if int(p['owner']) == owner)
        hand = [0] * 4
        seen = set()
        for entry in player.get('hand', []):
            slot = int(entry['slot'])
            if slot in seen or not 0 <= slot < 4:
                raise ValueError('duplicate or invalid native hand slot')
            seen.add(slot)
            hand[slot] = max(0, int(entry['card_id']))
        if not any(hand):
            self.last_status = 'idle'
            return None
        elixir = float(player['elixir'])
        if not math.isfinite(elixir) or not 0 <= elixir <= 10.001:
            raise ValueError('invalid live elixir')
        tick = int(data['tick'])
        if tick < 0:
            raise ValueError('negative tick')
        return BattleState(tick, owner, elixir, int(player.get('elixir_raw', elixir * 10000)),
                           hand, list(map(int, player.get('deck', []))),
                           tuple(map(int, data.get('crowns', [0, 0]))), data.get('entities', []), data)

    def get_battle_state(self):
        data = self.query()
        if data is None:
            return None
        try:
            return self.parse(data)
        except (KeyError, ValueError, TypeError, StopIteration) as exc:
            self.last_error = str(exc)
            self.last_status = 'invalid'
            return None

    def get_live_battle_state(self, max_staleness_sec=1.2):
        state = self.get_battle_state()
        if state is None:
            return None
        # A verified terminal snapshot is useful even when the final tick is
        # frozen or is the first snapshot after reconnect. It cannot authorize input.
        if state.native_finalized:
            self.last_status = 'finalized'
            return state
        now = time.perf_counter()
        if state.identity != self._identity or self._last_tick is None or state.tick < self._last_tick:
            self._identity = state.identity
            self._fresh_ticks_count = 0
            self._last_tick = None
        if state.tick != self._last_tick:
            self._last_tick = state.tick
            self._last_change_time = now
            self._fresh_ticks_count += 1
        if now - self._last_change_time > max_staleness_sec:
            self.last_status = 'stale'
            self._fresh_ticks_count = 0
            return None
        if self._fresh_ticks_count < 2:
            self.last_status = 'warming'
            return None
        self.last_status = 'live'
        return state

    def is_in_battle(self):
        state = self.get_live_battle_state()
        return state is not None and not state.native_finalized
