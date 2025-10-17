# app/schemas/result_schema.py

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


# --- НОВАЯ СХЕМА ---
class SessionMetricsSchema(BaseModel):
    startTime: datetime
    endTime: datetime
    totalFocusLoss: int
    totalBlurTime: float
    printAttempts: int


class UserInfoSchema(BaseModel):
    lastName: str = Field(..., min_length=1, max_length=100)
    firstName: str = Field(..., min_length=1, max_length=100)
    middleName: Optional[str] = Field(None, max_length=100)
    position: str = Field(..., min_length=1, max_length=200)


class TestResultSchema(BaseModel):
    percentage: int = Field(..., ge=0, le=100)


class SaveResultsRequest(BaseModel):
    test_type: str = Field(..., min_length=1, alias="testType")
    sessionId: str
    userInfo: UserInfoSchema
    testResults: TestResultSchema
    persistentId: Dict[str, Optional[str]]
    fingerprint: Dict[str, Any]

    # --- ДОБАВЛЕНО НОВОЕ ПОЛЕ ---
    sessionMetrics: SessionMetricsSchema

    model_config = ConfigDict(
        populate_by_name=True,
    )
