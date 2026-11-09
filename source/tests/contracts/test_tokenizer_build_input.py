import hashlib
import shutil
import subprocess
import sys
from pathlib import Path


def test_locked_tokenizer_loads_offline_after_cache_copy(tmp_path):
    root = Path(__file__).resolve().parents[2]
    tokenizer = root / "kernel/vendor/tiktoken/o200k_base.tiktoken"
    assert hashlib.sha256(tokenizer.read_bytes()).hexdigest() == "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
    cache = tmp_path / "fb374d419588a4632f3f557e76b4b70aebbca790"
    shutil.copyfile(tokenizer, cache)
    script = """
import os,sys,requests,tiktoken
os.environ['TIKTOKEN_CACHE_DIR'] = sys.argv[1]
def no_network(*args, **kwargs):
    raise AssertionError('Tokenizer attempted network access')
requests.get = no_network
encoder = tiktoken.get_encoding('o200k_base')
assert encoder.decode(encoder.encode('地下水统计与知识检索')) == '地下水统计与知识检索'
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True)
