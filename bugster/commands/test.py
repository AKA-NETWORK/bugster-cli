import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.status import Status

from bugster.clients.mcp_client import MCPStdioClient
from bugster.clients.ws_client import WebSocketClient
from bugster.commands.middleware import require_api_key
from bugster.commands.sync import get_current_branch
from bugster.libs.services.results_stream_service import ResultsStreamService
from bugster.libs.services.update_service import DetectAffectedSpecsService
from bugster.types import (
    Config,
    NamedTestResult,
    Test,
    WebSocketCompleteMessage,
    WebSocketInitTestMessage,
    WebSocketStepRequestMessage,
    WebSocketStepResultMessage,
)
from bugster.utils.file import get_mcp_config_path, load_config, load_test_files
from bugster.libs.services.run_limits_service import apply_test_limit, get_test_limit_from_config, count_total_tests
from bugster.utils.console_messages import RunMessages

console = Console()

def handle_test_result_streaming(
    stream_service: ResultsStreamService,
    api_run_id: str,
    result: NamedTestResult,
    video_path: Optional[Path],
):
    """Handle streaming of test result and video upload in background."""
    try:
        test_case_data = {
            "id": result.metadata.id,
            "name": result.name,
            "result": result.result,
            "reason": result.reason,
            "time": result.time,
        }

        # Add test case to run
        stream_service.add_test_case(api_run_id, test_case_data)

        # Upload video if it exists
        if video_path and video_path.exists():
            video_url = stream_service.upload_video(video_path)
            if video_url:
                stream_service.update_test_case_with_video(
                    api_run_id, result.metadata.id, video_url
                )

    except Exception as e:
        RunMessages.streaming_warning(result.name, str(e))

def initialize_streaming_service(
    config: Config, run_id: str, silent: bool = False
) -> tuple[Optional[ResultsStreamService], Optional[str]]:
    """Initialize the streaming service and create initial run record."""
    try:
        stream_service = ResultsStreamService()
        branch = get_current_branch()

        # Create initial run record
        run_data = {
            "id": run_id,
            "base_url": config.base_url,
            "branch": branch,
            "result": "running",
            "time": 0,
            "test_cases": [],
        }
        api_run = stream_service.create_run(run_data)
        api_run_id = api_run.get("id", run_id)

        if not silent:
            RunMessages.streaming_results_to_run(api_run_id)

        return stream_service, api_run_id
    except Exception as e:
        RunMessages.streaming_init_warning(str(e))
        return None, None

def finalize_streaming_run(
    stream_service: Optional[ResultsStreamService],
    api_run_id: Optional[str],
    results: List[NamedTestResult],
    total_time: float,
):
    """Update final run status when streaming is enabled."""
    if not stream_service or not api_run_id:
        return

    try:
        overall_result = "pass" if all(r.result == "pass" for r in results) else "fail"
        final_run_data = {"result": overall_result, "time": total_time}
        stream_service.update_run(api_run_id, final_run_data)
    except Exception as e:
        RunMessages.streaming_warning("final run status", str(e))

def save_results_to_json(
    output: str,
    config: Config,
    run_id: str,
    results: List[NamedTestResult],
    total_time: float,
):
    """Save test results to JSON file."""
    try:
        output_data = {
            "id": run_id,
            "base_url": config.base_url,
            "project_id": config.project_id,
            "branch": get_current_branch(),
            "result": "pass" if all(r.result == "pass" for r in results) else "fail",
            "time": total_time,
            "test_cases": [
                {
                    "id": r.metadata.id,
                    "name": r.name,
                    "result": r.result,
                    "reason": r.reason,
                    "time": r.time,
                    "video": "",  # Video URLs would need to be tracked separately
                }
                for r in results
            ],
        }

        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2)

        RunMessages.results_saved(output)
    except Exception as e:
        RunMessages.save_results_error(output, str(e))

def get_video_path_for_test(video_dir: Path, test_name: str) -> Optional[Path]:
    """Get the video path for a given test name."""
    if not video_dir.exists():
        return None

    slugified_name = test_name.lower().replace(" ", "_")
    video_filename = f"test__{slugified_name}.webm"
    video_path = video_dir / video_filename

    return video_path if video_path.exists() else None

async def handle_step_request(
    step_request: WebSocketStepRequestMessage,
    mcp_client: MCPStdioClient,
    ws_client: WebSocketClient,
    silent: bool = False,
) -> None:
    """Handle a step request from the WebSocket server.""" 
    result = await mcp_client.execute(step_request.tool)

    await ws_client.send(
        WebSocketStepResultMessage(
            job_id=step_request.job_id,
            tool=step_request.tool,
            status="success" if not result.isError else "error",
            output=str(result.content[0].model_dump()) if result.content else "",
        ).model_dump()
    )

