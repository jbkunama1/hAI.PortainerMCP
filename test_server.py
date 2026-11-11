'''Self-check for hAI.PortainerMCP.

Run:  python test_server.py
'''

import inspect
import os
import tempfile

import server

TMP = tempfile.mkdtemp()
server.ALIASES_FILE = os.path.join(TMP, 'aliases.json')
server.write_aliases({})


def check(name, cond):
    if not cond:
        raise SystemExit(f'FAIL: {name}')
    print(f'ok - {name}')


r = server.portainer_alias_add('homelab', 'https://portainer.example.com', 'ptr_abc')
check('add homelab', r.get('ok') is True)

r = server.portainer_alias_add('homelab', 'https://x', 'ptr_yyy')
check('duplicate rejected', 'already exists' in r.get('error', ''))

r = server.portainer_alias_add('bad name!', 'https://x', 'ptr_yyy')
check('invalid name rejected', 'invalid alias name' in r.get('error', ''))

r = server.portainer_alias_list()
check('list has homelab', any(a['alias'] == 'homelab' for a in r['aliases']))
check('list hides key', all('api_key' not in a for a in r['aliases']))
check('list has has_key', all('has_key' in a for a in r['aliases']))

r = server.portainer_alias_get('homelab')
check('get masks key by default', 'api_key' not in r and r.get('has_key') is True)
r = server.portainer_alias_get('homelab', reveal_key=True)
check('get reveals key on request', r.get('api_key') == 'ptr_abc')

r = server.portainer_alias_update('homelab', url='http://10.0.0.20:9000')
check('update url', r.get('ok') is True)
check('key kept', server.read_aliases()['homelab']['api_key'] == 'ptr_abc')

r = server.portainer_alias_get('nope')
check('unknown alias error', 'not found' in r.get('error', ''))

params = inspect.signature(server._api).parameters
check('_api supports method and payload', 'method' in params and 'payload' in params)
check('execute_sql tool removed', not hasattr(server, 'portainer_execute_sql'))

server.ADMIN_PASSWORD = 'secret'
tok = server._make_token()
check('valid session token', server._valid_token(tok))
check('forged token rejected', not server._valid_token('9999999999.deadbeef'))
check('garbage token rejected', not server._valid_token('1'))

r = server.portainer_alias_remove('homelab')
check('remove', r.get('ok') is True)
check('gone after remove', 'homelab' not in server.read_aliases())

print('ALL CHECKS PASSED')
