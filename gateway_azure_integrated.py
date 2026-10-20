"""Store-and-Forward IoT Gateway with local simulation and optional Azure IoT Hub delivery.

Set IOTHUB_DEVICE_CONNECTION_STRING in .env and run with --azure for real Azure sends.
The outage is application-simulated; this does not physically disconnect the network.
"""
from __future__ import annotations
import argparse, csv, json, os, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "gateway.db"
RESULTS_DIR = ROOT / "results"

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

class StoreForwardGateway:
    def __init__(self, db_path: Path = DB_PATH, azure_client: Any = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.azure_client = azure_client
        self.azure_mode = azure_client is not None
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS queue (
            message_id TEXT PRIMARY KEY, created_at_utc TEXT NOT NULL,
            temperature_c REAL NOT NULL, humidity_pct REAL NOT NULL,
            local_analysis TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            sent_at_utc TEXT
        );
        CREATE TABLE IF NOT EXISTS cloud_received (
            message_id TEXT PRIMARY KEY, created_at_utc TEXT NOT NULL,
            received_at_utc TEXT NOT NULL, temperature_c REAL NOT NULL,
            humidity_pct REAL NOT NULL, local_analysis TEXT NOT NULL
        );
        """)
        self.conn.commit()

    @staticmethod
    def analyze(temperature_c: float, humidity_pct: float) -> str:
        alerts = []
        if temperature_c >= 35.0: alerts.append("HIGH_TEMPERATURE")
        if humidity_pct >= 80.0: alerts.append("HIGH_HUMIDITY")
        return "|".join(alerts) if alerts else "NORMAL"

    def create_reading(self, sequence: int, temperature_c: float,
                       humidity_pct: float, online: bool) -> dict[str, Any]:
        message = {
            "message_id": f"sensor-01-{sequence:05d}",
            "created_at_utc": utc_now(),
            "temperature_c": round(temperature_c, 2),
            "humidity_pct": round(humidity_pct, 2),
            "local_analysis": self.analyze(temperature_c, humidity_pct),
        }
        self.conn.execute(
            """INSERT OR IGNORE INTO queue
               (message_id, created_at_utc, temperature_c, humidity_pct, local_analysis, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (message["message_id"], message["created_at_utc"], message["temperature_c"],
             message["humidity_pct"], message["local_analysis"]))
        self.conn.commit()
        if online: self.forward_pending()
        return message

    def _send_to_azure(self, row: sqlite3.Row) -> None:
        from azure.iot.device import Message
        payload = {
            "message_id": row["message_id"], "created_at_utc": row["created_at_utc"],
            "temperature_c": row["temperature_c"], "humidity_pct": row["humidity_pct"],
            "local_analysis": row["local_analysis"], "source": "store-forward-iot-gateway",
        }
        msg = Message(json.dumps(payload))
        msg.content_type = "application/json"
        msg.content_encoding = "utf-8"
        msg.message_id = row["message_id"]
        msg.custom_properties["created_at_utc"] = row["created_at_utc"]
        msg.custom_properties["local_analysis"] = row["local_analysis"]
        self.azure_client.send_message(msg)
        print(f"  Azure SDK send succeeded for {row['message_id']}")

    def _cloud_accept(self, row: sqlite3.Row) -> bool:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO cloud_received
               (message_id, created_at_utc, received_at_utc, temperature_c, humidity_pct, local_analysis)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row["message_id"], row["created_at_utc"], utc_now(), row["temperature_c"],
             row["humidity_pct"], row["local_analysis"]))
        self.conn.commit()
        return cur.rowcount == 1

    def forward_pending(self, duplicate_test: bool = False) -> int:
        """Send queued rows; mark sent only after local acceptance or SDK send succeeds.

        Azure retries can still duplicate a message if the outcome of a send is ambiguous.
        The stable message_id is provided so a downstream Azure consumer can deduplicate.
        """
        sent_count = 0
        rows = self.conn.execute(
            "SELECT * FROM queue WHERE status='pending' ORDER BY created_at_utc, message_id"
        ).fetchall()
        for row in rows:
            if self.azure_mode:
                self._send_to_azure(row)
            else:
                self._cloud_accept(row)
                if duplicate_test and sent_count == 0:
                    self._cloud_accept(row)
            self.conn.execute(
                "UPDATE queue SET status='sent', sent_at_utc=? WHERE message_id=?",
                (utc_now(), row["message_id"]))
            self.conn.commit()
            sent_count += 1
        return sent_count

    def counts(self) -> dict[str, int]:
        generated = self.conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        pending = self.conn.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0]
        sent = self.conn.execute("SELECT COUNT(*) FROM queue WHERE status='sent'").fetchone()[0]
        received = self.conn.execute("SELECT COUNT(*) FROM cloud_received").fetchone()[0]
        return {"generated_unique": generated, "pending": pending, "sent": sent,
                "local_simulated_cloud_received_unique": received}

    def storage_bytes(self) -> int:
        total = self.db_path.stat().st_size if self.db_path.exists() else 0
        for suffix in ("-wal", "-shm"):
            p = Path(str(self.db_path) + suffix)
            if p.exists(): total += p.stat().st_size
        return total

    def export_results(self, metrics: dict[str, Any]) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        rows = self.conn.execute(
            """SELECT message_id, created_at_utc, temperature_c, humidity_pct,
                      local_analysis, status, sent_at_utc FROM queue ORDER BY message_id"""
        ).fetchall()
        with (RESULTS_DIR / "message_log.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["message_id", "created_at_utc", "temperature_c", "humidity_pct",
                             "local_analysis", "status", "sent_at_utc"])
            writer.writerows([tuple(r) for r in rows])

    def close(self) -> None:
        self.conn.close()

