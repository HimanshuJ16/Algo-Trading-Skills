# Pre-Flight Checklist

- [ ] Are Data Quality scores calculated across Completeness, Timeliness, Accuracy, Uniqueness, and Liveness?
- [ ] Are dead feeds ($\text{TPS} = 0$) intercepted within 5 seconds?
- [ ] Is secondary feed failover triggered when DQ Score drops below 70.0?
- [ ] Are DQ alert notifications routed to Grafana/PagerDuty?
