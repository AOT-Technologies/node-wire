from __future__ import annotations

import asyncio
import threading

from bindings.grpc_server.async_runner import BackgroundAsyncRunner


def test_background_runner_uses_same_event_loop() -> None:
    runner = BackgroundAsyncRunner()
    runner.start()
    loop_ids: list[int] = []

    async def record_loop() -> int:
        return id(asyncio.get_running_loop())

    loop_ids.append(runner.run(record_loop()))
    loop_ids.append(runner.run(record_loop()))
    assert loop_ids[0] == loop_ids[1]


def test_background_runner_handles_concurrent_calls() -> None:
    runner = BackgroundAsyncRunner()
    runner.start()
    errors: list[BaseException] = []

    async def echo(value: int) -> int:
        await asyncio.sleep(0.01)
        return value

    def worker(value: int) -> None:
        try:
            assert runner.run(echo(value)) == value
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
