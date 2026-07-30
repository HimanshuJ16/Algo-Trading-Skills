# Workflows for Deployment Freeze Windows Around Market Events

1. **Event Calendar Ingestion**:
   - Register macro announcements and daily open/close freeze buffers.
2. **Deployment Request Audit**:
   - Evaluate target environment, timestamp, and emergency flags.
3. **Freeze Interception**:
   - Reject production deployments during active freeze buffers.
4. **Break-Glass Validation**:
   - Verify dual sign-off (Risk Officer + Head of Trading) for emergency hotfixes.
