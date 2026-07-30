# Pre-Flight Checklist

- [ ] Are Primary and Secondary gateway endpoints configured in Active-Standby mode?
- [ ] Is heartbeat timeout ($> 3000\text{ms}$) set as an automated failover trigger?
- [ ] Is `PossDupFlag = Y` added to retransmitted in-flight orders during failover?
- [ ] Is gateway failover recovery time verified to meet $< 100\text{ms}$ RTO SLAs?
