'''hAI.PortainerMCP - MCP server to administer many Portainer instances via aliases.

- MCP (streamable-http) on PORTAINER_MCP_PORT (default 8025), endpoint /mcp.
  Protected by Bearer token when PORTAINER_MCP_API_KEY is set (set it in your own env).
- GET /health on the MCP port (no auth).
- Admin web page on PORTAINER_ADMIN_PORT (default 8026) that edits aliases in a JSON file.
- Aliases map a friendly name -> {url, api_key}; Portainer is called via REST API v2
  using the X-API-Key header.
'''

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('portainer-mcp')

MAX_ALIASES = 20
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$')
STACK_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,62}$')

MCP_PORT = int(os.getenv('PORTAINER_MCP_PORT', '8025'))
ADMIN_PORT = int(os.getenv('PORTAINER_ADMIN_PORT', '8026'))
ADMIN_PASSWORD = os.getenv('PORTAINER_ADMIN_PASSWORD', '').strip()
MCP_API_KEY = os.getenv('PORTAINER_MCP_API_KEY', '').strip()
ALIASES_FILE = os.getenv('PORTAINER_ALIASES_FILE', 'data/portainer_aliases.json')

SESSION_TTL = 8 * 3600
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 300

_lock = threading.RLock()
_failed_logins: dict = {}

mcp = FastMCP(
    'portainer-mcp',
    host='0.0.0.0',
    port=MCP_PORT,
    instructions=(
        'Administer multiple Portainer instances via aliases. '
        'Aliases are managed with portainer_alias_* tools; actual Portainer data '
        'comes from portainer_status, portainer_endpoints, portainer_containers_list, '
        'portainer_stacks_list, portainer_pull_image, portainer_docker_images, '
        'portainer_networks, portainer_volumes, portainer_system_info, '
        'portainer_deploy_stack, portainer_undeploy_stack. '
        'Tools that act on an endpoint accept an optional endpoint_id (default: first endpoint). '
        'The MCP endpoint is protected with a Bearer token (PORTAINER_MCP_API_KEY).'
    ),
)


# ---------------------------------------------------------------- persistence

def read_aliases() -> dict:
    try:
        with open(ALIASES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_aliases(aliases: dict) -> None:
    with _lock:
        os.makedirs(os.path.dirname(ALIASES_FILE) or '.', exist_ok=True)
        tmp = ALIASES_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(aliases, f, indent=2, ensure_ascii=False)
            f.write(chr(10))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, ALIASES_FILE)


def public_view(aliases: dict) -> list:
    out = []
    for name in sorted(aliases):
        a = aliases[name]
        out.append({'alias': name, 'url': a.get('url', ''), 'has_key': bool(a.get('api_key'))})
    return out


# ---------------------------------------------------------------- portainer api

def _api(url: str, api_key: str, path: str, method: str = 'GET', payload=None):
    '''Call a Portainer REST API v2 path. Returns (http_status, parsed_json). Status 0 = network error.'''
    data = None
    headers = {'X-API-Key': api_key}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url.rstrip('/') + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            status = res.status
    except urllib.error.HTTPError as e:
        detail = e.read()[:2000].decode('utf-8', 'replace')
        return e.code, {'error': e.reason, 'detail': detail}
    except Exception as e:  # noqa: BLE001 - network errors are surfaced to the agent
        return 0, {'error': str(e)}
    if not raw:
        return status, {}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {'raw': raw.decode('utf-8', 'replace')[:2000]}


def _failed(status: int) -> bool:
    return status == 0 or status >= 400


def _alias(alias: str):
    a = read_aliases().get(alias)
    if not a:
        return None, {'error': f'alias {alias!r} not found'}
    return a, None


def _endpoint_id(a: dict, endpoint_id):
    '''Use the given endpoint id or fall back to the first endpoint.'''
    if endpoint_id is not None:
        return endpoint_id, None
    status, eps = _api(a['url'], a['api_key'], '/api/endpoints')
    if not isinstance(eps, list):
        return None, {'status': status, 'error': eps}
    if not eps:
        return None, {'error': 'no endpoints found'}
    return eps[0].get('Id'), None


