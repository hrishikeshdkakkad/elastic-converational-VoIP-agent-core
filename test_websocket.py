#!/usr/bin/env python3
"""Test WebSocket connection to ngrok tunnel."""

import asyncio
import sys

try:
    import websockets
except ImportError:
    print("websockets module not found, will install...")
    sys.exit(2)


async def test_websocket_connection():
    """Test WebSocket connection through ngrok."""
    uri = "wss://45c72608a2fb.ngrok-free.app/twilio/ws/media/test-connection-12345"

    print(f"🔄 Attempting WebSocket connection to: {uri}")
    print(f"   (This simulates what Twilio would do)")
    print()

    try:
        # Attempt connection with Twilio-like headers
        async with websockets.connect(
            uri,
            additional_headers={
                "User-Agent": "TwilioProxy/1.1",  # Mimic Twilio's User-Agent
                "Origin": "https://www.twilio.com"
            },
            open_timeout=10
        ) as websocket:
            print("✅ SUCCESS: WebSocket connection established!")
            print(f"   Connection state: {websocket.state}")
            print()

            # Try to send a test message
            test_msg = '{"event": "test", "data": "hello"}'
            await websocket.send(test_msg)
            print(f"✅ Sent test message: {test_msg}")
            print()

            # Wait briefly for any response
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                print(f"✅ Received response: {response}")
            except asyncio.TimeoutError:
                print("⏱️  No response received (timeout after 2s) - this is OK for our test")

            print()
            print("🎉 WebSocket connection works through ngrok!")
            print("   This means ngrok is NOT the problem.")

            return True

    except Exception as http_error:
        # Check if it's an HTTP error
        if hasattr(http_error, 'status_code'):
            print(f"❌ HTTP Error: {http_error.status_code}")
            if hasattr(http_error, 'headers'):
                print(f"   Response headers: {http_error.headers}")
            if getattr(http_error, 'status_code', None) == 403:
                print()
                print("   🚨 This suggests ngrok browser warning page is blocking!")
            return False
        # Re-raise if not an HTTP error to be caught by other handlers
        raise

    except websockets.exceptions.WebSocketException as e:
        print(f"❌ WebSocket Error: {type(e).__name__}: {e}")
        return False

    except ConnectionRefusedError:
        print("❌ Connection Refused - Server not accepting connections")
        return False

    except asyncio.TimeoutError:
        print("❌ Connection Timeout - Server not responding")
        return False

    except Exception as e:
        print(f"❌ Unexpected Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Main test function."""
    print("=" * 70)
    print("WebSocket Connection Test - Twilio Media Streams + ngrok")
    print("=" * 70)
    print()

    success = await test_websocket_connection()

    print()
    print("=" * 70)
    if success:
        print("RESULT: ngrok WebSocket forwarding is WORKING ✅")
        print()
        print("Next steps:")
        print("  - Check Twilio account settings")
        print("  - Verify TwiML is being executed correctly")
        print("  - Check for Twilio-side firewall/IP restrictions")
    else:
        print("RESULT: ngrok WebSocket forwarding FAILED ❌")
        print()
        print("Likely cause:")
        print("  - ngrok free tier browser warning page")
        print("  - SSL certificate trust issue")
        print()
        print("Solutions:")
        print("  1. Upgrade to ngrok paid plan ($8/month)")
        print("  2. Use Cloudflare Tunnel (free)")
        print("  3. Deploy to cloud with public IP")
    print("=" * 70)

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
