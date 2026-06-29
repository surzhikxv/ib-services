import httpx

from kontur.connectors.instagram.client import InstagramClient


def test_prod_client_carries_proxy_transport():
    c = InstagramClient("tok", proxy_url="http://relay:3128")
    try:
        assert isinstance(c._http._transport, httpx.HTTPTransport)   # прод-транспорт с прокси
    finally:
        c.close()


def test_proxy_and_transport_mutually_exclusive():
    try:
        InstagramClient("tok", proxy_url="http://relay:3128",
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        assert False, "expected ValueError"
    except ValueError:
        pass