def run_demo(readings: int, outage_readings: int, delay_ms: int, db_path: Path,
             reset: bool, duplicate_test: bool, azure_mode: bool) -> dict[str, Any]:
    if readings < 1: raise ValueError("--readings must be at least 1")
    if not 0 <= outage_readings <= readings:
        raise ValueError("--outage-readings must be between 0 and --readings")
    if delay_ms < 0: raise ValueError("--delay-ms cannot be negative")
    if azure_mode and duplicate_test:
        raise ValueError("--duplicate-test is local-simulation-only; Azure dedup requires a downstream consumer")

    if reset:
        for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            if p.exists(): p.unlink()

    azure_client = None
    if azure_mode:
        try:
            from dotenv import load_dotenv
            from azure.iot.device import IoTHubDeviceClient
        except ImportError:
            raise RuntimeError("Install dependencies with: python -m pip install azure-iot-device python-dotenv")
        load_dotenv(ROOT / ".env")
        cs = os.getenv("IOTHUB_DEVICE_CONNECTION_STRING")
        if not cs: raise RuntimeError("IOTHUB_DEVICE_CONNECTION_STRING missing from .env")
        azure_client = IoTHubDeviceClient.create_from_connection_string(cs)
        print("Connecting to Azure IoT Hub...")
        azure_client.connect()
        print("Connected to Azure IoT Hub.")

    gateway = StoreForwardGateway(db_path, azure_client)
    before = gateway.counts()
    outage_start = time.perf_counter()
    recovery_seconds = 0.0
    recovery_forwarded = 0
    target = "AZURE IOT HUB" if azure_mode else "LOCAL SIMULATED RECEIVER"
    print("\n=== STORE-AND-FORWARD IoT GATEWAY DEMO ===")
    print(f"Planned readings: {readings}")
    print(f"Simulated outage: first {outage_readings} readings")
    print(f"Delivery target: {target}")
    print("\n[1/4] Generating sensor readings...")
    try:
        for i in range(1, readings + 1):
            temp = 24.0 + ((i * 7) % 17) * 0.8
            humidity = 42.0 + ((i * 11) % 51) * 0.9
            if i == outage_readings + 1 and outage_readings > 0:
                t0 = time.perf_counter()
                recovery_forwarded = gateway.forward_pending()
                recovery_seconds = time.perf_counter() - t0
            online = i > outage_readings
            gateway.create_reading(i, temp, humidity, online)
            if i == 1 or i == outage_readings or i == readings:
                print(f"  Reading {i:03d}: temp={temp:.1f} C, humidity={humidity:.1f}%, network={'ONLINE' if online else 'OFFLINE'}")
            if delay_ms: time.sleep(delay_ms / 1000.0)

        # Covers outage_readings == readings and any rows left pending.
        t1 = time.perf_counter()
        final_drain = gateway.forward_pending()
        final_drain_seconds = time.perf_counter() - t1
        if outage_readings == readings:
            recovery_forwarded, recovery_seconds = final_drain, final_drain_seconds

        print("\n[2/4] Recovery forwarding finished.")
        print(f"  Messages forwarded at recovery: {recovery_forwarded}")
        print("\n[3/4] Checking duplicate handling...")
        if azure_mode:
            duplicate_result = "Stable IDs attached; downstream Azure consumer must verify deduplication"
        elif duplicate_test:
            first = gateway.conn.execute("SELECT * FROM queue ORDER BY message_id LIMIT 1").fetchone()
            accepted = gateway._cloud_accept(first) if first else False
            duplicate_result = "ERROR: duplicate accepted" if accepted else "Duplicate rejected; counted once"
        else:
            duplicate_result = "Not explicitly injected"

        counts = gateway.counts()
        storage = gateway.storage_bytes()
        new_generated = counts["generated_unique"] - before["generated_unique"]
        if azure_mode:
            delivered = counts["sent"] - before["sent"]
            completeness_note = "Device-side SDK-send ratio, not independently verified cloud ingestion."
        else:
            delivered = counts["local_simulated_cloud_received_unique"] - before["local_simulated_cloud_received_unique"]
            completeness_note = "Measured against local simulated SQLite receiver."
        completeness = 100.0 * delivered / new_generated if new_generated else 100.0
        metrics = {
            "run_timestamp_utc": utc_now(), "simulation_mode": not azure_mode,
            "azure_iot_hub_connected": bool(azure_mode), "delivery_target": target,
            "planned_readings_this_run": readings, "unique_readings_added_this_run": new_generated,
            "outage_readings_configured": outage_readings,
            "recovery_upload_seconds": round(recovery_seconds, 6),
            "messages_marked_sent_this_run": delivered,
            "data_completeness_percent": round(completeness, 2),
            "data_completeness_interpretation": completeness_note,
            "duplicate_test": duplicate_test, "duplicate_test_result": duplicate_result,
            "database_and_sqlite_sidecar_bytes_after_run": storage, "counts_after_run": counts,
            "notes": [
                "Sensor readings are software-generated; no physical sensor is used.",
                "Outage is simulated in application logic, not by disconnecting the laptop network.",
                "Recovery time measures local queue forwarding duration.",
                "Azure send success is not the same as downstream processing confirmation.",
                "Stable message_id is included for downstream deduplication.",
                "Use --reset only when you intend to delete the selected local database.",
            ],
        }
        gateway.export_results(metrics)
        print("\n[4/4] RESULTS")
        print(f"  Unique readings added:         {new_generated}")
        print(f"  Messages marked sent:          {delivered}")
        print(f"  Data completeness indicator:   {completeness:.2f}%")
        print(f"  Recovery forwarding time:      {recovery_seconds:.6f} seconds")
        print(f"  Local DB + SQLite sidecars:    {storage} bytes")
        print(f"  Pending messages remaining:    {counts['pending']}")
        print(f"  Duplicate test:                {duplicate_result}")
        print(f"\nResults saved to: {RESULTS_DIR}")
        print(f"NOTE: {completeness_note}")
        return metrics
    finally:
        gateway.close()
        if azure_client is not None: azure_client.shutdown()

def main() -> None:
    parser = argparse.ArgumentParser(description="Store-and-forward IoT gateway simulation")
    parser.add_argument("--readings", type=int, default=100)
    parser.add_argument("--outage-readings", type=int, default=60)
    parser.add_argument("--delay-ms", type=int, default=5)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--duplicate-test", action="store_true")
    parser.add_argument("--azure", action="store_true", help="Send telemetry to real Azure IoT Hub")
    args = parser.parse_args()
    try:
        run_demo(args.readings, args.outage_readings, args.delay_ms, args.db,
                 args.reset, args.duplicate_test, args.azure)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
