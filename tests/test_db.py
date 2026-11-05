from triconsensus.db import SupabaseRepository


def test_config_hash_is_stable_for_key_order():
    a = SupabaseRepository.config_hash({"a": 1, "b": 2})
    b = SupabaseRepository.config_hash({"b": 2, "a": 1})
    assert a == b
