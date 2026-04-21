# scripts/send_update.py
import json
import paho.mqtt.publish as publish

payload = {"capacity_ah": 80.0}

publish.single(
    topic="cloud/updates/BESS_01",
    payload=json.dumps(payload),
    hostname="127.0.0.1",
    port=1883
)

print(f"Published to cloud/updates/BESS_01: {payload}")