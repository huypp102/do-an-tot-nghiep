from epsipkg.textops import checksum, count_vowels


def test_checksum_stable():
    assert checksum("hello") == checksum("hello")
    assert checksum("hello") != checksum("world")


def test_checksum_empty():
    assert checksum("") == 0


def test_count_vowels():
    assert count_vowels("Hello World") == 3
    assert count_vowels("xyz") == 0
