# Файл: tests/test_performance.py
import pytest
from concurrent.futures import ThreadPoolExecutor
# --- ИЗМЕНЕНИЕ: Импортируем правильные модели и фабрики ---
from app.models import ResultMetadata
from tests.fixtures.sample_data import UserFactory, ResultMetadataFactory

class TestPerformance:
    def test_concurrent_requests(self, client):
        """Test handling concurrent requests."""
        def make_request():
            return client.get('/health')
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(50)]
            results = [f.result() for f in futures]
        
        assert all(r.status_code == 200 for r in results)

    @pytest.mark.benchmark
    def test_database_query_performance(self, db_session, benchmark):
        """Benchmark database query performance."""
        # --- ИЗМЕНЕНИЕ: Используем фабрики для создания тестовых данных ---
        user = UserFactory()
        for i in range(100):
            ResultMetadataFactory(user=user)
        
        # --- ИЗМЕНЕНИЕ: Бенчмарк запроса к реальной модели ResultMetadata ---
        def query_to_benchmark():
            ResultMetadata.query.filter_by(user_id=user.id).all()

        benchmark(query_to_benchmark)
