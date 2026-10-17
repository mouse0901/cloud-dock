"""Reuse TLS trust loading while keeping each account cookie jar isolated."""
import functools, os, ssl
import httpx, certifi
@functools.lru_cache(maxsize=2)
def context(trust_env=True):
    if trust_env and os.environ.get('SSL_CERT_FILE'):
        return ssl.create_default_context(cafile=os.environ['SSL_CERT_FILE'])
    if trust_env and os.environ.get('SSL_CERT_DIR'):
        return ssl.create_default_context(capath=os.environ['SSL_CERT_DIR'])
    return ssl.create_default_context(cafile=certifi.where())
def client(*,trust_env=True,**kwargs):
    return httpx.AsyncClient(verify=context(trust_env),trust_env=trust_env,**kwargs)
