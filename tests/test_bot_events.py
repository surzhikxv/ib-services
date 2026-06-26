import asyncio


def test_emit_swallows_exceptions_and_runs_in_thread():
    from bot import bot as botmod

    calls = []

    def ok(a, b=None):
        calls.append((a, b))

    def boom(*_a, **_k):
        raise RuntimeError("lake down")

    # ok path runs the function
    asyncio.run(botmod._emit(ok, 1, b=2))
    assert calls == [(1, 2)]
    # error path must NOT raise (funnel/Prodamus never blocked)
    asyncio.run(botmod._emit(boom, 1))  # no exception propagates
