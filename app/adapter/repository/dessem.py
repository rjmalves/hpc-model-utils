import json
from datetime import datetime
from os import curdir, getenv, listdir
from os.path import isdir, isfile, join
from pathlib import Path
from shutil import move
from typing import Any

import pandas as pd  # type: ignore
from cfinterface.components.defaultblock import DefaultBlock
from idessem.dessem import DesLogRelato, DessemArq, Dessopc, Entdados, Operut

from app.adapter.repository.abstractmodel import (
    AbstractModel,
    ModelFactory,
)
from app.adapter.repository.decomp import DECOMP
from app.models.runstatus import RunStatus
from app.utils.commands import ModelOpsCommands
from app.utils.constants import (
    AWS_ACCESS_KEY_ID_ENV,
    AWS_SECRET_ACCESS_KEY_ENV,
    INPUTS_ECHO_PREFIX,
    METADATA_FILE,
    METADATA_JOB_ID,
    METADATA_MODEL_NAME,
    METADATA_MODEL_VERSION,
    METADATA_PARENT_PATH,
    METADATA_PARENT_STARTING_DATE,
    METADATA_STATUS,
    METADATA_STUDY_NAME,
    METADATA_STUDY_STARTING_DATE,
    MODEL_EXECUTABLE_DIRECTORY,
    MODEL_EXECUTABLE_PERMISSIONS,
    OUTPUTS_PREFIX,
    PROCESSED_DECK_FILE,
    RAW_DECK_FILE,
    STATUS_DIAGNOSIS_FILE,
    SYNTHESIS_DIR,
)
from app.utils.fs import (
    change_file_permission,
    clean_files,
    compress_files_to_zip,
    compress_files_to_zip_parallel,
    extract_zip_content,
    find_file_case_insensitive,
    list_files_by_regexes,
    moves_content_to_rootdir,
)
from app.utils.s3 import (
    check_and_delete_bucket_item,
    check_and_download_bucket_items,
    check_and_get_bucket_item,
    path_to_bucket_and_key,
    upload_file_to_bucket,
)
from app.utils.terminal import cast_encoding_to_utf8, run_in_terminal
from app.utils.timing import time_and_log


