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


def test_full_name_joins_first_last():
    from types import SimpleNamespace
    from bot import bot as b
    assert b._full_name(SimpleNamespace(first_name="Иван", last_name="П")) == "Иван П"
    assert b._full_name(SimpleNamespace(first_name="Иван", last_name=None)) == "Иван"
    assert b._full_name(SimpleNamespace(first_name=None, last_name=None)) is None


def test_button_title_lookup_and_guard():
    from types import SimpleNamespace
    from bot import bot as b
    steps = [SimpleNamespace(blocks=[SimpleNamespace(
        buttons=[SimpleNamespace(title="Подал заявку")])])]
    assert b._button_title(steps, 0, 0, 0) == "Подал заявку"
    assert b._button_title(steps, 9, 0, 0) is None   # IndexError → None
