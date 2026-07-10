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


def test_record_payment_links_subscriber_tariff_and_source(monkeypatch, tmp_path):
    from sqlalchemy import select

    from bot import bot as b
    from kontur.db import init_db, make_engine, make_session_factory
    from kontur.models import Payment, Source, Subscriber, Tariff

    db_url = f"sqlite:///{tmp_path / 'kontur.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    engine = make_engine(db_url)
    init_db(engine)
    sf = make_session_factory(engine)

    with sf() as session:
        src = Source(kind="start_link", code="c1778263208250-ds", utm_source="telegram")
        session.add(src)
        session.flush()
        session.add(
            Subscriber(
                source_system="telegram_bot",
                external_id="202",
                tg_user_id="202",
                source_id=src.id,
            )
        )
        session.commit()

    b._record_payment(202, "standard", {"order_id": "46493613", "sum": "1990.00", "currency": "rub"})

    with sf() as session:
        payment = session.scalars(select(Payment).where(Payment.external_id == "46493613")).one()
        standard = session.scalars(select(Tariff).where(Tariff.key == "standard")).one()
        subscriber = session.scalars(select(Subscriber).where(Subscriber.external_id == "202")).one()
        assert payment.subscriber_id == subscriber.id
        assert payment.tariff_id == standard.id
        assert payment.source_id == subscriber.source_id
        assert float(payment.amount) == 1990.0
    assert b._has_successful_payment(202, "standard") is True
    assert b._has_successful_payment(202, "premium") is False


def test_send_paid_confirmation_uses_personal_invite(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b

    sent = []
    deleted = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))
            return SimpleNamespace(message_id=501)

        async def delete_message(self, chat_id, message_id):
            deleted.append((chat_id, message_id))

    monkeypatch.setattr(b, "STEPS", [object() for _ in range(9)])
    b.STEP_MESSAGES[777] = [10, 11]

    ok = asyncio.run(
        b._send_paid_confirmation(FakeBot(), 777, "premium", invite_link="https://t.me/+personal")
    )

    assert ok is True
    assert deleted == [(777, 10), (777, 11)]
    assert 777 not in b.STEP_MESSAGES
    chat_id, text, kwargs = sent[0]
    assert chat_id == 777
    assert "Премиум" in text
    assert "Доступ открыт" in text
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Войти в канал"
    assert button.url == "https://t.me/+personal"
    back = kwargs["reply_markup"].inline_keyboard[1][0]
    assert back.text == "Назад"
    assert back.callback_data == "paid_back:1"


def test_send_paid_confirmation_uses_static_channel_fallback(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b
    from bot.routing import Route

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))
            return SimpleNamespace(message_id=502)

        async def delete_message(self, chat_id, message_id):
            pass

    steps = [object() for _ in range(9)]
    steps[6] = SimpleNamespace(blocks=[SimpleNamespace(buttons=[object(), object(), object()])])
    monkeypatch.setattr(b, "STEPS", steps)
    monkeypatch.setattr(b, "ROUTES", {
        (6, 0, 1): Route("url", url="https://t.me/+basic"),
        (6, 0, 2): Route("terminal"),
    })

    ok = asyncio.run(b._send_paid_confirmation(FakeBot(), 888, "basic"))

    assert ok is True
    _, text, kwargs = sent[0]
    assert "Базовый" in text
    assert "Доступ открыт. Нажми кнопку ниже, чтобы перейти в канал." in text
    assert "заяв" not in text.lower()
    assert "Подал заявку" not in text
    keyboard = kwargs["reply_markup"].inline_keyboard
    assert len(keyboard) == 2
    channel_button = keyboard[0][0]
    assert channel_button.text == "Перейти в канал"
    assert channel_button.url == "https://t.me/+basic"
    back = keyboard[1][0]
    assert back.text == "Назад"
    assert back.callback_data == "paid_back:1"


def test_send_paid_confirmation_does_not_fall_back_to_legacy_confirm_step(monkeypatch):
    import asyncio
    from bot import bot as b

    captured = []
    sent = []

    async def fake_send_step(bot, chat_id, step, **kwargs):
        captured.append((bot, chat_id, step, kwargs))

    class FakeBot:
        async def send_message(self, chat_id, text, **_kwargs):
            sent.append((chat_id, text))

    monkeypatch.setattr(b, "send_step", fake_send_step)
    monkeypatch.setattr(b, "STEPS", [object() for _ in range(9)])
    monkeypatch.setattr(b, "ROUTES", {})

    ok = asyncio.run(b._send_paid_confirmation(FakeBot(), 777, "premium"))

    assert ok is False
    assert captured == []
    assert sent == [(
        777,
        "Оплата прошла. Спасибо! Не удалось автоматически отправить ссылку входа, "
        "напишите администратору.",
    )]


