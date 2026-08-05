# Standards for Saxo Bank OpenAPI Integration

| Parameter | Mandatory Value / Standard |
|---|---|
| Instrument Identifier | Must use numeric `Uic` resolved via instrument search API. |
| AssetType Values | `FxSpot`, `Stock`, `ContractFutures`, `OptionRoot`, `CfdOnStock`. |
| Bearer Token Auth | Headers MUST include `Authorization: Bearer {token}`. |
