"""Shared Pydantic models for the RecordLoop API."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class AzureConfig(BaseModel):
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    deployment: Optional[str] = None
    api_version: Optional[str] = None


class LLMConfig(BaseModel):
    provider: Literal["openai", "azure"] = "openai"
    model: Optional[str] = None        # defaults to gpt-5.4 in analyzer
    api_key: Optional[str] = None      # used when provider == "openai"
    azure: Optional[AzureConfig] = None


class TriggerRequest(BaseModel):
    repo: str                                # "owner/repo"
    pr_number: int
    preview_url: str = ""                     # Vercel/Netlify/etc. preview URL
    github_token: str                        # for reading PR + posting comment
    llm: LLMConfig = Field(default_factory=LLMConfig)
    pr_head_sha: str = ""                    # for fetching .github/recordloop.md at correct ref


class TriggerResponse(BaseModel):
    job_id: str
    status: str
    message: str = ""


class JobStatus(BaseModel):
    job_id: str
    status: str          # queued | analyzing | recording | done | failed
    repo: str
    pr_number: int
    preview_url: str
    created_at: str
    files_changed: Optional[int] = None
    flows_generated: Optional[int] = None
    recordings: Optional[list] = None
    note: Optional[str] = None
    error: Optional[str] = None