def test_send_paid_confirmation_fallback_message(monkeypatch):
    import asyncio
    from bot import bot as b

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **_kwargs):
            sent.append((chat_id, text))

    monkeypatch.setattr(b, "STEPS", [object() for _ in range(9)])
    monkeypatch.setattr(b, "ROUTES", {})

    ok = asyncio.run(b._send_paid_confirmation(FakeBot(), 888, "basic"))

    assert ok is False
    assert sent == [(
        888,
        "Оплата прошла. Спасибо! Не удалось автоматически отправить ссылку входа, "
        "напишите администратору.",
    )]


def test_cmd_start_emits_bot_start_with_identity_and_payload(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b
    from kontur import ingest

    captured = []
    order = []

    async def fake_emit(fn, *a, **k):
        captured.append((fn, a, k))
    async def fake_send_step(*a, **k):
        order.append(("send_step", a, k))
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

    assert order[0][0] == "send_step"
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


def test_paid_back_returns_to_package_choice_without_tracking_paid_message(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b
    from kontur import ingest

    captured = []
    sent = []
    answers = []

    async def fake_emit(fn, *a, **k):
        captured.append((fn, a, k))

    async def fake_send_step(bot, chat_id, step, **kwargs):
        sent.append((bot, chat_id, step, kwargs))

    async def fake_answer(*a, **k):
        answers.append((a, k))

    monkeypatch.setattr(b, "_emit", fake_emit)
    monkeypatch.setattr(b, "send_step", fake_send_step)
    steps = [object(), object()]
    monkeypatch.setattr(b, "STEPS", steps)
    b.STEP_MESSAGES.pop(888, None)

    call = SimpleNamespace(
        data="paid_back:1",
        id="PB",
        bot=object(),
        message=SimpleNamespace(chat=SimpleNamespace(id=888)),
        answer=fake_answer,
    )
    asyncio.run(b.on_paid_back(call))

    assert answers == [((), {})]
    assert sent == [(call.bot, 888, steps[1], {"track": True})]
    assert 888 not in b.STEP_MESSAGES
    fn, args, kwargs = captured[0]
    assert fn is ingest.record_step_enter
    assert args == (888, 1)
    assert kwargs == {"uid": "cqPB", "stage_key": "package_choice", "tariff_key": None}


def test_on_button_terminal_emits_applied(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b
    from bot.routing import Route
    from kontur import ingest

    captured = []
    approvals = []
    answers = []
    async def fake_emit(fn, *a, **k):
        captured.append((fn, a, k))
    async def fake_approve(bot, tg_id, tariff):
        approvals.append((bot, tg_id, tariff))
        return True
    async def fake_send_step(*a, **k):
        pass
    async def fake_answer(*a, **k):
        answers.append((a, k))
    monkeypatch.setattr(b, "_emit", fake_emit)
    monkeypatch.setattr(b, "approve_join_request", fake_approve)
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

    assert approvals == [(call.bot, 888, "premium")]
    assert answers == [(("Заявка одобрена ✅",), {"show_alert": True})]
    assert len(captured) == 1
    fn, args, kwargs = captured[0]
    assert fn is ingest.record_applied
    assert args == (888, 5, "Подал заявку")
    assert kwargs == {"uid": "cqT"}


def test_chat_join_request_approves_paid_user(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot import bot as b

    approvals = []
    checked = []

    async def fake_approve(bot, tg_id, tariff):
        approvals.append((bot, tg_id, tariff))
        return True

    def fake_has_payment(tg_id, tariff):
        checked.append((tg_id, tariff))
        return tg_id == 202 and tariff == "premium"

    monkeypatch.setattr(b, "tariffs_for_chat_id", lambda chat_id: ("standard", "premium") if chat_id == -1001 else ())
    monkeypatch.setattr(b, "_has_successful_payment", fake_has_payment)
    monkeypatch.setattr(b, "approve_join_request", fake_approve)

    req = SimpleNamespace(
        bot=object(),
        chat=SimpleNamespace(id=-1001),
        from_user=SimpleNamespace(id=202),
    )

    asyncio.run(b.on_chat_join_request(req))

    assert checked == [(202, "standard"), (202, "premium")]
    assert approvals == [(req.bot, 202, "premium")]
