# Pre-Flight / Sign-off Checklist — dynamic-position-sizing-based-on-realized-volatility

- [ ] EWMA / Rolling StdDev annual volatility estimation active.
- [ ] Volatility targeting scalar $\frac{\sigma_{\text{target}}}{\sigma_{\text{realized}}}$ computed.
- [ ] Min/Max scalar bounding limits ($0.20\times – 2.0\times$) enforced.
- [ ] Volatility floor protection active.
- [ ] Automated Testing: Run `python scripts/test_realized_vol_sizer.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
