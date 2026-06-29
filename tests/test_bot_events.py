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
    assert b._button_title([SimpleNamespace()], 0, 0, 0) is None  # AttributeError → None


def test_cmd_start_emits_bot_start_with_identity_and_payload(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b
    from kontur import ingest

    captured = []
    async def fake_emit(fn, *a, **k):
        captured.append((fn, a, k))
    async def fake_send_step(*a, **k):
        pass
    monkeypatch.setattr(b, "_emit", fake_emit)
    monkeypatch.setattr(b, "send_step", fake_send_step)
    monkeypatch.setattr(b, "STEPS", [object()])  # ENTRY_STEP=0 → index ok

    msg = SimpleNamespace(
        bot=object(), chat=SimpleNamespace(id=123), message_id=55,
        from_user=SimpleNamespace(first_name="Иван", last_name="П", username="ivanp"),
    )
    cmd = SimpleNamespace(args="s-ig_c-july")
    asyncio.run(b.cmd_start(msg, cmd))

    assert len(captured) == 1
    fn, args, kwargs = captured[0]
    assert fn is ingest.record_bot_start
    assert args == (123,)
    assert kwargs == {"uid": "m55", "name": "Иван П", "username": "ivanp", "source_code": "s-ig_c-july"}


def test_on_button_step_emits_step_enter_with_stage_and_uid(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b
    from bot.routing import Route
    from kontur import ingest

    captured = []
    async def fake_emit(fn, *a, **k):
        captured.append((fn, a, k))
    async def fake_send_step(*a, **k):
        pass
    async def fake_answer(*a, **k):
        pass
    monkeypatch.setattr(b, "_emit", fake_emit)
    monkeypatch.setattr(b, "send_step", fake_send_step)
    monkeypatch.setattr(b, "STEPS", [object(), object(), object()])
    monkeypatch.setattr(b, "ROUTES", {(1, 0, 0): Route("step", target=2)})

    call = SimpleNamespace(
        data="go:1:0:0", id="999", bot=object(),
        message=SimpleNamespace(chat=SimpleNamespace(id=777)), answer=fake_answer,
    )
    asyncio.run(b.on_button(call))

    assert len(captured) == 1
    fn, args, kwargs = captured[0]
    assert fn is ingest.record_step_enter
    assert args == (777, 2)
    assert kwargs == {"uid": "cq999", "stage_key": "package_info", "tariff_key": "basic"}


def test_on_button_terminal_emits_applied(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b
    from bot.routing import Route
    from kontur import ingest

    captured = []
    async def fake_emit(fn, *a, **k):
        captured.append((fn, a, k))
    async def fake_send_step(*a, **k):
        pass
    async def fake_answer(*a, **k):
        pass
    monkeypatch.setattr(b, "_emit", fake_emit)
    monkeypatch.setattr(b, "send_step", fake_send_step)
    fake_steps = [object()] * 5 + [SimpleNamespace(
        blocks=[SimpleNamespace(buttons=[object(), object(), SimpleNamespace(title="Подал заявку")])])]
    monkeypatch.setattr(b, "STEPS", fake_steps)
    monkeypatch.setattr(b, "ROUTES", {(5, 0, 2): Route("terminal")})

    call = SimpleNamespace(
        data="go:5:0:2", id="T", bot=object(),
        message=SimpleNamespace(chat=SimpleNamespace(id=888)), answer=fake_answer,
    )
    asyncio.run(b.on_button(call))

    assert len(captured) == 1
    fn, args, kwargs = captured[0]
    assert fn is ingest.record_applied
    assert args == (888, 5, "Подал заявку")
    assert kwargs == {"uid": "cqT"}
