import json
from datetime import datetime
from os import curdir, environ, getenv, listdir
from os.path import isdir, isfile, join
from pathlib import Path
from shutil import move
from typing import Any

import pandas as pd  # type: ignore
from inewave.newave import Arquivos, Caso, Dger, Pmo
from pytz import UTC  # type: ignore

from app.adapter.repository.abstractmodel import (
    AbstractModel,
    ModelFactory,
)
from app.models.runstatus import RunStatus
from app.utils.commands import ModelOpsCommands
from app.utils.constants import (
    AWS_ACCESS_KEY_ID_ENV,
    AWS_SECRET_ACCESS_KEY_ENV,
    INPUTS_ECHO_PREFIX,
    JOB_CANCELLATION_TIMEOUT,
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
from app.utils.scheduler import (
    cancel_submitted_job,
    follow_submitted_job,
    submit_job,
    wait_cancelled_job,
)
from app.utils.terminal import cast_encoding_to_utf8, run_in_terminal
from app.utils.timing import time_and_log


class NEWAVE(AbstractModel):
    MODEL_NAME = "newave"
    NAMECAST_PROGRAM_NAME = "ConverteNomesArquivos"
    MODEL_ENTRY_FILE = "caso.dat"
    NWLISTCF_ENTRY_FILE = "arquivos.dat"
    LIBS_ENTRY_FILE = "indices.csv"
    LICENSE_FILENAMES = [
        "newave.lic",
        "ddsNEWAVE.cep",
        "newave.cep",
        "newave_trial.cep",
    ]
    CUT_FILE = "cortes.zip"
    RESOURCES_FILE = "recursos.zip"
    SIMULATION_FILE = "simulacao.zip"
    NWLISTCF_EXECUTABLE = join(MODEL_EXECUTABLE_DIRECTORY, "nwlistcf")
    NWLISTOP_EXECUTABLE = join(MODEL_EXECUTABLE_DIRECTORY, "nwlistop")
    NEWAVE_JOB_PATH = "hpc-model-utils/assets/jobs/newave.job"
    NEWAVE_POST_JOB_PATH = "hpc-model-utils/assets/jobs/newave_post.job"
    NEWAVE_JOB_TIMEOUT = 172800  # 48h
    NWLISTCF_NWLISTOP_TIMEOUT = 3600

    DECK_DATA_CACHING: dict[str, Any] = {}

    @property
    def caso_dat(self) -> Caso:
        name = "caso"
        if name not in self.DECK_DATA_CACHING:
            self._log.info(f"Reading file: {self.MODEL_ENTRY_FILE}")
            self.DECK_DATA_CACHING[name] = Caso.read(self.MODEL_ENTRY_FILE)
        return self.DECK_DATA_CACHING[name]

    @property
    def arquivos_dat(self) -> Arquivos:
        name = "arquivos"
        if name not in self.DECK_DATA_CACHING:
            filename = self.caso_dat.arquivos
            if not filename:
                msg = f"No content found in {self.MODEL_ENTRY_FILE}"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            self._log.info(f"Reading file: {filename}")
            self.DECK_DATA_CACHING[name] = Arquivos.read(filename)
        return self.DECK_DATA_CACHING[name]

    @property
    def dger(self) -> Dger:
        name = "dger"
        if name not in self.DECK_DATA_CACHING:
            filename = self.arquivos_dat.dger
            if not filename:
                msg = f"No <dger.dat> found in {self.caso_dat.arquivos}"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            self._log.info(f"Reading file: {filename}")
            self.DECK_DATA_CACHING[name] = Dger.read(filename)
        return self.DECK_DATA_CACHING[name]

    @property
    def pmo(self) -> Pmo:
        name = "pmo"
        if name not in self.DECK_DATA_CACHING:
            filename = self.arquivos_dat.pmo
            if not filename:
                msg = f"No <pmo.dat> found in {self.caso_dat.arquivos}"
                self._log.error(msg)
                raise FileNotFoundError(msg)
            self._log.info(f"Reading file: {filename}")
            self.DECK_DATA_CACHING[name] = Pmo.read(filename)
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
            # Downloads parent metadata and check if is a NEWAVE execution
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
                != NEWAVE.MODEL_NAME.upper()
            ):
                raise ValueError(
                    f"Parent model is not {NEWAVE.MODEL_NAME.upper()}"
                )
            success_status = RunStatus.SUCCESS.value
            if parent_metadata[METADATA_STATUS] != success_status:
                raise ValueError(
                    f"Parent execution status was not {success_status}"
                )

            for parent_file in [
                self.CUT_FILE,
                self.RESOURCES_FILE,
                self.SIMULATION_FILE,
            ]:
                remote_filepath = join(parent_key, OUTPUTS_PREFIX, parent_file)
                self._log.info(f"Fetching parent file from {remote_filepath}")
                check_and_download_bucket_items(
                    parent_bucket,
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

        metadata = {METADATA_PARENT_PATH: parent_path}
        self._update_metadata(metadata)
        ModelOpsCommands.set_metadata(METADATA_PARENT_PATH, parent_path)
        self._log.info("Inputs successfully fetched!")

    def extract_sanitize_inputs(self):
        extracted_files = (
            extract_zip_content(RAW_DECK_FILE) if isfile(RAW_DECK_FILE) else []
        )
        self._log.info(f"Extracted input files: {extracted_files}")
        code, _ = run_in_terminal(
            [join(MODEL_EXECUTABLE_DIRECTORY, self.NAMECAST_PROGRAM_NAME)],
            log_output=True,
        )
        if code != 0:
            self._log.warning(
                f"Running {self.NAMECAST_PROGRAM_NAME} resulted in:"
            )

        self._log.info("Forcing encoding to utf-8")
        for f in listdir():
            if f in self.LICENSE_FILENAMES:
                self._log.info(f"Ignoring license file: {f}")
                continue
            cast_encoding_to_utf8(f)

        # Unzips the parent files
        for parent_file, files_to_extract in {
            self.CUT_FILE: None,
            self.RESOURCES_FILE: [
                "engthd.dat",
                "engfiobac.dat",
                "engfio.dat",
                "engfiob.dat",
                "engthd.dat",
                "engnat.dat",
                "engcont.dat",
                "vazthd.dat",
                "vazinat.dat",
            ],
            self.SIMULATION_FILE: ["newdesp.dat"],
        }.items():
            if isfile(parent_file):
                extracted_files = extract_zip_content(
                    parent_file, members=files_to_extract
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

        study_name = self.dger.nome_caso
        study_year = self.dger.ano_inicio_estudo
        study_month = self.dger.mes_inicio_estudo
        if not study_year:
            raise ValueError("Study year not found in <dger.dat>")
        if not study_month:
            raise ValueError("Study month not found in <dger.dat>")
        study_starting_date = datetime(study_year, study_month, 1, tzinfo=UTC)
        metadata = {
            METADATA_STUDY_STARTING_DATE: study_starting_date.isoformat(),
            METADATA_STUDY_NAME: study_name if study_name else "",
        }
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)

    def preprocess(self, execution_name: str):
        path = str(Path(MODEL_EXECUTABLE_DIRECTORY).resolve())
        self._log.info(f"Updating 'caso.dat' input with: {path}/")
        self.caso_dat.gerenciador_processos = path + "/"
        self.caso_dat.write(self.MODEL_ENTRY_FILE)
        self._log.info(f"Updating 'dger.dat' study name with: {execution_name}")
        self.dger.nome_caso = execution_name
        self.dger.write(self.arquivos_dat.dger)

    def run(
        self,
        queue: str,
        core_count: int,
        mpich_path: str,
        slurm_path: str,
        max_cores_per_node: int | None = None,
        max_job_time_hours: int | None = None,
        skip_model: bool = False,
    ):
        self._log.info(f"Job script file: {self.NEWAVE_JOB_PATH}")
        environ["PATH"] += ":" + ":".join([mpich_path, slurm_path])
        jobs_to_run = (
            [self.NEWAVE_JOB_PATH, self.NEWAVE_POST_JOB_PATH]
            if not skip_model
            else [self.NEWAVE_POST_JOB_PATH]
        )
        for job_file in jobs_to_run:
            job_id = submit_job(
                queue,
                core_count,
                job_file,
                cpus_per_task=2,
                max_tasks_per_node=max_cores_per_node,
                max_job_time_hours=max_job_time_hours,
            )
            if job_id:
                follow_submitted_job(job_id, self.NEWAVE_JOB_TIMEOUT)

    def generate_execution_status(self, job_id: str) -> str:
        self._log.info("Reading 'pmo.dat' file for generating status...")
        pmo_dat = self.pmo
        dger_dat = self.dger
        execution_kind = dger_dat.tipo_execucao
        simulation_kind = dger_dat.tipo_simulacao_final

        status = RunStatus.SUCCESS
        if execution_kind == 0:
            if pmo_dat.custo_operacao_series_simuladas is None:
                status = RunStatus.DATA_ERROR
        if execution_kind == 1:
            if pmo_dat.convergencia is None:
                status = RunStatus.DATA_ERROR
        if simulation_kind == 3:
            # TODO - better evaluate this execution mode
            status = RunStatus.SUCCESS

        status_value = status.value
        with open(STATUS_DIAGNOSIS_FILE, "w") as f:
            f.write(status_value)
        metadata = {METADATA_JOB_ID: job_id, METADATA_STATUS: status_value}
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)
        return status_value

    def _generate_nwlistcf_arquivos_dat_file(self, month: int):
        lines = [
            "ARQUIVO DE DADOS GERAIS     : nwlistcf.dat\n",
            f"ARQUIVO DE CORTES DE BENDERS: cortes-{str(month).zfill(3)}.dat\n",
            "ARQUIVO DE CABECALHO CORTES : cortesh.dat\n",
            "ARQUIVO P/DESPACHO HIDROTERM: newdesp.dat\n",
            f"ARQUIVO DE ESTADOS CORTES   : cortese-{str(month).zfill(3)}.dat\n",
            "ARQUIVO DE ENERGIAS FORWARD : energiaf.dat\n",
            "ARQUIVO DE RESTRICOES SAR   : rsar.dat\n",
            "ARQUIVO DE CABECALHO SAR    : rsarh.dat\n",
            "ARQUIVO DE INDICE SAR       : rsari.dat\n",
            "ARQUIVO LISTAGEM CORTES     : nwlistcf.rel\n",
            "ARQUIVO LISTAGEM ESTADOS FCF: estados.rel\n",
            "ARQUIVO LISTAGEM SAR        : rsar.rel\n",
            "ARQUIVO DE ENERGIAS X FORW  : energiaxf.dat\n",
            "ARQUIVO DE VAZAO FORWARD    : vazaof.dat\n",
            "ARQUIVO DE VAZAO X FORWARD  : vazaoxf.dat\n",
        ]
        with open(self.NWLISTCF_ENTRY_FILE, "w") as arq:
            arq.writelines(lines)

    def _generate_nwlistcf_dat_file(self, month: int, option: int):
        previous_lines = [
            " INI FIM FC (FC = 1: IMPRIME TODOS CORTES, FC = 0: IMPRIME APENAS CORTES VALIDOS NA ULTIMA ITERACAO)\n",
            " XXX XXX X\n",
        ]
        following_lines = [
            " OPCOES DE IMPRESSAO : 01 - CORTES FCF  02 - ESTADOS FCF  03 - RESTRICAO SAR\n",
            " XX XX XX (SE 99 CONSIDERA TODAS)\n",
            f" {str(option).zfill(2)}\n",
        ]
        self._log.info(f"Generating nwlistcf.dat for month: {month}")
        with open("nwlistcf.dat", "w") as arq:
            arq.writelines(previous_lines)
            arq.write(f"  {month}  {month} 1\n")
            arq.writelines(following_lines)

    def _generate_nwlistop_dat_file(self, option: int):
        dger_dat = self.dger
        num_study_years = dger_dat.num_anos_estudo
        study_starting_month = dger_dat.mes_inicio_estudo
        pre_study_years = dger_dat.num_anos_pre_estudo
        post_study_years_final_sim = dger_dat.num_anos_pos_sim_final
        if (
            num_study_years is None
            or study_starting_month is None
            or pre_study_years is None
            or post_study_years_final_sim is None
        ):
            raise ValueError("Error processing dger.dat")
        initial_stage = pre_study_years * 12 + 1
        final_stage = (
            num_study_years * 12
            + post_study_years_final_sim * 12
            - (study_starting_month - 1)
        )
        self._log.info(
            f"Generating nwlistop.dat option {option} between "
            + f"stages: {initial_stage} - {final_stage}",
        )
        lines = [
            f" {option}\n",
            "FORWARD  (ARQ. DE DADOS)    : forward.dat\n",
            "FORWARDH (ARQ. CABECALHOS)  : forwarh.dat\n",
            "NEWDESP  (REL. CONFIGS)     : newdesp.dat\n",
            "-----------------------------------------\n",
            " XXX XXX    PERIODOS INICIAL E FINAL\n",
            f" {str(initial_stage).zfill(3)} {str(final_stage).zfill(3)}\n",
            " 1-CMO           2-DEFICITS         3-ENA CONTROL.   4-EARM FINAL       5-ENA FIO BRUTA 6-EVAPORACAO    7-VERTIMENTO\n",
            " 8-VAZAO MIN.    9-GER.HIDR.CONT   10-GER. TERMICA  11-INTERCAMBIOS    12-MERC.LIQ.    13-VALOR AGUA   14-VOLUME MORTO\n",
            "15-EXCESSO      16-GHMAX           17-OUTROS USOS   18-BENEF.INT/AGR   19-F.CORR.EC    20-GHTOTAL      21-ENA BRUTA\n",
            "22-ACOPLAMENTO  23-INVASAO CG      24-PENAL.INV.CG. 25-ACIONAMENTO MAR 26-COPER        27-CTERM        28-CDEFICIT\n",
            "29-GER.FIO LIQ. 30-PERDA FIO       31-ENA FIO LIQ.  32- BENEF. GNL     33-VIOL.GHMIN   34-PERDAS       37-GEE             38-SOMA AFL.PAS.\n",
            " XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX (SE 99 CONSIDERA TODAS)\n",
            " 99\n",
            "-----------------------------------------------------------------------------------------------------------------------\n",
            " 1-VOL.ARMAZ       2-GER.HID         3-VOL.TURB.     4-VOL. VERT.      5-VIOL.GHMIN    6-ENCH.MORTO   7-FOLGA DEPMIN.\n",
            " 8-DESV. AGUA      9-DESV. POS.      10-DESVIO NEG.  11-FOLGA FPGHA   12-VAZAO AFL.  13-VAZAO INCREM. 14-VARM PCT.\n",
            " XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX (SE 99 CONSIDERA TODAS)\n",
            " 99\n",
            " XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX  (SE 999 CONSIDERA TODAS AS USINAS)\n",
            " 999\n",
        ]
        with open("nwlistop.dat", "w") as arq:
            arq.writelines(lines)

    def _run_nwlistcf(self, stage: int):
        tmp_filename = "arquivos.dat.bkp"
        caso_dat = self.caso_dat
        dger_dat = self.dger
        num_study_years = dger_dat.num_anos_estudo
        study_starting_month = dger_dat.mes_inicio_estudo
        if num_study_years is None or study_starting_month is None:
            raise ValueError("Error processing dger.dat")
        if stage < 0:
            stage = (num_study_years * 12) - (stage + 1)
        else:
            stage = study_starting_month + stage - 1
        month = str(stage).zfill(2)
        files_filename = caso_dat.arquivos
        if files_filename is None:
            raise ValueError("Error processing caso.dat")
        try:
            move(files_filename, tmp_filename)
            self._generate_nwlistcf_arquivos_dat_file(month)
            for option in [1, 2]:
                self._generate_nwlistcf_dat_file(month, option)
                self._log.info(f"Running NWLISTCF option {option}")
                status_code, _ = run_in_terminal(
                    [self.NWLISTCF_EXECUTABLE, "2>&1"],
                    timeout=self.NWLISTCF_NWLISTOP_TIMEOUT,
                    log_output=True,
                )
                self._log.info(f"NWLISTCF status: {status_code}")

        except Exception as e:
            self._log.warning(f"Error running NWLISTCF: {str(e)}")
        finally:
            move(self.NWLISTCF_ENTRY_FILE, "arquivos-nwlistcf.dat")
            move(tmp_filename, files_filename)

    def _run_nwlistop(self, option: int):
        try:
            self._generate_nwlistop_dat_file(option)
            self._log.info(f"Running NWLISTOP option {option}")
            status_code, _ = run_in_terminal(
                [self.NWLISTOP_EXECUTABLE, "2>&1"],
                timeout=self.NWLISTCF_NWLISTOP_TIMEOUT,
                log_output=True,
            )
            self._log.info(f"NWLISTOP status: {status_code}")

        except Exception as e:
            self._log.warning(f"Error running NWLISTOP: {str(e)}")

    def postprocess(self):
        with time_and_log("Running NWLISTCF and NWLISTOP", logger=self._log):
            self._run_nwlistcf(stage=2)
            self._run_nwlistop(option=2)
            self._run_nwlistop(option=4)

    def _list_input_files(self) -> list[str]:
        caso_dat = self.caso_dat
        arquivos_dat = self.arquivos_dat
        common_input_files = [
            self.MODEL_ENTRY_FILE,
            caso_dat.arquivos,
            arquivos_dat.adterm,
            arquivos_dat.agrint,
            arquivos_dat.c_adic,
            arquivos_dat.cvar,
            arquivos_dat.sar,
            arquivos_dat.clast,
            arquivos_dat.confhd,
            arquivos_dat.conft,
            arquivos_dat.curva,
            arquivos_dat.dger,
            arquivos_dat.dsvagua,
            arquivos_dat.vazpast,
            arquivos_dat.exph,
            arquivos_dat.expt,
            arquivos_dat.ghmin,
            arquivos_dat.gtminpat,
            "hidr.dat",
            arquivos_dat.perda,
            arquivos_dat.manutt,
            arquivos_dat.modif,
            arquivos_dat.patamar,
            arquivos_dat.penalid,
            "postos.dat",
            arquivos_dat.shist,
            arquivos_dat.sistema,
            arquivos_dat.term,
            "vazoes.dat",
            arquivos_dat.tecno,
            "selcor.dat",
            arquivos_dat.re,
            arquivos_dat.ree,
            arquivos_dat.clasgas,
            arquivos_dat.abertura,
            arquivos_dat.gee,
            "dbgcortes.dat",
            arquivos_dat.cortesh_pos_estudo,
            arquivos_dat.cortes_pos_estudo,
            arquivos_dat.volume_referencia_sazonal,
            arquivos_dat.eliminacao_cortes,
        ]
        index_file = (
            [self.LIBS_ENTRY_FILE] if self.LIBS_ENTRY_FILE in listdir() else []
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

        input_files = (
            common_input_files
            + index_file
            + [a.strip() for a in libs_input_files]
        )
        input_files = [a for a in input_files if a is not None]

        self._log.info(f"Files considered as input: {input_files}")
        return input_files

    def _list_nwlistop_files(self, input_files: list[str]) -> list[str]:
        nwlistop_output_file_regex = [
            r"^.*\.CSV$",
            r"^.*\.out$",
        ]
        nwlistop_files = ["nwlistop.dat"]
        nwlistop_files += list_files_by_regexes(
            input_files + ["mensagens.csv"], nwlistop_output_file_regex
        )

        self._log.info(f"Files considered as operation: {nwlistop_files}")
        return nwlistop_files

    def _list_report_files(self, input_files: list[str]) -> list[str]:
        arquivos_dat = self.arquivos_dat
        pmo_file = arquivos_dat.pmo if arquivos_dat.pmo else ""
        parp_file = arquivos_dat.parp if arquivos_dat.parp else ""
        sim_file = (
            arquivos_dat.dados_simulacao_final
            if arquivos_dat.dados_simulacao_final
            else ""
        )
        report_output_files: list[str] = [
            pmo_file,
            parp_file,
            sim_file,
            "newave.tim",
            "nwv_avl_evap.csv",
            "nwv_cortes_evap.csv",
            "nwv_eco_evap.csv",
            "evap_avl_desv.csv",
            "evap_eco.csv",
            "evap_cortes.csv",
            "boots.rel",
            "consultafcf.rel",
            "eco_fpha_.dat",
            "eco_fpha.csv",
            "fpha_eco.csv",
            "fpha_cortes.csv",
            "avl_cortesfpha_nwv.dat",
            "avl_cortesfpha_nwv.csv",
            "parpeol.dat",
            "parpvaz.dat",
            "runtrace.dat",
            "runstate.dat",
            "prociter.rel",
            "CONVERG.TMP",
            "ETAPA.TMP",
            "TAREFA.TMP",
            "mensagens.csv",
            "indice_saida.csv",
        ]
        report_output_file_regex = [
            r"^alertainv.*\.rel$",
            r"^cativo_.*\.rel$",
            r"^avl_desvfpha.*\.dat$",
            r"^avl_desvfpha.*\.csv$",
            r"^newave_.*\.log$",
            r"^nwv_.*\.rel$",
        ]
        report_output_files = [a for a in report_output_files if a is not None]
        report_output_files += list_files_by_regexes(
            input_files, report_output_file_regex
        )
        self._log.info(f"Files considered as report: {report_output_files}")
        return report_output_files

    def _list_resource_files(self, input_files: list[str]) -> list[str]:
        resource_output_file_regex = [
            r"^energiaas.*\.dat$",
            r"^energiaasx.*\.dat$",
            r"^energiaf.*\.dat$",
            r"^energiaaf.*\.dat$",
            r"^energiab.*\.dat$",
            r"^energiaxf.*\.dat$",
            r"^energias.*\.dat$",
            r"^energiaxs.*\.dat$",
            r"^energiap.*\.dat$",
            r"^energiaas.*\.csv$",
            r"^energiaasx.*\.csv$",
            r"^energiaf.*\.csv$",
            r"^energiaaf.*\.csv$",
            r"^energiab.*\.csv$",
            r"^energiax.*\.csv$",
            r"^energiaxf.*\.csv$",
            r"^energiaxs.*\.csv$",
            r"^energiap.*\.csv$",
            r"^eng.*\.dat$",
            r"^enavazf.*\.dat$",
            r"^enavazxf.*\.dat$",
            r"^enavazxs.*\.dat$",
            r"^enavazb.*\.dat$",
            r"^enavazs.*\.dat$",
            r"^enavazf.*\.csv$",
            r"^enavazxf.*\.csv$",
            r"^enavazaf.*\.csv$",
            r"^enavazb.*\.csv$",
            r"^enavazs.*\.csv$",
            r"^vazaof.*\.dat$",
            r"^vazaoaf.*\.dat$",
            r"^vazaob.*\.dat$",
            r"^vazaos.*\.dat$",
            r"^vazaoas.*\.dat$",
            r"^vazaoxs.*\.dat$",
            r"^vazaoxf.*\.dat$",
            r"^vazaop.*\.dat$",
            r"^vazaof.*\.csv$",
            r"^vazaoaf.*\.csv$",
            r"^vazaob.*\.csv$",
            r"^vazaos.*\.csv$",
            r"^vazthd.*\.dat$",
            r"^vazinat.*\.dat$",
            r"^ventos.*\.dat$",
            r"^vento.*\.csv$",
            r"^eolicaf.*\.dat$",
            r"^eolicab.*\.dat$",
            r"^eolicas.*\.dat$",
            r"^eolp.*\.dat$",
            r"^eolf.*\.csv$",
            r"^eolb.*\.csv$",
            r"^eolp.*\.csv$",
            r"^eols.*\.csv$",
        ]
        resource_output_files = ["mlt.dat"] + list_files_by_regexes(
            input_files, resource_output_file_regex
        )
        self._log.info(f"Files considered as resource: {resource_output_files}")
        return resource_output_files

    def _list_cuts_files(self, input_files: list[str]) -> list[str]:
        arquivos_dat = self.arquivos_dat
        header_file = arquivos_dat.cortesh if arquivos_dat.cortesh else ""
        cut_file = arquivos_dat.cortes if arquivos_dat.cortes else ""
        cuts_output_files = [
            header_file,
            cut_file,
            "arquivos-nwlistcf.dat",
            "nwlistcf.rel",
        ]
        cuts_output_file_regex = [r"^cortes\-[0-9]*.*\.dat$"]
        cuts_output_files += list_files_by_regexes(
            input_files, cuts_output_file_regex
        )
        self._log.info(f"Files considered as cuts: {cuts_output_files}")
        return cuts_output_files

    def _list_states_files(self, input_files: list[str]) -> list[str]:
        states_output_files = ["cortese.dat", "estados.rel"]
        states_output_file_regex = [r"^cortese\-[0-9]*.*\.dat$"]
        states_output_files += list_files_by_regexes(
            input_files, states_output_file_regex
        )
        self._log.info(f"Files considered as states: {states_output_files}")
        return states_output_files

    def _list_simulation_files(self, input_files: list[str]) -> list[str]:
        arquivos_dat = self.arquivos_dat
        sim_file = arquivos_dat.forward if arquivos_dat.forward else ""
        header_file = arquivos_dat.forwardh if arquivos_dat.forwardh else ""
        config_file = arquivos_dat.newdesp if arquivos_dat.newdesp else ""
        simulation_output_files = [
            sim_file,
            header_file,
            config_file,
            "planej.dat",
            "daduhe.dat",
            "nwdant.dat",
            "saida.rel",
        ]
        self._log.info(
            f"Files considered as simulation: {simulation_output_files}"
        )
        return simulation_output_files

    def _cleanup_files(
        self,
        input_files: list[str],
        nwlistop_files: list[str],
        report_files: list[str],
        resource_files: list[str],
        cuts_files: list[str],
        states_files: list[str],
        simulation_files: list[str],
    ):
        arquivos_dat = self.arquivos_dat
        keeping_files = [
            "newave.tim",
            arquivos_dat.pmo,
            arquivos_dat.dados_simulacao_final,
            "mensagens.csv",
        ]
        keeping_files = [a for a in keeping_files if a is not None]
        compressed_files = (
            input_files
            + nwlistop_files
            + report_files
            + resource_files
            + cuts_files
            + states_files
            + simulation_files
        )
        cleaning_files = [a for a in compressed_files if a not in keeping_files]
        cleaning_files_regex = [r"^svc.*$", r"^fort\..*"]
        cleaning_files += list_files_by_regexes(
            input_files, cleaning_files_regex
        )
        cleaning_files += [
            "nwlistcf.dat",
            "nwlistop.dat",
            "format.tmp",
            "mensag.tmp",
            "NewaveMsgPortug.txt",
            "ConvNomeArqsDados.log",
            "ETAPA.TMP",
            "LEITURA.TMP",
        ]
        self._log.info(f"Cleaning files: {cleaning_files}")
        clean_files(cleaning_files)

    def output_compression_and_cleanup(self, num_cpus: int):
        with time_and_log("Output compression and cleanup", logger=self._log):
            input_files = self._list_input_files()
            compress_files_to_zip(
                input_files, PROCESSED_DECK_FILE.rstrip(".zip")
            )
            # Moves content from NEWAVE subdirectories to root
            for d in ["out", "evaporacao", "fpha", "log"]:
                moves_content_to_rootdir(d)
            # Parallel compression
            nwlistop_files = self._list_nwlistop_files(input_files)
            compress_files_to_zip_parallel(nwlistop_files, "operacao", num_cpus)
            report_files = self._list_report_files(input_files)
            compress_files_to_zip_parallel(report_files, "relatorios", num_cpus)
            resource_files = self._list_resource_files(input_files)
            compress_files_to_zip_parallel(resource_files, "recursos", num_cpus)
            cuts_files = self._list_cuts_files(input_files)
            compress_files_to_zip_parallel(cuts_files, "cortes", num_cpus)
            states_files = self._list_states_files(input_files)
            compress_files_to_zip_parallel(states_files, "estados", num_cpus)
            simulation_files = self._list_simulation_files(input_files)
            compress_files_to_zip_parallel(
                simulation_files, "simulacao", num_cpus
            )
            self._cleanup_files(
                input_files,
                nwlistop_files,
                report_files,
                resource_files,
                cuts_files,
                states_files,
                simulation_files,
            )

    def _upload_input_echo(self, path: str):
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        with time_and_log("Time for uploading input echo", logger=self._log):
            if isfile(RAW_DECK_FILE):
                upload_file_to_bucket(
                    RAW_DECK_FILE,
                    bucket,
                    join(key, INPUTS_ECHO_PREFIX, RAW_DECK_FILE),
                    aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                    aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
                )
            if isfile(PROCESSED_DECK_FILE):
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
                "newave.tim",
                "cortes.zip",
                "estados.zip",
                "simulacao.zip",
                "recursos.zip",
                "relatorios.zip",
                "operacao.zip",
            ]
            output_files += list_files_by_regexes(
                [], [r".*\.dat", r".*\.modelops"]
            )
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
        try:
            status = RunStatus.factory(metadata[METADATA_STATUS])

            if status == RunStatus.SUCCESS:
                ModelOpsCommands.set_success()
            elif status == RunStatus.DATA_ERROR:
                ModelOpsCommands.set_data_error()
            else:
                ModelOpsCommands.set_model_error()

        except Exception:
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
        if job_id:
            environ["PATH"] += f":{slurm_path}"
            self._log.info(f"Canceling job {job_id}")
            cancel_submitted_job(job_id)
            wait_cancelled_job(job_id, JOB_CANCELLATION_TIMEOUT)

    def download_executed_run(self, artifacts_path: str, fetch_inputs: bool = True):
        self._log.info(f"Fetching artifact data in {artifacts_path}...")

        if fetch_inputs:
            inputs_path = join(artifacts_path, INPUTS_ECHO_PREFIX)
            inputs_path_data = path_to_bucket_and_key(inputs_path)
            bucket = inputs_path_data["bucket"]
            key = inputs_path_data["key"]
            downloaded_filepaths = check_and_download_bucket_items(
                bucket, str(Path(curdir).resolve()), key, self._log
            )
            for filepath in downloaded_filepaths:
                self._log.info(
                    f"Downloaded {filepath}"
                )

        outputs_path = join(artifacts_path, OUTPUTS_PREFIX)
        outputs_path_data = path_to_bucket_and_key(outputs_path)
        bucket = outputs_path_data["bucket"]
        key = outputs_path_data["key"]
        downloaded_filepaths = check_and_download_bucket_items(
            bucket, str(Path(curdir).resolve()), key, self._log
        )
        for filepath in downloaded_filepaths:
            self._log.info(
                f"Downloaded {filepath}"
            )


ModelFactory().register(NEWAVE.MODEL_NAME, NEWAVE)