def handle_complete_message(
    complete_message: WebSocketCompleteMessage, test: Test, elapsed_time: float
) -> NamedTestResult:
    """Handle a complete message from the WebSocket server."""
    result = NamedTestResult(
        name=test.name,
        metadata=test.metadata,
        result=complete_message.result.result,
        reason=complete_message.result.reason,
    )
    result.time = elapsed_time  # Add time attribute
    return result

async def execute_test(test: Test, config: Config, **kwargs) -> NamedTestResult:
    """Execute a single test using WebSocket and MCP clients."""
    ws_client = WebSocketClient()
    mcp_client = MCPStdioClient()
    silent = kwargs.get("silent", False)
    run_id = kwargs.get("run_id", str(uuid.uuid4()))

    try:
        # Connect to WebSocket and initialize MCP
        with Status(RunMessages.connecting_to_agent(), spinner="dots") as status:
            await ws_client.connect()
            status.update(RunMessages.connected_successfully())

        mcp_config = {
            "browser": {
                "contextOptions": {
                    "viewport": {"width": 1280, "height": 720},
                    "recordVideo": {
                        "dir": f".bugster/videos/{run_id}/{test.metadata.id}",
                        "size": {"width": 1280, "height": 720},
                    },
                }
            }
        }
        playwright_config = get_mcp_config_path(mcp_config, version="v1")
        mcp_command = "npx"
        mcp_args = [
            "@playwright/mcp@latest",
            "--isolated",
            "--no-sandbox",
            "--config",
            playwright_config,
        ]
        if kwargs.get("headless"):
            mcp_args.append("--headless")

        await mcp_client.init_client(mcp_command, mcp_args)

        # Send initial test data with config
        await ws_client.send(
            WebSocketInitTestMessage(
                test=test,
                config=config,
            ).model_dump()
        )

        # Main test loop
        with Status(RunMessages.running_test_status(test.name), spinner="line") as status:
            last_step_request = None
            timeout_retry_count = 0
            unknown_retry_count = 0
            max_retries = 2

            while True:
                try:
                    message = await ws_client.receive(timeout=300)
                except asyncio.TimeoutError:
                    RunMessages.error("Timeout: No response from Bugster Agent")
                    raise typer.Exit(1)

                if message.get("action") == "step_request":
                    step_request = WebSocketStepRequestMessage(**message)
                    last_step_request = step_request
                    timeout_retry_count = 0  # Reset retry count for new step
                    unknown_retry_count = 0  # Reset retry count for new step

                    await handle_step_request(
                        step_request, mcp_client, ws_client, silent
                    )
                    if not silent:
                        status.update(RunMessages.running_test_status(test.name, step_request.message))

                elif message.get("action") == "complete":
                    complete_message = WebSocketCompleteMessage(**message)
                    result = handle_complete_message(
                        complete_message, test, 0
                    )  # time is added later
                    return result
                elif message.get("message") == "Endpoint request timed out":
                    if last_step_request and timeout_retry_count < max_retries:
                        timeout_retry_count += 1
                        logger.warning(
                            f"Timeout occurred, retrying step ({timeout_retry_count}/{max_retries}): {last_step_request.message}"
                        )
                        if not silent:
                            status.update(RunMessages.retrying_step(
                                test.name, timeout_retry_count, max_retries, last_step_request.message
                            ))

                        await handle_step_request(
                            last_step_request, mcp_client, ws_client, silent
                        )
                    else:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded for step: {last_step_request.message if last_step_request else 'Unknown step'}"
                        )
                        if not silent:
                            RunMessages.max_retries_exceeded()
                        raise typer.Exit(1)
                else:
                    if last_step_request and unknown_retry_count < max_retries:
                        unknown_retry_count += 1
                        logger.warning(
                            f"Unknown message received, waiting 30s and retrying step ({unknown_retry_count}/{max_retries}): {last_step_request.message}"
                        )
                        logger.debug(f"Unknown message content: {message}")
                        if not silent:
                            status.update(RunMessages.retrying_step(
                                test.name, unknown_retry_count, max_retries, last_step_request.message, False
                            ))

                        await asyncio.sleep(30)

                        if not silent:
                            status.update(RunMessages.running_test_status(
                                test.name, f"Retrying ({unknown_retry_count}/{max_retries}): {last_step_request.message}"
                            ))

                        await handle_step_request(
                            last_step_request, mcp_client, ws_client, silent
                        )
                    else:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded for unknown message. Last step: {last_step_request.message if last_step_request else 'Unknown step'}"
                        )
                        logger.error(f"Final unknown message: {message}")
                        if not silent:
                            RunMessages.internal_error()
                        raise typer.Exit(1)

    finally:
        await ws_client.close()
        await mcp_client.close()

