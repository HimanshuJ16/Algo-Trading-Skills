# Standards — smart-contract-approval-scope-minimization

There is no regulator or standards body that mandates allowance sizing. What follows
separates what the Ethereum standards actually say from what this library enforces as
its own policy.

## What the standards say (verified against the primary sources)

| Fact | Source | Normative force |
|---|---|---|
| `approve(_spender, _value)` "overwrites the current allowance with `_value`" — it does not add to it. | [EIP-20](https://eips.ethereum.org/EIPS/eip-20), *Methods → approve* | Specified behavior |
| Because of the front-running attack vector, "clients SHOULD make sure to create user interfaces in such a way that they set the allowance first to `0` before setting it to another value for the same spender". | [EIP-20](https://eips.ethereum.org/EIPS/eip-20), note under *approve* | **SHOULD**, client-side only |
| "THOUGH The contract itself shouldn't enforce it, to allow backwards compatibility with contracts deployed before" — the zero-reset is explicitly *not* required of token contracts. | [EIP-20](https://eips.ethereum.org/EIPS/eip-20), same note | Explicit non-mandate |
| `permit(address owner, address spender, uint256 value, uint256 deadline, uint8 v, bytes32 r, bytes32 s)`; succeeds only while "the current blocktime is less than or equal to `deadline`". | [EIP-2612](https://eips.ethereum.org/EIPS/eip-2612) | Specified signature; `deadline` is **uint256 seconds** |
| Compliant tokens must expose `nonces(address owner)` and `DOMAIN_SEPARATOR`; the signed struct is `Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)`; the domain separator "should be unique to the contract and chain to prevent replay attacks". | [EIP-2612](https://eips.ethereum.org/EIPS/eip-2612), [EIP-712](https://eips.ethereum.org/EIPS/eip-712) | MUST (for the token); the caller must supply nonce and domain |
| EIP-2612 prescribes **no** maximum or recommended deadline duration. | [EIP-2612](https://eips.ethereum.org/EIPS/eip-2612) | Absence of a rule — any "600s standard" claim is false |
| Permit2 stores allowances as `uint160` and treats `type(uint160).max` as unlimited (not decremented on transfer); its approval carries a `uint48 expiration` — `approve(address token, address spender, uint160 amount, uint48 expiration)`. | [Uniswap/permit2 `AllowanceTransfer.sol`](https://github.com/Uniswap/permit2/blob/main/src/AllowanceTransfer.sol), [Uniswap allowance-transfer docs](https://developers.uniswap.org/docs/protocols/permit2/concepts/allowance-transfer) | Implementation behavior |

## Token-implementation facts that change the plan

| Token | Behavior | Consequence |
|---|---|---|
| USDT (`0xdAC17F958D2ee523a2206206994597C13D831ec7`) | `approve` reverts when the existing allowance and the new value are both non-zero ("To change the approve amount you first have to reduce the addresses allowance to zero"). Discussed at the [OpenZeppelin forum](https://forum.openzeppelin.com/t/can-not-call-the-function-approve-of-the-usdt-contract/2130) and repeatedly reported as an integration bug (e.g. [MetaMask #18610](https://github.com/MetaMask/metamask-extension/issues/18610)). | The zero-reset is mandatory here, not advisory: skipping it fails the trade. |
| USDT and other pre-finalization tokens | `approve`/`transfer` return no value despite an `IERC20`-shaped interface. | Decoding a `bool` return reverts; the submission layer needs a safe wrapper. |

## House policy (this library's defaults — not standards)

Calibrate these per deployment and record the rationale.

| Parameter | Default | What it does |
|---|---|---|
| `unlimited_allowance_threshold` | `2**200` | Absolute allowance at or above which an approval is treated as effectively unlimited, in addition to the exact `uint256.max` / `uint160.max` sentinels. Token decimals are not modelled, so this is a coarse guard, not a calibrated limit. |
| `max_permit_validity_seconds` | `600.0` | Longest permit deadline the engine will plan, so a leaked signature expires quickly. EIP-2612 imposes no such cap. |
| `DEFAULT_PERMIT_VALIDITY_SECONDS` | `300.0` | Deadline used when the caller does not specify one. |
| `stale_after_seconds` | unset | Staleness window for the revocation audit. Opt-in: with no window, only unlimited allowances are planned for revocation. |
