"""Local launcher with a graceful UI shutdown callback."""
import argparse
import asyncio

import uvicorn

from .main import app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    # without a timeout, a video still playing in another tab holds the shutdown forever
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, proxy_headers=False,
                                           timeout_graceful_shutdown=5))

    async def serve():
        loop = asyncio.get_running_loop()
        app.state.request_shutdown = lambda: loop.call_later(0.3, setattr, server, "should_exit", True)
        await server.serve()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
