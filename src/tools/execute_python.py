"""Execute Python in a Docker sandbox (persistent per session)."""

import textwrap
import time

import docker

from src.core.logger import logger
from src.core.prompts import get_tool_description, get_tool_examples
from src.tools.base import Tool, ToolResult


class ExecutePythonTool(Tool):
    def __init__(self):
        self._client = None
        self._container = None
        self._image = "python:slim"

    @property
    def name(self) -> str:
        return "execute_python"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {"code": "The Python code to execute."}

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    def _ensure_container(self):
        if self._client is None:
            try:
                self._client = docker.from_env()
            except Exception as e:
                logger.error(f"Failed to connect to Docker: {e}")
                raise RuntimeError("Docker is not available.")

        if self._container is None:
            logger.info(f"🚀 Starting sandbox container ({self._image})...")
            try:
                self._container = self._client.containers.run(
                    self._image,
                    command="tail -f /dev/null",
                    detach=True,
                    remove=True,
                    mem_limit="512m",
                    nano_cpus=500000000,
                    network_disabled=True,
                    working_dir="/workspace",
                )
                self._container.exec_run("mkdir -p /workspace")
                time.sleep(1)
            except Exception as e:
                logger.error(f"Failed to start container: {e}")
                raise

    async def execute(self, **kwargs: object) -> ToolResult:
        code = str(kwargs.get("code", ""))
        logger.tool_execute(self.name, {"code": code})
        try:
            import asyncio

            return await asyncio.to_thread(self._sync_execute, code)
        except Exception as e:
            logger.error(f"Sandbox execution failed: {e}")
            return ToolResult.fail(f"Execution Error: {str(e)}")

    def _sync_execute(self, code: str) -> ToolResult:
        self._ensure_container()
        wrapped_code = textwrap.dedent(code)
        self._container.put_archive(
            "/workspace", self._create_tar_with_code(wrapped_code)
        )
        result = self._container.exec_run(
            ["python3", "/workspace/script.py"], demux=True
        )

        stdout = result.output[0].decode() if result.output[0] else ""
        stderr = result.output[1].decode() if result.output[1] else ""

        combined_output = stdout
        if stderr:
            combined_output += f"\n--- Errors ---\n{stderr}"

        if not combined_output.strip() and result.exit_code == 0:
            combined_output = "(Code executed successfully with no output)"

        success = result.exit_code == 0
        error_reason = (
            None if success else (combined_output or "Unknown error in sandbox")
        )
        logger.tool_result(
            self.name, len(combined_output), success, error_reason=error_reason or None
        )
        if success:
            return ToolResult.ok(combined_output)
        return ToolResult.fail(combined_output or "Unknown error in sandbox")

    def _create_tar_with_code(self, code: str) -> bytes:
        import io
        import tarfile

        file_data = code.encode("utf8")
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar_info = tarfile.TarInfo(name="script.py")
            tar_info.size = len(file_data)
            tar_info.mtime = time.time()
            tar.addfile(tar_info, io.BytesIO(file_data))

        return tar_stream.getvalue()

    def close(self):
        if self._container:
            logger.info("🛑 Shutting down sandbox container...")
            try:
                self._container.stop(timeout=2)
                self._container = None
            except Exception as e:
                logger.error(f"Error stopping container: {e}")
