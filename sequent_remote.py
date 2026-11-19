"""Sequent Remote: opt-in, outbound-only ntfy command relay (Python stdlib).

This companion module belongs to Sequent; it has no third-party dependencies.
Nothing listens on a network interface. Commands are signed, expiring and
one-use; only predefined operations are available. Disable remote whenever
running sensitive workflows or when the relay is not approved.
"""
from __future__ import annotations
import collections
import base64
import hashlib
import hmac
import struct
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

TOPIC_RE = re.compile(r'^[A-Za-z0-9_-]{12,64}$')
SAFE_COMMANDS = frozenset({'pause', 'resume', 'stop', 'status', 'screenshot', 'dashboard', 'approve', 'choose', 'launch', 'schedule', 'panel_file', 'panel_login', 'panel_status', 'panel_presets', 'record_start', 'record_stop', 'lock_desktop', 'smart_lock', 'lock_after', 'live_preview', 'preview_stop'})
PANEL_KDF_ROUNDS = 310_000
PANEL_LIFETIME = 2 * 3600
MAX_RESPONSE = 256_000
# ntfy.sh replenishes request tokens slowly; use one cache poll per 10s on
# networks where the efficient streaming subscription is unavailable.
FALLBACK_POLL_SECONDS = 10
STREAM_PROBE_INTERVAL_SECONDS = 900


class _IdleStreamTimeout(Exception):
    """The established subscription was quiet until its socket read timed out.

    It is safe to reopen the stream using the last message ID. Unlike a timeout
    while establishing a connection, this is not an authentication failure or
    evidence that the receiver's worker has stopped.
    """




