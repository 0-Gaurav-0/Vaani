import os, pytest, uuid, signal, subprocess
def test_keyring_probe():
    if not os.environ.get('VAANI_KEYRING_PROBE'): pytest.skip('probe disabled')
    try:
        p = subprocess.run(['busctl','--user','--timeout=2','status','org.freedesktop.secrets'], timeout=3, capture_output=True)
        if p.returncode != 0:
            err = p.stderr.decode(errors='replace').lower()
            if any(x in err for x in ('no such service','connection refused','service unknown','not found','no such device or address')):
                pytest.skip('Secret Service unavailable')
            raise AssertionError(f'busctl preflight failed: {err}')
    except (FileNotFoundError, subprocess.TimeoutExpired): pytest.skip('Secret Service unavailable')
    try:
        import keyring
        backend=keyring.get_keyring(); service='vaani-probe-'+uuid.uuid4().hex; account='probe'
        def alarm(*_): raise TimeoutError('keyring probe timeout')
        old = signal.signal(signal.SIGALRM, alarm); signal.alarm(5)
        try:
            try:
                backend.set_password(service, account, 'canary-value')
                assert backend.get_password(service, account) == 'canary-value'
            finally:
                backend.delete_password(service, account)
                assert backend.get_password(service, account) is None
        finally:
            signal.alarm(0); signal.signal(signal.SIGALRM, old)
    except Exception as exc:
        text=str(exc).lower()
        if any(x in text for x in ('secret service is locked','collection is locked','no dbus session','dbus.exceptions.nosuchname')):
            pytest.skip(f'Secret Service unavailable: {type(exc).__name__}')
        raise
