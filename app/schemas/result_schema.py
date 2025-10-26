# app/schemas/result_schema.py

from datetime import datetime
from typing import Any, Dict, List, Optional  # <--- Добавьте List

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


# --- Новая вспомогательная схема для объекта 'grade' ---
class GradeSchema(BaseModel):
    """Описывает объект оценки, присылаемый с фронтенда."""

    number: int
    text: str
    class_name: str = Field(
        ..., alias="class"
    )  # Используем alias, т.к. "class" - ключевое слово
    model_config = ConfigDict(
        populate_by_name=True,  # Разрешает использовать alias "class"
    )


class TestResultSchema(BaseModel):
    """Расширенная схема для testResults."""

    percentage: int = Field(..., ge=0, le=100)
    # --- Новые поля ---
    totalQuestions: int = Field(..., ge=0)
    correctAnswers: int = Field(..., ge=0)
    incorrectAnswers: int = Field(..., ge=0)
    grade: GradeSchema  # Используем новую схему для объекта grade
    incorrectQuestionsList: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


# ================== НАЧАЛО НОВОГО БЛОКА ==================
# Схемы, которые отсутствовали и которые нужны для графиков
class PerQuestionMetrics(BaseModel):
    # Pydantic v2 автоматически обработает 'latency' из JS (число) в int
    latency: Optional[int] = 0
    answerChanges: Optional[int] = 0
    focusLoss: Optional[int] = 0
    blurTime: Optional[float] = 0.0
    # Оставляем Any, т.к. там смешанные типы [x, y, timestamp]
    mouseMovements: Optional[List[List[Any]]] = Field(default_factory=list)


class KeyboardDynamics(BaseModel):
    lastName: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    firstName: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    middleName: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


class BehavioralMetrics(BaseModel):
    perQuestion: Optional[List[PerQuestionMetrics]] = Field(default_factory=list)
    keyboard: Optional[KeyboardDynamics] = Field(default_factory=KeyboardDynamics)


# ================== КОНЕЦ НОВОГО БЛОКА ==================


class SaveResultsRequest(BaseModel):
    test_type: str = Field(..., min_length=1, alias="testType")
    sessionId: str
    userInfo: UserInfoSchema
    testResults: TestResultSchema
    persistentId: Dict[str, Optional[str]]
    fingerprint: Dict[str, Any]

    # --- ДОБАВЛЕНО НОВОЕ ПОЛЕ ---
    sessionMetrics: SessionMetricsSchema

    behavioralMetrics: Optional[BehavioralMetrics] = Field(
        default_factory=BehavioralMetrics
    )
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,  # Добавлено для сложных типов в List[Any]
    )
