#!/usr/bin/env python3
"""HA-Kleve TCP-Forwarder.

Reicht TCP-Verbindungen an die Home-Assistant-Instanz in Kleve weiter,
damit Container OHNE Tailscale (z. B. hermes) den HA-MCP-Endpunkt
ueber dieses Relay (coder-infra-Netz) erreichen.

Aufruf: python3 ha-forward.py [LISTEN_PORT] [TARGET_HOST] [TARGET_PORT]
Standard: 18123 -> 192.168.178.52:8123
"""
import asyncio
import sys

LOCAL_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18123
TARGET_HOST = sys.argv[2] if len(sys.argv) > 2 else "192.168.178.52"
TARGET_PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 8123


async def _pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle(reader, writer):
    try:
        tr, tw = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except Exception as exc:
        print(f"forward: Ziel nicht erreichbar: {exc}", flush=True)
        try:
            writer.close()
        except Exception:
            pass
        return
    try:
        await asyncio.gather(_pipe(reader, tw), _pipe(tr, writer))
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(_handle, "0.0.0.0", LOCAL_PORT)
    print(f"HA-Kleve-TCP-Forwarder: 0.0.0.0:{LOCAL_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