def rename_video(video_dir: Path, test_name: str) -> None:
    """Rename video files to include the test name."""
    # There is not way to identify the video file corresponding to the test
    # so after the test run, we need to rename the new video to the test name
    if video_dir.exists():
        # Find video files that don't start with "test"
        for video_file in video_dir.glob("*.webm"):
            if not video_file.name.startswith("test"):
                # Create new filename with test name, slugified
                slugified_name = test_name.lower().replace(" ", "_")
                new_name = f"test__{slugified_name}.webm"
                new_path = video_dir / new_name
                # Rename the file
                video_file.rename(new_path)
                break

async def execute_single_test(
    test: Test,
    config: Config,
    test_executor_kwargs: dict,
    stream_service: Optional[ResultsStreamService],
    api_run_id: Optional[str],
    run_id: str,
    executor: ThreadPoolExecutor,
    silent: bool = False,
) -> NamedTestResult:
    """Execute a single test and handle streaming."""
    if not silent:
        RunMessages.test_start(test.name)

    test_start_time = time.time()
    result = await execute_test(test, config, **test_executor_kwargs)
    test_elapsed_time = time.time() - test_start_time

    # Add elapsed time to result
    result.time = test_elapsed_time

    RunMessages.test_result(test.name, result.result, test_elapsed_time)

    # Rename the video to the test name
    video_dir = Path(".bugster/videos") / run_id / test.metadata.id
    rename_video(video_dir, test.name)

    # Stream result if enabled (in background)
    if stream_service and api_run_id:
        video_path = get_video_path_for_test(video_dir, test.name)

        # Submit both test case creation and video upload to thread pool
        executor.submit(
            handle_test_result_streaming,
            stream_service,
            api_run_id,
            result,
            video_path,
        )

    return result

@require_api_key
async def test_command(
    test_path: Optional[str] = None,
    headless: Optional[bool] = False,
    silent: Optional[bool] = False,
    stream_results: Optional[bool] = False,
    output: Optional[str] = None,
    run_id: Optional[str] = None,
    base_url: Optional[str] = None,
    only_affected: Optional[bool] = None,
):
    """Run Bugster tests."""
    total_start_time = time.time()

    try:
        # Load configuration and test files
        config = load_config()
        max_tests = get_test_limit_from_config()

        if base_url:
            # Override the base URL in the config
            # Used for CI/CD pipelines
            config.base_url = base_url

        path = Path(test_path) if test_path else None

        if only_affected:
            test_files = DetectAffectedSpecsService().run()
            if test_files:
                console.print(RunMessages.create_affected_tests_table(test_files))
                console.print()
        else:
            test_files = load_test_files(path)

        if not test_files:
            RunMessages.no_tests_found()
            return

        original_count = count_total_tests(test_files)
        limited_test_files, folder_distribution = apply_test_limit(test_files, max_tests)
        selected_count = count_total_tests(limited_test_files)
        # Print test limit information if limiting was applied
        if max_tests is not None:
            console.print(RunMessages.create_test_limit_panel(
                original_count=original_count,
                selected_count=selected_count,
                max_tests=max_tests,
                folder_distribution=folder_distribution
            ))

        # Use the limited test files for execution
        test_files = limited_test_files

        results = []
        run_id = run_id or str(uuid.uuid4())

        # Initialize streaming service if requested
        stream_service, api_run_id = None, None
        if stream_results:
            stream_service, api_run_id = initialize_streaming_service(
                config, run_id, silent
            )

        # Create thread pool executor for background operations
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Execute each test
            for test_file in test_files:
                if not silent:
                    RunMessages.running_test_file(test_file['file'])

                # Handle both single test object and list of test objects
                content = test_file["content"]
                if not isinstance(content, list):
                    RunMessages.invalid_test_file_format(test_file['file'])
                    continue

                for test_data in content:
                    # Extract metadata before creating Test object
                    test = Test(**test_data)
                    test_executor_kwargs = {
                        "headless": headless,
                        "silent": silent,
                        "run_id": run_id,
                    }

                    result = await execute_single_test(
                        test,
                        config,
                        test_executor_kwargs,
                        stream_service,
                        api_run_id,
                        run_id,
                        executor,
                        silent,
                    )

                    results.append(result)

            if stream_results:
                RunMessages.updating_final_status()

        # Display results table
        console.print(RunMessages.create_results_table(results))

        # Display total time
        RunMessages.total_execution_time(time.time() - total_start_time)

        # Update final run status if streaming
        finalize_streaming_run(stream_service, api_run_id, results, time.time() - total_start_time)

        # Save results to JSON if output specified
        if output:
            save_results_to_json(output, config, run_id, results, time.time() - total_start_time)

        # Exit with non-zero status if any test failed
        if any(result.result == "fail" for result in results):
            raise typer.Exit(1)

    except typer.Exit:
        raise

    except Exception as e:
        RunMessages.error(str(e))
        raise typer.Exit(1) 