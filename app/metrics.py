# app/metrics.py
from prometheus_client import Counter, Gauge, Histogram

# --- Counters ---
# Счетчик завершенных тестов с метками (labels) для типа теста и результата (успех/неудача)
TESTS_COMPLETED_TOTAL = Counter(
    "app_tests_completed_total",
    "Total number of completed tests",
    ["test_type", "result"],
)

# Счетчик сгенерированных документов (сертификатов)
DOCUMENTS_GENERATED_TOTAL = Counter(
    "app_documents_generated_total",
    "Total number of generated documents",
    ["test_type"],
)

# --- Histograms ---
# Гистограмма для измерения времени анализа траектории мыши
ANALYSIS_DURATION_SECONDS = Histogram(
    "app_analysis_duration_seconds", "Time spent analyzing mouse trajectory"
)

# --- Gauges ---
# Датчик для отслеживания активных WebSocket-соединений
ACTIVE_WEBSOCKET_CONNECTIONS = Gauge(
    "app_active_websocket_connections", "Number of active WebSocket connections"
)
