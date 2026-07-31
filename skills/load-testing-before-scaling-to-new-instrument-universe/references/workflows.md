# Workflows for Load Testing Before Scaling

1. **Peak Load Projection**:
   - Calculate projected msg/sec, RAM GB, network Mbps, and DB IOPS for target universe size.
2. **Capacity Utilization Audit**:
   - Compare projected resource utilization against available hardware capacity.
3. **Pre-Scaling Safety Threshold Audit**:
   - Ensure all resource utilization metrics are $\le 80.0\%$ under peak stress.
4. **Audit Report Generation**:
   - Output structured load test report.