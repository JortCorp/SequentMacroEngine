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
SAFE_COMMANDS = frozenset({'pause', 'resume', 'stop', 'status', 'screenshot', 'dashboard', 'approve', 'choose', 'launch', 'schedule', 'panel_file', 'panel_login', 'panel_status'})
PANEL_KDF_ROUNDS = 310_000
PANEL_LIFETIME = 2 * 3600
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
            'schedule': []}


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
        self._status_throttle = {}
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
        if version != 4 and payload['op'] in ('panel_login', 'panel_status'):
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

    def send_panel_file(self):
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
              'screenshots':bool(self.cfg.get('allow_screenshots'))}
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
            if json.loads(response).get('event')!='message':
                raise ValueError('ntfy did not accept the panel attachment')
        except Exception:
            self.panel_sessions.pop(sid,None)
            raise
        self.last_push=self.clock()
        return True

    def receive_message(self, msg):
        if msg.get('event') != 'message' or msg.get('topic') != self.cfg['inbox_topic']:
            return False
        raw=msg.get('message','')
        # A plain /panel request only sends the public HTML shell to the
        # owner's configured phone topic. It authorizes NO desktop commands.
        if isinstance(raw,str) and raw.strip().lower() in ('/panel','/sequent panel'):
            if not self.cfg.get('local_panel_enabled'):
                return False
            now=self.clock()
            if now-self._status_throttle.get('panel_file',0)<60:
                return False
            self._status_throttle['panel_file']=now
            self.on_command('panel_file','')
            return True
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
            if now-self._status_throttle.get(payload['op'],0) < (25 if payload['op'] in ('screenshot','status') else 1):
                return False
            self._status_throttle[payload['op']] = now
            self.on_command(payload['op'],payload['value'])
        if version==4 and payload['op'] not in ('panel_login','panel_status'):
            try:
                self._panel_reply(payload['session'],{'kind':'accepted','nonce':payload['nonce'],
                                        'op':payload['op'],'ok':True})
            except Exception:
                pass  # Command was already authenticated and dispatched.
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
