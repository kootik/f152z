import json
import math
import os
import re
from typing import Any, Dict, List, Optional

import numpy as np
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

from app.extensions import db
from app.models import ResultMetadata
from app.utils.validators import validate_session_id

PASSING_SCORE_THRESHOLD = 80
MAX_STUDY_SESSION_LOOKUP_HOURS = 24
MAX_CONTENT_LENGTH = 10 * 1024 * 1024
PAUSE_THRESHOLD_SEC = 0.25
MAX_DISTANCE_THRESHOLD_PX = 1000

MAX_SESSION_ID_LENGTH = 128
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_EVENT_TYPE_LENGTH = 64


def extract_initial_stroke(trajectory: List[List[float]]) -> List[List[float]]:
    """Extract only the first deliberate movement before a long pause."""
    if len(trajectory) < 2:
        return trajectory

    for i in range(1, len(trajectory)):
        time_delta = (trajectory[i][2] - trajectory[i - 1][2]) / 1000.0
        if time_delta > PAUSE_THRESHOLD_SEC:
            return trajectory[:i]

    return trajectory


def compare_mouse_trajectories(
    traj1: List[List[float]], traj2: List[List[float]]
) -> float:
    """
    Compare two mouse trajectories using three-factor analysis.
    Returns similarity percentage (0-100).
    """
    stroke1 = extract_initial_stroke(traj1)
    stroke2 = extract_initial_stroke(traj2)

    if not stroke1 or not stroke2 or len(stroke1) < 10 or len(stroke2) < 10:
        return 0.0

    points1 = np.array([[p[0], p[1]] for p in stroke1])
    points2 = np.array([[p[0], p[1]] for p in stroke2])

    # FACTOR 1: Shape similarity (DTW)
    def get_shape_similarity(p1, p2):
        def normalize(points):
            min_coords = points.min(axis=0)
            max_coords = points.max(axis=0)
            range_coords = max_coords - min_coords
            range_coords[range_coords == 0] = 1
            return 1000 * (points - min_coords) / range_coords

        norm_p1 = normalize(p1)
        norm_p2 = normalize(p2)
        distance, _ = fastdtw(norm_p1, norm_p2, dist=euclidean)

        max_dist = 1000 * math.sqrt(2) * max(len(norm_p1), len(norm_p2))
        normalized_distance = distance / max_dist if max_dist > 0 else 1.0

        similarity = (1 - normalized_distance) * 100
        return max(0, min(100, similarity))

    shape_sim = get_shape_similarity(points1, points2)

    # FACTOR 2: Scale similarity
    def get_scale_similarity(p1, p2):
        size1 = p1.max(axis=0) - p1.min(axis=0)
        size2 = p2.max(axis=0) - p2.min(axis=0)

        width_diff = abs(size1[0] - size2[0])
        height_diff = abs(size1[1] - size2[1])

        width_sim = max(0, 1 - width_diff / 500)
        height_sim = max(0, 1 - height_diff / 500)

        return ((width_sim + height_sim) / 2) * 100

    scale_sim = get_scale_similarity(points1, points2)

    # FACTOR 3: Position similarity
    def get_position_similarity(p1, p2):
        center1 = p1.mean(axis=0)
        center2 = p2.mean(axis=0)
        distance = euclidean(center1, center2)
        return max(0, 1 - distance / MAX_DISTANCE_THRESHOLD_PX) * 100

    position_sim = get_position_similarity(points1, points2)

    # Weighted combination
    weights = {"shape": 0.60, "scale": 0.25, "position": 0.15}

    final_sim = (
        shape_sim * weights["shape"]
        + scale_sim * weights["scale"]
        + position_sim * weights["position"]
    )

    # Increase contrast for high similarities
    if final_sim > 80:
        final_sim = 80 + (final_sim - 80) * 1.5

    return max(0, min(100, final_sim))


from typing import Any, Dict, Optional

# Импортируем нашу модель SQLAlchemy и валидатор
from app.models import ResultMetadata
from app.utils.validators import validate_session_id


def find_result_file_by_session_id(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Находит и возвращает полные "сырые" данные результата для указанного ID сессии,
    обращаясь к базе данных через SQLAlchemy.

    Args:
        session_id: Уникальный идентификатор сессии.

    Returns:
        Словарь (dict) с полными данными теста или None, если сессия не найдена.
    """
    if not validate_session_id(session_id):
        # В реальном приложении здесь стоит добавить логирование
        # current_app.logger.warning(f"Invalid session ID format: {session_id}")
        return None

    # Запрос к БД с помощью SQLAlchemy. .get() ищет по первичному ключу.
    # Это очень быстро и эффективно.
    result = db.session.get(ResultMetadata, session_id)

    if result:
        # Поле raw_data имеет тип JSONB, SQLAlchemy автоматически
        # преобразует его в словарь Python.
        return result.raw_data

    return None
