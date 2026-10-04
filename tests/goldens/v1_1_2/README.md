# v1.1.2 projection goldens

`projections.json` freezes the exact `metadata.modelops` and `status.modelops`
bytes that the real legacy `app/` code produces for eight characterization
sequences (NEWAVE and DECOMP, with and without a parent run, the NEWAVE
offline-ingestion path, and three NEWAVE "toolbox" `generate_execution_status`
calls). R93 (operator-confirmed) requires these two files to stay
byte-compatible with v1.1.2 in every detail: the bare status token, the flat
JSON object with v1's keys, value forms and insertion order, and its
`ensure_ascii` escaping.

**These goldens are frozen. Do not regenerate them from v2 output.**
Regenerating them from v2 would silently void R93 — v2's own correctness is
asserted *against* these bytes (ticket-013), so the golden and the thing it
checks can never be the same source.

They were captured by running the real legacy code at tag `v1.1.2`
(`git diff --stat v1.1.2 HEAD -- app/` is empty, so `app/` is unchanged since
that tag) with `capture.py`, against the vendored fixture decks in
`tests/fixtures/decks/`. See ADR-048 (which supersedes ADR-026 after the
test-strategist sign-off objection, R136) for the capture scope and rationale.

`capture.py` was deleted with `app/` in ticket-057; git history keeps it (`git log --diff-filter=D -1 -- tests/goldens/v1_1_2/capture.py`).
It is not a pytest module (pytest's `python_files = ["test_*.py"]` setting
does not match it), and it must not be renamed to match that pattern.

## Archive goldens

`archives.json` freezes three more pieces of real v1.1.2 behavior (R90, R16,
R17), captured by `capture_archives()` for NEWAVE and DECOMP: each extracted
fixture deck gets a fixed synthetic output set written over it, then runs the
real `output_compression_and_cleanup(2)` and `result_upload` unchanged, with
only `upload_file_to_bucket` patched to record calls instead of talking to
S3. For each model it records:

- `archives`: every produced `*.zip`'s name mapped to its sorted
  `ZipFile.namelist()`, including `deck_processado.zip`.
- `remaining`: the sorted top-level files left in the working directory after
  cleanup.
- `uploads`: the sorted S3 keys `result_upload` writes, relative to the
  `artifacts/hash01/` prefix.

Operator decision (2026-10-01): the capture's working directory emulates two
things a real cluster run always does, neither of which is available inside
this harness, so the pre-cleanup state matches prod. First, it copies the
fixture deck zip byte for byte to `eco_deck.zip` before extracting it,
mirroring `check_and_fetch_inputs`' `move(filename, RAW_DECK_FILE)` — on a
real run `eco_deck.zip` is always present when `output_compression_and_cleanup`
and `result_upload` run. Second, `_emulate_namecast` renames every extracted
deck file with uppercase characters to lowercase, standing in for the CEPEL
`ConverteNomesArquivos` (NEWAVE) / `convertenomesdecomp` (DECOMP) binaries
that `extract_sanitize_inputs` runs on a real cluster and which are not
available locally; a real run always lowercases deck filenames before
compression and cleanup see them. Both are external-binary/external-I/O
stand-ins, not patches of legacy decision logic, and `eco_deck.zip` itself is
never renamed.

See ADR-048 (R90, R16, R17, R55, R136) for the capture scope and rationale.

## Intended differences

| Golden | v1 behavior | v2 behavior | Reason |
| --- | --- | --- | --- |
| `newave_toolbox_existing_metadata` | Keeps the pre-existing `metadata.modelops` keys (`{**existing, **new}` merge-on-write in `_update_metadata`) | Writes only `job_id` and `status`; never reads a projection back | ADR-011 (v2 never reads a projection back) and ranqueamento reads only each scenario's `status.modelops`, never the scenario `metadata.modelops` the toolbox call writes (`ranqueamento-prospectivo-utils/app/adapter/repository/ranqueamento.py:240-283`) |
| `parent_starting_date` SetMetadata hook | v1 never emits a `SetMetadata` hook for `parent_starting_date` | v2 emits it from the same projection items | A hook-encoding difference (ADR-007 changes the hook encoding), not a file difference — `parent_starting_date` lands in `metadata.modelops` identically in both; hooks are not frozen by this ticket |
| `archives.decomp.uploads` | Never uploads `saidas/relgnl.<ext>` | Uploads `saidas/relgnl.<ext>` deliberately | C6 (R17) requires the raw DECOMP `relgnl` output; ADR-048 names this addition |
| `archives.newave.uploads` / `archives.decomp.uploads` | Uploads `stdout.modelops` and `stderr.modelops` | Uploads `saidas/logs/<phase>-<jobid>.out` instead | ADR-040 changes how execution logs are captured and stored |
| `archives.newave.archives.deck_processado.zip` | omits `bid.dat`, `elnino.dat`, `ensoaux.dat`, `itaipu.dat` | includes them | C5/R16; ticket-046 Decision B |
| `archives.newave.uploads.residual_inputs` | residual `saidas/{bid,elnino,ensoaux,itaipu}.dat` | not uploaded | they are deck inputs inside `entradas/deck_processado.zip`; no consumer reads them |
| `archives.newave.uploads.residual_rule` | residual names matched `.*\.dat` with `search` | names ending in `.dat` (root and the four v1 directories) | v1's regex also matched names such as `x.data` |
| `archives.decomp.uploads.dadger_echo` | uploads `entradas/<dadger>` (`entradas/dadger.rv0`) | not uploaded | no consumer reads it; R55's entradas set is `eco_deck.zip` and `deck_processado.zip`, both of which contain the dadger; ticket-052 Decision A |
| `archives.*.uploads.status_modelops` | uploads `saidas/status.modelops` whenever present (the `.*\.modelops` rule); the capture workspace had none | always uploads it, before `run.json` and the final `metadata.modelops` | parity with a real v1 run; ADR-042 fixes its position |
| `archives.*.uploads.run_json` | no `run.json` | uploads `saidas/run.json` | R55: additive run record |
| `archives.*.remaining` | deletes every archived file and moves `out/` to the root | deletes nothing at the root; archives are written under `.hpcmu/outputs/` | R70: compression is folded into finalize; no step mutates the deck directory after the run |
| `deck_newave.zip` `dsvagua.dat` encoding (not frozen) | left Latin-1 (`file -i` reads only a leading window) | converted to UTF-8 like every other Latin-1 member | ticket-021 whole-file classification; the accented text is a trailing name field, so no fixed-width column moves |

Ticket-008 and later tickets append their rows below this table.