class _SecureNtfyWebSocket:
    """Small, read-only RFC 6455 client for ntfy's WSS subscription endpoint.

    Uses only Python's standard library, validates TLS and the WebSocket
    handshake, supports HTTP CONNECT proxies, and caps every received frame.
    The client only sends protocol control frames (masked ping/pong/close),
    never unauthenticated application commands.
    """

    def __init__(self, server, topic, since, headers=None, timeout=12):
        parsed = urllib.parse.urlsplit(server)
        if parsed.scheme != 'https' or not parsed.hostname:
            raise ValueError('Secure WebSocket requires an HTTPS ntfy server')
        host = parsed.hostname
        port = parsed.port or 443
        socket_host = host
        socket_port = port
        proxy = None
        try:
            if not urllib.request.proxy_bypass(host):
                proxy = urllib.request.getproxies().get('https')
        except (OSError, ValueError):
            proxy = urllib.request.getproxies().get('https')
        if proxy:
            # urllib accepts proxy names without a scheme on Windows.
            pp = urllib.parse.urlsplit(proxy if '://' in proxy else 'http://' + proxy)
            if pp.scheme.lower() != 'http' or not pp.hostname:
                raise ConnectionError('WebSocket proxy protocol unsupported; trying HTTPS streaming')
            if pp.username or pp.password:
                raise ConnectionError('Authenticated WebSocket proxy unsupported; trying HTTPS streaming')
            socket_host, socket_port = pp.hostname, pp.port or 80
        self.sock = None
        self.reader = None
        try:
            conn = socket.create_connection((socket_host, socket_port), timeout=timeout)
            self.sock = conn
            conn.settimeout(timeout)
            if proxy:
                authority = f'{host}:{port}'
                conn.sendall((f'CONNECT {authority} HTTP/1.1\r\n'
                              f'Host: {authority}\r\n'
                              'Proxy-Connection: Keep-Alive\r\n\r\n').encode('ascii'))
                pfile = conn.makefile('rb')
                try:
                    code, _headers = self._read_headers(pfile)
                finally:
                    pfile.close()
                if code != 200:
                    raise ConnectionError(f'WebSocket proxy CONNECT failed (HTTP {code})')
            tls = ssl.create_default_context().wrap_socket(conn, server_hostname=host)
            self.sock = tls
            tls.settimeout(timeout)
            urlpath = ('/' + urllib.parse.quote(topic, safe=',') + '/ws?' +
                       urllib.parse.urlencode({'since': str(since)}))
            key = base64.b64encode(secrets.token_bytes(16)).decode('ascii')
            authority = host if port == 443 else f'{host}:{port}'
            parts = [f'GET {urlpath} HTTP/1.1', f'Host: {authority}',
                     'Upgrade: websocket', 'Connection: Upgrade',
                     f'Sec-WebSocket-Key: {key}', 'Sec-WebSocket-Version: 13',
                     'Cache-Control: no-cache']
            for name, value in (headers or {}).items():
                if name.lower() not in ('authorization',):
                    raise ValueError('Unexpected WebSocket header')
                if '\r' in value or '\n' in value:
                    raise ValueError('Invalid WebSocket authorization header')
                parts.append(f'{name}: {value}')
            tls.sendall(('\r\n'.join(parts) + '\r\n\r\n').encode('ascii'))
            self.reader = tls.makefile('rb')
            status, response_headers = self._read_headers(self.reader)
            if status != 101:
                if status == 429:
                    raise urllib.error.HTTPError(server, 429, 'ntfy WebSocket rate limited',
                                                 response_headers, None)
                raise ConnectionError(f'ntfy WebSocket upgrade returned HTTP {status}')
            expected = base64.b64encode(hashlib.sha1(
                (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')
            ).digest()).decode('ascii')
            if (response_headers.get('sec-websocket-accept') != expected or
                    response_headers.get('upgrade', '').lower() != 'websocket' or
                    'upgrade' not in response_headers.get('connection','').lower()):
                raise ConnectionError('ntfy WebSocket handshake validation failed')
            tls.settimeout(85)  # ntfy keepalives normally arrive before this.
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _read_headers(reader):
        first = reader.readline(2049)
        if len(first) > 2048 or not first.startswith(b'HTTP/1.'):
            raise ConnectionError('Invalid ntfy HTTP upgrade response')
        try:
            status = int(first.split(b' ', 2)[1])
        except (ValueError, IndexError):
            raise ConnectionError('Invalid ntfy HTTP response status') from None
        values = {}
        total = len(first)
        for _ in range(64):
            line = reader.readline(2049)
            total += len(line)
            if total > 16384 or len(line) > 2048 or not line:
                raise ConnectionError('Oversized or truncated ntfy HTTP headers')
            if line in (b'\r\n', b'\n'):
                return status, values
            if b':' in line:
                name, value = line.split(b':', 1)
                values[name.decode('ascii','ignore').strip().lower()] = value.decode('latin1').strip()
        raise ConnectionError('Too many ntfy HTTP headers')

    def _read_exact(self, size):
        data = self.reader.read(size)
        if data is None or len(data) != size:
            raise ConnectionError('ntfy WebSocket closed unexpectedly')
        return data

    def _control(self, opcode, payload=b''):
        if len(payload) > 125:
            raise ValueError('WebSocket control frame too long')
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[i%4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked)

    def recv_json(self):
        accumulated = bytearray()
        fragmented = False
        while True:
            b1, b2 = self._read_exact(2)
            fin, opcode, masked = bool(b1 & 0x80), b1 & 0x0f, bool(b2 & 0x80)
            if b1 & 0x70 or masked:
                raise ConnectionError('Invalid ntfy WebSocket frame')
            length = b2 & 0x7f
            if length == 126:
                length = struct.unpack('!H', self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self._read_exact(8))[0]
            if length > MAX_RESPONSE or len(accumulated) + length > MAX_RESPONSE:
                raise ValueError('ntfy WebSocket message exceeded safety limit')
            payload = self._read_exact(length)
            if opcode == 0x8:
                raise ConnectionError('ntfy WebSocket closed the subscription')
            if opcode == 0x9:
                if not fin or length > 125:
                    raise ConnectionError('Invalid ntfy WebSocket ping')
                self._control(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode not in (0x0, 0x1) or (opcode == 0x0) != fragmented:
                raise ConnectionError('Unsupported ntfy WebSocket frame sequence')
            if opcode == 0x1:
                fragmented = not fin
                accumulated = bytearray(payload)
            else:
                accumulated.extend(payload)
                fragmented = not fin
            if fin:
                return json.loads(accumulated.decode('utf-8'))

    def close(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
        reader, self.reader = self.reader, None
        if reader is not None:
            try:
                reader.close()
            except (OSError, ValueError):
                pass
        if sock is not None:
            try:
                sock.close()
            except (OSError, ValueError):
                pass


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


def validate_dashboard_url(value):
    """Only link to the configured HTTPS dashboard root; never embed a token.

    The hosted panel may be served at / or /panel.html. Do not accept
    URL-embedded passwords, query parameters or fragments in notifications.
    """
    value = str(value or '').strip()
    if not value:
        return ''
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('', '/', '/panel.html')):
        raise ValueError('Dashboard URL must be an HTTPS Worker URL ending in / or /panel.html without credentials or query parameters')
    if parsed.hostname.lower() in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Dashboard must use a publicly reachable HTTPS URL')
    return urllib.parse.urlunsplit(('https', parsed.netloc, parsed.path or '/', '', ''))


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
            'allow_screenshots': False, 'allow_launch': False, 'discord_enabled': False, 'web_enabled': False,
            'dashboard_url': '', 'screen_scope':'Monitor at Cursor',
            'local_panel_enabled': False, 'panel_salt': '', 'panel_auth_key': '',
            'schedule': [], 'allow_remote_record': False, 'allow_remote_lock': False,
            'allow_live_preview': False, 'lock_after_execution': False,
            'safety_locked': False, 'auto_approve_new_presets': True,
            'allow_panel_slash_from_phone': False}


def set_panel_password(cfg, password):
    """Store only a PBKDF2-derived verifier; never persist the plaintext password.

    The derived key is used only for the downloadable panel's HMAC channel.
    Existing phone buttons/hosted Worker continue using their own key.
    """
    if not isinstance(password, str) or len(password) < 16:
        raise ValueError('Panel password must be at least 16 characters')
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PANEL_KDF_ROUNDS)
    cfg['panel_salt'], cfg['panel_auth_key'] = salt.hex(), key.hex()
    return cfg


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
    validate_dashboard_url(cfg.get('dashboard_url', ''))
    if cfg.get('phone_topic') and not TOPIC_RE.fullmatch(cfg['phone_topic']):
        raise ValueError('Phone topic must be 12–64 letters, digits, _ or -')
    if not TOPIC_RE.fullmatch(cfg['inbox_topic']):
        raise ValueError('Invalid command topic')
    if not re.fullmatch(r'[a-f0-9]{64}', cfg['secret']):
        raise ValueError('Invalid remote signing key')
    if cfg.get('allow_panel_slash_from_phone') and not cfg.get('local_panel_enabled'):
        raise ValueError('Enable Downloadable HTML Panel before enabling /panel in phone topic')
    if cfg.get('allow_panel_slash_from_phone') and not TOPIC_RE.fullmatch(cfg.get('phone_topic','')):
        raise ValueError('A valid phone notification topic is required for /panel')
    if cfg.get('local_panel_enabled'):
        if not re.fullmatch(r'[a-f0-9]{32}', cfg.get('panel_salt','')) or not re.fullmatch(r'[a-f0-9]{64}', cfg.get('panel_auth_key','')):
            raise ValueError('Set a panel password (16+ characters) before enabling HTML Panel')
        if cfg.get('token_env'):
            raise ValueError('Downloaded HTML Panel requires browser access to ntfy. Authenticated ntfy token mode is not supported by the password-only panel.')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as file:
        os.chmod(tmp, 0o600)
        json.dump(cfg, file, indent=2)
        file.write('\n')
    os.replace(tmp, path)
    return path