def _docker_get(alias: str, endpoint_id, suffix: str):
    a, err = _alias(alias)
    if err:
        return None, None, err
    ep, err = _endpoint_id(a, endpoint_id)
    if err:
        return None, None, {'alias': alias, **err}
    status, body = _api(a['url'], a['api_key'], f'/api/endpoints/{ep}/docker/{suffix}')
    if _failed(status):
        return None, None, {'alias': alias, 'endpoint_id': ep, 'status': status, 'error': body}
    return ep, body, None


# ---------------------------------------------------------------- mcp tools

@mcp.tool()
def portainer_alias_list() -> dict:
    '''List all configured Portainer aliases (name, url, has_key). Never returns API keys.'''
    return {'aliases': public_view(read_aliases())}


@mcp.tool()
def portainer_alias_get(alias: str, reveal_key: bool = False) -> dict:
    '''Get details for one Portainer alias. The api key is only included when reveal_key=true.'''
    a = read_aliases().get(alias)
    if not a:
        return {'error': f'alias {alias!r} not found'}
    out = {'alias': alias, 'url': a.get('url', ''), 'has_key': bool(a.get('api_key'))}
    if reveal_key:
        out['api_key'] = a.get('api_key', '')
        log.info('api key of alias %s revealed via MCP', alias)
    return out


@mcp.tool()
def portainer_alias_add(alias: str, url: str, api_key: str) -> dict:
    '''Add a new Portainer alias. url = https://host:port of the Portainer instance.'''
    if not NAME_RE.match(alias):
        return {'error': 'invalid alias name (a-z0-9, _, -, max 32 chars)'}
    if not url.startswith(('http://', 'https://')):
        return {'error': 'url must start with http:// or https://'}
    if not api_key:
        return {'error': 'api_key is required'}
    with _lock:
        aliases = read_aliases()
        if alias in aliases:
            return {'error': f'alias {alias!r} already exists (use portainer_alias_update)'}
        if len(aliases) >= MAX_ALIASES:
            return {'error': f'maximum of {MAX_ALIASES} aliases reached'}
        aliases[alias] = {'url': url.rstrip('/'), 'api_key': api_key}
        write_aliases(aliases)
    log.info('alias %s added', alias)
    return {'ok': True, 'alias': alias, 'url': url.rstrip('/')}


@mcp.tool()
def portainer_alias_update(alias: str, url: str | None = None, api_key: str | None = None) -> dict:
    '''Update url and/or api_key of an existing alias. Omitted fields are kept.'''
    with _lock:
        aliases = read_aliases()
        a = aliases.get(alias)
        if not a:
            return {'error': f'alias {alias!r} not found'}
        if url is not None:
            if not url.startswith(('http://', 'https://')):
                return {'error': 'url must start with http:// or https://'}
            a['url'] = url.rstrip('/')
        if api_key is not None:
            if not api_key:
                return {'error': 'api_key must not be empty'}
            a['api_key'] = api_key
        write_aliases(aliases)
    log.info('alias %s updated', alias)
    return {'ok': True, 'alias': alias}


@mcp.tool()
def portainer_alias_remove(alias: str) -> dict:
    '''Remove a Portainer alias.'''
    with _lock:
        aliases = read_aliases()
        if alias not in aliases:
            return {'error': f'alias {alias!r} not found'}
        del aliases[alias]
        write_aliases(aliases)
    log.info('alias %s removed', alias)
    return {'ok': True}


@mcp.tool()
def portainer_status(alias: str) -> dict:
    '''Probe one alias: reachability + Portainer version. Uses GET /api/status.'''
    a, err = _alias(alias)
    if err:
        return err
    status, body = _api(a['url'], a['api_key'], '/api/status')
    if _failed(status):
        return {'alias': alias, 'ok': False, 'status': status, 'error': body.get('error', body)}
    return {'alias': alias, 'ok': True, 'version': body.get('Version', 'unknown')}


@mcp.tool()
def portainer_endpoints(alias: str) -> dict:
    '''List the environments/endpoints of a Portainer instance. Uses GET /api/endpoints.'''
    a, err = _alias(alias)
    if err:
        return err
    status, body = _api(a['url'], a['api_key'], '/api/endpoints')
    if not isinstance(body, list):
        return {'alias': alias, 'status': status, 'error': body}
    return {
        'alias': alias,
        'count': len(body),
        'endpoints': [
            {'id': e.get('Id'), 'name': e.get('Name'), 'type': e.get('Type'),
             'url': e.get('URL'), 'status': e.get('Status')}
            for e in body
        ],
    }


