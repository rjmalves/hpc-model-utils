# Fixture Provenance

These are public PMO decks and mock files vendored under R103 so that
GitHub-hosted CI can run every test against real deck data without reaching
the private repositories they were produced in. They must never be mutated;
any change to deck content must land as a new, re-vendored file with an
updated row below, never as an in-place edit.

| Path | Source repo | Source path | Commit | sha256 |
| --- | --- | --- | --- | --- |
| `tests/fixtures/decks/deck_newave.zip` | `rjmalves/modelops` | `fixtures/decks/deck_newave.zip` | `406712b961be968b8ac9360fbdf2584d369e359e` | `26e4a58e87c8f2e88daa2716c159535959d3e4895c050880aed85e796daa4cfa` |
| `tests/fixtures/decks/deck_decomp.zip` | `rjmalves/modelops` | `fixtures/decks/deck_decomp.zip` | `406712b961be968b8ac9360fbdf2584d369e359e` | `b7d4b73e50cde90f8c4d06188f8a7869fb34529dabcd7e8cd50499a4aca915c0` |
| `tests/fixtures/flexibilizador/dadger.rv0` | `rjmalves/encadeador-pem` | `packages/flexibilizador/tests/fixtures/mocks/dadger.rv0` | `86e16461b913b1058005a5c86a31eee4826b89a6` | `b60d59465b991ece241a621006625ed1e0268198ee49846b6e39ebcc645c2182` |
| `tests/fixtures/flexibilizador/relato.rv0` | `rjmalves/encadeador-pem` | `packages/flexibilizador/tests/fixtures/mocks/relato.rv0` | `86e16461b913b1058005a5c86a31eee4826b89a6` | `b6898af40d4d4e77d740c170ed87465b4032edbae11bab6e01b2b74da84b1b3c` |
| `tests/fixtures/flexibilizador/inviab_unic.rv0` | `rjmalves/encadeador-pem` | `packages/flexibilizador/tests/fixtures/mocks/inviab_unic.rv0` | `86e16461b913b1058005a5c86a31eee4826b89a6` | `b89ebb694486cf4f30c7005f889693a991930ad6368e5eb66e5aec47ef350618` |
| `tests/fixtures/flexibilizador/deck_processado.zip` | `rjmalves/encadeador-pem` | `packages/flexibilizador/tests/fixtures/mocks/deck_processado.zip` | `86e16461b913b1058005a5c86a31eee4826b89a6` | `f5449204fcbb88185930e23a6e115a6f1158ff18a1e7f763440f4606186aec5b` |
