"""Isolated loopback WebSocket delivery benchmark; no simulation or frontend work.

python -m flowstate.performance.transport --frame frame.json --output transport.json
"""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import time


async def measure(frame, count=200):
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    payload = json.dumps(frame, separators=(",", ":"))
    async def echo(socket):
        async for message in socket:
            await socket.send(message)
    async with serve(echo, "127.0.0.1", 0, compression=None) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}", compression=None) as socket:
            timings = []
            cpu_start = time.process_time()
            for _ in range(count):
                start = time.perf_counter()
                await socket.send(payload)
                received = await socket.recv()
                timings.append(time.perf_counter() - start)
                assert received == payload
    return {"messages": count, "payloadBytes": len(payload.encode()), "compression": False,
            "clients": 1, "loopbackRoundTripMeanSeconds": statistics.mean(timings),
            "loopbackRoundTripP95Seconds": sorted(timings)[int(count * .95) - 1],
            "cpuSeconds": time.process_time() - cpu_start,
            "limits": "Local echo RTT only; not WAN latency, browser rendering, or service fan-out."}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(measure(json.loads(args.frame.read_text(encoding="utf-8"))))
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
