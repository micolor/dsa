from src.services import runtime_scheduler as module


def test_scheduler_accessor_round_trip():
    previous = module.get_runtime_scheduler_service()
    sentinel = object()
    try:
        module.set_runtime_scheduler_service(sentinel)
        assert module.get_runtime_scheduler_service() is sentinel
    finally:
        module.set_runtime_scheduler_service(previous)
