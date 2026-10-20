
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from azure.iot.device import IoTHubDeviceClient, Message

load_dotenv()

connection_string = os.getenv("IOTHUB_DEVICE_CONNECTION_STRING")

if not connection_string:
    sys.exit("ERROR: Connection string not found in .env")

client = None

try:
    client = IoTHubDeviceClient.create_from_connection_string(
        connection_string
    )

    print("Connecting to Azure IoT Hub...")
    client.connect()
    print("Connected successfully!")

    telemetry = {
        "message_id": "azure-test-001",
        "temperature_c": 28.5,
        "humidity_pct": 61.0,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": "python-simulation"
    }

    message = Message(json.dumps(telemetry))
    message.content_type = "application/json"
    message.content_encoding = "utf-8"

    client.send_message(message)

    print("Telemetry sent successfully!")
    print(json.dumps(telemetry, indent=2))

except Exception as exc:
    print(f"Azure connection or send failed: {type(exc).__name__}: {exc}")
    sys.exit(1)

finally:
    if client is not None:
        client.shutdown()
