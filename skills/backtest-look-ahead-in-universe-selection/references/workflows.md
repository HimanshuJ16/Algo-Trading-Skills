# Workflow for Universe Lookahead Auditing

1. Define the rules used for universe selection.
2. Initialize `UniverseLookaheadAuditor` with these rules.
3. Compare the snapshot date of the universe with the timestamp of the latest data point used for selection.