@mcp.tool()
def portainer_containers_list(alias: str, endpoint_id: int | None = None, all: bool = False) -> dict:
    '''List containers of one endpoint (default: first endpoint). all=true includes stopped containers.'''
    ep, body, err = _docker_get(alias, endpoint_id, f'containers/json?all={str(all).lower()}')
    if err:
        return err
    if not isinstance(body, list):
        return {'alias': alias, 'endpoint_id': ep, 'error': body}
    return {
        'alias': alias,
        'endpoint_id': ep,
        'count': len(body),
        'containers': [
            {'id': c.get('Id', '')[:12], 'name': (c.get('Names') or [''])[0].lstrip('/'),
             'image': c.get('Image'), 'state': c.get('State'), 'status': c.get('Status')}
            for c in body
        ],
    }


@mcp.tool()
def portainer_stacks_list(alias: str) -> dict:
    '''List all stacks provisioned on a Portainer instance. Uses GET /api/stacks.'''
    a, err = _alias(alias)
    if err:
        return err
    status, body = _api(a['url'], a['api_key'], '/api/stacks')
    if not isinstance(body, list):
        return {'alias': alias, 'status': status, 'error': body}
    return {
        'alias': alias,
        'count': len(body),
        'stacks': [
            {'id': s.get('Id'), 'name': s.get('Name'), 'type': s.get('Type'),
             'status': s.get('Status'), 'endpoint_id': s.get('EndpointId')}
            for s in body
        ],
    }


@mcp.tool()
def portainer_docker_images(alias: str, endpoint_id: int | None = None) -> dict:
    '''List Docker images of an endpoint (default: first endpoint).'''
    ep, body, err = _docker_get(alias, endpoint_id, 'images/json')
    if err:
        return err
    if not isinstance(body, list):
        return {'alias': alias, 'endpoint_id': ep, 'error': body}
    return {
        'alias': alias,
        'endpoint_id': ep,
        'count': len(body),
        'images': [
            {'id': i.get('Id', '')[:12], 'tags': i.get('RepoTags') or ['<none>'],
             'size': i.get('Size'), 'created': i.get('Created')}
            for i in body
        ],
    }


@mcp.tool()
def portainer_networks(alias: str, endpoint_id: int | None = None) -> dict:
    '''List Docker networks of an endpoint (default: first endpoint).'''
    ep, body, err = _docker_get(alias, endpoint_id, 'networks')
    if err:
        return err
    if not isinstance(body, list):
        return {'alias': alias, 'endpoint_id': ep, 'error': body}
    return {
        'alias': alias,
        'endpoint_id': ep,
        'count': len(body),
        'networks': [
            {'id': n.get('Id', '')[:12], 'name': n.get('Name'), 'driver': n.get('Driver'),
             'scope': n.get('Scope'), 'ipam': (n.get('IPAM') or {}).get('Config', [])}
            for n in body
        ],
    }


@mcp.tool()
def portainer_volumes(alias: str, endpoint_id: int | None = None) -> dict:
    '''List Docker volumes of an endpoint (default: first endpoint).'''
    ep, body, err = _docker_get(alias, endpoint_id, 'volumes')
    if err:
        return err
    vols = body.get('Volumes') if isinstance(body, dict) else None
    vols = vols or []
    return {
        'alias': alias,
        'endpoint_id': ep,
        'count': len(vols),
        'volumes': [
            {'name': v.get('Name'), 'driver': v.get('Driver'),
             'mountpoint': v.get('Mountpoint'), 'scope': v.get('Scope')}
            for v in vols
        ],
    }


@mcp.tool()
def portainer_system_info(alias: str) -> dict:
    '''Get Portainer system information. Uses GET /api/system/status.'''
    a, err = _alias(alias)
    if err:
        return err
    status, body = _api(a['url'], a['api_key'], '/api/system/status')
    if _failed(status):
        return {'alias': alias, 'ok': False, 'status': status, 'error': body}
    return {'alias': alias, 'ok': True, 'system': body}


