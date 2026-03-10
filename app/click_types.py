"""Custom Click parameter types for semantic validation at argument-parse time.

These types are defense-in-depth: the explicit per-command validators in
app/validation.py remain the primary validation layer. These types provide
earlier feedback for the most common invalid inputs, using Click's standard
error formatting (exits with code 2 before the command body executes).

Usage in command definitions:
    @click.argument("model_name", type=ModelNameType())
    @click.argument("path", type=S3PathType())
    @click.argument("core_count", type=PositiveIntType())
"""

import click


class ModelNameType(click.ParamType):
    """Click parameter type that validates a registered model name.

    The ModelFactory is imported lazily inside ``convert()`` to avoid circular
    imports: model modules import from abstractmodel.py and register themselves
    at import time, so importing ModelFactory at module level here could cause
    this module to be resolved before registration completes.
    """

    name = "MODEL_NAME"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        # Click passes already-converted values through unchanged (e.g. when a
        # default is supplied as the correct Python type). Guard against that.
        if not isinstance(value, str):
            return self.fail(  # type: ignore[return-value]
                f"Expected a string, got {type(value).__name__}",
                param,
                ctx,
            )

        try:
            from app.adapter.repository.abstractmodel import ModelFactory

            factory = ModelFactory()
            valid_names = sorted(factory._models.keys())
        except ImportError:
            # If ModelFactory cannot be imported (e.g. during isolated unit
            # testing without the full package installed), pass validation
            # through to the in-function validators.
            return value

        if value not in factory._models:
            valid_str = (
                ", ".join(valid_names) if valid_names else "(none registered)"
            )
            self.fail(
                f'Model "{value}" not found. Valid: {valid_str}',
                param,
                ctx,
            )

        return value


class S3PathType(click.ParamType):
    """Click parameter type that validates an ``s3://bucket/key`` S3 path."""

    name = "S3_PATH"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        if not isinstance(value, str):
            return self.fail(  # type: ignore[return-value]
                f"Expected a string, got {type(value).__name__}",
                param,
                ctx,
            )

        prefix = "s3://"
        if not value.startswith(prefix):
            self.fail(
                f'"{value}" is not a valid S3 path. Expected format: s3://bucket/key',
                param,
                ctx,
            )

        remainder = value[len(prefix):]
        parts = remainder.split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        if not bucket or not key:
            self.fail(
                f'"{value}" is not a valid S3 path. Expected format: s3://bucket/key',
                param,
                ctx,
            )

        return value


class PositiveIntType(click.ParamType):
    """Click parameter type that converts to ``int`` and validates ``> 0``.

    Accepts both ``str`` (from the command line) and ``int`` (when a default
    value is passed through by Click's machinery). ``None`` is passed through
    unchanged so that Click can handle optional parameters correctly.
    """

    name = "POSITIVE_INT"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> int:
        if value is None:
            return value  # type: ignore[return-value]

        if isinstance(value, int) and not isinstance(value, bool):
            int_value = value
        elif isinstance(value, str):
            try:
                int_value = int(value)
            except ValueError:
                self.fail(
                    f'"{value}" is not a positive integer',
                    param,
                    ctx,
                )
        else:
            self.fail(
                f'"{value}" is not a positive integer',
                param,
                ctx,
            )

        if int_value <= 0:
            self.fail(
                f'"{value}" is not a positive integer',
                param,
                ctx,
            )

        return int_value
