# deploy/modelops

Versioned ModelOps Task and Workflow definitions (JSON plus one script file
per Task) and the operator tooling that applies them. This repository is
public: every environment-specific value is a placeholder in the committed
files, and the real values live only in an uncommitted environment file
(ADR-031, ADR-049).

## Layout

| Path | Content |
| --- | --- |
| `workflows/<slug>.json` | One Workflow document: `newave-pem`, `decomp-pem`, `upload-newave` |
| `tasks/<slug>.json` | One Task document without its `script` |
| `tasks/<slug>.sh` | The Task's script, verbatim, LF line endings |
| `env/prd.example.json` | The environment file shape, dummy values only |
| `scripts/` | Shell helpers, referenced from Task scripts by `@@script:FILE@@` |
| `apply.py`, `modelops_api.py` | The operator CLI and its API client |

Audit fields (`_id`, `createdBy`, `createdDate`, `lastChangeBy`,
`lastChangeDate`, `scheduleStatus`) are never committed. The apply script sets
`createdBy` and `lastChangeBy` when it writes.

## Placeholder grammar

A placeholder is a whole JSON string value (or a token inside a `.sh` file):

| Token | NAME / SLUG / FILE must fullmatch | Replaced by |
| --- | --- | --- |
| `@@env:NAME@@` | `[A-Za-z][A-Za-z0-9]*` | `env.NAME` of the environment file |
| `@@task:SLUG@@` | `[a-z0-9][a-z0-9-]*` | the live `_id` of Task `SLUG` (`tasks.SLUG`) |
| `@@script:FILE@@` | `[a-z0-9][a-z0-9-]*\.sh`, a file in `scripts/` | the file's content; allowed only in `tasks/*.sh` |

ModelOps' own `{{...}}` and `${...}` stay untouched. `workflowTaskId` values
are client-chosen and internal to a document, so they stay literal.

## Environment file

The apply script takes the real file through its required `--env-file` option.
`env/prd.example.json` has the same shape with dummy values only:

```json
{
  "env": {"<NAME>": "<value>"},
  "workflows": {"<workflow-slug>": "<live workflow _id>"},
  "tasks": {"<task-slug>": "<live task _id>"},
  "protectedTaskIds": ["<live task _id>"]
}
```

`protectedTaskIds` lists every Task `_id` that exists live, including Tasks of
Workflows this tree does not manage: the repository only ever writes Task
documents it created.

Real files never enter this tree. `.gitignore` excludes
`deploy/modelops/env/*.json` except `*.example.json`, as defense in depth
should a real file be copied here. The structural lint
`tests/deploy/test_definitions_structure.py` reads no real value and gates
publication (ADR-049), together with the pre-push hook in `.githooks` and the
CI secret scan.

## The `v1-` baseline

The Tasks prefixed `v1-` and the three Workflows are the export of what runs
today. They are shared and protected: the apply script never writes them. They
are the zero-diff reference for the dry run against live, the rollback target,
and the authoring source of the v2 Tasks, which take the plain names. The `v1-`
Tasks are deleted when the rollback window closes (ticket-067).

## Operator entry point

```sh
uv run python -m deploy.modelops.apply --help
```