@mcp.tool()
def portainer_deploy_stack(alias: str, stack_name: str, stack_file: str, endpoint_id: int | None = None) -> dict:
    '''Deploy a standalone Docker Compose stack. stack_file is the content of a docker-compose.yml.
    Uses POST /api/stacks/create/standalone/string?endpointId=<id> (default: first endpoint).'''
    if not STACK_RE.match(stack_name):
        return {'error': 'invalid stack_name (a-z0-9, _, -, max 63 chars)'}
    a, err = _alias(alias)
    if err:
        return err
    ep, err = _endpoint_id(a, endpoint_id)
    if err:
        return {'alias': alias, **err}
    payload = {'name': stack_name, 'stackFileContent': stack_file}
    status, body = _api(a['url'], a['api_key'], f'/api/stacks/create/standalone/string?endpointId={ep}', 'POST', payload)
    if _failed(status):
        return {'alias': alias, 'ok': False, 'status': status, 'error': body}
    log.info('stack %s deployed on %s (endpoint %s)', stack_name, alias, ep)
    return {'alias': alias, 'ok': True, 'endpoint_id': ep, 'result': body}


@mcp.tool()
def portainer_pull_image(alias: str, image: str, endpoint_id: int | None = None) -> dict:
    '''Pull a Docker image on an endpoint (default: first endpoint), e.g. image=nginx:latest.'''
    a, err = _alias(alias)
    if err:
        return err
    ep, err = _endpoint_id(a, endpoint_id)
    if err:
        return {'alias': alias, **err}
    q = urllib.parse.quote(image, safe='')
    status, body = _api(a['url'], a['api_key'], f'/api/endpoints/{ep}/docker/images/create?fromImage={q}', 'POST')
    if _failed(status):
        return {'alias': alias, 'status': status, 'error': body}
    log.info('image %s pulled on %s (endpoint %s)', image, alias, ep)
    return {'alias': alias, 'endpoint_id': ep, 'status': status, 'image': image, 'message': 'Pull finished'}


@mcp.tool()
def portainer_undeploy_stack(alias: str, stack_id: int, endpoint_id: int | None = None) -> dict:
    '''Remove a stack. Uses DELETE /api/stacks/{stack_id}?endpointId=<id>. If endpoint_id is omitted it is read from the stack.'''
    a, err = _alias(alias)
    if err:
        return err
    ep = endpoint_id
    if ep is None:
        status, stack = _api(a['url'], a['api_key'], f'/api/stacks/{int(stack_id)}')
        if _failed(status) or not isinstance(stack, dict):
            return {'alias': alias, 'ok': False, 'status': status, 'error': stack}
        ep = stack.get('EndpointId')
    status, body = _api(a['url'], a['api_key'], f'/api/stacks/{int(stack_id)}?endpointId={ep}', 'DELETE')
    if _failed(status):
        return {'alias': alias, 'ok': False, 'status': status, 'error': body}
    log.info('stack %s removed on %s (endpoint %s)', stack_id, alias, ep)
    return {'alias': alias, 'ok': True, 'result': body}


# ---------------------------------------------------------------- admin auth

def _sig(exp: int) -> str:
    return hmac.new(ADMIN_PASSWORD.encode('utf-8'), f'admin|{exp}'.encode('utf-8'), hashlib.sha256).hexdigest()


def _make_token() -> str:
    exp = int(time.time()) + SESSION_TTL
    return f'{exp}.{_sig(exp)}'


def _valid_token(tok: str) -> bool:
    if not ADMIN_PASSWORD:
        return False
    try:
        exp_s, sig = tok.split('.', 1)
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sig, _sig(exp))


def _password_ok(candidate: str) -> bool:
    return bool(ADMIN_PASSWORD) and hmac.compare_digest(candidate.encode('utf-8'), ADMIN_PASSWORD.encode('utf-8'))


def _rate_limited(ip: str) -> bool:
    now = time.time()
    fails = [t for t in _failed_logins.get(ip, []) if now - t < LOGIN_WINDOW]
    _failed_logins[ip] = fails
    return len(fails) >= LOGIN_MAX_FAILS


# ---------------------------------------------------------------- admin web page

