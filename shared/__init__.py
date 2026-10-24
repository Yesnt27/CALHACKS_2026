# python.org builds of Python on macOS ship without a CA bundle, which breaks TLS to
# Agentverse (mailbox, manifests). Point OpenSSL at certifi before aiohttp is imported.
import os

try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:  # pragma: no cover
    pass
