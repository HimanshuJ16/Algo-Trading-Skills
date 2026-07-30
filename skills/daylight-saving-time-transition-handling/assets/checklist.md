# Pre-Flight Checklist

- [ ] Are all system timestamps and database tables configured in UTC?
- [ ] Are IANA timezone strings (`America/New_York`, `Europe/London`) used for session open/close parsing?
- [ ] Are 2-week March/October US-EU DST desynchronization windows detected?
- [ ] Are cron triggers and execution timers dynamically recalibrated to UTC?
