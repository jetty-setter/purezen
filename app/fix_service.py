key = open('/root/purezen/.env').read().strip().split('=', 1)[1]
lines = [
    '[Unit]',
    'Description=PureZen API',
    'After=network.target',
    '',
    '[Service]',
    'User=root',
    'WorkingDirectory=/root/purezen',
    'Environment=ANTHROPIC_API_KEY=' + key,
    'ExecStart=/usr/local/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000',
    'Restart=always',
    'RestartSec=3',
    '',
    '[Install]',
    'WantedBy=multi-user.target',
]
open('/etc/systemd/system/purezen.service', 'w').write('\n'.join(lines) + '\n')
print('Done')
