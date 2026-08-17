from app.rate_limiter import RateLimiter


def test_allows_up_to_per_minute_limit():
    limiter = RateLimiter(per_phone_per_minute=3, per_phone_per_day=100, global_per_minute=100)
    for _ in range(3):
        assert limiter.allow("+41790000000") is True
    assert limiter.allow("+41790000000") is False


def test_per_phone_limit_does_not_affect_other_numbers():
    limiter = RateLimiter(per_phone_per_minute=1, per_phone_per_day=100, global_per_minute=100)
    assert limiter.allow("+41790000001") is True
    assert limiter.allow("+41790000001") is False
    assert limiter.allow("+41790000002") is True


def test_global_limit_blocks_across_numbers():
    limiter = RateLimiter(per_phone_per_minute=100, per_phone_per_day=100, global_per_minute=2)
    assert limiter.allow("+41790000001") is True
    assert limiter.allow("+41790000002") is True
    assert limiter.allow("+41790000003") is False


def test_per_phone_daily_limit():
    limiter = RateLimiter(per_phone_per_minute=1000, per_phone_per_day=2, global_per_minute=1000)
    assert limiter.allow("+41790000001") is True
    assert limiter.allow("+41790000001") is True
    assert limiter.allow("+41790000001") is False


def test_auth_rate_limit_config_blocks_after_five_attempts_per_key():
    """Gleiche Werte wie app/bootstrap.py's auth_rate_limiter - Schutz gegen
    Brute-Force auf /api/firma/login und /api/firma/signup."""
    limiter = RateLimiter(per_phone_per_minute=5, per_phone_per_day=50, global_per_minute=30)
    key = "test@example.com|127.0.0.1"
    for _ in range(5):
        assert limiter.allow(key) is True
    assert limiter.allow(key) is False
