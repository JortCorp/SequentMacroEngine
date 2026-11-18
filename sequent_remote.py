"""Sequent Remote: opt-in, outbound-only ntfy command relay (Python stdlib).

This companion module belongs to Sequent; it has no third-party dependencies.
Nothing listens on a network interface. Commands are signed, expiring and
one-use; only predefined operations are available. Disable remote whenever
running sensitive workflows or when the relay is not approved.
"""
from __future__ import annotations
import collections
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import threading
import time
import urllib.parse
import urllib.request

TOPIC_RE = re.compile(r'^[A-Za-z0-9_-]{12,64}$')
SAFE_COMMANDS = frozenset({'pause', 'resume', 'stop', 'status', 'screenshot', 'approve', 'choose', 'launch', 'schedule'})
MAX_RESPONSE = 256_000


def _config_dir():
    root = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.config')
    path = Path(root) / 'Sequent'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_url(server):
    parsed = urllib.parse.urlsplit(server.strip().rstrip('/'))
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Remote requires an HTTPS ntfy server URL without URL credentials or path')
    return f'https://{parsed.netloc}'


def _request(url, *, payload=None, headers=None, timeout=12):
    if not url.lower().startswith('https://'):
        raise ValueError('Remote HTTPS required')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, hdrs, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, data=payload, method='POST' if payload is not None else 'GET', headers=headers or {})
    with opener.open(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise ValueError('ntfy response exceeds safety limit')
        return data


def config_default():
    return {'enabled': False, 'server': 'https://ntfy.sh',
            'phone_topic': '', 'inbox_topic': 'seq_cmd_' + secrets.token_urlsafe(24).replace('-', 'x').replace('_','y'),
            'secret': secrets.token_hex(32), 'token_env':'',
            'auto_error_alert': False, 'error_screenshot': False,
            'heartbeat_minutes': 0, 'allowed_macros': [], 'commands_enabled': True,
            'allow_screenshots': False, 'allow_launch': False, 'discord_enabled': False, 'web_enabled': False, 'screen_scope':'Monitor at Cursor',
            'schedule': []}


def load_config(path=None):
    path = Path(path or _config_dir() / 'remote.json')
    if not path.exists():
        return config_default()
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('Remote configuration must be an object')
    defaults = config_default()
    defaults.update(config)
    return defaults


def save_config(cfg, path=None):
    path = Path(path or _config_dir() / 'remote.json')
    _safe_url(cfg['server'])
    if cfg.get('phone_topic') and not TOPIC_RE.fullmatch(cfg['phone_topic']):
        raise ValueError('Phone topic must be 12–64 letters, digits, _ or -')
    if not TOPIC_RE.fullmatch(cfg['inbox_topic']):
        raise ValueError('Invalid command topic')
    if not re.fullmatch(r'[a-f0-9]{64}', cfg['secret']):
        raise ValueError('Invalid remote signing key')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as file:
        os.chmod(tmp, 0o600)
        json.dump(cfg, file, indent=2)
        file.write('\n')
    os.replace(tmp, path)
    return path


class RemoteRelay:
    def __init__(self, config, on_command, *, transport=_request, clock=time.time):
        self.cfg = dict(config)
        self.server = _safe_url(self.cfg['server'])
        if not TOPIC_RE.fullmatch(self.cfg['inbox_topic']):
            raise ValueError('Invalid command topic')
        if not re.fullmatch(r'[a-f0-9]{64}', self.cfg['secret']):
            raise ValueError('Invalid signing secret')
        self.on_command = on_command
        self.transport, self.clock = transport, clock
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.worker = None
        self.pending = {}
        self.pending_lock = threading.RLock()
        self.seen = collections.OrderedDict()
        self.last_id = 'latest'
        self.last_error = ''
        self.last_command = ''
        self.last_push = 0
        self.last_poll = 0
        self._status_throttle = {}

    def _headers(self):
        env = self.cfg.get('token_env', '').strip()
        if env:
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env):
                raise ValueError('Invalid token environment variable name')
            token = os.environ.get(env)
            if not token:
                raise ValueError(f'Environment variable {env} is not set')
            return {'Authorization': 'Bearer ' + token}
        return {}

    def publish(self, title, message, actions=(), *, priority=3):
        topic = self.cfg.get('phone_topic', '')
        if not TOPIC_RE.fullmatch(topic):
            raise ValueError('Set a valid phone notification topic in Remote Console')
        payload = {'topic':topic, 'title':str(title)[:120], 'message':str(message)[:4000],
                   'priority':priority}
        if actions:
            payload['actions'] = list(actions)[:3]
        headers = {'Content-Type':'application/json', **self._headers()}
        answer = self.transport(self.server+'/', payload=json.dumps(payload,ensure_ascii=False).encode(), headers=headers)
        result = json.loads(answer)
        if result.get('event') != 'message':
            raise ValueError('ntfy did not acknowledge the notification')
        self.last_push = self.clock()
        return result

    def sign(self, operation, *, value='', request_id='', ttl=1800):
        if operation not in SAFE_COMMANDS:
            raise ValueError('Unrecognized remote command')
        command = {'v': 1, 'op':operation, 'value':str(value), 'request':str(request_id),
                   'nonce':secrets.token_hex(12), 'exp':int(self.clock()+min(3600,max(5,ttl)))}
        msg = json.dumps(command,sort_keys=True,separators=(',',':')).encode('utf-8')
        command['sig'] = hmac.new(bytes.fromhex(self.cfg['secret']), msg, hashlib.sha256).hexdigest()
        return command

    def action_button(self, label, operation, *, value='', request_id='', ttl=1800):
        data = json.dumps(self.sign(operation, value=value, request_id=request_id, ttl=ttl), separators=(',',':'))
        action = {'action':'http','label':label[:30], 'url':self.server+'/'+self.cfg['inbox_topic'],
                  'method':'POST','body':data,'clear':True}
        header = self._headers().get('Authorization')
        if header:
            action['headers'] = {'Authorization': header}
        return action

    def verify(self, payload):
        """Authenticate phone buttons (v1), Discord (v2), and Web (v3).

        Each relay has a separate explicit permission. The HMAC covers the
        source field, so commands cannot bypass disabled channels.
        """
        if not isinstance(payload, dict):
            return None
        v1_fields = {'v', 'op', 'value', 'request', 'nonce', 'exp', 'sig'}
        source_fields = v1_fields | {'src'}
        version = payload.get('v')
        if version == 1:
            if set(payload) != v1_fields:
                return None
        elif version in (2, 3):
            expected_source = 'discord' if version == 2 else 'web'
            permission = 'discord_enabled' if version == 2 else 'web_enabled'
            if (set(payload) != source_fields or
                    payload.get('src') != expected_source or
                    not self.cfg.get(permission, False)):
                return None
        else:
            return None
        if payload['op'] not in SAFE_COMMANDS:
            return None
        if version in (2, 3) and payload['op'] in ('approve', 'choose'):
            # Remote decisions require individual one-time phone buttons.
            return None
        if any(not isinstance(payload[k], str) or len(payload[k]) > 250
               for k in ('value', 'request', 'nonce', 'sig')):
            return None
        if not isinstance(payload['exp'], int) or isinstance(payload['exp'], bool):
            return None
        now = self.clock()
        if payload['exp'] < now or payload['exp'] > now + (181 if version in (2, 3) else 3601):
            return None
        signed = {k: v for k, v in payload.items() if k != 'sig'}
        # Keep the v1 wire format unchanged for existing ntfy action buttons.
        msg = json.dumps(signed, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=(version == 1)).encode('utf-8')
        expected = hmac.new(bytes.fromhex(self.cfg['secret']), msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, payload['sig']):
            return None
        nonce = payload['nonce']
        if not nonce or not re.fullmatch(r'[A-Za-z0-9_-]{6,64}', nonce):
            return None
        with self.pending_lock:
            if nonce in self.seen:
                return None
            self.seen[nonce] = payload['exp']
            while len(self.seen) > 1500:
                self.seen.popitem(last=False)
        return payload

    def receive_message(self, msg):
        if msg.get('event') != 'message' or msg.get('topic') != self.cfg['inbox_topic']:
            return False
        try:
            payload = self.verify(json.loads(msg.get('message','')))
        except (ValueError,TypeError,OverflowError):
            return False
        if not payload:
            return False
        if payload['op'] in ('approve','choose'):
            with self.pending_lock:
                record = self.pending.get(payload['request'])
                if not record or payload['value'] not in record['options']:
                    return False
                record['selected'] = payload['value']
                record['event'].set()
        elif payload['op'] == 'launch':
            # The desktop owner must have explicitly allowlisted this preset.
            if payload['value'] not in self.cfg.get('allowed_macros',[]):
                return False
            self.on_command(payload['op'],payload['value'])
        elif payload['op'] == 'schedule':
            try:
                preset, delay = payload['value'].rsplit('@', 1)
                delay = int(delay)
            except (ValueError, TypeError):
                return False
            if preset not in self.cfg.get('allowed_macros',[]) or delay not in (5,15,30):
                return False
            self.on_command(payload['op'],payload['value'])
        else:
            now = self.clock()
            if now-self._status_throttle.get(payload['op'],0) < (25 if payload['op'] in ('screenshot','status') else 1):
                return False
            self._status_throttle[payload['op']] = now
            self.on_command(payload['op'],payload['value'])
        self.last_command = payload['op']
        return True

    def poll_once(self):
        query = urllib.parse.urlencode({'poll':'1', 'since':self.last_id})
        url = self.server+'/'+self.cfg['inbox_topic']+'/json?'+query
        content = self.transport(url, headers=self._headers(), timeout=12)
        self.last_poll = self.clock()
        # Initialize cursor but never accept old commands cached before connecting.
        initial = not self.ready.is_set()
        for line in content.splitlines():
            try:
                msg = json.loads(line)
            except (ValueError,UnicodeDecodeError):
                continue
            if msg.get('event') == 'message' and msg.get('topic') == self.cfg['inbox_topic']:
                if isinstance(msg.get('id'),str) and len(msg['id']) < 100:
                    self.last_id = msg['id']
                if not initial:
                    self.receive_message(msg)
        self.ready.set()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._loop, daemon=True, name='SequentRemoteRelay')
        self.worker.start()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.poll_once()
                self.last_error = ''
            except Exception as exc:
                self.last_error = f'{type(exc).__name__}: {exc}'[:300]
            self.stop_event.wait(4)

    def stop(self):
        self.stop_event.set()
        with self.pending_lock:
            for record in self.pending.values():
                record['event'].set()
            self.pending.clear()

    def ask(self, title, message, options, *, timeout=600, should_cancel=lambda:False):
        if not self.ready.wait(15) or self.stop_event.is_set():
            raise RuntimeError('Remote command listener unavailable')
        names = [(str(label),str(value)) for label,value in options]
        if not names or len(names)>3 or len(set(v for _,v in names)) != len(names):
            raise ValueError('Remote approval needs 1 to 3 unique choices')
        reqid = secrets.token_hex(12)
        record = {'event':threading.Event(), 'options':{v for _,v in names},'selected':None}
        with self.pending_lock:
            self.pending[reqid] = record
        try:
            self.publish(title,message,[self.action_button(label,'choose',value=value,request_id=reqid,ttl=timeout)
                                        for label,value in names], priority=4)
            cutoff = time.monotonic()+timeout
            while time.monotonic()<cutoff:
                if should_cancel() or self.stop_event.is_set():
                    return None
                if record['event'].wait(0.1):
                    return record['selected']
            return None
        finally:
            with self.pending_lock:
                self.pending.pop(reqid,None)

    def control_panels(self, allowed_macros=()):
        if not self.ready.wait(15):
            raise RuntimeError('Remote listener has not connected')
        self.publish('Sequent Remote • Playback','Use buttons to control the current macro.',[
            self.action_button('Pause','pause'),self.action_button('Resume','resume'),self.action_button('Stop','stop')])
        self.publish('Sequent Remote • Inspect','Request current execution status or a desktop screenshot.',[
            self.action_button('Status','status'),self.action_button('Screenshot','screenshot')])
        permitted = set(self.cfg.get('allowed_macros',()))
        macros = [name for name in allowed_macros if name in permitted][:3]
        if macros and self.cfg.get('allow_launch'):
            self.publish('Sequent Remote • Approved Presets','Only allowlisted macros are listed.',[
                self.action_button('Run '+name[:23],'launch',value=name) for name in macros])
            self.publish('Sequent Remote • Schedule','Queue an approved preset to run in 15 minutes, if the PC stays on.',[
                self.action_button('In 15m: '+name[:18],'schedule',value=name+'@15') for name in macros])
