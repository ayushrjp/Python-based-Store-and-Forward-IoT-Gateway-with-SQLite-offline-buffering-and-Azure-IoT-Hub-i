# Jury Presentation Outline (8 slides)

1. Title: Store-and-Forward IoT Gateway for Intermittent Networks
2. Problem Statement: Internet outages can interrupt sensor-to-cloud delivery; readings may be lost or duplicated.
3. Objectives: collect, analyze locally, buffer offline, forward after recovery, deduplicate, measure reliability.
4. Architecture: simulated sensors -> Python gateway -> SQLite queue -> simulated receiver; show offline/online path.
5. Implementation: Python, SQLite, UTC timestamps, unique message IDs, local threshold analysis.
6. Demonstration: generate readings; simulate outage; inspect pending queue; restore connection; forward backlog; inject duplicate.
7. Results: use actual values from results/metrics.json for completeness, recovery processing time, storage bytes, and duplicate test.
8. Limitations and Future Scope: local receiver instead of Azure, no physical sensors; future Azure IoT Hub/MQTT/TLS and real edge hardware.

Do not state that Azure IoT Hub was connected. Clearly identify this as a local simulation.