class DESSEM(AbstractModel):
    MODEL_NAME = "dessem"
    MODEL_ENTRY_FILE = "dessem.arq"
    NWLISTCF_ENTRY_FILE = "arquivos.dat"
    LIBS_ENTRY_FILE = "indices.csv"
    LICENSE_FILENAMES = ["dessem.lic", "ddsDESSEM.cep", "dessem.cep"]
    CUT_FILE = "cortes.zip"
    RESOURCES_FILE = "recursos.zip"
    SIMULATION_FILE = "simulacao.zip"
    DESSEM_PATH = "hpc-model-utils/assets/jobs/dessem.sh"
    DESSEM_TIMEOUT = 172800  # 48h
    DATA_ERROR_PATTERN = "ERRO(S) NA ENTRADA DE DADOS"

    DECK_DATA_CACHING: dict[str, Any] = {}

    CUT_HEADER_FILE_PATTERN = r"^mapcut.*$"
    CUT_FILE_PATTERN = r"^cortdeco.*$"

    @property
    def dessem_arq(self) -> DessemArq:
        name = "dessemarq"
        if name not in self.DECK_DATA_CACHING:
            self._log.info(f"Reading file: {self.MODEL_ENTRY_FILE}")
            self.DECK_DATA_CACHING[name] = DessemArq.read(self.MODEL_ENTRY_FILE)
        return self.DECK_DATA_CACHING[name]

    @property
    def entdados(self) -> Entdados:
        name = "entdados"
        if name not in self.DECK_DATA_CACHING:
            caso_register = self.dessem_arq.caso
            if not caso_register:
                msg = f"No content found in {self.MODEL_ENTRY_FILE}"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            extension = (
                caso_register.valor
                if caso_register.valor is not None
                else "DAT"
            )
            filename = f"ENTDADOS.{extension}"
            filename = find_file_case_insensitive(".", filename)
            self._log.info(f"Reading file: {filename}")
            Entdados.ENCODING = "iso-8859-1"
            self.DECK_DATA_CACHING[name] = Entdados.read(filename)
        return self.DECK_DATA_CACHING[name]

    @property
    def dessopc(self) -> Dessopc:
        name = "dessopc"
        if name not in self.DECK_DATA_CACHING:
            caso_register = self.dessem_arq.caso
            if not caso_register:
                msg = f"No content found in {self.MODEL_ENTRY_FILE}"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            extension = (
                caso_register.valor
                if caso_register.valor is not None
                else "DAT"
            )
            dessopc_register = self.dessem_arq.dessopc
            if not dessopc_register:
                msg = "No content found in DESSOPC"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            dessopc_filename = dessopc_register.valor or "DESSOPC"
            filename = f"{dessopc_filename}.{extension}"
            filename = find_file_case_insensitive(".", filename)
            self._log.info(f"Reading file: {filename}")
            Dessopc.ENCODING = "iso-8859-1"
            self.DECK_DATA_CACHING[name] = Dessopc.read(filename)
        return self.DECK_DATA_CACHING[name]

    @property
    def operut(self) -> Operut:
        name = "operut"
        if name not in self.DECK_DATA_CACHING:
            caso_register = self.dessem_arq.caso
            if not caso_register:
                msg = f"No content found in {self.MODEL_ENTRY_FILE}"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            extension = (
                caso_register.valor
                if caso_register.valor is not None
                else "DAT"
            )
            operut_register = self.dessem_arq.operut
            if not operut_register:
                msg = "No content found in OPERUT"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            oprut_filename = operut_register.valor or "OPERUT"
            filename = f"{oprut_filename}.{extension}"
            filename = find_file_case_insensitive(".", filename)
            self._log.info(f"Reading file: {filename}")
            Operut.ENCODING = "iso-8859-1"
            self.DECK_DATA_CACHING[name] = Operut.read(filename)
        return self.DECK_DATA_CACHING[name]

    @property
    def des_log_relato(self) -> DesLogRelato:
        name = "des_log_relato"
        if name not in self.DECK_DATA_CACHING:
            caso_register = self.dessem_arq.caso
            if not caso_register:
                msg = f"No content found in {self.MODEL_ENTRY_FILE}"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            extension = (
                caso_register.valor
                if caso_register.valor is not None
                else "DAT"
            )
            filename = f"DES_LOG_RELATO.{extension}"
            filename = find_file_case_insensitive(".", filename)
            self._log.info(f"Reading file: {filename}")
            self.DECK_DATA_CACHING[name] = DesLogRelato.read(filename)
        return self.DECK_DATA_CACHING[name]

    def _update_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if isfile(METADATA_FILE):
            with open(METADATA_FILE, "r") as f:
                metadata = {**json.load(f), **metadata}
        with open(METADATA_FILE, "w") as f:
            json.dump(metadata, f)
        return metadata

    def check_and_fetch_executables(self, path: str):
        self._log.info(f"Fetching executables in {path}...")
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        version = key.split("/")[-1]
        if version == "":
            version = key.split("/")[-2]

        downloaded_filepaths = check_and_download_bucket_items(
            bucket, MODEL_EXECUTABLE_DIRECTORY, key, self._log
        )
        for filepath in downloaded_filepaths:
            change_file_permission(filepath, MODEL_EXECUTABLE_PERMISSIONS)
            self._log.info(
                f"Changed {filepath} permissions to"
                + f" {MODEL_EXECUTABLE_PERMISSIONS:o}"
            )

        metadata = {
            METADATA_MODEL_NAME: self.MODEL_NAME.upper(),
            METADATA_MODEL_VERSION: version,
        }
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)
        self._log.info("Executables successfully fetched and ready!")

    def check_and_fetch_inputs(
        self,
        path: str,
        parent_path: str,
        delete: bool = True,
    ):
        self._log.info(f"Fetching input data in {path}...")

        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        filename = key.split("/")[-1]

        check_and_download_bucket_items(
            bucket, str(Path(curdir).resolve()), key, self._log
        )

        if delete:
            self._log.info(f"Removing inputs from {path}...")
            check_and_delete_bucket_item(bucket, filename, key, self._log)

        self._log.info(f"Renaming input file to {RAW_DECK_FILE}")
        move(filename, RAW_DECK_FILE)

        if len(parent_path) > 0:
            # Downloads parent metadata and check if is a DECOMP execution
            # with SUCCESS status
            parent_path_data = path_to_bucket_and_key(parent_path)
            parent_bucket = parent_path_data["bucket"]
            parent_key = parent_path_data["key"]
            self._log.info(f"Fetching parent data from {parent_path}")

            remote_filepath = join(parent_key, OUTPUTS_PREFIX, METADATA_FILE)
            parent_metadata = json.loads(
                check_and_get_bucket_item(
                    parent_bucket, remote_filepath, self._log
                )
            )
            if any(
                [
                    k not in parent_metadata
                    for k in [
                        METADATA_MODEL_NAME,
                        METADATA_STATUS,
                        METADATA_STUDY_STARTING_DATE,
                    ]
                ]
            ):
                raise ValueError(
                    f"Parent metadata is incomplete [{parent_metadata}]"
                )
            if (
                parent_metadata[METADATA_MODEL_NAME]
                != DECOMP.MODEL_NAME.upper()
            ):
                raise ValueError(
                    f"Parent model is not {DECOMP.MODEL_NAME.upper()}"
                )
            success_status = RunStatus.SUCCESS.value
            if parent_metadata[METADATA_STATUS] != success_status:
                raise ValueError(
                    f"Parent execution status was not {success_status}"
                )

            # Downloads parent cut file
            remote_filepath = join(parent_key, OUTPUTS_PREFIX, self.CUT_FILE)
            self._log.info(f"Fetching parent file from {remote_filepath}")
            check_and_download_bucket_items(
                bucket,
                str(Path(curdir).resolve()),
                remote_filepath,
                self._log,
            )
            metadata = {
                METADATA_PARENT_PATH: parent_path,
                METADATA_PARENT_STARTING_DATE: parent_metadata[
                    METADATA_STUDY_STARTING_DATE
                ],
            }
            self._update_metadata(metadata)
        else:
            self._log.info("No parent id was given!")

        self._log.info("Inputs successfully fetched!")

    def _get_cut_filepatterns_for_extraction(self) -> list[str]:
        return [self.CUT_FILE_PATTERN, self.CUT_HEADER_FILE_PATTERN]

    def extract_sanitize_inputs(self):
        extracted_files = (
            extract_zip_content(RAW_DECK_FILE) if isfile(RAW_DECK_FILE) else []
        )
        self._log.info(f"Extracted input files: {extracted_files}")

        self._log.info("Forcing encoding to utf-8")
        for f in listdir():
            if f in self.LICENSE_FILENAMES:
                self._log.info(f"Ignoring license file: {f}")
                continue
            cast_encoding_to_utf8(f)

        # Unzips the parent files
        for parent_file, patterns_to_extract in {
            self.CUT_FILE: self._get_cut_filepatterns_for_extraction(),
        }.items():
            if isfile(parent_file):
                extracted_files = extract_zip_content(
                    parent_file, patterns=patterns_to_extract
                )
                self._log.info(f"Extracted parent files: {extracted_files}")

        # Copies license file
        for license_filename in self.LICENSE_FILENAMES:
            license_path = join(MODEL_EXECUTABLE_DIRECTORY, license_filename)
            if isfile(license_path):
                move(
                    license_path,
                    join(curdir, license_filename),
                )
                self._log.info(f"Moved {license_filename} to executables dir")

        titulo = self.dessem_arq.titulo
        if not titulo:
            raise ValueError("TITULO register not found in <dessem.arq>")
        study_name = titulo.valor

        # TODO - esperando suporte ao dadvaz.dat
        # dt = self.dadger.dt
        # if not dt:
        #     raise ValueError("DT register not found in <dadger>")
        # year = dt.ano
        # month = dt.mes
        # day = dt.dia
        # if year is None:
        #     raise ValueError("DT register with incomplete info (year)")
        # if month is None:
        #     raise ValueError("DT register with incomplete info (month)")
        # if day is None:
        #     raise ValueError("DT register with incomplete info (day)")
        # study_starting_date = datetime(year, month, day, tzinfo=UTC)
        study_starting_date = datetime.today()
        metadata = {
            METADATA_STUDY_STARTING_DATE: study_starting_date.isoformat(),
            METADATA_STUDY_NAME: study_name if study_name else "",
        }
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)

    def preprocess(self, execution_name: str):
        dessem_arq = self.dessem_arq
        dessem_arq.titulo.valor = execution_name
        mapfcf = dessem_arq.mapfcf
        if mapfcf:
            mapcut_files = [f for f in listdir() if "mapcut" in f]
            if len(mapcut_files) == 0:
                self._log.info("Could not find mapcut file")
                raise RuntimeError()
            mapcut_file = mapcut_files[0]
            self._log.info(f"Overwriting MAPFCF register with {mapcut_file}")
        cortfcf = dessem_arq.cortfcf
        if cortfcf:
            cortdeco_files = [f for f in listdir() if "cortdeco" in f]
            if len(cortdeco_files) == 0:
                self._log.info("Could not find cortdeco file")
                raise RuntimeError()
            cortdeco_file = cortdeco_files[0]
            self._log.info(f"Overwriting CORTFCF register with {cortdeco_file}")
            cortfcf.valor = cortdeco_file
        dessem_arq.write(self.MODEL_ENTRY_FILE)

    def _evaluate_data_error(self, des_log_relato: DesLogRelato) -> bool:
        return any(
            [
                self.DATA_ERROR_PATTERN in b.data
                for b in des_log_relato.data.of_type(DefaultBlock)
            ]
        )

    # TODO - aguardando suporte do arquivo LOG_INVIAB na idessem (até a idessem 0.0.23 não possui)
    # def _evaluate_feasibility(self, log_inviab: LogInviab) -> bool:
    #     if not isinstance(log_inviab, LogInviab):
    #         return True
    #     inviabs = log_inviab.tabela
    #     if inviabs is None:
    #         return False
    #     elif inviabs.empty:
    #         return False
    #     else:
    #         return True

    def _evaluate_des_log_relato_outputs(
        self, des_log_relato: DesLogRelato
    ) -> bool:
        return des_log_relato.tempo_processamento is None

    def _edit_core_count_dessopc(self, core_count: int):
        dessopc = self.dessopc
        if dessopc.uctpar is not None:
            dessopc.uctpar = core_count
            dessopc_file = self.dessem_arq.dessopc
            if dessopc_file is not None:
                filename = self.dessem_arq.dessopc.valor
                if filename is not None:
                    dessopc.write(filename)
            else:
                self._log.info("No DESSOPC filename found")
        else:
            self._log.info("No UCTPAR found in DESSOPC")

    def _edit_core_count_operut(self, core_count: int):
        operut = self.operut
        if operut.uctpar is not None:
            operut.uctpar = core_count
            operut_file = self.dessem_arq.operut
            if operut_file is not None:
                filename = self.dessem_arq.operut.valor
                if filename is not None:
                    operut.write(filename)
        else:
            self._log.info("No UCTPAR found in OPERUT")

    def _edit_core_count(self, core_count: int):
        dessem_arq = self.dessem_arq
        if dessem_arq.dessopc is not None:
            self._edit_core_count_dessopc(core_count)
        else:
            self._edit_core_count_operut(core_count)

    def run(
        self,
        queue: str,
        core_count: int,
        mpich_path: str,
        slurm_path: str,
        max_cores_per_node: int | None = None,
        skip_model: bool = False,
    ):
        self._edit_core_count(core_count)
        self._log.info(f"Script file: {self.DESSEM_PATH}")
        run_in_terminal(
            [self.DESSEM_PATH], timeout=self.DESSEM_TIMEOUT, log_output=True
        )

    def generate_execution_status(self, job_id: str) -> str:
        self._log.info("Reading 'DES_LOG_RELATO' file for generating status...")
        des_log_relato = self.des_log_relato
        # TODO - aguardando suporte do arquivo LOG_INVIAB na idessem
        # (até a idessem 0.0.23 não possui)
        # self._log.info("Reading 'LOG_INVIAB' file for generating status...")
        # log_inviab = self.log_inviab

        status = RunStatus.SUCCESS

        if self._evaluate_data_error(des_log_relato):
            status = RunStatus.DATA_ERROR
        # elif self._evaluate_feasibility(log_inviab):
        #     status = RunStatus.INFEASIBLE
        elif self._evaluate_des_log_relato_outputs(des_log_relato):
            status = RunStatus.DATA_ERROR

        status_value = status.value
        with open(STATUS_DIAGNOSIS_FILE, "w") as f:
            f.write(status_value)
        metadata = {METADATA_JOB_ID: job_id, METADATA_STATUS: status_value}
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)
        return status_value

    def postprocess(self):
        pass

    def _list_input_files(self) -> list[str]:
        dessem_arq = self.dessem_arq
        common_input_files: list[str | None] = [self.MODEL_ENTRY_FILE]
        common_input_registers = [
            dessem_arq.vazoes,
            dessem_arq.dadger,
            dessem_arq.mapfcf,
            dessem_arq.cortfcf,
            dessem_arq.cadusih,
            dessem_arq.operuh,
            dessem_arq.deflant,
            dessem_arq.cadterm,
            dessem_arq.operut,
            dessem_arq.indelet,
            dessem_arq.ilstri,
            dessem_arq.cotasr11,
            dessem_arq.areacont,
            dessem_arq.respot,
            dessem_arq.mlt,
            dessem_arq.curvtviag,
            dessem_arq.ptoper,
            dessem_arq.infofcf,
            dessem_arq.ree,
            dessem_arq.eolica,
            dessem_arq.rampas,
            dessem_arq.rstlpp,
            dessem_arq.restseg,
            dessem_arq.respotele,
            dessem_arq.uch,
            dessem_arq.dessopc,
        ]
        for reg in common_input_registers:
            if reg is not None:
                common_input_files.append(reg.valor)
        index_file = (
            [dessem_arq.ilibs.valor] if dessem_arq.ilibs is not None else []
        )
        libs_input_files = (
            pd.read_csv(index_file[0], delimiter=";", comment="&", header=None)[
                2
            ]
            .unique()
            .tolist()
            if len(index_file) == 1
            else []
        )
        network_files = [
            a for a in listdir(curdir) if all(["pat" in a, ".afp" in a])
        ] + [a for a in listdir(curdir) if ".pwf" in a]
        input_files = (
            [a for a in common_input_files if len(a) > 0]
            + index_file
            + [a.strip() for a in libs_input_files]
            + network_files
        )
        input_files = [a for a in input_files if a is not None]
        self._log.info(f"Files considered as input: {input_files}")
        return input_files

    def _list_report_files(self, input_files: list[str]) -> list[str]:
        report_output_files = []
        report_output_file_regex = [
            r"AVL_.*$",
            r"DES_.*$",
            r"LOG_.*$",
            r"PDO_AVAL.*$",
            r"PDO_ECO.*$",
            r"PTOPER.*\.PWF$",
        ]
        report_output_files += list_files_by_regexes(
            input_files, report_output_file_regex
        )
        self._log.info(f"Files considered as report: {report_output_files}")
        return report_output_files

    def _list_operation_files(self, input_files: list[str]) -> list[str]:
        operation_output_file_regex = [
            r"^PDO_OPER.*$",
            r"^PDO_AVAL_.*$",
            r"^PDO_CMO.*$",
            r"^PDO_CONTR.*$",
            r"^PDO_DESV.*$",
            r"^PDO_ELEV.*$",
            r"^PDO_EOLICA.*$",
            r"^PDO_FLUXLIN.*$",
            r"^PDO_GERBARR.*$",
            r"^PDO_HIDR.*$",
            r"^PDO_INTER.*$",
            r"^PDO_RESERVA.*$",
            r"^PDO_REST.*$",
            r"^PDO_SIST.*$",
            r"^PDO_SOMFLUX.*$",
            r"^PDO_STATREDE_ITER.*$",
            r"^PDO_SUMAOPER.*$",
            r"^PDO_TERM.*$",
            r"^PDO_VAGUA.*$",
            r"^PDO_VERT.*$",
        ]
        operation_output_files = list_files_by_regexes(
            input_files, operation_output_file_regex
        )
        self._log.info(
            f"Files considered as operation: {operation_output_files}"
        )
        return operation_output_files

    def _cleanup_files(
        self,
        input_files: list[str],
        report_files: list[str],
        operation_files: list[str],
    ):
        caso_register = self.dessem_arq.caso
        if not caso_register:
            msg = f"No content found in {self.MODEL_ENTRY_FILE}"
            self._log.error(msg)
            raise FileNotFoundError(msg)
        extension = (
            caso_register.valor if caso_register.valor is not None else "DAT"
        )
        keeping_files = input_files + [
            "DES_LOG_RELATO." + extension,
            "PDO_CMOBAR." + extension,
            "PDO_CMOSIST." + extension,
            "PDO_CONTR." + extension,
            "PDO_EOLICA." + extension,
            "PDO_HIDR." + extension,
            "PDO_OPER_CONTR." + extension,
            "PDO_OPER_TERM." + extension,
            "PDO_OPER_TITULACAO_CONTRATOS." + extension,
            "PDO_OPER_TITULACAO_USINAS." + extension,
            "PDO_OPERACAO." + extension,
            "PDO_SIST." + extension,
            "PDO_SOMFLUX." + extension,
            "PDO_SUMAOPER." + extension,
            "PDO_TERM." + extension,
            "LOG_MATRIZ." + extension,
            "AVL_ESTATFPHA." + extension,
            "LOG_INVIAB." + extension,
        ]
        keeping_files = [a for a in keeping_files if a is not None]
        compressed_files = input_files + report_files + operation_files
        cleaning_files = [a for a in compressed_files if a not in keeping_files]
        cleaning_files_regex = [
            r"^fort.*$",
            r"^fpha_.*$",
            r"^SAVERADIAL.*$",
            r"^SIM_ECO.*$",
            r"^SVC_.*$",
        ]
        cleaning_files += list_files_by_regexes(
            input_files, cleaning_files_regex
        )
        self._log.info(f"Cleaning files: {cleaning_files}")
        clean_files(cleaning_files)

    def output_compression_and_cleanup(self, num_cpus: int):
        with time_and_log("Output compression and cleanup", logger=self._log):
            input_files = self._list_input_files()
            compress_files_to_zip(
                input_files, PROCESSED_DECK_FILE.rstrip(".zip")
            )
            # Moves content from DESSEM subdirectories to root
            for d in ["out"]:
                moves_content_to_rootdir(d)
            # Parallel compression
            operation_files = self._list_operation_files(input_files)
            compress_files_to_zip_parallel(
                operation_files, "operacao", num_cpus
            )
            report_files = self._list_report_files(input_files)
            compress_files_to_zip_parallel(report_files, "relatorios", num_cpus)
            self._cleanup_files(input_files, report_files, operation_files)

    def _upload_input_echo(self, path: str):
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        with time_and_log("Time for uploading input echo", logger=self._log):
            upload_file_to_bucket(
                RAW_DECK_FILE,
                bucket,
                join(key, INPUTS_ECHO_PREFIX, RAW_DECK_FILE),
                aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
            )
            upload_file_to_bucket(
                PROCESSED_DECK_FILE,
                bucket,
                join(key, INPUTS_ECHO_PREFIX, PROCESSED_DECK_FILE),
                aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
            )

    def _upload_outputs(self, path: str):
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        with time_and_log("Time for uploading outputs", logger=self._log):
            output_files = [
                "decomp.tim",
                "cortes.zip",
                "relatorios.zip",
                "operacao.zip",
            ]
            output_files += list_files_by_regexes([], [r".*\.modelops"])
            for f in output_files:
                if isfile(f):
                    self._log.info(f"Uploading {f}")
                    upload_file_to_bucket(
                        f,
                        bucket,
                        join(key, OUTPUTS_PREFIX, f),
                        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
                    )

    def _upload_synthesis(self, path: str):
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        with time_and_log("Time for uploading synthesis", logger=self._log):
            output_files = listdir(SYNTHESIS_DIR)
            for f in output_files:
                if isfile(join(SYNTHESIS_DIR, f)):
                    self._log.info(f"Uploading {f}")
                    upload_file_to_bucket(
                        join(SYNTHESIS_DIR, f),
                        bucket,
                        join(key, SYNTHESIS_DIR, f),
                        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
                    )

    def _set_status(self):
        metadata = self._update_metadata({})
        status = RunStatus.factory(metadata[METADATA_STATUS])

        if status == RunStatus.SUCCESS:
            ModelOpsCommands.set_success()
        elif status == RunStatus.DATA_ERROR:
            ModelOpsCommands.set_data_error()
        else:
            ModelOpsCommands.set_model_error()

    def result_upload(self, path: str):
        ModelOpsCommands.set_execution_artifacts_path(path)

        self._set_status()

        self._log.info(f"Uploading results for {self.MODEL_NAME}")

        self._upload_input_echo(path)
        self._upload_outputs(path)
        self._upload_synthesis(path) if isdir(
            SYNTHESIS_DIR
        ) else self._log.warning("No synthesis directory found!")

    def cancel_run(self, job_id: str, slurm_path: str):
        raise NotImplementedError

    def download_executed_run(self, outputs_path: str, delete: bool = True):
        raise NotImplementedError


ModelFactory().register(DESSEM.MODEL_NAME, DESSEM)
