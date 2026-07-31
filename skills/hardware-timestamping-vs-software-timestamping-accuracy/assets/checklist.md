# Pre-Flight Checklist

- [ ] Is hardware timestamping enabled on PCIe SmartNIC (Solarflare/Exablaze)?
- [ ] Is PTP IEEE 1588 grandmaster clock sync active?
- [ ] Are 3-layer timestamps (Hardware, Kernel, Application) captured for analysis?
- [ ] Is MiFID II RTS 25 compliance ($\le 100\mu\text{s}$ UTC drift) verified?
