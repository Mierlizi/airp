"""Small JSON-RPC server used to verify AIRP's LSP adapter process boundary."""
import json
import sys


root_uri = ''


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b'\r\n', b'\n'):
            break
        name, _, value = line.decode('ascii').partition(':')
        headers[name.casefold().strip()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(headers['content-length'])))


def send(message):
    payload = json.dumps(message, separators=(',', ':')).encode()
    sys.stdout.buffer.write(f'Content-Length: {len(payload)}\r\n\r\n'.encode())
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def item(name, uri):
    return {
        'name': name,
        'kind': 12,
        'uri': uri,
        'range': {'start': {'line': 0, 'character': 0},
                  'end': {'line': 2, 'character': 1}},
        'selectionRange': {'start': {'line': 0, 'character': 3},
                           'end': {'line': 0, 'character': 9}},
    }


while True:
    message = read_message()
    if message is None:
        break
    method = message.get('method')
    request_id = message.get('id')
    if method == 'initialize':
        root_uri = message['params']['rootUri']
        send({'jsonrpc': '2.0', 'id': 900, 'method': 'workspace/configuration',
              'params': {'items': [{'section': 'fake'}]}})
        client_response = read_message()
        if client_response.get('id') != 900 or client_response.get('result') != [None]:
            raise RuntimeError('client did not answer workspace/configuration')
        result = {'capabilities': {'callHierarchyProvider': True}}
    elif method == 'initialized':
        send({'jsonrpc': '2.0', 'method': 'experimental/serverStatus',
              'params': {'health': 'ok', 'quiescent': False}})
        send({'jsonrpc': '2.0', 'method': 'experimental/serverStatus',
              'params': {'health': 'ok', 'quiescent': True}})
        continue
    elif method == 'textDocument/prepareCallHierarchy':
        uri = message['params']['textDocument']['uri']
        name = 'caller' if uri.endswith('/a.rs') else 'target'
        result = [item(name, uri)]
    elif method == 'callHierarchy/outgoingCalls':
        current = message['params']['item']['name']
        result = ([{'to': item('target', root_uri + '/b.rs'),
                    'fromRanges': [{'start': {'line': 1, 'character': 4},
                                    'end': {'line': 1, 'character': 10}}]}]
                  if current == 'caller' else [])
    elif method == 'callHierarchy/incomingCalls':
        current = message['params']['item']['name']
        result = ([{'from': item('caller', root_uri + '/a.rs'),
                    'fromRanges': [{'start': {'line': 1, 'character': 4},
                                    'end': {'line': 1, 'character': 10}}]}]
                  if current == 'target' else [])
    elif method == 'shutdown':
        result = None
    elif method == 'exit':
        break
    else:
        continue
    if request_id is not None:
        send({'jsonrpc': '2.0', 'id': request_id, 'result': result})
