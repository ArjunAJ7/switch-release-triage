"""Switch agent protocol client: SSE down, HTTP up.

Implements the wire contract from docs.switchagents.ai/flintai/switch/internals/agent-protocol:
- one SSE stream carries room events (connection created on open)
- heartbeat every 2s keeps the connection live
- operations are plain HTTP POSTs to /agents/{id}/ops/{name}
"""
import json
import threading
import time
import urllib.request
import uuid


class SwitchClient:
    def __init__(self, bridge, agent_id, api_key):
        self.bridge = bridge.rstrip("/")
        self.agent_id = agent_id
        self.api_key = api_key
        self.connection_id = str(uuid.uuid4())
        self.cursor = None
        self.rooms = []
        self._stop = threading.Event()
        self._beat_thread = None
        self._stream_thread = None

    def _headers(self, extra=None):
        h = {"Authorization": f"Bearer {self.api_key}"}
        if extra:
            h.update(extra)
        return h

    def _post(self, path, body=None, timeout=15):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            self.bridge + path, data=data, method="POST",
            headers=self._headers({"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def op(self, name, args=None, timeout=30):
        return self._post(
            f"/agents/{self.agent_id}/ops/{name}", args or {}, timeout=timeout)

    def post_message(self, room_id, content):
        return self._post(f"/agents/{self.agent_id}/message",
                          {"room_id": room_id, "content": content})

    # -- stream + heartbeat -------------------------------------------------
    def open_stream(self, on_event, scope="all", filt="all", rooms=None):
        url = (f"{self.bridge}/agents/{self.agent_id}/events"
               f"?connection_id={self.connection_id}&scope={scope}&filter={filt}"
               f"&start_from=head")
        if rooms:
            url += "&rooms=" + ",".join(rooms)
        req = urllib.request.Request(
            url, headers=self._headers({"Accept": "text/event-stream"}))
        resp = urllib.request.urlopen(req, timeout=None)
        event_name, data_lines = "message", []
        for raw in resp:
            if self._stop.is_set():
                break
            line = raw.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
            if line == "":
                if data_lines:
                    data = "\n".join(data_lines)
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        payload = {"raw": data}
                    on_event(event_name, payload)
                event_name, data_lines = "message", []
            elif line.startswith(":"):
                continue
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line.startswith("id:"):
                pass

    def heartbeat_loop(self, interval=2.0):
        while not self._stop.is_set():
            try:
                r = self._post(
                    f"/agents/{self.agent_id}/connection/beat",
                    {"connection_id": self.connection_id,
                     "cursor": self.cursor or 0})
                self.rooms = r.get("rooms", self.rooms)
                self.cursor = r.get("cursor", self.cursor)
            except Exception as e:
                print(f"[beat] {e}", flush=True)
            self._stop.wait(interval)

    def run(self, on_event, rooms=None):
        self._beat_thread = threading.Thread(
            target=self.heartbeat_loop, daemon=True)
        self._beat_thread.start()
        backoff = 1
        while not self._stop.is_set():
            try:
                self.open_stream(on_event, rooms=rooms)
                backoff = 1
            except Exception as e:
                print(f"[stream] {e}; reopen in {backoff}s", flush=True)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)

    def stop(self):
        self._stop.set()
