# Security Policy

## Scope

This repository contains documentation, workflow guidance, and reference helper
scripts for building algorithmic trading systems. It does not itself run against
live broker accounts or hold credentials. Security concerns fall into two
categories:

1. **A skill's guidance would lead to an insecure implementation** (e.g., a
   pattern that encourages storing credentials insecurely, or a risk-control
   design with a structural gap).
2. **A reference script in `scripts/` has a vulnerability** (e.g., unsafe
   deserialization, injection risk in a helper).

## Reporting a Vulnerability

Please do **not** open a public issue for security concerns that could plausibly
be used to compromise a live trading deployment based on this repo's guidance.

Instead, report privately via GitHub's private vulnerability reporting feature
on this repository (Security tab → "Report a vulnerability"), or open a
minimal-detail issue asking a maintainer to reach out for a private channel.

We aim to acknowledge reports within 48 hours.

## Not Covered by This Policy

- Vulnerabilities or outages in third-party broker APIs themselves — report
  those to the broker directly.
- Financial losses resulting from strategy performance, market conditions, or
  configuration choices made by an implementer. This repo provides engineering
  patterns, not investment advice or a guarantee of correctness for any
  specific deployment.

## Responsible Use

Several skills describe risk-control mechanisms (kill switches, drawdown
limits) whose entire purpose is protecting real capital. If you find a gap in
one of these designs, please treat it with the same urgency as a security
vulnerability — a broken risk control is a financial-safety issue.
 