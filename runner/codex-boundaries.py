"""Process/network qualification in an owned credential-free Linux container."""
import json
from pathlib import Path
import socket
import subprocess
import threading
import time

PAYLOAD = """import errno, os, pathlib, socket, sys
pid, port, network = map(int, sys.argv[1:])
for path in [f'/proc/{pid}/environ', f'/proc/{pid}/root/var/tmp/credential-canary']:
    try:
        pathlib.Path(path).read_bytes()
    except OSError as error:
        assert error.errno in (errno.ENOENT, errno.EACCES, errno.EPERM), error
    else:
        raise AssertionError('sibling process path readable')
try:
    os.kill(pid, 0)
except OSError as error:
    assert error.errno in (errno.ESRCH, errno.EPERM), error
else:
    raise AssertionError('sibling signal access')
try:
    with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
        data = connection.recv(20)
except OSError:
    assert not network, 'configured network unavailable'
else:
    assert network and data == b'control', 'network restriction missing'
print('process-and-network-ok', network)
"""


def protected_checks(pid, port):
    for network in (False, True):
        args = ['codex', 'sandbox', '-c', 'sandbox_mode="workspace-write"',
                '-c', 'sandbox_workspace_write.network_access=' + str(network).lower(),
                '--', 'python3', '-c', PAYLOAD, str(pid), str(port), str(int(network))]
        result = subprocess.run(args, cwd='/workspaces/boundaries', capture_output=True,
                                text=True, timeout=15)
        sentinel = 'process-and-network-ok ' + str(int(network))
        if result.returncode != 0 or result.stdout.strip() != sentinel:
            raise RuntimeError('protected process/network effects failed')
        print(json.dumps({'network': network, 'status': result.returncode,
                          'result': sentinel}), flush=True)


def main():
    Path('/workspaces/boundaries/.git').mkdir(parents=True, exist_ok=True)
    peer = None
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(4)
        port = listener.getsockname()[1]

        def serve():
            while True:
                try:
                    connection, _ = listener.accept()
                    with connection:
                        connection.sendall(b'control')
                except OSError:
                    return

        threading.Thread(target=serve, daemon=True).start()
        try:
            # Ordinary runtime controls prove the protected denials are meaningful.
            peer = subprocess.Popen(['sleep', '40'], env={'CANARY_CREDENTIAL': 'disposable-no-account-secret'})
            deadline = time.monotonic() + 2
            while b'CANARY_CREDENTIAL' not in Path(f'/proc/{peer.pid}/environ').read_bytes():
                if time.monotonic() >= deadline:
                    raise RuntimeError('peer failed to exec')
                time.sleep(0.01)
            with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
                assert connection.recv(20) == b'control'
            Path('/var/tmp/credential-canary').write_text('disposable-no-account-secret')
            protected_checks(peer.pid, port)
        finally:
            if peer is not None:
                peer.terminate()
                peer.wait(timeout=2)
            Path('/var/tmp/credential-canary').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
