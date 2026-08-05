# Workflows for Shamir Secret Sharing for Key Backup

1. **Polynomial Generation**:
   - Create degree $K-1$ polynomial with secret constant term over prime field $M_{127}$.
2. **Share Distribution**:
   - Generate $N$ point evaluations $(x_i, y_i)$ and distribute to key custodians.
3. **Threshold Collection**:
   - Gather $K$ or more distinct shares.
4. **Lagrange Reconstruction**:
   - Apply modular Lagrange interpolation at $x=0$ to recover original key.