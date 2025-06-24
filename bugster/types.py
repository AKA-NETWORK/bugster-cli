from typing import Literal, Optional, List

from pydantic import BaseModel, Field


class TestMetadata(BaseModel):
    id: str
    last_modified: str


class Test(BaseModel):
    name: str
    task: str
    expected_result: str
    metadata: TestMetadata
    steps: Optional[list[str]] = None


class Credential(BaseModel):
    id: Optional[str] = None
    username: str
    password: str


class TestPreferences(BaseModel):
    always_run: Optional[List[str]] = Field(
        default=None,
        description="List of test file paths that should always be executed, regardless of changes"
    )


class Config(BaseModel):
    base_url: str
    credentials: list[Credential]
    project_id: str
    project_name: str
    x_vercel_protection_bypass: Optional[str] = Field(
        None, alias="x-vercel-protection-bypass"
    )
    preferences: Optional[TestPreferences] = Field(
        default=None,
        description="Test execution preferences and configurations"
    )


class WebSocketMessage(BaseModel):
    action: str


class WebSocketInitTestMessage(WebSocketMessage):
    action: Literal["init_test"] = "init_test"
    test: Test
    config: Config


class ToolRequest(BaseModel):
    id: str
    name: str
    args: dict


class WebSocketStepResultMessage(WebSocketMessage):
    action: Literal["step_result"] = "step_result"
    job_id: str
    tool: ToolRequest
    status: Literal["success", "error"]
    output: str


class WebSocketStepRequestMessage(WebSocketMessage):
    action: Literal["step_request"] = "step_request"
    job_id: str
    tool: ToolRequest
    message: str


class TestResult(BaseModel):
    result: Literal["pass", "fail"]
    reason: str


class WebSocketCompleteMessage(WebSocketMessage):
    action: Literal["complete"] = "complete"
    result: TestResult


class NamedTestResult(TestResult):
    name: str
    metadata: TestMetadata
    time: Optional[float] = None
