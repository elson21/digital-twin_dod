# Architectural Decision Record - Decoupling the Edge Twin and Cloud Simulator

### 1. The Original Monolithic Architecture

My initial design co-located the Real-Time Digital Twin and the Shadow Twin (Simulator) on the same physical Edge device (e.g., an Industrial PC). The architecture consisted of two primary loops:

- **The Hot Path:** A high-frequency (100Hz) vectorized engine computing simple linear physics (Coulomb counting, Open Circuit Voltage) and providing sub-millisecond telemetry.
- **The Shadow Path:** A low-frequency (5-second) background process running a heavy PyBAMM Differential Algebraic Equation (DAE) solver to calculate deep chemical degradation (e.g., capacity fade and SEI layer growth).

### 2. The Hardware and Determinism Problem (Why The Pivot)

While logically sound, deploying both time domains onto constrained Edge silicon violates the principles of hardware sympathy and predictable latency.

- **Cache Thrashing & OS Preemption:** The PyBAMM CasADi solver allocates massive, complex object graphs. When it executes, it completely flushes the CPU’s L1 and L2 caches, evicting the Hot Path’s contiguous memory arrays. When the OS scheduler inevitably pre-empts the 100Hz physics loop to service the 100% CPU solver thread, the real-time latencies spike from microseconds to milliseconds.
- **Thermal Throttling:** Running heavy DAE solvers continuously on passively cooled Edge hardware within an electrical cabinet induces thermal throttling, further destabilizing the deterministic 100Hz loop.
- **Time Domain Conflict:** The Hot Path is strictly bound to the physical wall-clock (real-time operation). The Shadow Path requires complex mathematical convergence that cannot guarantee a strict execution time limit.

To protect the integrity of the Real-Time Twin, I am decoupling the system into two distinct products operating on separate hardware layers.

---

### 3. Product A: The Real-Time Edge Twin (Operations & Safety)

The Edge Twin is a pure, deterministic, allocation-free execution engine deployed directly on-site.

- **Core Function:** It reads live sensor data via fieldbuses (Modbus/CAN bus), computes fast linear mathematics in pure C-level NumPy, and enforces instantaneous boundary checks without ever pausing for Python Garbage Collection.
- **Telemetry Pipeline:** It operates entirely lock-free, taking zero-copy `O(1)` memory snapshots of the battery state and streaming them as high-throughput MQTT packets to the cloud.
- **Dependency on Cloud:** Because the Edge Twin no longer calculates its own complex chemical degradation, it relies on the Cloud Simulator to periodically update macroscopic parameters (like `capacity_ah` and internal resistance).

### 4. Product B: The Cloud Simulator & Shadow Twin (Planning & Economics)

The Shadow Twin is migrated to cloud infrastructure where CPU, memory, and thermal budgets are virtually unconstrained. It serves two distinct purposes:

- **The Shadow Twin (Automated Degradation):** It operates asynchronously, subscribing to the Edge Twin's historical MQTT telemetry. Periodically (e.g., daily or weekly), it feeds this historical load data into the heavy PyBAMM solvers to calculate accumulated capacity fade. It then publishes a control message back down to the Edge Twin to lock-free update its active parameters.
- **The Accelerated Simulator (Predictive Planning):** Detached entirely from the wall-clock, it acts as a sandbox. Engineers can feed it either live Edge data or synthetic user profiles (e.g., 8760-hour yearly load profiles) to run Monte Carlo simulations. Because it utilizes a `GlobalClockBuffer` rather than OS-level sleeping, it can simulate weeks or years of microgrid physics in mere seconds to validate installations, test grid import/export limits, and project ROI.