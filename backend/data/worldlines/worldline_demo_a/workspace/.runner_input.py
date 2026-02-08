
import urllib.request
from pathlib import Path

try:
    urllib.request.urlopen('https://example.com', timeout=4).read(10)
    print('network_unexpected_success')
except Exception as e:
    print('network_blocked', type(e).__name__)

try:
    Path('/etc/proof.txt').write_text('x')
    print('rootfs_unexpected_write_success')
except Exception as e:
    print('rootfs_blocked', type(e).__name__)