ADMIN_HTML = '''<!DOCTYPE html>
<html lang=de>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>hAI.PortainerMCP - Alias-Verwaltung</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; background: #0f1419; color: #e6edf3; margin: 0; padding: 24px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #8b98a9; font-size: 13px; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; max-width: 900px; background: #161c22; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #242c36; font-size: 14px; }
  th { color: #8b98a9; font-weight: 600; }
  .mono { font-family: ui-monospace, monospace; }
  button { background: #2d6cdf; color: #fff; border: 0; border-radius: 6px; padding: 7px 12px; cursor: pointer; font-size: 13px; }
  button.ghost { background: transparent; color: #c9d1d9; border: 1px solid #3a4553; }
  button.danger { background: #b23b3b; }
  form { max-width: 900px; background: #161c22; padding: 16px; border-radius: 8px; }
  label { display: block; color: #8b98a9; font-size: 12px; margin: 10px 2px 4px; }
  input { width: 100%; box-sizing: border-box; background: #1c242c; color: #e6edf3; border: 1px solid #2d3742; border-radius: 6px; padding: 8px; font-size: 14px; }
  .err { background: #3d1f1f; border: 1px solid #8a3b3b; padding: 10px 12px; border-radius: 6px; }
  .badge { font-size: 11px; padding: 2px 7px; border-radius: 10px; background: #242c36; color: #8b98a9; }
  .login { max-width: 340px; margin: 80px auto; }
</style>
</head>
<body>
<div id=app></div>
<script>
var app = document.getElementById('app');

function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

function load() {
  fetch('/api/aliases').then(function (r) {
    if (r.status === 401) return login();
    r.json().then(function (data) {
      var items = data.aliases || [];
      var h = '<h1>hAI.PortainerMCP</h1><p class=sub>Alias-Verwaltung für ' + items.length + ' Portainer-Instanzen (max. 20). Zugangsdaten liegen in data/portainer_aliases.json.</p><p><button id=add>+ Neuer Alias</button></p>';
      if (!items.length) h += '<p class=sub>Noch keine Aliase hinterlegt.</p>';
      else {
        h += '<table><tr><th>Alias</th><th>URL</th><th>Key</th><th></th></tr>';
        items.forEach(function (a, i) {
          h += '<tr><td class=mono>' + esc(a.alias) + '</td><td class=mono>' + esc(a.url) + '</td><td>' + (a.has_key ? '<span class=badge>key</span>' : '-') + '</td><td><button class=ghost data-edit=' + i + '>Bearbeiten</button> <button class=danger data-del=' + i + '>Löschen</button></td></tr>';
        });
        h += '</table>';
      }
      app.innerHTML = h;
      document.getElementById('add').onclick = function () { showForm(null); };
      app.querySelectorAll('[data-edit]').forEach(function (b) { b.onclick = function () { showForm(items[+b.getAttribute('data-edit')]); }; });
      app.querySelectorAll('[data-del]').forEach(function (b) {
        b.onclick = function () {
          var al = items[+b.getAttribute('data-del')].alias;
          if (!window.confirm('Alias ' + al + ' löschen?')) return;
          fetch('/api/aliases/' + encodeURIComponent(al), { method: 'DELETE' }).then(function (r) { if (r.ok) load(); });
        };
      });
    });
  });
}

function showForm(item) {
  var isEdit = !!item;
  var alias = isEdit ? item.alias : null;
  app.innerHTML = (isEdit ? '<p><button class=ghost id=back>← Zurück</button></p>' : '') +
    '<form id=f><h1>' + (isEdit ? 'Alias bearbeiten: ' + esc(alias) : 'Neuer Alias') + '</h1>' +
    (isEdit ? '' : '<label>Alias</label><input id=name class=mono placeholder=homelab>') +
    '<label>URL</label><input id=url placeholder=https://portainer.example.com>' +
    '<label>API-Key' + (isEdit ? ' (leer lassen = behalten)' : '') + '</label><input id=key type=password class=mono placeholder=ptr_xxx autocomplete=off>' +
    '<p><button type=submit>Speichern</button></p><p class=err id=err style=display:none></p></form>';
  if (isEdit) {
    document.getElementById('back').onclick = load;
    document.getElementById('url').value = item.url || '';
  }
  document.getElementById('f').onsubmit = function (ev) {
    ev.preventDefault();
    var name = isEdit ? alias : document.getElementById('name').value.trim();
    var payload = { url: document.getElementById('url').value.trim(), api_key: document.getElementById('key').value.trim() };
    fetch('/api/aliases/' + encodeURIComponent(name), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(function (r) {
      if (r.ok) load();
      else r.text().then(function (t) { var msg = 'Fehler ' + r.status; try { msg = JSON.parse(t).error || msg; } catch (e) {} var el = document.getElementById('err'); el.textContent = msg; el.style.display = 'block'; });
    });
  };
}

function login() {
  app.innerHTML = '<form class=login><h1>Admin-Zugang</h1><p class=sub>Passwort eingeben.</p>' +
    '<label>Passwort</label><input type=password id=pw autofocus>' +
    '<p><button type=submit>Anmelden</button></p>' +
    '<p class=err id=loginErr style=display:none></p></form>';
  app.querySelector('form').onsubmit = function (ev) {
    ev.preventDefault();
    fetch('/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: document.getElementById('pw').value }) }).then(function (r) {
      if (r.ok) load();
      else { var el = document.getElementById('loginErr'); el.textContent = r.status === 429 ? 'Zu viele Versuche. Bitte später erneut.' : 'Falsches Passwort.'; el.style.display = 'block'; }
    });
  };
}

load();
</script>
</body>
</html>
'''


class AdminHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: object, extra: dict | None = None):
        self._send(status, json.dumps(obj).encode(), 'application/json', extra)

    def _body(self) -> str:
        length = int(self.headers.get('Content-Length') or 0)
        if length > 1024 * 1024:
            raise ValueError('body too large')
        return self.rfile.read(length).decode('utf-8')

    def _authed(self) -> bool:
        if not ADMIN_PASSWORD:
            return False
        token = self.headers.get('X-Admin-Token') or ''
        if token and _password_ok(token):
            return True
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie') or '')
        except Exception:  # noqa: BLE001
            return False
        morsel = jar.get('ptadmin')
        return bool(morsel) and _valid_token(morsel.value)

    def do_GET(self):  # noqa: N802
        if self.path == '/health':
            self._json(200, {'ok': True})
            return
        if not ADMIN_PASSWORD:
            self._send(503, b'admin disabled (PORTAINER_ADMIN_PASSWORD not set)', 'text/plain')
            return
        if self.path in ('/', '/index.html'):
            self._send(200, ADMIN_HTML.encode(), 'text/html; charset=utf-8')
            return
        if self.path == '/api/aliases':
            if not self._authed():
                self._json(401, {'error': 'unauthorized'})
                return
            self._json(200, {'aliases': public_view(read_aliases())})
            return
        parts = [p for p in self.path.split('/') if p]
        if len(parts) == 3 and parts[0] == 'api' and parts[1] == 'aliases':
            if not self._authed():
                self._json(401, {'error': 'unauthorized'})
                return
            alias = urllib.parse.unquote(parts[2])
            a = read_aliases().get(alias)
            if not a:
                self._json(404, {'error': 'alias not found'})
                return
            self._json(200, {'alias': alias, 'url': a.get('url', ''), 'has_key': bool(a.get('api_key'))})
            return
        self._json(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        if self.path == '/login':
            if not ADMIN_PASSWORD:
                self._json(503, {'error': 'admin disabled'})
                return
            ip = self.client_address[0]
            if _rate_limited(ip):
                self._json(429, {'error': 'too many attempts'})
                return
            try:
                pw = str(json.loads(self._body()).get('password', ''))
            except Exception:  # noqa: BLE001
                pw = ''
            if not _password_ok(pw):
                _failed_logins.setdefault(ip, []).append(time.time())
                log.warning('failed admin login from %s', ip)
                self._json(401, {'error': 'invalid password'})
                return
            _failed_logins.pop(ip, None)
            cookie = f'ptadmin={_make_token()}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}'
            self._json(200, {'ok': True}, {'Set-Cookie': cookie})
            return
        self._json(404, {'error': 'not found'})

    def do_PUT(self):  # noqa: N802
        parts = [p for p in self.path.split('/') if p]
        if not (len(parts) == 3 and parts[0] == 'api' and parts[1] == 'aliases'):
            self._json(404, {'error': 'not found'})
            return
        if not self._authed():
            self._json(401, {'error': 'unauthorized'})
            return
        alias = urllib.parse.unquote(parts[2])
        try:
            body = json.loads(self._body())
        except Exception:  # noqa: BLE001
            self._json(400, {'error': 'invalid JSON'})
            return
        url = (body.get('url') or '').strip()
        api_key = (body.get('api_key') or '').strip()
        with _lock:
            aliases = read_aliases()
            existing = alias in aliases
            if not existing and not NAME_RE.match(alias):
                self._json(400, {'error': 'invalid alias name (a-z0-9, _, -, max 32 chars)'})
                return
            if url and not url.startswith(('http://', 'https://')):
                self._json(400, {'error': 'url must start with http:// or https://'})
                return
            if existing:
                a = aliases[alias]
                if url:
                    a['url'] = url.rstrip('/')
                if api_key:
                    a['api_key'] = api_key
            else:
                if not url:
                    self._json(400, {'error': 'url must start with http:// or https://'})
                    return
                if not api_key:
                    self._json(400, {'error': 'api_key is required'})
                    return
                if len(aliases) >= MAX_ALIASES:
                    self._json(400, {'error': f'maximum of {MAX_ALIASES} aliases reached'})
                    return
                a = aliases[alias] = {'url': url.rstrip('/'), 'api_key': api_key}
            try:
                write_aliases(aliases)
            except OSError as e:
                self._json(500, {'error': f'save failed: {e} - is {ALIASES_FILE} writable?'})
                return
        log.info('alias %s saved via admin page', alias)
        self._json(200, {'ok': True, 'alias': alias, 'has_key': bool(a.get('api_key'))})

    def do_DELETE(self):  # noqa: N802
        parts = [p for p in self.path.split('/') if p]
        if not (len(parts) == 3 and parts[0] == 'api' and parts[1] == 'aliases'):
            self._json(404, {'error': 'not found'})
            return
        if not self._authed():
            self._json(401, {'error': 'unauthorized'})
            return
        alias = urllib.parse.unquote(parts[2])
        with _lock:
            aliases = read_aliases()
            if alias not in aliases:
                self._json(404, {'error': 'alias not found'})
                return
            del aliases[alias]
            write_aliases(aliases)
        log.info('alias %s deleted via admin page', alias)
        self._json(200, {'ok': True})


def start_admin_server() -> threading.Thread:
    if not ADMIN_PASSWORD:
        log.warning('admin page disabled - set PORTAINER_ADMIN_PASSWORD (would listen on :%s)', ADMIN_PORT)
        return threading.Thread()
    if ADMIN_PASSWORD == 'CHANGE_ME_ADMIN':
        log.warning('PORTAINER_ADMIN_PASSWORD is still the default CHANGE_ME_ADMIN - change it!')
    server = ThreadingHTTPServer(('0.0.0.0', ADMIN_PORT), AdminHandler)
    log.info('admin page on http://0.0.0.0:%s', ADMIN_PORT)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- MCP auth + health (ASGI)

class BearerAuth:
    '''ASGI middleware: /health is open, everything else needs Authorization: Bearer <PORTAINER_MCP_API_KEY>.'''

    def __init__(self, app):
        self.app = app

    async def _reply(self, send, status: int, body: bytes, extra=None):
        headers = [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]
        headers += extra or []
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': body})

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            if scope.get('path') == '/health':
                await self._reply(send, 200, b'{"ok": true}')
                return
            if MCP_API_KEY:
                auth = ''
                for k, v in scope.get('headers', []):
                    if k == b'authorization':
                        auth = v.decode('latin-1')
                        break
                expected = 'Bearer ' + MCP_API_KEY
                if not hmac.compare_digest(auth.encode('utf-8'), expected.encode('utf-8')):
                    await self._reply(send, 401, b'{"error": "unauthorized"}', [(b'www-authenticate', b'Bearer')])
                    return
        await self.app(scope, receive, send)


if __name__ == '__main__':
    import uvicorn

    os.makedirs(os.path.dirname(ALIASES_FILE) or '.', exist_ok=True)
    if not os.path.exists(ALIASES_FILE):
        write_aliases({})
        log.info('created empty alias file %s', ALIASES_FILE)
    if not MCP_API_KEY:
        log.warning('PORTAINER_MCP_API_KEY is not set - the MCP endpoint is NOT protected!')
    start_admin_server()
    log.info('MCP (streamable-http) on http://0.0.0.0:%s/mcp, health on /health', MCP_PORT)
    uvicorn.run(BearerAuth(mcp.streamable_http_app()), host='0.0.0.0', port=MCP_PORT, log_level='info')
