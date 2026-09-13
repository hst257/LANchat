import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Start LAN Chat and show its LAN link")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("LAN_CHAT_PORT", "8000")),
        help="TCP port to use (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart automatically when source files change",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    os.environ["LAN_CHAT_PORT"] = str(args.port)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=args.port,
        reload=args.reload,
        workers=1,
    )


if __name__ == "__main__":
    main()
