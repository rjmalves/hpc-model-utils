class ModelOpsCommands:
    @staticmethod
    def set_model_error():
        text = _modelops_wrapper("CurrentExecution.SetModelError()")
        _send_command(text)

    @staticmethod
    def set_data_error():
        text = _modelops_wrapper("CurrentExecution.SetDataError()")
        _send_command(text)

    @staticmethod
    def set_success():
        text = _modelops_wrapper("CurrentExecution.SetSuccess()")
        _send_command(text)

    @staticmethod
    def cancel_execution():
        text = _modelops_wrapper("CurrentExecution.CancelExecution()")
        _send_command(text)

    @staticmethod
    def set_execution_cost(cost: float):
        text = _modelops_wrapper(f"CurrentExecution.SetExecutionCost({cost})")
        _send_command(text)

    @staticmethod
    def set_annotation(content: str):
        text = _modelops_wrapper(f'CurrentExecution.SetAnnotation("{content}")')
        _send_command(text)

    @staticmethod
    def set_execution_artifacts_path(path: str):
        text = _modelops_wrapper(
            f'CurrentExecution.SetExecutionArtifactsPath("{path}")'
        )
        _send_command(text)

    @staticmethod
    def check_cancellation_requested():
        text = _modelops_wrapper(
            "CurrentExecution.CheckIfExecutionStatusIsCancellationRequested()"
        )
        _send_command(text)

    @staticmethod
    def set_parameter_value(name: str, value: str):
        text = _modelops_wrapper(
            f'CurrentExecution.SetVariableValue("{name}", "{value}")'
        )
        _send_command(text)

    @staticmethod
    def set_metadata(key: str, value: str):
        text = _modelops_wrapper(
            f'CurrentExecution.SetMetadata("{key}", "{value}")'
        )
        _send_command(text)


def _modelops_wrapper(expr: str) -> str:
    return "${" + expr + "}"


def _send_command(command: str):
    print(command)