class RemoteRelay:
    def __init__(self, config, on_command, *, transport=_request, clock=time.time,
                 on_diagnostic=None):
        self.cfg = dict(config)
        self.server = _safe_url(self.cfg['server'])
        if not TOPIC_RE.fullmatch(self.cfg['inbox_topic']):
            raise ValueError('Invalid command topic')
        if not re.fullmatch(r'[a-f0-9]{64}', self.cfg['secret']):
            raise ValueError('Invalid signing secret')
        self.on_command = on_command
        self.on_diagnostic = on_diagnostic
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
        self.poll_failures = 0
        self.next_retry = 0
        self._active_stream = None
        self._stream_active = False
        self._idle_rollovers = 0
        self._stream_lock = threading.RLock()
        self._status_throttle = {}
        # Streaming is preferable, but some networks allow uploads while
        # timing out the long-lived GET subscription. Fall back to deliberately
        # conservative polling until a periodic streaming probe.
        self._fallback_until = 0
        self._fallback_last_ok = 0
        self._stream_connect_failures = 0
        self._stream_open_count = 0
        self._stream_started_at = 0
        self._live_transport = 'websocket'
        self._live_name = ''
        self.panel_sessions = {}  # ephemeral two-hour downloadable-panel sessions

    def _diagnostic(self, detail):
        """Report protocol failures locally, not on public ntfy topics."""
        if self.on_diagnostic is not None:
            try:
                self.on_diagnostic('Remote panel: ' + str(detail)[:170])
            except Exception:
                pass

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

    def publish(self, title, message, actions=(), *, priority=3, click_url=''):
        topic = self.cfg.get('phone_topic', '')
        if not TOPIC_RE.fullmatch(topic):
            raise ValueError('Set a valid phone notification topic in Remote Console')
        payload = {'topic':topic, 'title':str(title)[:120], 'message':str(message)[:4000],
                   'priority':priority}
        if actions:
            payload['actions'] = list(actions)[:3]
        if click_url:
            payload['click'] = validate_dashboard_url(click_url)
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
        """Validate signed phone, Discord, hosted-Web, and downloaded-panel commands."""
        if not isinstance(payload, dict):
            return None
        v1_fields = {'v', 'op', 'value', 'request', 'nonce', 'exp', 'sig'}
        source_fields = v1_fields | {'src'}
        panel_fields = {'v','src','op','value','session','nonce','exp','sig'}
        version = payload.get('v')
        if version == 1:
            if set(payload) != v1_fields:
                return None
        elif version in (2,3):
            permission = 'discord_enabled' if version == 2 else 'web_enabled'
            if (set(payload) != source_fields or payload.get('src') != ('discord' if version==2 else 'web')
                    or not self.cfg.get(permission, False)):
                return None
        elif version == 4:
            if (set(payload) != panel_fields or payload.get('src') != 'panel' or
                    not self.cfg.get('local_panel_enabled',False)):
                return None
            sid = payload.get('session')
            if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', sid):
                return None
            session = self.panel_sessions.get(sid)
            if not session or session['expires'] <= self.clock():
                return None
        else:
            return None
        if payload.get('op') not in SAFE_COMMANDS:
            return None
        if version in (2,3,4) and payload['op'] in ('approve','choose'):
            return None
        if version != 4 and payload['op'] in ('panel_login', 'panel_status', 'panel_presets'):
            return None
        if version == 4 and payload['op'] in ('dashboard','panel_file'):
            return None
        if any(not isinstance(payload.get(k),str) or len(payload[k]) > 250
               for k in ('value','nonce','sig')):
            return None
        if version != 4 and (not isinstance(payload.get('request'),str) or len(payload['request'])>250):
            return None
        if not isinstance(payload.get('exp'),int) or isinstance(payload['exp'],bool):
            return None
        now=self.clock()
        if payload['exp'] < now or payload['exp'] > now + (121 if version==4 else (181 if version in (2,3) else 3601)):
            return None
        signed = {k:v for k,v in payload.items() if k!='sig'}
        msg=json.dumps(signed, sort_keys=True, separators=(',',':'),
                       ensure_ascii=(version==1)).encode('utf-8')
        key=bytes.fromhex(self.cfg.get('panel_auth_key','')) if version==4 else bytes.fromhex(self.cfg['secret'])
        expected=hmac.new(key,msg,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,payload['sig']):
            return None
        nonce=payload['nonce']
        if not re.fullmatch(r'[A-Za-z0-9_-]{6,64}',nonce):
            return None
        with self.pending_lock:
            if nonce in self.seen:
                return None
            self.seen[nonce]=payload['exp']
            while len(self.seen)>1500: self.seen.popitem(last=False)
        return payload

    def _panel_reply(self, sid, reply):
        session=self.panel_sessions.get(sid)
        if not session or session['expires'] <= self.clock():
            return False
        # Reply topics may be readable/writable by others on public ntfy.
        # Authenticate ALL responses, including login confirmation and status.
        signed_reply=dict(reply, v=4, session=sid, issued=int(self.clock()))
        proof=json.dumps(signed_reply,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
        signed_reply['sig']=hmac.new(bytes.fromhex(self.cfg['panel_auth_key']),proof,hashlib.sha256).hexdigest()
        body={'topic':session['reply_topic'], 'title':'Sequent Panel',
              'message':json.dumps(signed_reply,ensure_ascii=False,separators=(',',':'))}
        answer=self.transport(self.server+'/',payload=json.dumps(body).encode('utf-8'),
                              headers={'Content-Type':'application/json',**self._headers()})
        return json.loads(answer).get('event')=='message'

    def panel_status_reply(self, packed, snapshot):
        """Called by the Tk thread's status handler; publish to the right panel session."""
        try:
            sid,nonce=packed.split('|',1)
        except (ValueError,TypeError):
            return False
        return self._panel_reply(sid,{'kind':'status','nonce':nonce,'text':str(snapshot)[:2500],
                                     'at':int(self.clock())})

    def panel_presets_reply(self, packed, available_names):
        """Reply with *current* saved AND approved presets, signed for this panel.

        A downloaded HTML file may contain an older launch list. Refresh never
        grants access to presets not explicitly approved in Remote Console.
        Keep the response within ntfy's message-size allowance.
        """
        try:
            sid, nonce = packed.split('|', 1)
        except (ValueError, AttributeError):
            return False
        allowed = set(self.cfg.get('allowed_macros', ())) if self.cfg.get('allow_launch') else set()
        names = []
        truncated = False
        for name in available_names:
            if not isinstance(name, str) or name not in allowed or name in names:
                continue
            if len(name) > 250 or len(names) >= 80:
                truncated = True
                break
            proposal = names + [name]
            if len(json.dumps(proposal, ensure_ascii=False).encode('utf-8')) > 2200:
                truncated = True
                break
            names.append(name)
        return self._panel_reply(sid, {
            'kind': 'presets', 'nonce': nonce, 'presets': names,
            'truncated': truncated, 'at': int(self.clock())})

    def _subscription_topics(self):
        """One ntfy connection handles signed commands plus optional /panel requests.

        The phone topic is never a source of executable macro commands. Only
        its exact /panel text is recognized, and only when explicitly enabled.
        """
        names = [self.cfg['inbox_topic']]
        phone = self.cfg.get('phone_topic', '')
        if (self.cfg.get('local_panel_enabled') and
                self.cfg.get('allow_panel_slash_from_phone') and
                TOPIC_RE.fullmatch(phone) and phone not in names):
            names.append(phone)
        return ','.join(names)

    def _publish_panel_attachment_link(self, attachment):
        """Share ntfy's *actual* attachment URL, not a guessed file URL."""
        if not isinstance(attachment, dict):
            self._diagnostic('ntfy did not provide an attachment link; the HTML file was sent.')
            return False
        link = str(attachment.get('url',''))
        parsed = urllib.parse.urlsplit(link)
        host = urllib.parse.urlsplit(self.server)
        if (parsed.scheme != 'https' or parsed.netloc.lower() != host.netloc.lower()
                or parsed.username or parsed.password or parsed.fragment or not parsed.path):
            self._diagnostic('ntfy returned no usable HTTPS attachment URL; the file was sent.')
            return False
        body = {
            'topic': self.cfg['phone_topic'],
            'title': 'Sequent Remote • Open Panel',
            'message': ('Tap Open Panel to get your new dashboard file. Open the downloaded '
                        'HTML in your browser and enter the password set in Remote Console. '
                        'The panel session lasts up to two hours; the attachment link may expire sooner.'),
            'actions': [{'action':'view', 'label':'Open Panel', 'url':link, 'clear': True}],
            'click': link,
        }
        try:
            response = self.transport(self.server+'/',
                                      payload=json.dumps(body).encode('utf-8'),
                                      headers={'Content-Type':'application/json', **self._headers()})
            if json.loads(response).get('event') != 'message':
                raise ValueError('ntfy did not acknowledge panel link')
            self.last_push = self.clock()
            return True
        except Exception as exc:
            # HTML delivery already succeeded; a rate-limited second message
            # must not invalidate its session or trigger extra retries.
            self._diagnostic('HTML attachment delivered, but Open Panel link could not be posted: '
                             + type(exc).__name__)
            return False

    def send_panel_file(self, *, include_open_link=False):
        """Read project-local panel.html, personalize session metadata and attach to ntfy.

        The file contains no password or HMAC signing key. It is read afresh
        from the project folder each request. Anyone who can read this topic
        can download the UI, but cannot sign commands without the password.
        """
        if not self.cfg.get('enabled') or not self.cfg.get('local_panel_enabled'):
            raise RuntimeError('Enable remote receiver and Downloadable HTML Panel first')
        if self.cfg.get('token_env'):
            raise RuntimeError('Downloaded HTML Panel cannot use an ntfy access token')
        if not TOPIC_RE.fullmatch(self.cfg.get('phone_topic','')):
            raise ValueError('Configure a valid phone topic')
        template=(Path(__file__).resolve().parent/'panel.html')
        if not template.is_file():
            raise FileNotFoundError('panel.html must be alongside sequent_remote.py')
        sid=secrets.token_urlsafe(18)
        reply_topic='seq_reply_'+secrets.token_hex(14)
        now=int(self.clock())
        self.panel_sessions={k:v for k,v in self.panel_sessions.items() if v['expires']>now}
        while len(self.panel_sessions)>=6:
            self.panel_sessions.pop(next(iter(self.panel_sessions)))
        self.panel_sessions[sid]={'reply_topic':reply_topic,'expires':now+PANEL_LIFETIME}
        data={'server':self.server,'inbox':self.cfg['inbox_topic'],
              'phone':self.cfg['phone_topic'],'reply':reply_topic,
              'session':sid,'expires':now+PANEL_LIFETIME,
              'salt':self.cfg['panel_salt'],'iterations':PANEL_KDF_ROUNDS,
              'allowed_macros':list(self.cfg.get('allowed_macros',[])) if self.cfg.get('allow_launch') else [],
              'screenshots':bool(self.cfg.get('allow_screenshots')),
              'remote_record':bool(self.cfg.get('allow_remote_record')),
              'remote_lock':bool(self.cfg.get('allow_remote_lock')),
              'live_preview':bool(self.cfg.get('allow_live_preview') and self.cfg.get('allow_screenshots')),
              'lock_after':bool(self.cfg.get('lock_after_execution')),
              'safety_locked':bool(self.cfg.get('safety_locked'))}
        template_data=template.read_text(encoding='utf-8')
        marker='/* SEQUENT_PANEL_CONFIG */'
        if template_data.count(marker)!=1:
            del self.panel_sessions[sid]
            if 'Sequent Remote · Command Center' in template_data or 'Sequent Remote &middot; Command Center' in template_data:
                raise ValueError(
                    'Wrong panel.html: this is the hosted web_dashboard/panel.html. '
                    'Replace it with the DOWNLOADABLE panel.html from the top level '
                    'of the Sequent package, next to Sequent.py and sequent_remote.py. '
                    f'File checked: {template}')
            raise ValueError(
                'Invalid downloadable panel template: expected one '
                "'/* SEQUENT_PANEL_CONFIG */' marker in the project-root "
                f'panel.html, but found {template_data.count(marker)}. '
                f'File checked: {template}')
        safe_json=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<',r'\u003c').replace('>',r'\u003e').replace('&',r'\u0026')
        document=template_data.replace(marker, 'window.SEQUENT_PANEL_CONFIG = '+safe_json+';')
        payload=document.encode('utf-8')
        if len(payload)>1_000_000:
            del self.panel_sessions[sid]
            raise ValueError('Panel attachment is too large')
        headers={'Content-Type':'text/html; charset=utf-8',
                 'X-Filename':'Sequent-Remote-Panel.html',
                 'X-Title':'Sequent Remote - HTML Control Panel',
                 'X-Message':'Open the attached HTML file in your browser. Enter the panel password set in Sequent Remote Console.',
                 **self._headers()}
        try:
            response=self.transport(self.server+'/'+self.cfg['phone_topic'],payload=payload,headers=headers)
            upload_result = json.loads(response)
            if upload_result.get('event')!='message':
                raise ValueError('ntfy did not accept the panel attachment')
        except Exception:
            self.panel_sessions.pop(sid,None)
            raise
        self.last_push=self.clock()
        if include_open_link:
            self._publish_panel_attachment_link(upload_result.get('attachment'))
        return True

    def receive_message(self, msg):
        if msg.get('event') != 'message':
            return False
        topic = msg.get('topic')
        inbox = self.cfg['inbox_topic']
        phone = self.cfg.get('phone_topic', '')
        is_phone = topic == phone
        is_inbox = topic == inbox
        if not is_inbox and not is_phone:
            return False
        raw = msg.get('message', '')
        # A plaintext /panel request can only deliver the public HTML shell.
        # No macro-control commands are accepted from the phone topic.
        if isinstance(raw, str) and raw.strip().lower() in ('/panel', '/sequent panel'):
            if not self.cfg.get('local_panel_enabled'):
                return False
            if is_phone and not self.cfg.get('allow_panel_slash_from_phone'):
                return False
            now = self.clock()
            if now-self._status_throttle.get('panel_file', 0) < 60:
                return False
            self._status_throttle['panel_file'] = now
            self.on_command('panel_file', 'link' if is_phone else '')
            return True
        if is_phone and not is_inbox:
            return False
        try:
            payload=self.verify(json.loads(raw))
        except (ValueError,TypeError,OverflowError):
            return False
        if not payload:
            # A wrong password must never receive a remotely distinguishable
            # error reply; diagnostics are available ONLY on the local console.
            try:
                candidate = json.loads(raw)
            except (ValueError, TypeError):
                candidate = None
            if (isinstance(candidate, dict) and candidate.get('v') == 4
                    and candidate.get('op') == 'panel_login'):
                sid = candidate.get('session')
                if not isinstance(sid, str) or sid not in self.panel_sessions:
                    self._diagnostic('Login rejected: session no longer exists. '
                                     'Request a NEW panel.html after reconnecting.')
                else:
                    self._diagnostic('Login rejected: signature/password mismatch, '
                                     'duplicate request, expired command, or clock skew.')
            return False
        version=payload['v']
        # All command sources share these local, explicitly opt-in permissions.
        op=payload['op']
        if op in ('record_start','record_stop') and not self.cfg.get('allow_remote_record'):
            self._diagnostic('Remote recording rejected: not enabled in Remote Console')
            return False
        if op in ('lock_desktop','smart_lock','lock_after') and not self.cfg.get('allow_remote_lock'):
            self._diagnostic('Desktop lock rejected: not enabled in Remote Console')
            return False
        if op in ('live_preview','preview_stop') and not (
                self.cfg.get('allow_live_preview') and self.cfg.get('allow_screenshots')):
            self._diagnostic('Live preview rejected: not enabled in Remote Console')
            return False
        if self.cfg.get('safety_locked') and op in ('record_start','launch','schedule'):
            self._diagnostic('Launch/record rejected: Smart Lock requires local re-arming')
            return False
        if version==4 and payload['op']=='panel_login':
            self._diagnostic('Valid login received; sending signed reply to phone.')
            try:
                if not self._panel_reply(payload['session'],
                        {'kind':'login','nonce':payload['nonce'],'ok':True}):
                    self._diagnostic('Login reply was not acknowledged by ntfy.')
                    return False
            except Exception as exc:
                self._diagnostic('Failed to send login reply: '
                                 f'{type(exc).__name__}: {exc}')
                raise
        elif version==4 and payload['op']=='panel_presets':
            now = self.clock()
            sid = payload['session']
            if now - self._status_throttle.get('panel_presets:'+sid, 0) < 3:
                return False
            self._status_throttle['panel_presets:'+sid] = now
            self.on_command('panel_presets', sid+'|'+payload['nonce'])
        elif version==4 and payload['op']=='panel_status':
            now=self.clock()
            sid=payload['session']
            if now-self._status_throttle.get('panel_status:'+sid,0)<3:
                return False
            self._status_throttle['panel_status:'+sid]=now
            self.on_command('panel_status',sid+'|'+payload['nonce'])
        elif payload['op'] == 'dashboard':
            # Don't allow even signed phone requests to share a dashboard link
            # while Web Dashboard access has been explicitly disabled.
            if not (self.cfg.get('web_enabled') and self.cfg.get('dashboard_url')):
                return False
            if self.clock() - self._status_throttle.get('dashboard', 0) < 15:
                return False
            self._status_throttle['dashboard'] = self.clock()
            self.on_command('dashboard', '')
        elif payload['op'] == 'panel_file':
            if not self.cfg.get('local_panel_enabled'):
                return False
            now=self.clock()
            if now-self._status_throttle.get('panel_file',0)<60:
                return False
            self._status_throttle['panel_file']=now
            self.on_command('panel_file','')
        elif payload['op'] in ('approve','choose'):
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
            if now-self._status_throttle.get(payload['op'],0) < (120 if payload['op']=='live_preview' else 25 if payload['op'] in ('screenshot','status') else 1):
                return False
            self._status_throttle[payload['op']] = now
            self.on_command(payload['op'],payload['value'])
        if version==4 and payload['op'] not in ('panel_login','panel_status','panel_presets'):
            try:
                self._panel_reply(payload['session'],{'kind':'accepted','nonce':payload['nonce'],
                                        'op':payload['op'],'ok':True})
            except Exception:
                pass  # Command was already authenticated and dispatched.
        self.last_command = payload['op']
        return True

    def poll_once(self):
        # 'latest' is a *moving* boundary: using it for every fallback poll
        # would return no messages forever. Convert an empty startup poll to a
        # fixed Unix-time cursor for subsequent polling/reconnection.
        initial = not self.ready.is_set()
        floor = str(int(self.clock())) if initial and self.last_id == 'latest' else None
        query = urllib.parse.urlencode({'poll':'1', 'since':self.last_id})
        url = self.server+'/'+self._subscription_topics()+'/json?'+query
        content = self.transport(url, headers=self._headers(), timeout=12)
        self.last_poll = self.clock()
        # Initialize cursor but never accept old commands cached before connecting.
        for line in content.splitlines():
            try:
                msg = json.loads(line)
            except (ValueError,UnicodeDecodeError):
                continue
            if msg.get('event') == 'message' and msg.get('topic') in self._subscription_topics().split(','):
                if isinstance(msg.get('id'),str) and len(msg['id']) < 100:
                    self.last_id = msg['id']
                if not initial:
                    self.receive_message(msg)
        if floor is not None and self.last_id == 'latest':
            self.last_id = floor
        self.ready.set()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._loop, daemon=True, name='SequentRemoteRelay')
        self.worker.start()

    def connection_state(self, now=None):
        """Report streaming health, not the last exception from an idle socket."""
        now = self.clock() if now is None else now
        if self.stop_event.is_set():
            return 'OFFLINE'
        if self.last_error.startswith('HTTP 429') and now < self.next_retry:
            return 'RATE LIMITED'
        if not self.ready.is_set():
            return 'CONNECTING'
        # In fallback mode, a recent successful cache poll is a working
        # receiver, albeit with slower command delivery than streaming.
        if self.transport is _request and self._fallback_until > now:
            if self._fallback_last_ok and now - self._fallback_last_ok <= 100:
                return 'POLLING'
            return 'RECONNECTING'
        # The normal production transport holds an active HTTPS subscription.
        # After it closes, do not claim a live connection merely because the
        # previous keepalive was recent. This also covers failed reconnects.
        if self.transport is _request:
            with self._stream_lock:
                active = self._stream_active
            if not active:
                return 'RECONNECTING'
        if self.last_poll and now - self.last_poll > 75:
            return 'RECONNECTING'
        return 'CONNECTED'

    def _retry_seconds(self, exc, failures):
        """Honor ntfy Retry-After, otherwise exponential cooldown without hammering."""
        code = getattr(exc, 'code', None)
        if code == 429:
            headers = getattr(exc, 'headers', None)
            raw = headers.get('Retry-After') if headers else None
            if raw:
                try:
                    delay = float(raw)
                except (ValueError, TypeError):
                    try:
                        delay = (parsedate_to_datetime(raw) -
                                 datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        delay = 60
                return max(5.0, min(900.0, delay))
            return min(300.0, 60.0 * (2 ** min(3, failures-1)))
        return min(60.0, 5.0 * (2 ** min(4, failures-1)))

    def websocket_once(self):
        """Preferred low-latency ntfy transport (WSS, stdlib only)."""
        ws = _SecureNtfyWebSocket(self.server, self._subscription_topics(),
                                 self.last_id, self._headers(), timeout=12)
        with self._stream_lock:
            self._active_stream = ws
            self._stream_active = True
        self._stream_open_count += 1
        self._stream_started_at = self.clock()
        self._stream_connect_failures = 0
        self._live_name = 'WebSocket'
        self.poll_failures = 0
        self.last_error = ''
        self.next_retry = 0
        self.last_poll = self.clock()
        self.ready.set()
        self._diagnostic('ntfy LIVE via secure WebSocket.')
        try:
            while not self.stop_event.is_set():
                try:
                    msg = ws.recv_json()
                except (TimeoutError, socket.timeout) as exc:
                    raise _IdleStreamTimeout() from exc
                event = msg.get('event') if isinstance(msg, dict) else None
                if event in ('open','keepalive','message'):
                    self.last_poll = self.clock()
                if event != 'message' or msg.get('topic') not in self._subscription_topics().split(','):
                    continue
                mid = msg.get('id')
                if isinstance(mid, str) and 0 < len(mid) < 100:
                    if mid == self.last_id:
                        continue
                    self.last_id = mid
                self.receive_message(msg)
        finally:
            with self._stream_lock:
                if self._active_stream is ws:
                    self._active_stream = None
                self._stream_active = False
            ws.close()
        if not self.stop_event.is_set():
            raise ConnectionError('ntfy WebSocket subscription ended')

    def stream_once(self):
        """Hold one JSON stream instead of issuing an HTTP poll every four seconds.

        Initial cached events are skipped by poll_once() before connecting.
        ntfy's open/keepalive frames are used to track connection health.
        """
        query=urllib.parse.urlencode({'since':self.last_id})
        url=self.server+'/'+self._subscription_topics()+'/json?'+query
        # Match _request: no redirects or non-HTTPS requests, and system TLS.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, message, hdrs, newurl):
                return None
        opener=urllib.request.build_opener(
            NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        request=urllib.request.Request(url, headers=self._headers())
        with opener.open(request, timeout=18) as response:
            # Fail a buffered/proxied upgrade quickly, but allow long idle
            # reads once HTTP response headers have actually arrived.
            try:
                response.fp.raw._sock.settimeout(85)
            except (AttributeError, OSError, ValueError):
                pass
            self._stream_open_count += 1
            self._stream_started_at = self.clock()
            self._live_name = 'HTTP stream'
            self._stream_connect_failures = 0
            with self._stream_lock:
                self._active_stream = response
                self._stream_active = True
            # A successful subscription supersedes any prior transient error.
            # The stream may stay open indefinitely, so clearing only after
            # stream_once returns would leave stale errors in the console.
            recovered = bool(self.poll_failures)
            self.poll_failures = 0
            self.last_error = ''
            self.next_retry = 0
            self.last_poll = self.clock()
            self.ready.set()
            if recovered:
                self._diagnostic('ntfy subscription reconnected.')
            try:
                while not self.stop_event.is_set():
                    try:
                        line = response.readline(MAX_RESPONSE + 1)
                    except (TimeoutError, socket.timeout) as exc:
                        # This connection *was* established successfully. A
                        # silent period should roll over the subscription, not
                        # produce a scary TimeoutError / OFFLINE status.
                        raise _IdleStreamTimeout from exc
                    if not line:
                        break
                    if len(line)>MAX_RESPONSE:
                        raise ValueError('ntfy stream message exceeded safety limit')
                    try:
                        msg=json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    event=msg.get('event')
                    if event in ('open','keepalive','message'):
                        self.last_poll=self.clock()
                    if event != 'message' or msg.get('topic') not in self._subscription_topics().split(','):
                        continue
                    mid=msg.get('id')
                    if isinstance(mid,str) and 0<len(mid)<100:
                        # A reconnection can replay the last message: skip it.
                        if mid == self.last_id:
                            continue
                        self.last_id=mid
                    self.receive_message(msg)
            finally:
                with self._stream_lock:
                    if self._active_stream is response:
                        self._active_stream = None
                    self._stream_active = False
        if not self.stop_event.is_set():
            # A healthy stream remains open. Fast disconnects must back off,
            # not turn into another rapid request loop.
            raise ConnectionError('ntfy subscription stream ended')

    def _loop(self):
        """Live WebSocket first, live HTTP second, rate-aware polling last.

        An ntfy server may allow POST and cache GET while a proxy buffers
        streaming responses. Trying the separate WebSocket protocol prevents
        permanently relegating a healthy WebSocket-capable network to polling.
        """
        while not self.stop_event.is_set():
            live_attempt = False
            mode_attempted = None
            opening_count = self._stream_open_count
            try:
                if self.transport is _request:
                    if not self.ready.is_set():
                        self.poll_once()  # fixed startup cursor; ignore older commands
                    now = self.clock()
                    if self._fallback_until > now:
                        self.poll_once()
                        self._fallback_last_ok = self.clock()
                        self.poll_failures = 0
                        self.next_retry = 0
                        self.last_error = ''
                        delay = FALLBACK_POLL_SECONDS
                    else:
                        if self._fallback_until:
                            self._fallback_until = 0
                            self._live_transport = 'websocket'
                        live_attempt = True
                        mode_attempted = self._live_transport
                        if mode_attempted == 'websocket':
                            self.websocket_once()
                        else:
                            self.stream_once()
                        self.poll_failures = 0
                        self.next_retry = 0
                        self.last_error = ''
                        delay = 2
                else:
                    # Existing injectable fake transports exercise the JSON
                    # polling protocol; never attempt real network in tests.
                    self.poll_once()
                    self.poll_failures = 0
                    self.next_retry = 0
                    self.last_error = ''
                    delay = 20
            except _IdleStreamTimeout:
                if self.stop_event.is_set():
                    break
                self._idle_rollovers += 1
                self.poll_failures = 0
                self.last_error = ''
                self.next_retry = 0
                delay = 2
                if self._idle_rollovers == 1:
                    self._diagnostic('ntfy LIVE idle stream refreshed; sessions and command cursor preserved.')
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                self.poll_failures += 1
                code = getattr(exc, 'code', None)
                self.last_error = (f'HTTP {code} (ntfy rate limit)' if code == 429
                                   else (f'HTTP {code}' if isinstance(code, int)
                                         else type(exc).__name__))
                delay = self._retry_seconds(exc, self.poll_failures)
                self.next_retry = self.clock() + delay
                # Try the other *live* protocol before polling. 401/403/429
                # are authorization/quota problems, not streaming problems.
                if live_attempt and self._stream_open_count == opening_count and code not in (401, 403, 429):
                    if mode_attempted == 'websocket':
                        self._live_transport = 'http'
                        delay = 2
                        self._diagnostic(f'WebSocket unavailable ({self.last_error}); trying live HTTP stream.')
                    else:
                        self._fallback_until = self.clock() + STREAM_PROBE_INTERVAL_SECONDS
                        self._fallback_last_ok = 0
                        delay = max(delay, 10)
                        self._diagnostic(f'Both live ntfy transports failed ({self.last_error}); '
                                         f'using {FALLBACK_POLL_SECONDS}-second POLLING as backup.')
                elif live_attempt and self._stream_open_count > opening_count:
                    # Connection got established: normal interruption. Keep
                    # retrying the transport that last worked.
                    pass
                if self.poll_failures in (1,3,10):
                    self._diagnostic(f'{self.last_error} in {mode_attempted or "poll"}; '
                                     f'retrying in {int(delay)}s. Receiver was not restarted.')
            self.stop_event.wait(delay)

    def stop(self):
        self.stop_event.set()
        # Release long-lived subscription when the Remote Console reconnects.
        with self._stream_lock:
            stream = self._active_stream
            self._active_stream = None
            self._stream_active = False
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
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

    def send_dashboard_link(self):
        """Send an ntfy message that opens the *hosted*, login-protected panel.

        The notification contains only the public URL. Authentication stays
        with the Cloudflare Worker; signing secrets never enter the message.
        """
        if not self.cfg.get('enabled') or not self.cfg.get('web_enabled'):
            raise RuntimeError('Enable the remote receiver and Web Dashboard in Remote Console')
        url = validate_dashboard_url(self.cfg.get('dashboard_url', ''))
        if not url:
            raise ValueError('Enter the hosted dashboard URL in Remote Console first')
        return self.publish(
            'Sequent Remote • Dashboard',
            'Tap Open Dashboard to sign in and control your approved macros. '
            'Your PC must remain running and connected.',
            [{'action': 'view', 'label': 'Open Dashboard', 'url': url, 'clear': True}],
            click_url=url,
        )

    def control_panels(self, allowed_macros=()):
        if not self.ready.wait(15):
            raise RuntimeError('Remote listener has not connected')
        self.publish('Sequent Remote • Playback','Use buttons to control the current macro.',[
            self.action_button('Pause','pause'),self.action_button('Resume','resume'),self.action_button('Stop','stop')])
        inspect = [self.action_button('Status','status'),
                   self.action_button('Screenshot','screenshot')]
        if self.cfg.get('web_enabled') and self.cfg.get('dashboard_url'):
            inspect.append(self.action_button('Get Dashboard','dashboard'))
        elif self.cfg.get('local_panel_enabled'):
            inspect.append(self.action_button('Get HTML Panel','panel_file'))
        self.publish('Sequent Remote • Inspect',
                     'Request status, a screenshot, or a private dashboard sign-in link.', inspect)
        permitted = set(self.cfg.get('allowed_macros',()))
        macros = [name for name in allowed_macros if name in permitted][:3]
        if macros and self.cfg.get('allow_launch'):
            self.publish('Sequent Remote • Approved Presets','Only allowlisted macros are listed.',[
                self.action_button('Run '+name[:23],'launch',value=name) for name in macros])
            self.publish('Sequent Remote • Schedule','Queue an approved preset to run in 15 minutes, if the PC stays on.',[
                self.action_button('In 15m: '+name[:18],'schedule',value=name+'@15') for name in macros])
